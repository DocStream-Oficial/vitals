"""
coach.py — genera el texto del Coach por reglas (HTML string).
Stub LLM: coach_llm() existe pero retorna None si no hay API key.

coach_card(dataset, locale="es") → {chips, bullets} para el template iOS.
build_coach(dataset, locale="es") → HTML string (template viejo, intacto).

Ronda 3: las 2 detecciones de fuerza de este módulo migran a strength_minutes()/
STRENGTH_RE (app/load.py) — antes cada una tenía su propio regex duplicado
("strength" in type; luego weight|strength|fuerza sin gym/resistance/musculac).
El nuevo regex es un SUPERSET, así que el chip/bullet de fuerza puede disparar
en más casos (p.ej. "gym" ahora cuenta) — cambio intencional, las 4 detecciones
de fuerza de la app (coach.py x2, coach_chat.py, insights.py) quedan idénticas.
"""
from __future__ import annotations
from app.scoring import recent_base
from app.load import strength_minutes
from app.i18n import tr


def coach_llm(dataset: dict) -> str | None:
    """Stub LLM — desconectado. Retorna None si no hay API key configurada."""
    import os
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    # gancho para implementación futura
    return None


def build_coach(dataset: dict, locale: str = "es") -> str:
    """Genera texto de coaching por reglas. Devuelve HTML string.
    Inputs: dataset con 'days' (lista), 'summary', 'exercises'.
    """
    # Intentar LLM primero (actualmente siempre None)
    llm_html = coach_llm(dataset)
    if llm_html:
        return llm_html

    days = dataset.get("days", [])
    exercises = dataset.get("exercises", [])
    summary = dataset.get("summary", {})

    if not days:
        return tr("no_data_coach", locale)

    today = days[-1]
    recovery = today.get("recovery")
    sleep_min = today.get("asleep")
    hrv = today.get("hrv")
    rhr = today.get("rhr")
    sleep_perf = today.get("sleep_perf")

    # Promedios recientes (últimos 7 días)
    recent = days[-7:]
    hrv_vals = [d["hrv"] for d in recent if d.get("hrv") is not None]
    hrv_avg = round(sum(hrv_vals) / len(hrv_vals), 1) if hrv_vals else None

    msgs = []

    # 1. Recovery de hoy
    if recovery is not None:
        if recovery >= 67:
            msgs.append(tr("recovery_high", locale, recovery=recovery))
        elif recovery >= 34:
            msgs.append(tr("recovery_mid", locale, recovery=recovery))
        else:
            msgs.append(tr("recovery_low", locale, recovery=recovery))

    # 2. Sueño vs meta 8h (480 min)
    SLEEP_GOAL = 480
    if sleep_min is not None:
        deficit = SLEEP_GOAL - sleep_min
        sleep_h = round(sleep_min / 60, 1)
        if deficit > 60:
            msgs.append(tr("sleep_deficit_big", locale, sleep_h=sleep_h, deficit=round(deficit / 60, 1)))
        elif deficit > 0:
            msgs.append(tr("sleep_deficit_small", locale, sleep_h=sleep_h))
        else:
            msgs.append(tr("sleep_goal_met", locale, sleep_h=sleep_h))

    # 3. HRV vs base y tendencia
    hrv_base = recent_base(summary, "hrv")
    if hrv is not None and hrv_base:
        diff = hrv - hrv_base
        if diff < -5:
            msgs.append(tr("hrv_below_base", locale, hrv=hrv, diff=abs(round(diff)), hrv_base=hrv_base))
        elif diff > 5:
            msgs.append(tr("hrv_above_base", locale, hrv=hrv, diff=round(diff), hrv_base=hrv_base))

    # 4. Fuerza estructurada — recordatorio (perfil del usuario: 0 sesiones de fuerza típicamente)
    # Ronda 3: strength_minutes() (superset del regex viejo "strength" in type) sobre
    # TODOS los ejercicios del dataset (mismo alcance que antes: sin filtro de fecha).
    if strength_minutes(exercises) == 0:
        msgs.append(tr("strength_gap_coach", locale))

    # 5. Tendencia HRV (comparar promedio 7d vs semana anterior)
    if len(days) >= 14:
        prev_week = days[-14:-7]
        prev_hrv_vals = [d["hrv"] for d in prev_week if d.get("hrv") is not None]
        if hrv_vals and prev_hrv_vals:
            curr_avg = sum(hrv_vals) / len(hrv_vals)
            prev_avg = sum(prev_hrv_vals) / len(prev_hrv_vals)
            delta = curr_avg - prev_avg
            if delta < -3:
                msgs.append(tr("hrv_trend_down", locale, curr_avg=round(curr_avg, 1), prev_avg=round(prev_avg, 1)))
            elif delta > 3:
                msgs.append(tr("hrv_trend_up", locale, curr_avg=round(curr_avg, 1), prev_avg=round(prev_avg, 1)))

    if not msgs:
        return tr("no_metrics_coach", locale)

    items = "".join(f"<li>{m}</li>" for m in msgs)
    return f"<ul style='padding-left:18px;line-height:1.8'>{items}</ul>"


