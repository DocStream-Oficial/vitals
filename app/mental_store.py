"""
mental_store.py — Expediente longitudinal del Coach Mental deportivo (Fase 1
del roadmap coach-mental). Esqueleto COPIADO de app/coach_store.py: lock de
módulo, escritura atómica .tmp + os.replace, `_user_data_dir()`
household-aware con fallback legacy, API pública None-safe (nunca lanza,
loguea y degrada).

Storage: data/mental_log.json (legacy) / data/users/<uid>/mental_log.json
(household) = {"version": 1, "profile": {}, "sessions": [], "updated": "<iso>"}

Cada sesión: {"id", "date", "conversation_id", "resumen", "focos", "temas",
"raw"} — "raw"=True cuando el cierre cayó al fallback de texto crudo (el LLM
no devolvió JSON parseable).

Cap: máx 200 sesiones (evicta la más vieja, mismo espíritu de _apply_caps de
coach_store.py).
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vitals.mental_store")

# Lock de módulo (re-entrante) — mismo criterio que _STORE_LOCK de
# coach_store.py: serializa toda mutación read-modify-write del archivo único.
_STORE_LOCK = threading.RLock()

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_STORE_FILE = _DATA_DIR / "mental_log.json"  # legacy — ver _store_file()

_MAX_SESSIONS = 200
_EXPEDIENTE_TRUNCATE = 400  # chars por resumen en el bloque de prompt


def _user_data_dir() -> Optional[Path]:
    """Directorio del usuario activo si hay un contexto household-aware, o
    None si no (tests preexistentes, scripts) — mismo patrón EXACTO que
    app/coach_store.py::_user_data_dir()."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir()
    except Exception:
        pass
    return None


def _store_file() -> Path:
    d = _user_data_dir()
    return (d / "mental_log.json") if d is not None else _STORE_FILE


# ── helpers internos ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_urlsafe(12)


def _empty_store() -> dict:
    return {"version": 1, "profile": {}, "sessions": [], "updated": None}


def _atomic_write(data: dict) -> None:
    store_file = _store_file()
    store_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = store_file.with_suffix(store_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, store_file)


def _load_store() -> dict:
    try:
        text = _store_file().read_text(encoding="utf-8")
        if not text.strip():
            return _empty_store()
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("mental_log.json con forma inesperada; reseteando.")
            return _empty_store()
        data.setdefault("version", 1)
        if not isinstance(data.get("profile"), dict):
            data["profile"] = {}
        if not isinstance(data.get("sessions"), list):
            data["sessions"] = []
        data.setdefault("updated", None)
        return data
    except FileNotFoundError:
        return _empty_store()
    except json.JSONDecodeError as exc:
        logger.warning("mental_log.json inválido (%s); devolviendo vacío.", exc)
        return _empty_store()
    except Exception as exc:
        logger.warning("Error leyendo mental_log.json: %s", exc)
        return _empty_store()


def _apply_cap(store: dict) -> None:
    sessions = store.get("sessions") or []
    if len(sessions) > _MAX_SESSIONS:
        # Se asume orden cronológico de inserción (append_session siempre
        # agrega al final) — evicta las más viejas del inicio.
        store["sessions"] = sessions[-_MAX_SESSIONS:]


def _save_store(store: dict) -> None:
    try:
        _apply_cap(store)
        store["updated"] = _now_iso()
        _atomic_write(store)
    except Exception as exc:
        logger.error("Error persistiendo mental_log.json: %s", exc)


# ── API pública ────────────────────────────────────────────────────────────

def get_profile() -> dict:
    """Perfil mental del usuario. Sin archivo/perfil -> {}. Nunca lanza."""
    try:
        store = _load_store()
        profile = store.get("profile")
        return dict(profile) if isinstance(profile, dict) else {}
    except Exception as exc:
        logger.warning("get_profile falló: %s", exc)
        return {}


