"""
bodyage.py — compute_body_age portado EXACTO de vitals_sync.py.
VO2máx NTNU/Nes 2011. NO cambiar fórmulas, percentiles, pesos ni constantes.

Añadidos aditivos (Tier 1):
- confidence: dict con rhr_days/hrv_days/sleep_days/exercise_sessions + level
- vo2max_percentile: percentil 0-100 por edad+sexo (normas Cooper Institute/ACSM)
- vo2max_label: etiqueta por percentil ("Superior (top 10%)", "Excelente", etc.)
"""
import statistics
import datetime as _dt

# ── Tabla de normas VO₂máx por sexo y grupo etario ────────────────────────────
# Fuente: Cooper Institute / ACSM (Percentiles: 10/25/50/75/90/95 aprox.)
# Formato: {(sexo, grupo): [(percentil, vo2), ...]} donde sexo='M'/'F'
# y grupo = límite inferior del decenio etario (20, 30, 40, 50, 60)
# Valores de referencia tablas normativas ACSM's Guidelines for Exercise Testing
# and Prescription, 11th ed. (masculino) y Cooper Institute data (femenino).
_VO2_NORMS = {
    # ── MASCULINO ────────────────────────────────────────────────────────────
    ("M", 20): [(10, 38), (25, 43), (50, 49), (75, 55), (90, 60), (95, 64)],
    ("M", 30): [(10, 36), (25, 41), (50, 47), (75, 53), (90, 58), (95, 62)],
    ("M", 40): [(10, 33), (25, 38), (50, 44), (75, 50), (90, 55), (95, 59)],
    ("M", 50): [(10, 30), (25, 35), (50, 41), (75, 46), (90, 51), (95, 55)],
    ("M", 60): [(10, 27), (25, 31), (50, 37), (75, 42), (90, 47), (95, 51)],
    # ── FEMENINO ─────────────────────────────────────────────────────────────
    ("F", 20): [(10, 31), (25, 36), (50, 41), (75, 47), (90, 52), (95, 56)],
    ("F", 30): [(10, 29), (25, 33), (50, 38), (75, 44), (90, 49), (95, 53)],
    ("F", 40): [(10, 26), (25, 30), (50, 35), (75, 41), (90, 46), (95, 50)],
    ("F", 50): [(10, 23), (25, 27), (50, 32), (75, 37), (90, 42), (95, 46)],
    ("F", 60): [(10, 20), (25, 24), (50, 29), (75, 34), (90, 38), (95, 42)],
}

_AGE_GROUPS = [20, 30, 40, 50, 60]


def _age_group(age: float) -> int:
    """Devuelve el límite inferior del grupo etario más apropiado para la edad."""
    for g in reversed(_AGE_GROUPS):
        if age >= g:
            return g
    return _AGE_GROUPS[0]  # <20 → usar grupo 20


def _vo2_percentile(vo2: float, age: float, sex: str) -> int:
    """
    Interpola linealmente el percentil de VO₂máx según edad y sexo.
    Clamp [1, 99]. Edad fuera de 20–70+ → usa el grupo extremo.
    """
    sex_key = "M" if str(sex).upper().startswith("M") else "F"
    group = _age_group(age)
    breakpoints = _VO2_NORMS[(sex_key, group)]

    # Extraer puntos de la curva percentil
    bp_vo2 = [b[1] for b in breakpoints]
    bp_pct  = [b[0] for b in breakpoints]

    # Interpolación lineal entre breakpoints
    if vo2 <= bp_vo2[0]:
        # Extrapolación baja: proyectar desde primer segmento
        if len(bp_vo2) >= 2:
            slope = (bp_pct[1] - bp_pct[0]) / (bp_vo2[1] - bp_vo2[0])
            p = bp_pct[0] + slope * (vo2 - bp_vo2[0])
        else:
            p = float(bp_pct[0])
    elif vo2 >= bp_vo2[-1]:
        # Extrapolación alta: proyectar desde último segmento
        if len(bp_vo2) >= 2:
            slope = (bp_pct[-1] - bp_pct[-2]) / (bp_vo2[-1] - bp_vo2[-2])
            p = bp_pct[-1] + slope * (vo2 - bp_vo2[-1])
        else:
            p = float(bp_pct[-1])
    else:
        # Interpolación entre breakpoints
        for i in range(len(bp_vo2) - 1):
            if bp_vo2[i] <= vo2 <= bp_vo2[i + 1]:
                t = (vo2 - bp_vo2[i]) / (bp_vo2[i + 1] - bp_vo2[i])
                p = bp_pct[i] + t * (bp_pct[i + 1] - bp_pct[i])
                break

    return int(max(1, min(99, round(p))))


