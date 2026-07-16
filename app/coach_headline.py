"""
coach_headline.py — Titular del Coach por IA (1-2 líneas), cacheado por firma
de estado. Paso 3 del roadmap Frescura de Alertas + Coach.

signature(dataset, changes) -> str
  Hash de factores medibles CUANTIZADOS (buckets/bandas, no valores exactos)
  + fecha del último día + kind del top cambio. Coarse a propósito: NO cambia
  por micro-fluctuaciones, solo cuando un factor cruza de banda o cambia el día.

load_cache()/save_cache() -> dict | None
  data/coach_headline.json = {signature, headline, generated_at, locale}.
  Escritura atómica (fsutil.atomic_write_text). None-safe: archivo ausente o
  corrupto -> None, nunca lanza.

maybe_regenerate(dataset, changes, locale, insights=None) -> None
  Si signature(dataset, changes, insights) != cache.signature (o locale
  distinto, o sin cache) -> delega en app.llm.generate() (backend
  intercambiable, F3 roadmap P0: claude_cli u openai_compat según
  settings.COACH_BACKEND) y cachea el resultado. Si la llamada falla -> deja
  el cache viejo intacto (no lo pisa con basura). SIEMPRE best-effort:
  try/except en torno a TODA la función — un fallo aquí nunca debe tumbar
  run_sync(). Se llama SOLO desde sync.py::run_sync() (nunca desde el path
  de GET /).
  `insights` (roadmap vitals-illness-proactivo, F3): lista completa de
  app.insights.evaluate(); los que traen severity=='alert' entran a la firma
  y al prompt (con instrucción explícita de no dar luz verde). Parámetro
  opcional — sin él, comportamiento idéntico a antes (backward-compat).

get_headline(dataset, changes, locale) -> str
  LEE el cache (nunca genera, nunca hace I/O de red ni subprocess). Si no hay
  cache, el locale no coincide, o el cache está corrupto -> fallback
  determinista (_fallback_headline). La llama main.py en / y /api/insights
  (si aplica) — instantáneo, cero llamada al LLM en el path del GET.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from app import llm as _llm
from app.config import settings
from app.fsutil import atomic_write_text
from app.i18n import tr

logger = logging.getLogger("vitals.coach_headline")

# Sentinel (deuda R2, aislamiento de tests): None en reposo -> el accessor
# resuelve SIEMPRE contra settings.DATA_DIR en runtime, así un
# importlib.reload(coach_headline) nunca re-liga esta constante a una ruta
# congelada de import-time. Override SOLO para tests (patch.object/
# monkeypatch.setattr(ch, "_CACHE_PATH", ruta) — sigue funcionando idéntico,
# ver docstring de _cache_path).
_CACHE_PATH: Optional[Path] = None  # legacy — usado si userctx no está activo


def _cache_path() -> Path:
    """Ruta a coach_headline.json del usuario activo (Fase 8D, paso D3:
    household). Fuera de un request household-aware (is_context_active()=
    False — tests preexistentes, scheduler sin contexto fijado), usa
    _CACHE_PATH tal cual si fue fijado explícitamente; si no, resuelve en
    RUNTIME contra settings.DATA_DIR (reload-proof — ver comentario del
    sentinel arriba). Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "coach_headline.json"
    except Exception:
        pass
    if _CACHE_PATH is not None:   # override explícito de un test
        return _CACHE_PATH
    return settings.DATA_DIR / "coach_headline.json"   # resolución RUNTIME
_BRAIN_PATH = Path(__file__).resolve().parent / "coach_brain.md"

_CLI_TIMEOUT = 60  # segundos; el titular es corto, no necesita los 90s del chat


# ── Buckets (coarse a propósito — no regenerar por micro-fluctuación) ───────

def _bucket_recovery(v) -> str:
    if v is None:
        return "na"
    v = float(v)
    if v >= 67:
        return "high"
    if v >= 34:
        return "mid"
    return "low"


def _bucket_hrv_vs_base(today, base) -> str:
    if today is None or base in (None, 0):
        return "na"
    pct = (float(today) - float(base)) / float(base)
    if pct >= 0.08:
        return "above"
    if pct <= -0.08:
        return "below"
    return "at_base"


def _bucket_sleep(asleep_min, target_min=480) -> str:
    if asleep_min is None:
        return "na"
    asleep_min = float(asleep_min)
    short_threshold = (target_min or 480) - 60
    if asleep_min < short_threshold:
        return "short"
    if asleep_min >= (target_min or 480):
        return "met"
    return "ok"


def _bucket_strain(v) -> str:
    if v is None:
        return "na"
    v = float(v)
    if v >= 14:
        return "high"
    if v >= 7:
        return "mid"
    return "low"


def _alert_ids(insights: Optional[list]) -> list[str]:
    """Ids de insights con severity=='alert', ordenados y sin duplicados
    (roadmap vitals-illness-proactivo, F3, criterio #7: estables -> misma
    lista de alertas produce SIEMPRE el mismo sufijo de firma)."""
    if not insights:
        return []
    ids = {i.get("id") for i in insights if isinstance(i, dict) and i.get("severity") == "alert" and i.get("id")}
    return sorted(ids)


