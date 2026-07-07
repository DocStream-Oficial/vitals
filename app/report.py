"""
report.py — Informe narrativo semanal/mensual (Fase 8B, paso B6).

build_report_data(dataset, journal, period, ref_date) -> dict | None
  PURO (sin I/O). Agrega el ÚLTIMO período COMPLETO (semana ISO lun-dom, o mes
  calendario) ANTERIOR o que ya terminó relativo a ref_date: medias de
  recovery/sueño/strain/HRV, deltas vs período anterior, mejor/peor día,
  adherencia del journal, top insight del período (driver más fuerte de
  app.drivers sobre los días del período), tendencia (app.trends). None si no
  hay NINGÚN día de dataset dentro del período completo más reciente.

maybe_regenerate_reports(dataset, journal, locale) -> None
  Llamada SOLO desde sync.py::run_sync(), tras coach_headline.maybe_regenerate.
  Firma = (period_key, locale) por cada period ("weekly","monthly"); si cambió
  -> delega en app.llm.generate() (backend intercambiable, F3 roadmap P0) con
  un prompt de los números del período + coach_brain.md -> narrativa 4-6
  frases con 2 acciones. Best-effort TOTAL: cualquier excepción se traga y
  loguea, nunca tumba run_sync(); si la llamada falla, el cache viejo se conserva.
  Caché data/reports.json = {weekly: {...}, monthly: {...}}.

get_report(period, locale) -> dict
  LEE el cache (nunca genera, cero I/O de red/subprocess). Si no hay cache
  para ese period+locale, cae al fallback determinista: solo los números
  agregados (sin narrativa) + nota i18n. Nunca 500 — instantáneo, apto para
  el path de GET /api/report.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Optional

from app import llm as _llm
from app.config import settings
from app.fsutil import atomic_write_text
from app.i18n import tr

logger = logging.getLogger("vitals.report")

# Sentinel (deuda R2, aislamiento de tests): None en reposo -> el accessor
# resuelve SIEMPRE contra settings.DATA_DIR en runtime, así un
# importlib.reload(report) nunca re-liga esta constante a una ruta congelada
# de import-time. Override SOLO para tests (patch.object(report,
# "_CACHE_PATH", ruta) — sigue funcionando idéntico, ver docstring de
# _cache_path).
_CACHE_PATH: Optional[Path] = None  # legacy — usado si userctx no está activo


def _cache_path() -> Path:
    """Ruta a reports.json del usuario activo (Fase 8D, paso D3: household).
    Fuera de un request household-aware (is_context_active()=False — tests
    preexistentes, scheduler sin contexto fijado), usa _CACHE_PATH tal cual
    si fue fijado explícitamente; si no, resuelve en RUNTIME contra
    settings.DATA_DIR (reload-proof — ver comentario del sentinel arriba).
    Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "reports.json"
    except Exception:
        pass
    if _CACHE_PATH is not None:   # override explícito de un test
        return _CACHE_PATH
    return settings.DATA_DIR / "reports.json"   # resolución RUNTIME
_BRAIN_PATH = Path(__file__).resolve().parent / "coach_brain.md"

_CLI_TIMEOUT = 90  # roadmap: recorte defensivo 2000 chars, timeout 90s

PERIODS = ("weekly", "monthly")


# ── Ventanas de período (ISO semana lun-dom / mes calendario) ───────────────

