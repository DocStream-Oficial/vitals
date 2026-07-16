"""
insights.py — Motor de insights determinista (sin LLM).

evaluate(dataset, locale="es") -> list[dict]
  Corre las 9 reglas, filtra None, ordena alert→watch→positive→info,
  limita a ~5. Cada insight:
  {id, severity, category, icon, title, summary, factors, recommendation}

Reglas tolerantes a None: ausencia de dato NO dispara alerta.
Sin dependencias externas (solo statistics de stdlib).

Tier 2 Feature A:
  rule_illness_early_warning usa z-score (|z|>1.5) cuando SD está disponible,
  con fallback a umbral absoluto cuando SD<ε o SD no disponible.
  Esto preserva el comportamiento de los tests existentes (ventanas sin varianza).

Ronda 3 (motor honesto):
  rule_strength_gap ahora usa strength_minutes() (app/load.py) sobre `exercises`
  en vez de `vigorous` (que era proxy de cardio/AZM, no de fuerza real — falso
  negativo para el insight más importante del usuario). evaluate() loguea (no silencia)
  las excepciones de reglas individuales.
"""
from __future__ import annotations

import logging
import statistics
from typing import Any

from app.scoring import recent_base
from app.load import strength_minutes
from app.i18n import tr
from app.changes import detect_changes

logger = logging.getLogger("vitals.insights")


# ── helpers ────────────────────────────────────────────────────────────────────

def _window(days: list[dict], n: int, field: str) -> list[float]:
    """Últimos N días con <field> no-None, en orden cronológico."""
    vals: list[float] = []
    for d in days[-n:]:
        v = d.get(field)
        if v is not None:
            vals.append(float(v))
    return vals


def _mean(vals: list[float]) -> float | None:
    return statistics.mean(vals) if vals else None


def _pstdev(vals: list[float]) -> float | None:
    return statistics.pstdev(vals) if len(vals) >= 2 else None


def _last_with_field(days: list[dict], field: str) -> float | None:
    """Valor del campo en el día más reciente que lo tenga."""
    for d in reversed(days):
        v = d.get(field)
        if v is not None:
            return float(v)
    return None


# ── z-score helper (Tier 2 Feature A) ─────────────────────────────────────────

_SD_EPSILON = 0.01   # SD mínima general para usar z-score
_TEMP_SD_MIN = 0.1   # SD mínima para temp/resp (per roadmap: sd>=0.1 and z>1.5)

# Umbral de delta absoluto para skin_temp. Es un OR con el z-score de abajo, no
# lo reemplaza: mismo criterio (delta = temp_hoy - media(días previos), sin
# dividir por sd) que la bandera de coach_chat._build_context(), para que el
# motor de insights y el del chat no diverjan ante la misma señal.
# Motivo: con una skin_temp de varianza alta, la sd infla el denominador y un
# delta clínicamente relevante nunca alcanza el z — la señal queda enmascarada.
# El delta absoluto la recupera. Calibrable con _dev/analyze_illness_threshold.py.
_TEMP_DELTA_ALERT = 0.6

# Umbral de HRV anómala BIDIRECCIONAL (|z| en cualquier dirección).
# Por qué bidireccional: una HRV anormalmente ALTA (no solo baja) junto con temp
# elevada es la firma del "pico parasimpático" de las primeras horas de una
# infección. La regla solo miraba HRV BAJA (z<-1.5), así que ese patrón pasaba
# desapercibido. Esta señal es ADITIVA: la de HRV baja se conserva sin cambios.
#
# Por qué 1.5 y no un margen más alto: el umbral vive en z-scores, así que
# depende de la ESCALA de la serie canónica de HRV — y esa serie CAMBIA de
# fuente (merge.py elige canónica por nº de días + frescura). Un mismo día se ve
# distinto según qué fuente gane: entre dos series reales del mismo histórico,
# el mismo día medía z=2.72 en una y z=1.69 en la otra. Con 2.0 el insight se
# degradaba a `watch` al cambiar la fuente; 1.5 lo detecta en ambas y marca los
# MISMOS días, sin costo de especificidad (2.0 y 1.5 miden idéntico en la serie
# donde ambos funcionan). 3.0 pierde el patrón en las dos.
# LECCIÓN: un umbral en z-scores es frágil al cambio de fuente canónica del
# merge — al recalibrar, medir contra la serie que esté ganando.
#
# Especificidad MEDIDA sobre un histórico real (397 días, 219 evaluables;
# corriendo la regla real sobre ventanas rodantes): la señal añade 2 días de
# alert (1.37% -> 2.28%), bajo el gate del 3%. El `watch` no se mueve (3.65%):
# hrv_anomala es UNA co-señal y el watch-sin-temp exige >=2. Falso positivo
# clave descartado: de los días con HRV alta y temp NORMAL (buena recuperación,
# incl. z=+3.77), ninguno dispara.
#
# 🔴 Honestidad sobre la evidencia: calibrado con muy pocos casos positivos y
# sin ground truth etiquetado. Lo que lo hace seguro NO es certeza clínica sino
# la frecuencia medida (~2 avisos en 6 meses): si alguno es falso positivo, el
# costo es bajo. El hábito `sick` del journal es ground-truth a futuro para
# re-calibrar cuando haya más episodios registrados.
_HRV_ANOMALY_Z = 1.5