def signature(dataset: dict, changes: Optional[list] = None, insights: Optional[list] = None) -> str:
    """Firma coarse del estado actual: buckets de recovery/HRV-vs-base/sueño/
    strain + fecha del último día + kind del top cambio (si hay). Dos estados
    "parecidos" (misma banda) producen la MISMA firma -> no regenera.
    None-safe: dataset vacío -> firma estable basada en "sin datos".

    F3 (roadmap vitals-illness-proactivo): `insights` es opcional y trae la
    lista completa de insights evaluados (evaluate()); se extraen solo los
    ids con severity=='alert' (ordenados) y se agregan al final del `raw`
    -> aparecer/desaparecer una alerta cambia la firma y fuerza regenerar el
    titular. SIN alertas (insights=None, [] o ninguna con severity=='alert')
    el sufijo queda vacío y `raw` es BYTE-IDÉNTICO al de antes de este
    cambio -> backward-compat: no invalida el cache de nadie sin motivo."""
    dataset = dataset or {}
    days = dataset.get("days") or []
    summary = dataset.get("summary") or {}
    changes = changes or []

    if not days:
        raw = "no_data"
    else:
        today = days[-1]
        date_str = today.get("date", "")
        hrv_base = summary.get("hrv_base_recent") or summary.get("hrv_base")
        rec_bucket = _bucket_recovery(today.get("recovery"))
        hrv_bucket = _bucket_hrv_vs_base(today.get("hrv"), hrv_base)
        # sleep-goal-vs-need: titular usa el OBJETIVO (sleep_goal_min) con
        # fallback a la NECESIDAD y luego 480, mismo patrón que app/changes.py.
        # _bucket_sleep NO cambia de firma.
        sleep_bucket = _bucket_sleep(today.get("asleep"), summary.get("sleep_goal_min") or summary.get("sleep_target_min") or 480)
        strain_bucket = _bucket_strain(today.get("strain"))
        top_kind = changes[0].get("kind", "none") if changes else "none"
        top_factor = changes[0].get("factor", "none") if changes else "none"
        raw = f"{date_str}|{rec_bucket}|{hrv_bucket}|{sleep_bucket}|{strain_bucket}|{top_factor}:{top_kind}"

    alert_ids = _alert_ids(insights)
    if alert_ids:
        raw += "|alerts:" + ",".join(alert_ids)

    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


# ── Cache atómico ────────────────────────────────────────────────────────────

def load_cache() -> Optional[dict]:
    """Lee coach_headline.json del usuario activo. None si no existe o está
    corrupto (nunca lanza)."""
    try:
        path = _cache_path()
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        return data
    except Exception as exc:
        logger.warning("No pude leer coach_headline.json (%s); se ignora el cache.", exc)
        return None


def save_cache(signature_val: str, headline: str, locale: str) -> None:
    """Escritura atómica del cache. Nunca lanza (best-effort, logueado)."""
    try:
        import datetime as _dt
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "signature": signature_val,
            "headline": headline,
            "generated_at": _dt.datetime.now().isoformat(timespec="seconds"),
            "locale": locale,
        }
        atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning("No pude guardar coach_headline.json: %s", exc)


# ── Fallback determinista ────────────────────────────────────────────────────

def _fallback_headline(changes: Optional[list], dataset: dict, locale: str = "es") -> str:
    """Frase armada del TOP evento de cambio (o del foco primario del coach si
    no hay cambios). i18n ×4. Nunca vacío feo."""
    dataset = dataset or {}
    days = (dataset or {}).get("days") or []
    if not days:
        return tr("headline_fallback_no_data", locale)

    if changes:
        top = changes[0]
        change_text = top.get("summary") or top.get("text") or ""
        if change_text:
            return tr("headline_fallback_change", locale, change_text=change_text)

    return tr("headline_fallback_static", locale)


# ── Prompt del titular IA ────────────────────────────────────────────────────

def _load_brain() -> str:
    try:
        return _BRAIN_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "Eres el Coach de Vitals, coach de salud personal directo. "
            "No eres médico. Aconseja con datos de wearable, sé específico y conciso."
        )


_LOCALE_LANG = {"es": "español", "en": "English", "fr": "français", "pt": "português"}


