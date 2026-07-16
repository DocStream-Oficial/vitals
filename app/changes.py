"""
changes.py — Capa de detección de cambios (Frescura de Alertas + Coach).

detect_changes(dataset, locale='es') -> list[dict]
  Compara el último día vs el día anterior, y vs las bases recientes del
  summary (hrv_base_recent/rhr_base_recent), y emite "eventos de cambio"
  cuando algún factor medible cruza un umbral. Determinista, pura, sin
  dependencias externas más allá de statistics de stdlib.

Cada evento: {factor, kind, direction, delta, magnitude, severity, text,
              title, summary, recommendation}
  - kind: "improvement" | "decline" | "milestone" | "streak"
  - severity: usada para el ranking en insights.evaluate() ("positive"/"watch"/"info")
  - text: resumen corto (para el titular del coach); title/summary/recommendation
    para cuando el evento se proyecta como insight completo.

None-safe: dataset vacío, un solo día, sin "ayer", o todos los campos None
-> [] sin crashear. ayer == hoy (mismos valores) -> [] (no inventa cambio).

Los umbrales son constantes de módulo (auditables, top del archivo).
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

from app.i18n import tr
from app.load import strength_minutes

# ── Umbrales (constantes de módulo, auditables) ─────────────────────────────
RECOVERY_DELTA = 8          # pts de recovery día vs día para disparar improvement/decline
HRV_PCT = 0.08               # % de desvío vs base reciente de HRV
RHR_DELTA = 3                # lpm de desvío vs base reciente de RHR
SLEEP_MIN_DELTA = 45         # minutos de sueño día vs día
STRAIN_DELTA = 3             # puntos de strain día vs día (escala 0-21)
STEPS_PCT = 0.25             # % de desvío vs media 7d de pasos
STREAK_MIN = 3                # noches consecutivas para disparar racha de sueño
STRENGTH_GAP_DAYS = 5         # días sin fuerza antes de celebrar la 1ª sesión nueva
BEDTIME_SD_DELTA = 15         # minutos de cambio en pstdev(bed_min) para reportar
VO2MAX_DELTA = 0.5            # cambio mínimo de VO2max para reportar

# Bandas de recovery (mismo criterio que coach.py/insights.py: alto>=67, medio>=34, bajo<34)
# Las labels son claves internas (band_high/mid/low), NO texto — se localizan
# vía tr("band_<label>", locale) al construir el texto del evento.
_RECOVERY_BANDS = (
    (67, "high"),
    (34, "mid"),
    (0, "low"),
)
_BAND_RANK = {"low": 0, "mid": 1, "high": 2}


def _recovery_band(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    for floor, label in _RECOVERY_BANDS:
        if value >= floor:
            return label
    return "low"


def _last_two_with_field(days: list[dict], field: str) -> tuple:
    """(hoy, ayer) valores no-None de `field` en los ÚLTIMOS dos días CONSECUTIVOS
    de la lista (days[-1] y days[-2]). Si cualquiera de los dos falta el campo,
    o no hay suficientes días, devuelve (valor_hoy_o_None, None)."""
    if not days:
        return (None, None)
    today_val = days[-1].get(field)
    if len(days) < 2:
        return (today_val, None)
    yesterday_val = days[-2].get(field)
    return (today_val, yesterday_val)


def _event(
    factor: str,
    kind: str,
    direction: str,
    delta_val: float,
    magnitude: float,
    severity: str,
    key_title: str,
    key_summary: str,
    key_rec: str,
    locale: str,
    **fmt,
) -> dict:
    """Construye el dict de evento. `delta_val`/`magnitude` son los campos
    NUMÉRICOS estructurados del evento (para consumo programático); `**fmt` son
    los kwargs de FORMATO de texto que puede incluir su propio 'delta' (p.ej.
    delta ya redondeado/absoluto para el string) — nombres separados a propósito
    para que nunca colisionen como argumento duplicado."""
    title = tr(key_title, locale, **fmt)
    summary = tr(key_summary, locale, **fmt)
    recommendation = tr(key_rec, locale, **fmt) if key_rec else ""
    return {
        "factor": factor,
        "kind": kind,
        "direction": direction,
        "delta": delta_val,
        "magnitude": magnitude,
        "severity": severity,
        "text": summary,
        "title": title,
        "summary": summary,
        "recommendation": recommendation,
    }


# ── Factor: recovery (delta día vs día + cruce de banda) ────────────────────

def _check_recovery(days: list[dict], locale: str) -> list[dict]:
    events = []
    today, yesterday = _last_two_with_field(days, "recovery")
    if today is None or yesterday is None:
        return events
    today, yesterday = float(today), float(yesterday)
    delta = today - yesterday
    if delta == 0:
        return events

    if abs(delta) >= RECOVERY_DELTA:
        if delta > 0:
            events.append(_event(
                "recovery", "improvement", "up", delta, abs(delta), "positive",
                "change_recovery_up_title", "change_recovery_up_summary", "change_recovery_rec_up",
                locale, today=today, yesterday=yesterday, delta=abs(delta),
            ))
        else:
            events.append(_event(
                "recovery", "decline", "down", delta, abs(delta), "watch",
                "change_recovery_down_title", "change_recovery_down_summary", "change_recovery_rec_down",
                locale, today=today, yesterday=yesterday, delta=abs(delta),
            ))

    prev_band = _recovery_band(yesterday)
    curr_band = _recovery_band(today)
    if prev_band and curr_band and prev_band != curr_band:
        prev_label = tr(f"band_{prev_band}", locale)
        curr_label = tr(f"band_{curr_band}", locale)
        if _BAND_RANK[curr_band] > _BAND_RANK[prev_band]:
            events.append(_event(
                "recovery", "milestone", "up", delta, abs(delta), "positive",
                "change_recovery_band_up_title", "change_recovery_band_up_summary", "change_recovery_rec_up",
                locale, today=today, prev_band=prev_label, curr_band=curr_label,
            ))
        else:
            events.append(_event(
                "recovery", "decline", "down", delta, abs(delta), "watch",
                "change_recovery_band_down_title", "change_recovery_band_down_summary", "change_recovery_rec_down",
                locale, today=today, prev_band=prev_label, curr_band=curr_label,
            ))
    return events


# ── Factor: HRV (vs hrv_base_recent) ────────────────────────────────────────

def _check_hrv(days: list[dict], summary: dict, locale: str) -> list[dict]:
    events = []
    if not days:
        return events
    today = days[-1].get("hrv")
    base = summary.get("hrv_base_recent") or summary.get("hrv_base")
    if today is None or base in (None, 0):
        return events
    today, base = float(today), float(base)
    pct = (today - base) / base
    if abs(pct) < HRV_PCT:
        return events
    if pct > 0:
        events.append(_event(
            "hrv", "improvement", "up", pct, abs(pct), "positive",
            "change_hrv_up_title", "change_hrv_up_summary", "change_hrv_rec_up",
            locale, today=today, base=base, pct=abs(pct) * 100,
        ))
    else:
        events.append(_event(
            "hrv", "decline", "down", pct, abs(pct), "watch",
            "change_hrv_down_title", "change_hrv_down_summary", "change_hrv_rec_down",
            locale, today=today, base=base, pct=abs(pct) * 100,
        ))
    return events


# ── Factor: RHR (vs rhr_base_recent) ────────────────────────────────────────

def _check_rhr(days: list[dict], summary: dict, locale: str) -> list[dict]:
    events = []
    if not days:
        return events
    today = days[-1].get("rhr")
    base = summary.get("rhr_base_recent") or summary.get("rhr_base")
    if today is None or base is None:
        return events
    today, base = float(today), float(base)
    delta = today - base
    if abs(delta) < RHR_DELTA:
        return events
    # RHR: subir es watch (peor), bajar es positive (mejor) — lower_better.
    if delta > 0:
        events.append(_event(
            "rhr", "decline", "up", delta, abs(delta), "watch",
            "change_rhr_up_title", "change_rhr_up_summary", "change_rhr_rec_up",
            locale, today=today, base=base, delta=abs(delta),
        ))
    else:
        events.append(_event(
            "rhr", "improvement", "down", delta, abs(delta), "positive",
            "change_rhr_down_title", "change_rhr_down_summary", "change_rhr_rec_down",
            locale, today=today, base=base, delta=abs(delta),
        ))
    return events


# ── Factor: sueño (delta día vs día + rachas) ───────────────────────────────

def _check_sleep(days: list[dict], summary: dict, locale: str) -> list[dict]:
    events = []
    today, yesterday = _last_two_with_field(days, "asleep")
    if today is not None and yesterday is not None:
        today_f, yesterday_f = float(today), float(yesterday)
        delta = today_f - yesterday_f
        if abs(delta) >= SLEEP_MIN_DELTA:
            today_h, yesterday_h, delta_h = today_f / 60, yesterday_f / 60, abs(delta) / 60
            if delta > 0:
                events.append(_event(
                    "sleep", "improvement", "up", delta, abs(delta), "positive",
                    "change_sleep_up_title", "change_sleep_up_summary", "change_sleep_rec_up",
                    locale, today_h=today_h, yesterday_h=yesterday_h, delta_h=delta_h,
                ))
            else:
                events.append(_event(
                    "sleep", "decline", "down", delta, abs(delta), "watch",
                    "change_sleep_down_title", "change_sleep_down_summary", "change_sleep_rec_down",
                    locale, today_h=today_h, yesterday_h=yesterday_h, delta_h=delta_h,
                ))

    # Rachas: usa el OBJETIVO personal (sleep_goal_min), con fallback a la
    # NECESIDAD (sleep_target_min) y luego 480 -- sleep-goal-vs-need. Datasets
    # viejos sin sleep_goal_min en summary (pre-deploy) caen al target, mismo
    # comportamiento de hoy: NUNCA .get("sleep_goal_min", 480) directo, eso
    # sería una regresión silenciosa para usuarios con necesidad != 480.
    target = summary.get("sleep_goal_min") or summary.get("sleep_target_min") or 480
    recent = [d for d in days[-14:] if d.get("asleep") is not None]
    if len(recent) >= STREAK_MIN:
        # Cuenta la racha actual (desde el final hacia atrás) de noches todas
        # buenas (>=target) o todas malas (<target).
        streak_good = 0
        streak_bad = 0
        for d in reversed(recent):
            v = float(d["asleep"])
            if v >= target:
                if streak_bad > 0:
                    break
                streak_good += 1
            else:
                if streak_good > 0:
                    break
                streak_bad += 1
        if streak_good >= STREAK_MIN:
            events.append(_event(
                "sleep", "streak", "up", streak_good, streak_good, "positive",
                "change_sleep_streak_good_title", "change_sleep_streak_good_summary",
                "change_sleep_streak_good_rec", locale, n=streak_good,
            ))
        elif streak_bad >= STREAK_MIN:
            events.append(_event(
                "sleep", "streak", "down", streak_bad, streak_bad, "watch",
                "change_sleep_streak_bad_title", "change_sleep_streak_bad_summary",
                "change_sleep_streak_bad_rec", locale, n=streak_bad,
            ))
    return events


# ── Factor: strain (delta día vs día) ───────────────────────────────────────

def _check_strain(days: list[dict], locale: str) -> list[dict]:
    events = []
    today, yesterday = _last_two_with_field(days, "strain")
    if today is None or yesterday is None:
        return events
    today, yesterday = float(today), float(yesterday)
    delta = today - yesterday
    if abs(delta) < STRAIN_DELTA:
        return events
    if delta > 0:
        events.append(_event(
            "strain", "milestone", "up", delta, abs(delta), "info",
            "change_strain_up_title", "change_strain_up_summary", "change_strain_rec_up",
            locale, today=today, yesterday=yesterday, delta=abs(delta),
        ))
    else:
        events.append(_event(
            "strain", "milestone", "down", delta, abs(delta), "info",
            "change_strain_down_title", "change_strain_down_summary", "change_strain_rec_down",
            locale, today=today, yesterday=yesterday, delta=abs(delta),
        ))
    return events


# ── Factor: pasos (vs media 7d) ─────────────────────────────────────────────

def _check_steps(days: list[dict], locale: str) -> list[dict]:
    events = []
    if not days:
        return events
    today = days[-1].get("steps")
    if today is None:
        return events
    window = [d["steps"] for d in days[-8:-1] if d.get("steps") is not None]
    if len(window) < 3:
        return events
    avg = statistics.mean(window)
    if avg == 0:
        return events
    today = float(today)
    pct = (today - avg) / avg
    if abs(pct) < STEPS_PCT:
        return events
    if pct > 0:
        events.append(_event(
            "steps", "milestone", "up", pct, abs(pct), "info",
            "change_steps_up_title", "change_steps_up_summary", "change_steps_rec_up",
            locale, today=today, avg=avg, pct=abs(pct) * 100,
        ))
    else:
        events.append(_event(
            "steps", "milestone", "down", pct, abs(pct), "info",
            "change_steps_down_title", "change_steps_down_summary", "change_steps_rec_down",
            locale, today=today, avg=avg, pct=abs(pct) * 100,
        ))
    return events


# ── Factor: skin_temp (señal de enfermedad aparece/desaparece) ─────────────

def _skin_temp_elevated(days: list[dict], idx: int) -> Optional[bool]:
    """True/False si se puede evaluar 'temp elevada' en days[idx] vs los 14
    días anteriores a idx (mismo criterio de insights.rule_illness_early_warning,
    fallback absoluto +0.5°C ya que aquí no tenemos las SDs de summary a mano
    por día histórico). None si no hay suficientes datos para evaluar."""
    if idx < 0 or idx >= len(days):
        return None
    today_val = days[idx].get("skin_temp")
    if today_val is None:
        return None
    window = [d.get("skin_temp") for d in days[max(0, idx - 14):idx] if d.get("skin_temp") is not None]
    if len(window) < 3:
        return None
    mean = statistics.mean(window)
    return float(today_val) > mean + 0.5


def _check_skin_temp(days: list[dict], locale: str) -> list[dict]:
    events = []
    if len(days) < 2:
        return events
    today_flag = _skin_temp_elevated(days, len(days) - 1)
    yesterday_flag = _skin_temp_elevated(days, len(days) - 2)
    if today_flag is None or yesterday_flag is None:
        return events
    if today_flag and not yesterday_flag:
        events.append(_event(
            "skin_temp", "decline", "up", 1, 1, "watch",
            "change_skin_temp_appeared_title", "change_skin_temp_appeared_summary",
            "change_skin_temp_appeared_rec", locale,
        ))
    elif yesterday_flag and not today_flag:
        events.append(_event(
            "skin_temp", "improvement", "down", -1, 1, "positive",
            "change_skin_temp_resolved_title", "change_skin_temp_resolved_summary",
            "change_skin_temp_resolved_rec", locale,
        ))
    return events


# ── Factor: bedtime (consistencia mejora/empeora) ──────────────────────────

def _check_bedtime(days: list[dict], locale: str) -> list[dict]:
    """Compara la consistencia (pstdev de bed_min) de los últimos 21 días vs el
    bloque de 21 días INMEDIATAMENTE ANTERIOR (sin solape: days[-42:-21] vs
    days[-21:]) — dos ventanas disjuntas, no una ventana desplazada 1 día
    (que se solaparía en 20/21 días y nunca detectaría el cambio real)."""
    events = []
    bed_vals_now = [d.get("bed_min") for d in days[-21:] if d.get("bed_min") is not None]
    bed_vals_prev = [d.get("bed_min") for d in days[-42:-21] if d.get("bed_min") is not None]
    if len(bed_vals_now) < 7 or len(bed_vals_prev) < 7:
        return events
    curr_sd = statistics.pstdev(bed_vals_now)
    prev_sd = statistics.pstdev(bed_vals_prev)
    delta = curr_sd - prev_sd
    if abs(delta) < BEDTIME_SD_DELTA:
        return events
    if delta < 0:
        events.append(_event(
            "bedtime", "improvement", "down", delta, abs(delta), "positive",
            "change_bedtime_improved_title", "change_bedtime_improved_summary",
            "change_bedtime_improved_rec", locale, prev_sd=prev_sd, curr_sd=curr_sd,
        ))
    else:
        events.append(_event(
            "bedtime", "decline", "up", delta, abs(delta), "watch",
            "change_bedtime_worsened_title", "change_bedtime_worsened_summary",
            "change_bedtime_worsened_rec", locale, prev_sd=prev_sd, curr_sd=curr_sd,
        ))
    return events


# ── Factor: fuerza (1ª sesión en N días) ────────────────────────────────────

def _check_strength(days: list[dict], exercises: list[dict], locale: str) -> list[dict]:
    events = []
    if not days or not exercises:
        return events
    today_date = days[-1].get("date")
    if not today_date:
        return events

    # Fechas (ordenadas) con al menos 1 minuto de fuerza real.
    by_date: dict[str, int] = {}
    for e in exercises:
        d = e.get("date")
        if not d:
            continue
        by_date[d] = by_date.get(d, 0) + (strength_minutes([e]) or 0)
    strength_dates = sorted(d for d, mins in by_date.items() if mins > 0)
    if not strength_dates or strength_dates[-1] != today_date:
        return events  # hoy no hubo sesión de fuerza -> nada que celebrar

    if len(strength_dates) < 2:
        return events  # primera sesión de la historia -> sin "gap" que reportar (None-safe)

    prev_date = strength_dates[-2]
    try:
        from datetime import date as _date
        gap_days = (_date.fromisoformat(today_date) - _date.fromisoformat(prev_date)).days
    except Exception:
        return events

    if gap_days >= STRENGTH_GAP_DAYS:
        events.append(_event(
            "strength", "milestone", "up", gap_days, gap_days, "positive",
            "change_strength_first_session_title", "change_strength_first_session_summary",
            "change_strength_first_session_rec", locale, gap_days=gap_days,
        ))
    return events


# ── Factor: VO2max (cambio) ──────────────────────────────────────────────────

def _check_vo2max(dataset: dict, locale: str) -> list[dict]:
    events = []
    summary = dataset.get("summary") or {}
    bodyage = summary.get("bodyage") or {}
    curr = bodyage.get("vo2max")
    prev = summary.get("_prev_vo2max")  # inyectado opcionalmente por el caller; None-safe si ausente
    if curr is None or prev is None:
        return events
    curr, prev = float(curr), float(prev)
    delta = curr - prev
    if abs(delta) < VO2MAX_DELTA:
        return events
    if delta > 0:
        events.append(_event(
            "vo2max", "improvement", "up", delta, abs(delta), "positive",
            "change_vo2max_up_title", "change_vo2max_up_summary", "change_vo2max_up_rec",
            locale, prev=prev, curr=curr,
        ))
    else:
        events.append(_event(
            "vo2max", "decline", "down", delta, abs(delta), "info",
            "change_vo2max_down_title", "change_vo2max_down_summary", "change_vo2max_down_rec",
            locale, prev=prev, curr=curr,
        ))
    return events


# ── detect_changes ───────────────────────────────────────────────────────────

def detect_changes(dataset: dict, locale: str = "es") -> list[dict]:
    """Compara el último día vs el anterior y vs las bases recientes; devuelve
    la lista de eventos de cambio detectados. None-safe: dataset vacío, un
    solo día, sin 'ayer', o todos los campos None -> [] sin crashear. Con
    datos idénticos ayer==hoy -> [] (no inventa cambio)."""
    if not dataset:
        return []
    days: list[dict] = dataset.get("days") or []
    if len(days) < 1:
        return []
    summary: dict = dataset.get("summary") or {}
    exercises: list[dict] = dataset.get("exercises") or []

    events: list[dict] = []
    try:
        events += _check_recovery(days, locale)
    except Exception:
        pass
    try:
        events += _check_hrv(days, summary, locale)
    except Exception:
        pass
    try:
        events += _check_rhr(days, summary, locale)
    except Exception:
        pass
    try:
        events += _check_sleep(days, summary, locale)
    except Exception:
        pass
    try:
        events += _check_strain(days, locale)
    except Exception:
        pass
    try:
        events += _check_steps(days, locale)
    except Exception:
        pass
    try:
        events += _check_skin_temp(days, locale)
    except Exception:
        pass
    try:
        events += _check_bedtime(days, locale)
    except Exception:
        pass
    try:
        events += _check_strength(days, exercises, locale)
    except Exception:
        pass
    try:
        events += _check_vo2max(dataset, locale)
    except Exception:
        pass

    return events
