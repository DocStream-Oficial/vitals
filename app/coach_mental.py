"""
coach_mental.py — Coach Deportivo (Sesión Master en texto, roadmap
coach-mental Fase 1, Paso 3). Espejo de la estructura de app/coach_chat.py:
mismo backend de LLM (app.llm.generate), mismo timeout (90s), mismo patrón de
degradación con gracia (nunca lanza, nunca deja perder la sesión).

Nombre de cara al usuario: "Coach Deportivo" (identificadores internos —
módulo, kind "mental_master", mental_log.json — se quedan como están, es
decisión explícita del roadmap: solo lo user-facing cambia de nombre).

build_master_prompt(question, dataset, history) -> str
  brain (app/coach_mental_brain.md) + contexto fisiológico (reusa
  coach_chat._build_context — NO se duplica) + expediente mental
  (mental_store.expediente_block) + historial de la conversación + pregunta.

ask_master(question, dataset, history) -> str
  Arma el prompt y llama a app.llm.generate(). None -> fallback amable i18n.

opening_message(dataset) -> str
  Genera el mensaje de apertura del Acto 1 (LLM caído -> fallback estático).

close_session(cid, dataset) -> dict
  Pide al LLM un JSON de cierre (resumen/temas/focos), lo parsea de forma
  defensiva y SIEMPRE persiste algo en el expediente — la sesión nunca se
  pierde, ni con JSON malformado ni con el LLM caído.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from app import llm as _llm
from app import mental_store as _mental_store
from app.coach_chat import _build_context
from app.i18n import tr as _tr

logger = logging.getLogger("vitals.coach_mental")

# ── Cerebro del Coach Deportivo (app/coach_mental_brain.md, NO se modifica) ──
_BRAIN_PATH = Path(__file__).resolve().parent / "coach_mental_brain.md"

_BRAIN_FALLBACK = (
    "Eres el Coach Deportivo de Vitals: psicólogo deportivo socrático, en "
    "español natural. Trabajas la cabeza del atleta (ACT, Inner Game, TCC, "
    "PST). No eres terapeuta clínico: ante señales de patología, reconoce el "
    "límite y sugiere ayuda profesional."
)

# Directiva de apertura del Acto 1 (mismo mecanismo de "pregunta fija
# interna" que describe el roadmap — nunca se le muestra al usuario).
_OPENING_QUESTION = (
    "Abre la Sesión Master de hoy (Acto 1): saluda breve y pregunta cómo "
    "viene. Si el expediente trae focos previos, NO los cobres aún."
)

_LOCALE_LANG = {
    "es": "español",
    "en": "English",
    "fr": "français",
    "pt": "português",
}

_CLOSE_INSTRUCTION = (
    "\n\n=== INSTRUCCIÓN DE CIERRE ===\n"
    "La Sesión Master terminó. Analiza la transcripción de arriba y responde "
    "SOLO con un JSON (nada de texto antes o después) con esta forma exacta:\n"
    '{"resumen": "resumen breve de la sesión en 2-4 líneas", '
    '"temas": ["tema1", "tema2"], "focos": ["foco concreto 1", "foco concreto 2 (máx 2)"]}'
)


def _load_brain() -> str:
    try:
        return _BRAIN_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("No pude leer coach_mental_brain.md (%s); uso fallback corto.", exc)
        return _BRAIN_FALLBACK


def _resolve_locale() -> str:
    """Locale efectivo del usuario, mismo mecanismo que coach_chat._build_prompt
    (duplicado local a propósito — el roadmap pide NO modificar coach_chat.py
    para esto)."""
    try:
        from app.profile import effective as _peff
        return _peff("locale") or "es"
    except Exception:
        return "es"


def _output_lang(locale: str) -> str:
    return _LOCALE_LANG.get(locale, "español")


def _history_block(history: Optional[list]) -> str:
    if not history:
        return ""
    lines = []
    for turn in history[-10:]:
        role = turn.get("role", "user") if isinstance(turn, dict) else "user"
        content = turn.get("content", "") if isinstance(turn, dict) else ""
        prefix = "USUARIO" if role == "user" else "COACH"
        lines.append(f"{prefix}: {content}")
    if not lines:
        return ""
    return "\nCONVERSACIÓN PREVIA:\n" + "\n".join(lines) + "\n"


def build_master_prompt(question: str, dataset: dict, history: Optional[list] = None) -> str:
    """Prompt completo de la Sesión Master: brain + contexto fisiológico +
    expediente mental + historial de ESTA conversación + pregunta + directiva
    de idioma. Nunca lanza (cada bloque se arma con try/except interno vía
    los módulos que consume, que ya son None-safe)."""
    brain = _load_brain()

    try:
        context = _build_context(dataset or {})
    except Exception as exc:
        logger.warning("build_master_prompt: _build_context falló (%s); contexto vacío.", exc)
        context = "CONTEXTO: (sin datos disponibles)."

    expediente = _mental_store.expediente_block()
    if not expediente:
        expediente = "(PRIMERA SESIÓN: sin expediente todavía — ver doctrina, Acto 3 de conocimiento.)"

    history_block = _history_block(history)
    locale = _resolve_locale()
    output_lang = _output_lang(locale)

    return (
        f"{brain}\n\n"
        f"=== CONTEXTO FISIOLÓGICO ===\n{context}\n\n"
        f"{expediente}\n"
        f"{history_block}\n"
        f"=== PREGUNTA ===\n{question}\n\n"
        f"Responde como el Coach Deportivo según la doctrina de arriba: en {output_lang}, "
        f"socrático, 2-5 líneas por turno, una pregunta por turno (máx dos)."
    )


def ask_master(question: str, dataset: dict, history: Optional[list] = None) -> str:
    """Arma el prompt de la Sesión Master y delega en app.llm.generate()
    (mismo backend intercambiable y timeout=90s que ask_coach). LLM caído
    -> fallback amable i18n, nunca lanza."""
    prompt = build_master_prompt(question, dataset, history)
    answer = _llm.generate(prompt, timeout=90, purpose="coach_mental")
    if answer:
        return answer
    return _tr("mental_llm_fallback", _resolve_locale())


def opening_message(dataset: dict) -> str:
    """Mensaje de apertura del Acto 1 de una Sesión Master nueva. LLM caído
    -> fallback estático i18n (nunca 500, nunca deja el chat vacío)."""
    prompt = build_master_prompt(_OPENING_QUESTION, dataset, history=None)
    answer = _llm.generate(prompt, timeout=90, purpose="coach_mental_opening")
    if answer:
        return answer
    return _tr("mental_opening_fallback", _resolve_locale())


def _extract_json_block(text: str) -> Optional[dict]:
    """Parseo defensivo: busca el primer '{' y el último '}' del output y
    intenta json.loads sobre ese slice. None si no hay match o el JSON es
    inválido — nunca lanza."""
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        parsed = json.loads(text[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _coerce_close_result(parsed: Optional[dict], raw_text: str) -> dict:
    """Valida la forma del JSON de cierre (resumen str, focos list[str] máx 2,
    temas list[str]). Cualquier fallo de forma -> fallback de texto crudo
    truncado, raw=True. Nunca lanza."""
    if not isinstance(parsed, dict):
        return {
            "resumen": (raw_text or "")[:2000],
            "temas": [],
            "focos": [],
            "raw": True,
        }
    try:
        resumen = parsed.get("resumen")
        resumen = str(resumen).strip() if resumen is not None else ""
        if not resumen:
            resumen = (raw_text or "")[:2000]

        temas = parsed.get("temas")
        temas = [str(t) for t in temas] if isinstance(temas, list) else []

        focos = parsed.get("focos")
        focos = [str(f) for f in focos] if isinstance(focos, list) else []
        focos = focos[:2]

        return {"resumen": resumen, "temas": temas, "focos": focos, "raw": False}
    except Exception:
        return {
            "resumen": (raw_text or "")[:2000],
            "temas": [],
            "focos": [],
            "raw": True,
        }


def close_session(cid: str, dataset: dict) -> dict:
    """Cierra una Sesión Master: pide al LLM un JSON {resumen, temas, focos},
    lo parsea de forma defensiva, y SIEMPRE guarda algo en el expediente (la
    sesión nunca se pierde, ni con JSON malformado ni con el LLM caído).
    Devuelve el dict guardado (incluye date/id/conversation_id)."""
    from app import coach_store as _coach_store

    try:
        conv = _coach_store.get_conversation(cid) or {}
        messages = conv.get("messages") or []
        transcript_lines = []
        for m in messages:
            role = "USUARIO" if m.get("role") == "user" else "COACH"
            transcript_lines.append(f"{role}: {m.get('content', '')}")
        transcript = "\n".join(transcript_lines)
    except Exception as exc:
        logger.warning("close_session: no pude leer la conversación %r (%s).", cid, exc)
        transcript = ""

    prompt = (
        f"{_load_brain()}\n\n"
        f"=== TRANSCRIPCIÓN DE LA SESIÓN ===\n{transcript}"
        f"{_CLOSE_INSTRUCTION}"
    )

    answer = _llm.generate(prompt, timeout=90, purpose="coach_mental_close")

    if not answer:
        result = {
            "resumen": "(sesión sin resumen — LLM no disponible)",
            "temas": [],
            "focos": [],
            "raw": True,
        }
    else:
        parsed = _extract_json_block(answer)
        result = _coerce_close_result(parsed, answer)

    result["conversation_id"] = cid
    _mental_store.append_session(dict(result))
    return result