def _build_headline_prompt(dataset: dict, changes: list, locale: str, alerts: Optional[list] = None) -> str:
    """F3 (roadmap vitals-illness-proactivo): `alerts` es opcional (lista de
    insights con severity=='alert'). SIN alertas, esta función devuelve
    EXACTAMENTE el mismo prompt que antes de este cambio (criterio #8: byte-
    idéntico) — el bloque de alertas y la instrucción de "no luz verde" solo
    se agregan cuando alerts trae algo."""
    brain = _load_brain()
    days = dataset.get("days") or []
    today = days[-1] if days else {}
    alerts = alerts or []

    lines = ["=== ESTADO DE HOY ==="]
    if today.get("recovery") is not None:
        lines.append(f"Recuperación: {today['recovery']}%")
    if today.get("hrv") is not None:
        lines.append(f"HRV: {today['hrv']} ms")
    if today.get("asleep") is not None:
        lines.append(f"Sueño: {round(today['asleep']/60, 1)}h")
    if today.get("strain") is not None:
        lines.append(f"Esfuerzo: {today['strain']}/21")

    if changes:
        lines.append("\n=== QUÉ CAMBIÓ HOY (vs ayer / base reciente) ===")
        for c in changes[:5]:
            lines.append(f"- {c.get('summary', '')}")
    else:
        lines.append("\n=== SIN CAMBIOS SIGNIFICATIVOS HOY (estado estable) ===")

    if alerts:
        lines.append("\n=== ALERTA(S) DE SALUD ACTIVA(S) (motor de insights) ===")
        for a in alerts:
            title = a.get("title", "")
            summ = a.get("summary", "")
            lines.append(f"- {title}: {summ}" if summ else f"- {title}")

    output_lang = _LOCALE_LANG.get(locale, "español")
    instruction = (
        f"Escribe un TITULAR de 1-2 líneas (máx ~140 caracteres) para la tarjeta del "
        f"Coach en la app, en {output_lang}. Si hay cambios, ancla el titular al cambio "
        f"más relevante de hoy; si no hay cambios, resume el estado general con foco "
        f"en la prioridad #1 del usuario. Sin saludos, sin markdown, sin comillas, "
        f"solo el texto del titular. No diagnostiques; ante señales de posible "
        f"enfermedad usa tono de vigilancia, no alarmista."
    )
    if alerts:
        instruction += (
            " Hay una alerta de salud ACTIVA arriba: NO des luz verde ni celebres "
            "los números aunque recovery/HRV/sueño se vean bien — menciona la alerta "
            "explícitamente y ajusta el tono a vigilancia, no la ignores."
        )

    return (
        f"{brain}\n\n"
        f"{chr(10).join(lines)}\n\n"
        f"{instruction}"
    )


def _call_cli(prompt: str) -> Optional[str]:
    """Delega en app.llm.generate() (backend intercambiable, F3 roadmap P0 —
    conserva el NOMBRE de la función porque main.py/tests siguen llamando
    coach_headline._call_cli como seam de mockeo). None si falla (nunca lanza).
    El recorte a 400 chars se conserva AQUÍ (en el caller), como pide el
    roadmap — generate() no trunca, eso es responsabilidad de cada consumidor."""
    answer = _llm.generate(prompt, timeout=_CLI_TIMEOUT, purpose="headline")
    if not answer:
        return None
    # El titular debe ser 1-2 líneas cortas; recorte defensivo por si el
    # backend devuelve más de lo pedido (nunca dejamos pasar un bloque enorme a la UI).
    return answer[:400]


# ── API pública ──────────────────────────────────────────────────────────────

def maybe_regenerate(dataset: dict, changes: Optional[list] = None, locale: str = "es",
                      insights: Optional[list] = None) -> None:
    """Regenera el titular SOLO si la firma cambió (o el locale cambió, o no
    hay cache). Best-effort total: cualquier excepción se traga y se loguea,
    NUNCA propaga — se llama desde run_sync() y no debe tumbar el sync.
    Si el CLI falla, el cache viejo se conserva intacto (no se sobreescribe
    con nada).

    F3 (roadmap vitals-illness-proactivo): `insights` es opcional (lista
    completa de evaluate()). Si trae insights con severity=='alert', esos
    entran a la firma (signature) Y al prompt (con la instrucción de no dar
    luz verde). Sin insights o sin alertas -> comportamiento y firma
    idénticos a antes de este parámetro (backward-compat, contrato #9)."""
    try:
        changes = changes or []
        sig = signature(dataset, changes, insights)
        cache = load_cache()
        if cache and cache.get("signature") == sig and cache.get("locale") == locale:
            return  # cache hit: firma sin cambios -> CERO llamadas al CLI

        alerts = [i for i in (insights or []) if isinstance(i, dict) and i.get("severity") == "alert"]
        prompt = _build_headline_prompt(dataset or {}, changes, locale, alerts)
        headline = _call_cli(prompt)
        if headline:
            save_cache(sig, headline, locale)
        else:
            logger.warning("maybe_regenerate: CLI falló, se conserva el cache anterior (si existe).")
    except Exception as exc:
        # Red de seguridad final: bajo NINGUNA circunstancia esto debe tumbar run_sync().
        logger.warning("maybe_regenerate falló por completo (best-effort, ignorado): %s", exc)


def get_headline(dataset: dict, changes: Optional[list] = None, locale: str = "es") -> str:
    """LEE el cache (nunca genera, cero I/O de red/subprocess). Si no hay
    cache, la firma no coincide, o el locale no coincide -> fallback
    determinista. Instantáneo — apto para el path de GET /."""
    try:
        changes = changes or []
        cache = load_cache()
        if cache and cache.get("locale") == locale and cache.get("headline"):
            return cache["headline"]
    except Exception as exc:
        logger.warning("get_headline: error leyendo cache, uso fallback: %s", exc)
    return _fallback_headline(changes, dataset, locale)