def _week_bounds(d: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Lunes-domingo de la semana ISO que contiene `d`."""
    monday = d - datetime.timedelta(days=d.weekday())
    sunday = monday + datetime.timedelta(days=6)
    return monday, sunday


def _month_bounds(d: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Primer-último día del mes calendario que contiene `d`."""
    first = d.replace(day=1)
    if d.month == 12:
        next_first = d.replace(year=d.year + 1, month=1, day=1)
    else:
        next_first = d.replace(month=d.month + 1, day=1)
    last = next_first - datetime.timedelta(days=1)
    return first, last


def _last_complete_period(period: str, ref_date: datetime.date) -> tuple[datetime.date, datetime.date]:
    """Ventana [start, end] del último período COMPLETO relativo a ref_date.
    Si ref_date cae DENTRO de un período aún en curso (end >= ref_date), se
    usa el período ANTERIOR (el actual no está completo todavía)."""
    if period == "monthly":
        start, end = _month_bounds(ref_date)
        if end >= ref_date:
            prev_ref = start - datetime.timedelta(days=1)
            start, end = _month_bounds(prev_ref)
        return start, end
    # weekly (default)
    start, end = _week_bounds(ref_date)
    if end >= ref_date:
        prev_ref = start - datetime.timedelta(days=1)
        start, end = _week_bounds(prev_ref)
    return start, end


def _period_key(period: str, start: datetime.date, end: datetime.date) -> str:
    if period == "monthly":
        return start.strftime("%Y-%m")
    return f"{start.isoformat()}_{end.isoformat()}"


# ── Helpers de agregación (puros) ───────────────────────────────────────────

def _days_in_range(days: list, start: datetime.date, end: datetime.date) -> list:
    out = []
    for d in days or []:
        if not isinstance(d, dict):
            continue
        dt = d.get("date")
        if not isinstance(dt, str):
            continue
        try:
            parsed = datetime.date.fromisoformat(dt)
        except Exception:
            continue
        if start <= parsed <= end:
            out.append(d)
    out.sort(key=lambda d: d.get("date", ""))
    return out


def _mean(vals: list) -> Optional[float]:
    clean = [v for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _metric_means(days: list) -> dict:
    metrics = ["recovery", "hrv", "strain", "asleep", "sleep_perf"]
    out = {}
    for m in metrics:
        out[m] = _mean([d.get(m) for d in days])
    return out


def _best_worst_day(days: list, metric: str = "recovery") -> tuple[Optional[str], Optional[str]]:
    """(fecha_mejor, fecha_peor) por `metric` entre los días del período con
    valor no-None. (None, None) si no hay ningún valor."""
    valid = [(d.get("date"), d.get(metric)) for d in days if d.get(metric) is not None]
    if not valid:
        return None, None
    best = max(valid, key=lambda t: t[1])
    worst = min(valid, key=lambda t: t[1])
    return best[0], worst[0]


def _journal_adherence(journal: dict, start: datetime.date, end: datetime.date) -> dict:
    """Días con AL MENOS una entrada de journal / días totales del período."""
    entries = (journal or {}).get("entries") or {}
    n_period_days = (end - start).days + 1
    logged = 0
    cur = start
    while cur <= end:
        if isinstance(entries.get(cur.isoformat()), dict):
            logged += 1
        cur += datetime.timedelta(days=1)
    pct = round((logged / n_period_days) * 100) if n_period_days else 0
    return {"days_logged": logged, "days_total": n_period_days, "pct": pct}


def _top_insight(days: list, locale: str) -> Optional[dict]:
    """Driver más fuerte (|ρ| máx) evaluado SOLO sobre los días del período,
    vía app.drivers.analyze_drivers (reuso, no duplicación). None si no hay
    suficientes datos en la ventana (analyze_drivers ya gatea por n)."""
    try:
        from app.drivers import analyze_drivers
        findings = analyze_drivers(days, locale=locale)
        return findings[0] if findings else None
    except Exception as exc:
        logger.warning("_top_insight (drivers) falló, se omite: %s", exc)
        return None


def _trend_for(days: list, metric: str) -> dict:
    try:
        from app.trends import trend_summary
        return trend_summary([d.get(metric) for d in days])
    except Exception as exc:
        logger.warning("_trend_for(%s) falló: %s", metric, exc)
        return {"slope": None, "direction": "estable", "significant": None, "n": 0}


# ── build_report_data — PURO ────────────────────────────────────────────────

def build_report_data(dataset: dict, journal: Optional[dict], period: str,
                       ref_date: Optional[datetime.date] = None) -> Optional[dict]:
    """Agrega el ÚLTIMO período COMPLETO (weekly|monthly) relativo a ref_date
    (hoy si None). PURO: sin I/O. None si el dataset no tiene NINGÚN día
    dentro de esa ventana (nada que reportar todavía), o si algo interno
    falla — nunca lanza (criterio del roadmap: 'maneja huecos sin crashear').
    """
    try:
        if period not in PERIODS:
            return None

        dataset = dataset or {}
        all_days = dataset.get("days") or []
        if not isinstance(all_days, list):
            all_days = []
        journal = journal or {}
        ref_date = ref_date or datetime.date.today()

        start, end = _last_complete_period(period, ref_date)
        period_days = _days_in_range(all_days, start, end)

        if not period_days:
            return None

        prev_ref = start - datetime.timedelta(days=1)
        # Ventana anterior INMEDIATA (mismo tamaño de período, justo antes de `start`).
        if period == "monthly":
            prev_start, prev_end = _month_bounds(prev_ref)
        else:
            prev_start, prev_end = _week_bounds(prev_ref)
        prev_period_days = _days_in_range(all_days, prev_start, prev_end)

        means = _metric_means(period_days)
        prev_means = _metric_means(prev_period_days) if prev_period_days else {}

        deltas = {}
        for metric, val in means.items():
            prev_val = prev_means.get(metric)
            if val is not None and prev_val is not None:
                deltas[metric] = round(val - prev_val, 1)
            else:
                deltas[metric] = None

        best_day, worst_day = _best_worst_day(period_days, "recovery")

        locale_for_insight = "es"  # se re-evalúa con el locale real en el caller (maybe_regenerate/get_report)
        top_insight = _top_insight(period_days, locale_for_insight)

        trend_recovery = _trend_for(period_days, "recovery")

        return {
            "period": period,
            "period_key": _period_key(period, start, end),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "n_days": len(period_days),
            "means": {k: (round(v, 1) if v is not None else None) for k, v in means.items()},
            "deltas": deltas,
            "best_day": best_day,
            "worst_day": worst_day,
            "adherence": _journal_adherence(journal, start, end),
            "top_insight": top_insight,
            "trend_recovery": trend_recovery,
        }
    except Exception as exc:
        logger.warning("build_report_data falló (degradando a None): %s", exc)
        return None


# ── Cache atómico (patrón coach_headline.py) ────────────────────────────────

def load_cache() -> dict:
    """Lee reports.json del usuario activo -> {weekly: {...}, monthly: {...}}.
    {} si no existe o está corrupto (nunca lanza)."""
    try:
        path = _cache_path()
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception as exc:
        logger.warning("No pude leer reports.json (%s); se ignora el cache.", exc)
        return {}


def save_cache(cache: dict) -> None:
    """Escritura atómica del cache completo. Nunca lanza (best-effort)."""
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(cache, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning("No pude guardar reports.json: %s", exc)


# ── Prompt + CLI (patrón EXACTO coach_headline._call_cli) ───────────────────

def _load_brain() -> str:
    try:
        return _BRAIN_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "Eres el Coach de Vitals, coach de salud personal directo. "
            "No eres médico. Aconseja con datos de wearable, sé específico y conciso."
        )


_LOCALE_LANG = {"es": "español", "en": "English", "fr": "français", "pt": "português"}


def _build_report_prompt(report_data: dict, period: str, locale: str) -> str:
    brain = _load_brain()
    means = report_data.get("means") or {}
    deltas = report_data.get("deltas") or {}
    adherence = report_data.get("adherence") or {}
    top_insight = report_data.get("top_insight")

    period_label = "semana" if period == "weekly" else "mes"
    lines = [f"=== INFORME {period_label.upper()} ({report_data.get('start')} a {report_data.get('end')}) ==="]
    if means.get("recovery") is not None:
        lines.append(f"Recuperación media: {means['recovery']}% (Δ {deltas.get('recovery')})")
    if means.get("hrv") is not None:
        lines.append(f"HRV media: {means['hrv']} ms (Δ {deltas.get('hrv')})")
    if means.get("strain") is not None:
        lines.append(f"Esfuerzo medio: {means['strain']}/21 (Δ {deltas.get('strain')})")
    if means.get("asleep") is not None:
        lines.append(f"Sueño medio: {round(means['asleep']/60, 1)}h (Δ {deltas.get('asleep')})")
    if means.get("sleep_perf") is not None:
        lines.append(f"Calidad de sueño media: {means['sleep_perf']}% (Δ {deltas.get('sleep_perf')})")
    if report_data.get("best_day"):
        lines.append(f"Mejor día (recuperación): {report_data['best_day']}")
    if report_data.get("worst_day"):
        lines.append(f"Peor día (recuperación): {report_data['worst_day']}")
    lines.append(f"Adherencia al diario de hábitos: {adherence.get('pct', 0)}% ({adherence.get('days_logged', 0)}/{adherence.get('days_total', 0)} días)")
    if top_insight:
        lines.append(f"Top hallazgo (palanca): {top_insight.get('headline', '')}")

    output_lang = _LOCALE_LANG.get(locale, "español")
    return (
        f"{brain}\n\n"
        f"{chr(10).join(lines)}\n\n"
        f"Escribe una narrativa de 4-6 frases del {period_label} en {output_lang}, con tono "
        f"directo y motivante (no genérico), anclada en los números de arriba. Cierra con "
        f"EXACTAMENTE 2 acciones concretas para el próximo {period_label}. Sin saludos, sin "
        f"markdown, sin comillas — solo el texto de la narrativa seguida de las 2 acciones. "
        f"No diagnostiques."
    )


def _call_cli(prompt: str) -> Optional[str]:
    """Delega en app.llm.generate() (backend intercambiable, F3 roadmap P0 —
    conserva el NOMBRE de la función porque los tests siguen mockeando
    report._call_cli como seam). None si falla (nunca lanza). El recorte a
    2000 chars se conserva AQUÍ (en el caller): la narrativa es más larga que
    el titular pero nunca debe dejar pasar un bloque enorme a la UI."""
    answer = _llm.generate(prompt, timeout=_CLI_TIMEOUT, purpose="report")
    if not answer:
        return None
    return answer[:2000]


# ── API pública ──────────────────────────────────────────────────────────────

def maybe_regenerate_reports(dataset: dict, journal: Optional[dict] = None, locale: str = "es") -> None:
    """Regenera (weekly y monthly) SOLO si la firma (period_key, locale)
    cambió. Best-effort TOTAL: cualquier excepción se traga y loguea, NUNCA
    propaga — se llama desde run_sync() tras coach_headline.maybe_regenerate
    y no debe tumbar ni ralentizar el sync más allá de la(s) llamada(s) al CLI
    cuando la firma cambió. Si el CLI falla, el cache viejo se conserva."""
    try:
        journal = journal or {}
        cache = load_cache()
        changed = False

        for period in PERIODS:
            try:
                report_data = build_report_data(dataset, journal, period)
                if report_data is None:
                    continue
                # Re-evalúa top_insight con el locale REAL (build_report_data usa "es"
                # internamente para no requerir locale en su firma pura; aquí sí lo
                # tenemos, así que lo recalculamos con el locale correcto).
                period_days = _days_in_range(dataset.get("days") or [],
                                              datetime.date.fromisoformat(report_data["start"]),
                                              datetime.date.fromisoformat(report_data["end"]))
                report_data["top_insight"] = _top_insight(period_days, locale)

                sig = report_data["period_key"]
                existing = cache.get(period) or {}
                if existing.get("signature") == sig and existing.get("locale") == locale and existing.get("narrative"):
                    continue  # cache hit: sin cambios -> cero llamadas al CLI

                prompt = _build_report_prompt(report_data, period, locale)
                narrative = _call_cli(prompt)
                if narrative:
                    cache[period] = {
                        "signature": sig,
                        "locale": locale,
                        "narrative": narrative,
                        "data": report_data,
                        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    }
                    changed = True
                else:
                    logger.warning(
                        "maybe_regenerate_reports(%s): CLI falló, se conserva el cache anterior (si existe).",
                        period,
                    )
                    # Fallback determinista (roadmap B6): si NO hay entrada previa
                    # que preservar (primer sync, o CLI ausente en el box), cachear
                    # los NÚMEROS del período sin narrativa — get_report() los
                    # servirá con la nota i18n de "narrativa no disponible". Nunca
                    # pisa una entrada existente (su narrativa vieja vale más que
                    # unos números sin narrativa).
                    if not existing:
                        cache[period] = {
                            "signature": sig,
                            "locale": locale,
                            "narrative": None,
                            "data": report_data,
                            "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
                        }
                        changed = True
            except Exception as exc:
                logger.warning("maybe_regenerate_reports(%s) falló (best-effort, ignorado): %s", period, exc)

        if changed:
            save_cache(cache)
    except Exception as exc:
        # Red de seguridad final: bajo NINGUNA circunstancia esto debe tumbar run_sync().
        logger.warning("maybe_regenerate_reports falló por completo (best-effort, ignorado): %s", exc)


def get_report(period: str, locale: str = "es") -> dict:
    """SOLO LEE el cache (nunca genera, cero I/O de red/subprocess, cero
    lectura de health_compact.json/journal_log.json — contrato estricto del
    roadmap). Si no hay cache para ese period, o el locale cacheado no
    coincide, o el cache no tiene narrativa todavía (CLI no ha corrido) ->
    fallback determinista: los datos agregados YA CACHEADOS si existen (sin
    narrativa) + nota i18n de narrativa no disponible; si ni siquiera hay
    cache, data=None. Nunca lanza — instantáneo, apto para GET /api/report."""
    if period not in PERIODS:
        period = "weekly"
    try:
        cache = load_cache()
        cached = cache.get(period)

        if cached and isinstance(cached, dict) and cached.get("narrative") \
                and cached.get("locale") == locale:
            report_data = cached.get("data") or {}
            return {
                "period": period,
                "start": report_data.get("start"),
                "end": report_data.get("end"),
                "narrative": cached["narrative"],
                "data": report_data,
                "has_narrative": True,
            }

        # Sin cache utilizable para este locale: fallback determinista. Si hay
        # ALGO en cache (de otro locale, o sin narrativa aún porque el CLI
        # falló), reusamos sus números ya agregados en vez de inventar I/O
        # nueva — get_report() nunca toca el dataset/journal en disco.
        if cached and isinstance(cached, dict) and cached.get("data"):
            report_data = cached["data"]
            return {
                "period": period,
                "start": report_data.get("start"),
                "end": report_data.get("end"),
                "narrative": tr("report_no_narrative", locale),
                "data": report_data,
                "has_narrative": False,
            }

        return {
            "period": period,
            "start": None,
            "end": None,
            "narrative": tr("report_no_narrative", locale),
            "data": None,
            "has_narrative": False,
        }
    except Exception as exc:
        logger.warning("get_report falló, degradando a estado vacío: %s", exc)
        return {
            "period": period, "start": None, "end": None,
            "narrative": tr("report_no_narrative", locale),
            "data": None, "has_narrative": False,
        }