def _z_score(value: float, mean: float, sd: float | None,
             sd_min: float = _SD_EPSILON) -> float | None:
    """
    Calcula z = (value - mean) / sd.
    Devuelve None si sd es None o sd < sd_min (ventana degenerada → fallback absoluto).
    NUNCA divide por cero.
    """
    if sd is None or sd < sd_min:
        return None
    return (value - mean) / sd


# ── Regla 1: illness_early_warning ─────────────────────────────────────────────

def rule_illness_early_warning(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """
    Señales del último día vs base.

    Tier 2: cada señal usa z-score (|z|>1.5) cuando la SD está disponible;
    fallback a umbral absoluto cuando SD<ε o no disponible.

    Señales:
    - skin_temp: z = (temp_today - mean14d) / sd14d; elevada si z>1.5
                 fallback: temp_today > mean14d + 0.5
                 F1 (2026-07): también elevada si delta = temp_today - mean14d
                 > _TEMP_DELTA_ALERT (0.6, mismo criterio que coach_chat) —
                 cubre sd alta que enmascara el z-score. Es un OR con lo de
                 arriba, no lo reemplaza.
    - rhr:       z = (rhr_today - rhr_base) / rhr_sd; alta si z>1.5
                 fallback: rhr_today > rhr_base + 5
    - hrv:       z = (hrv_today - hrv_base) / hrv_sd; baja si z<-1.5
                 fallback: hrv_today < hrv_base * 0.85
                 F2 (roadmap "vitals-illness-hrv-bidireccional"): además,
                 anómala si |z| > _HRV_ANOMALY_Z (1.5), en CUALQUIER dirección
                 (alta o baja) — solo cuando hay z-score real (sd disponible;
                 sin sd no hay fallback bidireccional, ver constante arriba).
                 Es aditivo: la señal de HRV baja (z<-1.5) se conserva igual.
    - resp:      z = (resp_today - mean14d) / sd14d; elevada si z>1.5
                 fallback: resp_today > mean14d + 1.5
    - spo2:      < 92 (sin cambio — no hay SD para SpO₂)

    Si temp_elevada + ≥1 señal → alert
    Si ≥2 señales (sin temp)   → watch
    """
    if not days:
        return None

    today = days[-1]
    hrv_base = recent_base(summary, "hrv")
    rhr_base = recent_base(summary, "rhr")

    # SDs desde summary (Feature A; None si no están disponibles → fallback)
    hrv_sd: float | None = summary.get("hrv_sd")
    rhr_sd: float | None = summary.get("rhr_sd")

    # Two separate lists: temp signal + non-temp signals (to avoid filtering by string content)
    temp_signals: list[str] = []
    non_temp_signals: list[str] = []
    temp_elevated = False

    # skin_temp
    # Per roadmap: z-score path if sd>=0.1; else fallback absoluto (+0.5°).
    # F1 (roadmap "vitals-illness-proactivo"): además del z-score, se marca
    # elevada si el delta absoluto vs la media de días previos supera
    # _TEMP_DELTA_ALERT (mismo cálculo/umbral que coach_chat._build_context,
    # ver constante arriba) — cuando la sd es alta el z-score puede enmascarar
    # un delta clínicamente relevante (p.ej. z=0.92 con delta=+1.17).
    # Es un OR, no un reemplazo: los casos que ya disparaban por z siguen igual.
    temp_window = _window(days, 14, "skin_temp")
    temp_today = today.get("skin_temp")
    if temp_today is not None and len(temp_window) >= 3:
        temp_mean = _mean(temp_window[:-1]) if len(temp_window) > 1 else _mean(temp_window)
        if temp_mean is not None:
            temp_sd = _pstdev(temp_window[:-1]) if len(temp_window) > 2 else None
            z = _z_score(float(temp_today), temp_mean, temp_sd, sd_min=_TEMP_SD_MIN)
            if z is not None:
                elevated = z > 1.5
            else:
                # Fallback absoluto (sd<0.1 o sin datos suficientes)
                elevated = float(temp_today) > temp_mean + 0.5
            delta = float(temp_today) - temp_mean
            if delta > _TEMP_DELTA_ALERT:
                elevated = True
            if elevated:
                temp_elevated = True
                temp_signals.append(tr("factor_temp_elevated", locale, temp_today=float(temp_today), temp_mean=temp_mean))

    # rhr
    rhr_today = today.get("rhr")
    if rhr_today is not None and rhr_base is not None:
        z = _z_score(float(rhr_today), float(rhr_base), rhr_sd)
        if z is not None:
            fired = z > 1.5
        else:
            # Fallback absoluto
            fired = float(rhr_today) > float(rhr_base) + 5
        if fired:
            non_temp_signals.append(tr("factor_rhr_high", locale, rhr_today=int(rhr_today), rhr_base=int(rhr_base)))

    # hrv
    hrv_today = today.get("hrv")
    if hrv_today is not None and hrv_base is not None:
        z = _z_score(float(hrv_today), float(hrv_base), hrv_sd)
        if z is not None:
            fired_low = z < -1.5
            # F2: hrv_anomala — bidireccional (|z|>2.0). Solo evaluable con
            # z-score real (sd presente y >= _SD_EPSILON); None-safe por
            # construcción: si sd falta o es degenerada, z es None y esta
            # rama ni se evalúa (ver bloque else abajo).
            fired_anomaly = abs(z) > _HRV_ANOMALY_Z
        else:
            # Fallback absoluto (sin sd → sin criterio bidireccional nuevo,
            # se conserva el comportamiento previo tal cual)
            fired_low = float(hrv_today) < float(hrv_base) * 0.85
            fired_anomaly = False
        if fired_low:
            # HRV baja (z<-1.5, o su fallback): incluye también el caso
            # z<-2.0 (que también cumpliría fired_anomaly) — un solo factor,
            # no se duplica la señal por el mismo metric.
            non_temp_signals.append(tr("factor_hrv_low", locale, hrv_today=int(hrv_today), hrv_base=int(hrv_base)))
        elif fired_anomaly:
            # Solo llega aquí si z>_HRV_ANOMALY_Z (HRV anormalmente ALTA;
            # el lado bajo ya lo cubrió fired_low arriba) — "pico
            # parasimpático" de infección temprana.
            non_temp_signals.append(tr("factor_hrv_anomalous", locale, hrv_today=int(hrv_today), hrv_base=int(hrv_base)))

    # resp — análogo a temp (sd_min=_TEMP_SD_MIN para consistencia)
    resp_window = _window(days, 14, "resp")
    resp_today = today.get("resp")
    if resp_today is not None and len(resp_window) >= 3:
        resp_mean = _mean(resp_window[:-1]) if len(resp_window) > 1 else _mean(resp_window)
        if resp_mean is not None:
            resp_sd = _pstdev(resp_window[:-1]) if len(resp_window) > 2 else None
            z = _z_score(float(resp_today), resp_mean, resp_sd, sd_min=_TEMP_SD_MIN)
            if z is not None:
                fired = z > 1.5
            else:
                # Fallback absoluto
                fired = float(resp_today) > resp_mean + 1.5
            if fired:
                non_temp_signals.append(tr("factor_resp_elevated", locale, resp_today=float(resp_today), resp_mean=resp_mean))

    # spo2
    spo2_today = today.get("spo2")
    if spo2_today is not None and float(spo2_today) < 92:
        non_temp_signals.append(tr("factor_spo2_low_signal", locale, spo2_today=float(spo2_today)))

    signals = temp_signals + non_temp_signals
    n_signals = len(signals)

    if temp_elevated and n_signals >= 2:
        return {
            "id": "illness_early_warning",
            "severity": "alert",
            "category": "salud",
            "icon": "🌡️",
            "title": tr("illness_alert_title", locale),
            "summary": tr("illness_alert_summary", locale),
            "factors": signals,
            "recommendation": tr("illness_alert_rec", locale),
        }
    elif temp_elevated and n_signals == 1:
        return {
            "id": "illness_early_warning",
            "severity": "watch",
            "category": "salud",
            "icon": "🌡️",
            "title": tr("illness_watch_temp_title", locale),
            "summary": tr("illness_watch_temp_summary", locale),
            "factors": signals,
            "recommendation": tr("illness_watch_temp_rec", locale),
        }
    elif not temp_elevated and len(non_temp_signals) >= 2:
        return {
            "id": "illness_early_warning",
            "severity": "watch",
            "category": "salud",
            "icon": "⚠️",
            "title": tr("illness_watch_stress_title", locale),
            "summary": tr("illness_watch_stress_summary", locale),
            "factors": non_temp_signals,
            "recommendation": tr("illness_watch_stress_rec", locale),
        }

    return None


# ── Regla 2: spo2_low ──────────────────────────────────────────────────────────

def rule_spo2_low(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """SpO₂ < 90% en ≥1 de las últimas 7 noches → alert médico."""
    recent = days[-7:]
    low_nights = [
        d["date"] for d in recent
        if d.get("spo2") is not None and float(d["spo2"]) < 90
    ]
    if not low_nights:
        return None
    return {
        "id": "spo2_low",
        "severity": "alert",
        "category": "salud",
        "icon": "💧",
        "title": tr("spo2_low_title", locale),
        "summary": tr("spo2_low_summary", locale, n=len(low_nights)),
        "factors": [tr("spo2_low_factor", locale, dates=", ".join(low_nights))],
        "recommendation": tr("spo2_low_rec", locale),
    }


# ── Regla 3: sleep_debt ────────────────────────────────────────────────────────

def rule_sleep_debt(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """≥3/7 noches < SHORT_NIGHT → watch; ≥5/7 → alert.

    Ronda 5: SHORT_NIGHT = summary["sleep_target_min"] - 60 (antes 420 literal).
    Con sleep_target_min default 480, SHORT_NIGHT = 420 — IDÉNTICO a antes.
    Fallback a 480 si summary no trae el campo (datasets viejos / tests directos)."""
    recent = days[-7:]
    short_night_threshold = summary.get("sleep_target_min", 480) - 60
    short_nights = [
        d for d in recent
        if d.get("asleep") is not None and float(d["asleep"]) < short_night_threshold
    ]
    n = len(short_nights)
    if n < 3:
        return None

    sleep_vals = [d["asleep"] for d in recent if d.get("asleep") is not None]
    avg_h = _mean(sleep_vals)
    avg_str = f"{avg_h/60:.1f}h" if avg_h else "N/D"
    severity = "alert" if n >= 5 else "watch"

    return {
        "id": "sleep_debt",
        "severity": severity,
        "category": tr("sleep_debt_cat", locale),
        "icon": "😴",
        "title": tr("sleep_debt_title", locale),
        "summary": tr("sleep_debt_summary", locale, n=n, avg_str=avg_str),
        "factors": [
            tr("sleep_debt_factor_nights", locale, n=n),
            tr("sleep_debt_factor_avg", locale, avg_str=avg_str),
        ],
        "recommendation": tr("sleep_debt_rec", locale),
    }


# ── Regla 4: overtraining ──────────────────────────────────────────────────────

def rule_overtraining(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """
    Strain 7d alto (promedio > 14) + (recovery 7d < recovery 30d - 10
    O ≥2 días recovery < 34 en últimos 7d).
    """
    recent7 = days[-7:]
    recent30 = days[-30:]

    strain_vals_7 = [float(d["strain"]) for d in recent7 if d.get("strain") is not None]
    if not strain_vals_7:
        return None
    avg_strain_7 = _mean(strain_vals_7)
    if avg_strain_7 is None or avg_strain_7 <= 14:
        return None

    rec_vals_7 = [float(d["recovery"]) for d in recent7 if d.get("recovery") is not None]
    rec_vals_30 = [float(d["recovery"]) for d in recent30 if d.get("recovery") is not None]

    avg_rec_7 = _mean(rec_vals_7)
    avg_rec_30 = _mean(rec_vals_30)

    low_rec_days = sum(1 for v in rec_vals_7 if v < 34)

    condition_a = (avg_rec_7 is not None and avg_rec_30 is not None and
                   avg_rec_7 < avg_rec_30 - 10)
    condition_b = low_rec_days >= 2

    if not (condition_a or condition_b):
        return None

    factors = [tr("overtraining_factor_strain", locale, avg_strain_7=avg_strain_7)]
    if avg_rec_7:
        factors.append(tr("overtraining_factor_rec", locale, avg_rec_7=avg_rec_7))
    if low_rec_days >= 2:
        factors.append(tr("overtraining_factor_lowrec", locale, low_rec_days=low_rec_days))

    return {
        "id": "overtraining",
        "severity": "watch",
        "category": "entrenamiento",
        "icon": "🔥",
        "title": "Posible sobreentrenamiento",
        "summary": tr("overtraining_summary", locale),
        "factors": factors,
        "recommendation": tr("overtraining_rec", locale),
    }


# ── Regla 5: recovery_declining ────────────────────────────────────────────────

def rule_recovery_declining(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """Recovery promedio 7d < 30d por margen ≥ 8 puntos (sin duplicar con overtraining)."""
    recent7 = days[-7:]
    recent30 = days[-30:]

    rec_vals_7 = [float(d["recovery"]) for d in recent7 if d.get("recovery") is not None]
    rec_vals_30 = [float(d["recovery"]) for d in recent30 if d.get("recovery") is not None]

    if len(rec_vals_7) < 3 or len(rec_vals_30) < 7:
        return None

    avg_7 = _mean(rec_vals_7)
    avg_30 = _mean(rec_vals_30)

    if avg_7 is None or avg_30 is None:
        return None

    margin = avg_30 - avg_7
    if margin < 8:
        return None

    # No duplicar con overtraining (si strain alto, esa regla ya lo capta)
    strain_vals_7 = [float(d["strain"]) for d in recent7 if d.get("strain") is not None]
    avg_strain_7 = _mean(strain_vals_7)
    if avg_strain_7 is not None and avg_strain_7 > 14:
        # overtraining ya lo reporta; evitar duplicado
        return None

    return {
        "id": "recovery_declining",
        "severity": "watch",
        "category": tr("rec_declining_cat", locale),
        "icon": "📉",
        "title": tr("rec_declining_title", locale),
        "summary": tr("rec_declining_summary", locale, avg_7=avg_7, margin=margin, avg_30=avg_30),
        "factors": [
            tr("rec_declining_factor_7d", locale, avg_7=avg_7),
            tr("rec_declining_factor_30d", locale, avg_30=avg_30),
            tr("rec_declining_factor_diff", locale, margin=margin),
        ],
        "recommendation": tr("rec_declining_rec", locale),
    }


# ── Regla 6: bedtime_inconsistency ─────────────────────────────────────────────

def rule_bedtime_inconsistency(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """pstdev(bed_min últimos 21d) > 75 min → watch."""
    bed_vals = _window(days, 21, "bed_min")
    if len(bed_vals) < 7:
        return None

    sd = _pstdev(bed_vals)
    if sd is None or sd <= 75:
        return None

    return {
        "id": "bedtime_inconsistency",
        "severity": "watch",
        "category": tr("bedtime_incons_cat", locale),
        "icon": "🕐",
        "title": tr("bedtime_incons_title", locale),
        "summary": tr("bedtime_incons_summary", locale, sd=sd),
        "factors": [
            tr("bedtime_incons_factor_sd", locale, sd=sd),
            tr("bedtime_incons_factor_n", locale, n=len(bed_vals)),
        ],
        "recommendation": tr("bedtime_incons_rec", locale),
    }


# ── Regla 7: strength_gap ──────────────────────────────────────────────────────

def rule_strength_gap(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """0 min de fuerza REAL (strength_minutes sobre exercises) en 7d → info.

    Ronda 3: antes usaba 'vigorous' (proxy de cardio/AZM) como señal — un falso
    negativo para el insight más importante del usuario (podía tener semanas 100% cardio
    vigoroso, cero pesas, y la regla no disparaba). Ahora usa strength_minutes()
    (app/load.py, mismo helper que coach.py/coach_chat.py) sobre los ejercicios REALES
    de la ventana de 7 días. `exercises` viaja en summary["_exercises"] (evaluate()
    lo inyecta desde dataset["exercises"] antes de correr las reglas) para no romper
    el signature uniforme rule(days, summary, locale) que usan las demás 8 reglas.

    Guard preservado de la versión anterior: 'sin datos de entrenamiento en absoluto
    → no disparar' (ausencia de dato ≠ malo). Antes ese guard miraba si había ALGÚN
    día con 'vigorous' no-None en los últimos 7 días; ahora mira si hubo AL MENOS UN
    ejercicio registrado (de cualquier tipo) en la misma ventana de 7 días — incluso
    si esos ejercicios eran solo cardio, ya hay dato de entrenamiento para evaluar.
    """
    recent7 = days[-7:]
    dates_window = {d.get("date") for d in recent7 if d.get("date")}
    if not dates_window:
        return None

    exercises = summary.get("_exercises") or []
    sessions_in_window = [e for e in exercises if e.get("date") in dates_window]

    if not sessions_in_window:
        # Sin datos de entrenamiento en absoluto en la ventana — no disparar.
        return None

    total_strength = strength_minutes(exercises, dates=dates_window)
    if total_strength > 0:
        return None

    return {
        "id": "strength_gap",
        "severity": "info",
        "category": "entrenamiento",
        "icon": "💪",
        "title": tr("strength_gap_title", locale),
        "summary": tr("strength_gap_summary", locale),
        "factors": [tr("strength_gap_factor", locale)],
        "recommendation": tr("strength_gap_rec", locale),
    }


# ── Regla 8: positive_hrv ──────────────────────────────────────────────────────

def rule_positive_hrv(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """HRV 7d > 30d y tendencia al alza ≥3 días consecutivos."""
    hrv_7 = _window(days, 7, "hrv")
    hrv_30 = _window(days, 30, "hrv")

    if len(hrv_7) < 3 or len(hrv_30) < 7:
        return None

    avg_7 = _mean(hrv_7)
    avg_30 = _mean(hrv_30)

    if avg_7 is None or avg_30 is None or avg_7 <= avg_30:
        return None

    # Verificar tendencia al alza ≥3 días consecutivos (al menos 3 de los últimos 7)
    rising = 0
    max_rising = 0
    for i in range(1, len(hrv_7)):
        if hrv_7[i] > hrv_7[i - 1]:
            rising += 1
            max_rising = max(max_rising, rising)
        else:
            rising = 0

    if max_rising < 2:
        return None

    return {
        "id": "positive_hrv",
        "severity": "positive",
        "category": tr("pos_hrv_cat", locale),
        "icon": "📈",
        "title": tr("pos_hrv_title", locale),
        "summary": tr("pos_hrv_summary", locale, avg_7=avg_7, avg_30=avg_30),
        "factors": [
            tr("pos_hrv_factor_7d", locale, avg_7=avg_7),
            tr("pos_hrv_factor_30d", locale, avg_30=avg_30),
            tr("pos_hrv_factor_trend", locale, delta=avg_7 - avg_30),
        ],
        "recommendation": tr("pos_hrv_rec", locale),
    }


# ── Regla 9: positive_sleep ────────────────────────────────────────────────────

def rule_positive_sleep(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """0 noches < (sleep_target_min - 60) en los últimos 7 días → positive.

    Ronda P1 (UI plain-language): threshold = summary["sleep_target_min"] - 60,
    misma derivación que rule_sleep_debt (Ronda 5) — antes literal 420.
    Con sleep_target_min default 480, threshold = 420 — IDÉNTICO a antes.
    Fallback a 480 si summary no trae el campo (datasets viejos / tests directos)."""
    recent7 = days[-7:]
    sleep_days = [d for d in recent7 if d.get("asleep") is not None]

    if len(sleep_days) < 5:
        # Menos de 5 noches con datos — no hay suficiente evidencia
        return None

    short_night_threshold = summary.get("sleep_target_min", 480) - 60
    short = [d for d in sleep_days if float(d["asleep"]) < short_night_threshold]
    if short:
        return None

    sleep_vals = [float(d["asleep"]) for d in sleep_days]
    avg_h = _mean(sleep_vals)
    avg_str = f"{avg_h/60:.1f}h" if avg_h else "N/D"

    return {
        "id": "positive_sleep",
        "severity": "positive",
        "category": tr("pos_sleep_cat", locale),
        "icon": "⭐",
        "title": tr("pos_sleep_title", locale),
        "summary": tr("pos_sleep_summary", locale, avg_str=avg_str),
        "factors": [
            tr("pos_sleep_factor_nights", locale, n_days=len(sleep_days)),
            tr("pos_sleep_factor_avg", locale, avg_str=avg_str),
        ],
        "recommendation": tr("pos_sleep_rec", locale),
    }


# ── Reglas Fase 7: salud femenina / ciclo (gateadas por summary["_cycle"]) ────
#
# summary["_cycle"] lo inyecta evaluate() desde dataset["_cycle"] (patrón calcado
# de "_exercises") ANTES de correr las reglas. Cada regla se auto-gatea: si
# _cycle es None o enabled=False (toggle opt-in apagado, o el motor de cycle.py
# no corrió/falló), la regla no dispara — cero fuga de datos de ciclo (criterio
# #1 del roadmap). Todas usan tr() para i18n. Las que muestran ventana fértil,
# retraso o señal peri/meno SIEMPRE incluyen el disclaimer (criterio #7).

def rule_cycle_phase(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """Info: fase actual del ciclo + día de ciclo. Sin datos suficientes (sin
    ningún periodo registrado, cycle_day None) -> no dispara."""
    cyc = summary.get("_cycle")
    if not cyc or not cyc.get("enabled"):
        return None
    phase = cyc.get("phase")
    cycle_day = cyc.get("cycle_day")
    if phase is None or cycle_day is None:
        return None

    return {
        "id": "cycle_phase",
        "severity": "info",
        "category": tr("cycle_cat", locale),
        "icon": "🌙",
        "title": tr("cycle_phase_title", locale, phase=tr(f"phase_{phase}", locale)),
        "summary": tr("cycle_phase_summary", locale, cycle_day=cycle_day, phase=tr(f"phase_{phase}", locale)),
        "factors": [tr("cycle_phase_factor", locale, cycle_day=cycle_day)],
        "recommendation": tr("cycle_phase_rec", locale),
    }


def rule_period_approaching(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """Watch/info: periodo predicho en <=3 días."""
    cyc = summary.get("_cycle")
    if not cyc or not cyc.get("enabled"):
        return None
    period = cyc.get("period") or {}
    days_until = period.get("days_until")
    if days_until is None or not (0 <= days_until <= 3):
        return None

    return {
        "id": "period_approaching",
        "severity": "info",
        "category": tr("cycle_cat", locale),
        "icon": "🩸",
        "title": tr("period_approaching_title", locale),
        "summary": tr("period_approaching_summary", locale, days_until=days_until),
        "factors": [tr("period_approaching_factor", locale, predicted=period.get("predicted_next", ""))],
        "recommendation": tr("period_approaching_rec", locale),
    }


def rule_cycle_delay(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """Watch: retraso detectado vs predicción. Incluye disclaimer (no-diagnóstico)."""
    cyc = summary.get("_cycle")
    if not cyc or not cyc.get("enabled"):
        return None
    delay = cyc.get("delay") or {}
    if not delay.get("is_delayed"):
        return None
    n_days = delay.get("days", 0)

    return {
        "id": "cycle_delay",
        "severity": "watch",
        "category": tr("cycle_cat", locale),
        "icon": "⏳",
        "title": tr("cycle_delay_title", locale),
        "summary": tr("cycle_delay_summary", locale, n_days=n_days),
        "factors": [tr("cycle_delay_factor", locale, n_days=n_days)],
        "recommendation": tr("cycle_delay_rec", locale) + " " + tr("cycle_disclaimer", locale),
    }


def rule_perimenopause_signal(days: list[dict], summary: dict, locale: str = "es") -> dict | None:
    """Info: patrón de irregularidad/ciclo saltado sugestivo de peri/menopausia.
    Historial insuficiente -> stage='insufficient_history' -> NO dispara (cero
    falsos positivos, criterio #6). Incluye disclaimer."""
    cyc = summary.get("_cycle")
    if not cyc or not cyc.get("enabled"):
        return None
    meno = cyc.get("menopause") or {}
    stage = meno.get("stage")
    if stage not in ("perimenopause_possible", "menopause_possible"):
        return None

    return {
        "id": "perimenopause_signal",
        "severity": "info",
        "category": tr("cycle_cat", locale),
        "icon": "🍂",
        "title": tr(f"{stage}_title", locale),
        "summary": tr(f"{stage}_summary", locale),
        "factors": [tr(f"meno_signal_{s}", locale) for s in meno.get("signals", [])],
        "recommendation": tr("perimenopause_rec", locale) + " " + tr("cycle_disclaimer", locale),
    }


# ── EVALUATE ───────────────────────────────────────────────────────────────────

_RULES = [
    rule_illness_early_warning,
    rule_spo2_low,
    rule_sleep_debt,
    rule_overtraining,
    rule_recovery_declining,
    rule_bedtime_inconsistency,
    rule_strength_gap,
    rule_positive_hrv,
    rule_positive_sleep,
    rule_cycle_phase,
    rule_period_approaching,
    rule_cycle_delay,
    rule_perimenopause_signal,
]

_SEVERITY_ORDER = {"alert": 0, "watch": 1, "positive": 2, "info": 3}

# Frescura de Alertas (Paso 2): tramo "fresh" entre alert y watch — un cambio
# nuevo significativo nunca debe quedar enterrado bajo un watch persistente
# repetido, pero SIEMPRE debajo de una alerta médica (illness/spo2, seguridad
# primero). positive/info de "fresh" se insertan igual en el tramo fresh (para
# que lo nuevo se note), no en su tramo histórico de severidad.
_FRESH_ORDER = 1  # entre alert(0) y watch(2 tras el corrimiento)
_ORDER_SHIFT = {  # severidades NO frescas se corren +1 para dejar hueco a "fresh"
    "alert": 0, "watch": 2, "positive": 3, "info": 4,
}

# Anti-duplicado: si una de estas reglas YA disparó (por id), el evento de
# cambio del mismo factor/dirección se omite — evita repetir la misma señal
# dos veces (p.ej. "recovery_declining" ya narra la caída sostenida; el evento
# fresco de un solo día de caída de recovery no debe listarse aparte).
# Insights POSITIVOS que se silencian cuando hay una alerta de enfermedad activa
# (ver evaluate()). Hoy solo el de HRV: es el mismo dato que alimenta la señal
# hrv_anomala de illness_early_warning, así que afirmarlo como buena noticia
# contradice la alerta el mismo día.
_ILLNESS_SILENCED_IDS = {"positive_hrv"}

# Eventos de cambio equivalentes (factor, kind) que se silencian por el mismo motivo
# — si no, entrarían por detect_changes al quedar libre el anti-duplicado de abajo.
_ILLNESS_SILENCED_CHANGES = {("hrv", "improvement")}

_CHANGE_ANTI_DUP = {
    ("recovery", "decline"): {"recovery_declining", "overtraining"},
    ("recovery", "improvement"): set(),
    ("hrv", "decline"): {"illness_early_warning"},
    ("hrv", "improvement"): {"positive_hrv"},
    ("rhr", "decline"): {"illness_early_warning"},
    ("sleep", "decline"): {"sleep_debt"},
    ("sleep", "streak"): {"sleep_debt", "positive_sleep"},
    ("bedtime", "decline"): {"bedtime_inconsistency"},
    ("strength", "milestone"): {"strength_gap"},
    ("skin_temp", "decline"): {"illness_early_warning"},
}


def _change_event_to_insight(event: dict) -> dict:
    """Proyecta un evento de app.changes.detect_changes() al shape de insight
    ({id, severity, category, icon, title, summary, factors, recommendation})
    que consume renderInsights() en el template. `fresh=True` marca el insight
    para el ranking especial (ver _FRESH_ORDER)."""
    factor = event.get("factor", "change")
    kind = event.get("kind", "milestone")
    _ICONS = {
        "recovery": "🔋", "hrv": "💓", "rhr": "❤️", "sleep": "😴",
        "strain": "🔥", "steps": "🚶", "skin_temp": "🌡️", "bedtime": "🕐",
        "strength": "💪", "vo2max": "📈",
    }
    return {
        "id": f"change_{factor}_{kind}",
        "severity": event.get("severity", "info"),
        "category": "change",
        "icon": _ICONS.get(factor, "✨"),
        "title": event.get("title", ""),
        "summary": event.get("summary", ""),
        "factors": [],
        "recommendation": event.get("recommendation", ""),
        "fresh": True,
        "_factor": factor,
        "_kind": kind,
    }


def _sort_key(insight: dict) -> tuple:
    base = _ORDER_SHIFT.get(insight.get("severity", "info"), 99)
    if insight.get("fresh"):
        return (_FRESH_ORDER, 0)
    return (base, 0)


def evaluate(dataset: dict, locale: str = "es") -> list[dict]:
    """
    Evalúa todas las reglas sobre el dataset y devuelve la lista de insights
    ordenada: alertas médicas -> cambios frescos significativos -> watch
    persistentes -> positive -> info, limitada a 5.

    dataset: {"days": [...], "summary": {...}, "exercises": [...]}

    Frescura de Alertas (Paso 2): los eventos de app.changes.detect_changes()
    se mezclan ANTES del sort con un flag fresh=True que los ubica justo
    después de las alertas médicas (illness/spo2) y antes de los watch
    persistentes — un cambio nuevo significativo nunca queda enterrado.
    Anti-duplicado: un evento de cambio se omite si una regla existente del
    mismo factor/dirección ya disparó (_CHANGE_ANTI_DUP). Con 0 cambios
    detectados, el comportamiento es EXACTAMENTE el de antes de este paso.
    """
    days: list[dict] = (dataset or {}).get("days", [])
    summary: dict = dict((dataset or {}).get("summary", {}) or {})
    # rule_strength_gap necesita exercises; se inyecta en una copia de summary para
    # no mutar el dataset del caller y sin romper el signature uniforme de las reglas.
    summary["_exercises"] = (dataset or {}).get("exercises", [])
    # Fase 7: mismo patrón para el estado de ciclo (salud femenina). El caller
    # (main.py, en / y /api/insights) computa cycle.compute_cycle_state(...) y lo
    # pasa como dataset["_cycle"]; None si el toggle está apagado o el motor falló
    # — las 4 reglas de ciclo se auto-gatean sobre esto (cero fuga con opt-out).
    summary["_cycle"] = (dataset or {}).get("_cycle")

    if not days:
        return []

    results: list[dict] = []
    for rule in _RULES:
        try:
            insight = rule(days, summary, locale)
            if insight is not None:
                results.append(insight)
        except Exception as exc:
            # Nunca crashear: una regla defectuosa no debe tumbar las demás, pero
            # Ronda 3: el fallo queda LOGUEADO (antes se silenciaba con `pass`).
            logger.warning("Regla %s falló: %s", getattr(rule, "__name__", rule), exc)

    # Coherencia de mensaje: el MISMO dato (HRV alta) puede
    # disparar A LA VEZ la alerta de enfermedad (vía la señal hrv_anomala) y el positivo
    # "HRV en racha ascendente" — dos mensajes opuestos en la misma tarjeta el mismo día.
    # Es la señal mixta que le quita credibilidad al coach proactivo: dar luz verde y
    # avisar de una posible enfermedad a la vez. Con una alerta ACTIVA, los positivos
    # de HRV se silencian: la alerta manda.
    illness_alert = any(
        r.get("id") == "illness_early_warning" and r.get("severity") == "alert"
        for r in results
    )
    if illness_alert:
        results = [r for r in results if r.get("id") not in _ILLNESS_SILENCED_IDS]

    # OJO: fired_ids se calcula DESPUÉS del silenciado — si no, el anti-duplicado de
    # abajo creería que positive_hrv sigue vivo.
    fired_ids = {r.get("id") for r in results}

    try:
        change_events = detect_changes(dataset, locale)
    except Exception as exc:
        logger.warning("detect_changes falló: %s", exc)
        change_events = []

    for event in change_events:
        factor = event.get("factor", "")
        kind = event.get("kind", "")
        # El mismo silenciado, por la puerta de los cambios: sin esto, quitar
        # positive_hrv liberaría el anti-duplicado (_CHANGE_ANTI_DUP lo bloqueaba vía
        # positive_hrv) y el evento "HRV mejoró" reintroduciría el mensaje contradictorio.
        if illness_alert and (factor, kind) in _ILLNESS_SILENCED_CHANGES:
            continue
        skip_ids = _CHANGE_ANTI_DUP.get((factor, kind), set())
        if fired_ids & skip_ids:
            continue  # una regla existente ya narra esta misma señal
        results.append(_change_event_to_insight(event))

    results.sort(key=_sort_key)
    return results[:5]