# ─────────────────────────────────────────────────────────────────────────────
# coach_card — salida estructurada para el template iOS
# ─────────────────────────────────────────────────────────────────────────────

def _hex_alpha(hex_color: str, alpha: float) -> str:
    """Convierte #RRGGBB a rgba(r,g,b,alpha)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def coach_card(dataset: dict, locale: str = "es") -> dict:
    """Genera chips + bullets para la tarjeta Coach IA (template iOS).

    Devuelve:
      {
        chips: [{t: str, c: str, bg: str, bd: str}],
        bullets: [{title: str, body: str}]
      }

    Reglas (perfil del usuario: hombre 40a, meta 1=dormir, 2=fuerza, 3=longevidad):
    - chip Recovery con valor numérico del día (verde/ámbar/rojo)
    - chip Sueño con horas vs meta 00:00 / 8h
    - chip Fuerza (rojo si 0 min en 7 días)
    - bullet edad corporal (de summary.bodyage)
    - bullet fuerza (si 0 min en 7d)
    - bullet sueño (acostarse más temprano, con hora real vs recomendada)
    - bullet HRV tendencia si desvío vs base
    """
    A = {
        "green": "#30D158",
        "orange": "#FF9F0A",
        "red": "#FF375F",
        "indigo": "#5E5CE6",
        "cyan": "#64D2FF",
    }

    days = dataset.get("days", [])
    exercises = dataset.get("exercises", [])
    summary = dataset.get("summary", {})
    bodyage = summary.get("bodyage", {})

    chips: list[dict] = []
    bullets: list[dict] = []

    if not days:
        return {"chips": chips, "bullets": bullets}

    today = days[-1]
    recovery = today.get("recovery")
    sleep_min = today.get("asleep")  # minutos de sueño
    hrv = today.get("hrv")
    hrv_base = recent_base(summary, "hrv")

    # ── CHIP 1: Recuperación ──────────────────────────────────────────────────
    if recovery is not None:
        if recovery >= 67:
            col = A["green"]
        elif recovery >= 34:
            col = A["orange"]
        else:
            col = A["red"]
        label = tr("chip_recovery", locale, recovery=recovery)
        chips.append({"t": label, "c": col, "bg": _hex_alpha(col, 0.15), "bd": _hex_alpha(col, 0.32)})

    # ── CHIP 2: Edad corporal ─────────────────────────────────────────────────
    body_age = bodyage.get("body_age")
    if body_age is not None:
        col = A["green"]
        chips.append({"t": tr("chip_body_age", locale, body_age=body_age), "c": col, "bg": _hex_alpha(col, 0.15), "bd": _hex_alpha(col, 0.32)})

    # ── CHIP 3: Fuerza (últimos 7 días) ──────────────────────────────────────
    last_day_date = today.get("date", "")
    if last_day_date:
        from datetime import datetime, timedelta
        try:
            cutoff_dt = datetime.fromisoformat(last_day_date) - timedelta(days=7)
            cutoff = cutoff_dt.date().isoformat()
        except Exception:
            cutoff = ""
    else:
        cutoff = ""

    # Ronda 3: strength_minutes() (superset del regex viejo weight|strength|fuerza,
    # ahora incluye pesas/gym/resistance/musculac) sobre las fechas >= cutoff. El
    # filtro por fecha se resuelve aquí (comparación de string ">="), no dentro del
    # helper (que filtra por pertenencia exacta a un set) — mismo alcance que antes.
    dates_in_window = {e.get("date", "") for e in exercises if e.get("date", "") >= cutoff}
    strength_min = strength_minutes(exercises, dates=dates_in_window)
    if strength_min == 0:
        col = A["red"]
        chips.append({"t": tr("chip_strength_zero", locale), "c": col, "bg": _hex_alpha(col, 0.15), "bd": _hex_alpha(col, 0.32)})

    # ── BULLET 1: Edad corporal ───────────────────────────────────────────────
    fitness_age = bodyage.get("fitness_age")
    vo2max = bodyage.get("vo2max")
    real_age = bodyage.get("age", 40)
    penalty = bodyage.get("penalty", 0)
    sleep_h_avg = bodyage.get("sleep_h", 0)

    if body_age is not None and fitness_age is not None:
        penalty_str = ""
        if penalty and penalty > 0:
            penalty_str = tr("bullet_body_age_penalty", locale, penalty=penalty, sleep_h_avg=sleep_h_avg)
        body_body = tr(
            "bullet_body_age_body", locale,
            vo2max=vo2max,
            category=bodyage.get("category", ""),
            real_age=real_age,
            rhr=bodyage.get("rhr", summary.get("rhr_base", "")),
            hrv=bodyage.get("hrv", summary.get("hrv_base", "")),
        ) + penalty_str
        bullets.append({
            "title": tr("bullet_body_age_title", locale, fitness_age=fitness_age, body_age=body_age),
            "body": body_body,
        })

    # ── BULLET 2: Fuerza ─────────────────────────────────────────────────────
    # Cuenta ejercicios de cardio en últimos 7d para el mensaje
    recent_ex = [e for e in exercises if e.get("date", "") >= cutoff] if cutoff else exercises[-14:]
    n_sessions = len(recent_ex)
    if strength_min == 0:
        bullets.append({
            "title": tr("bullet_strength_title", locale),
            "body": tr("bullet_strength_body", locale, n_sessions=n_sessions),
        })

    # ── BULLET 3: Sueño / acostarse ──────────────────────────────────────────
    bedtime = today.get("bedtime", "")
    bed_min_val = today.get("bed_min")  # minutos de desvío vs 00:00
    if sleep_min is not None:
        sleep_h_today = round(sleep_min / 60, 1)
        deficit = 480 - sleep_min
        if deficit > 30 or (bed_min_val is not None and bed_min_val > 30):
            late_str = ""
            if bed_min_val is not None and bed_min_val > 30:
                late_str = tr("bullet_sleep_late_str", locale, bedtime=bedtime)
            if late_str:
                body_text = tr("bullet_sleep_body_only_late", locale, bedtime=bedtime)
            else:
                body_text = tr("bullet_sleep_body_late", locale, sleep_h_today=sleep_h_today, deficit=round(deficit / 60, 1))
            bullets.append({
                "title": tr("bullet_sleep_title", locale),
                "body": body_text,
            })

    # ── BULLET 4: Tendencia HRV ───────────────────────────────────────────────
    if hrv is not None and hrv_base and len(days) >= 7:
        hrv_vals_7d = [d["hrv"] for d in days[-7:] if d.get("hrv") is not None]
        if hrv_vals_7d:
            avg7 = sum(hrv_vals_7d) / len(hrv_vals_7d)
            diff = avg7 - hrv_base
            if diff < -4:
                bullets.append({
                    "title": tr("bullet_hrv_down_title", locale),
                    "body": tr("bullet_hrv_down_body", locale, avg7=round(avg7, 1), hrv_base=hrv_base),
                })

    # Garantizar mínimos
    if not bullets:
        bullets.append({
            "title": tr("bullet_keep_going_title", locale),
            "body": tr("bullet_keep_going_body", locale),
        })

    return {"chips": chips, "bullets": bullets}