def _vo2_label(percentile: int) -> str:
    """Etiqueta basada en percentil (distinta de category, que es valor absoluto)."""
    if percentile >= 90:
        return "Superior (top 10%)"
    if percentile >= 70:
        return "Excelente"
    if percentile >= 50:
        return "Sobre promedio"
    if percentile >= 30:
        return "Promedio"
    return "Bajo"


def compute_body_age(days, exercises, age, waist, sex="M", sleep_penalty_h: float = 7.0):
    """Edad corporal: VO2máx (NTNU/Nes 2011) -> edad de fitness validada,
    + edad compuesta (HRV y sueño solo pueden envejecer, no rejuvenecer bajo la base cardiaca).

    Ronda 5: sleep_penalty_h parametriza el umbral de horas de sueño bajo el cual
    se aplica penalty en body_age (antes era 7 literal). Default 7.0 = comportamiento
    IDÉNTICO a antes. sync.py lo deriva de (sleep_target_min - 60) / 60 para que
    los tres consumidores del umbral de sueño (recovery, insights, bodyage) se
    muevan juntos cuando el perfil cambia su sleep_target_min."""

    def recent(k, n=14):
        return [v[k] for v in days[-n:] if v.get(k) is not None]

    rhr_v = recent("rhr"); hrv_v = recent("hrv"); slp_v = recent("asleep")
    rhr = round(statistics.mean(rhr_v), 1) if rhr_v else 55.0
    hrv = round(statistics.mean(hrv_v), 1) if hrv_v else None
    sleep_h = (statistics.mean(slp_v) / 60) if slp_v else None
    ref = days[-1]["date"] if days else _dt.date.today().isoformat()
    cutoff = (_dt.date.fromisoformat(ref) - _dt.timedelta(days=28)).isoformat()
    rec = [e for e in exercises if e.get("date", "") >= cutoff]
    freq = len(set(e["date"] for e in rec)) / 4.0
    hrs = [e["avg_hr"] for e in rec if e.get("avg_hr")]
    durs = [e["dur_min"] for e in rec if e.get("dur_min")]
    ahr = statistics.mean(hrs) if hrs else 0
    adur = statistics.mean(durs) if durs else 0
    fs = 5 if freq >= 5 else 4 if freq >= 3 else 3 if freq >= 2 else 2 if freq >= 1 else 0
    iss = 5 if ahr >= 120 else 4 if ahr >= 105 else 3 if ahr >= 90 else 2 if ahr > 0 else 0
    ds = 5 if adur >= 60 else 4 if adur >= 30 else 3 if adur >= 15 else 2 if adur > 0 else 0
    PA = fs + iss + ds
    male = str(sex).upper().startswith("M")
    if male:
        vo2 = 100.27 - 0.296*age + 0.226*PA - 0.369*waist - 0.155*rhr
    else:
        vo2 = 74.736 - 0.247*age + 0.198*PA - 0.259*waist - 0.114*rhr
    vo2 = round(vo2, 1)
    intercept = 55.1 if male else 49.0
    fitness_age = max(20, min(80, (intercept - vo2) / 0.363))
    pen = 0.0
    if hrv is not None:
        exp_hrv = 50 - 0.5*(age - 20)
        if hrv < exp_hrv:
            pen += min(5, (exp_hrv - hrv) / 5)
    if sleep_h is not None and sleep_h < sleep_penalty_h:
        pen += min(6, (sleep_penalty_h - sleep_h) * 2)
    body_age = max(18, min(90, fitness_age + pen))
    cat = ("Superior" if vo2 > 53 else "Excelente" if vo2 >= 48 else "Sobre promedio"
           if vo2 >= 43 else "Promedio" if vo2 >= 36 else "Bajo")

    # ── Tier 1 aditivos ──────────────────────────────────────────────────────
    # confidence: cobertura de datos que alimentó este cómputo
    n = {
        "rhr_days": len(rhr_v),
        "hrv_days": len(hrv_v),
        "sleep_days": len(slp_v),
        "exercise_sessions": len(rec),
    }
    min_core = min(n["rhr_days"], n["hrv_days"], n["sleep_days"])
    conf_level = "high" if min_core >= 10 else ("med" if min_core >= 5 else "low")
    confidence = {**n, "level": conf_level}

    # vo2max_percentile + label (aditivos, no tocan ninguna fórmula existente)
    v2p = _vo2_percentile(vo2, age, sex)
    v2l = _vo2_label(v2p)

    return {"vo2max": vo2, "fitness_age": round(fitness_age), "body_age": round(body_age),
            "category": cat, "rhr": rhr, "hrv": hrv,
            "sleep_h": round(sleep_h, 1) if sleep_h else None, "pa_index": PA,
            "waist": waist, "age": age, "penalty": round(pen, 1),
            "confidence": confidence,
            "vo2max_percentile": v2p,
            "vo2max_label": v2l}