def set_profile(profile: dict) -> None:
    """Persiste el perfil mental (reemplaza el anterior entero). `profile`
    debe ser dict; si no, loguea y no escribe (None-safe, nunca lanza)."""
    if not isinstance(profile, dict):
        logger.warning("set_profile: se esperaba dict, se recibió %s; ignorado.", type(profile))
        return
    try:
        with _STORE_LOCK:
            store = _load_store()
            store["profile"] = dict(profile)
            _save_store(store)
    except Exception as exc:
        logger.error("set_profile falló: %s", exc)


def append_session(entry: dict) -> None:
    """Agrega una sesión al expediente. Completa id/fecha si faltan. Cap de
    _MAX_SESSIONS (evicta la más vieja). Nunca lanza."""
    if not isinstance(entry, dict):
        logger.warning("append_session: se esperaba dict, se recibió %s; ignorado.", type(entry))
        return
    try:
        with _STORE_LOCK:
            store = _load_store()
            e = dict(entry)
            e.setdefault("id", _new_id())
            e.setdefault("date", datetime.now(timezone.utc).date().isoformat())
            e.setdefault("resumen", "")
            e.setdefault("focos", [])
            e.setdefault("temas", [])
            e.setdefault("raw", False)
            sessions = store.setdefault("sessions", [])
            sessions.append(e)
            _save_store(store)
    except Exception as exc:
        logger.error("append_session falló: %s", exc)


def list_sessions(n: Optional[int] = None) -> list:
    """Sesiones ordenadas más recientes AL FINAL (orden de inserción). Sin
    `n`, todas. Nunca lanza."""
    try:
        store = _load_store()
        sessions = store.get("sessions") or []
        sessions = [dict(s) for s in sessions if isinstance(s, dict)]
        return sessions[-n:] if n else sessions
    except Exception as exc:
        logger.warning("list_sessions falló: %s", exc)
        return []


def expediente_block(n: int = 5) -> str:
    """Bloque de texto '=== EXPEDIENTE MENTAL ===' para el prompt del Coach
    Mental: perfil (arquetipo, calibraciones, encuesta) + últimas n sesiones
    (fecha + resumen truncado + focos) + línea de focos de la sesión más
    reciente. Sin perfil NI sesiones -> "" (primera sesión, sin expediente).
    Nunca lanza."""
    try:
        profile = get_profile()
        sessions = list_sessions(n)

        if not profile and not sessions:
            return ""

        lines = ["=== EXPEDIENTE MENTAL ==="]

        if profile:
            archetype = profile.get("archetype")
            if archetype:
                lines.append(f"Arquetipo de entrega: {archetype}")
            calibraciones = profile.get("calibraciones") or []
            if calibraciones:
                lines.append("Calibraciones:")
                for c in calibraciones:
                    lines.append(f"• {c}")
            survey = profile.get("survey") or {}
            if isinstance(survey, dict) and survey:
                lines.append("Respuestas de encuesta:")
                for k, v in survey.items():
                    lines.append(f"• {k}: {v}")
            deporte = profile.get("deporte")
            if deporte:
                lines.append(f"Deporte: {deporte}")

        if sessions:
            lines.append("")
            lines.append("SESIONES ANTERIORES:")
            for s in sessions:
                resumen = (s.get("resumen") or "").strip()
                if len(resumen) > _EXPEDIENTE_TRUNCATE:
                    resumen = resumen[:_EXPEDIENTE_TRUNCATE] + "…"
                focos = s.get("focos") or []
                focos_str = f" · Focos: {', '.join(focos)}" if focos else ""
                lines.append(f"• {s.get('date', '?')}: {resumen}{focos_str}")

            last_focos = sessions[-1].get("focos") or []
            if last_focos:
                lines.append("")
                lines.append(
                    "FOCOS DE LA SEMANA PASADA (cobrar adherencia en Acto 2): "
                    + ", ".join(last_focos)
                )

        return "\n".join(lines)
    except Exception as exc:
        logger.warning("expediente_block falló: %s", exc)
        return ""
