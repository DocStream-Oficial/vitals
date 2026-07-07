"""
healthspan.py — Healthspan / Pace of Aging (Fase 8D, paso D2).

compute_healthspan(days, exercises, profile) recomputa body age por VENTANAS
TRAILING de 90 días con paso MENSUAL sobre el histórico, reusando
app/bodyage.py::compute_body_age SIN tocar su fórmula (roadmap §Arquitectura,
"Pace of Aging"). Cada punto de la serie es "si solo hubiéramos tenido los
últimos 90 días terminando en esa fecha, ¿qué body age habría salido?" — el
MISMO cálculo que ya corre en vivo en sync.py, solo que congelado en el
tiempo y repetido mes a mes.

pace = pendiente anualizada del gap (body_age - chrono_age) vía
app/trends.py::linreg_slope. pace < 1 = el gap se achica con el tiempo
(envejeciendo más lento que el calendario); pace > 1 = se agranda.

<120 días de historial -> None (gate duro, mismo criterio que confidence=low
de bodyage pero a nivel de serie completa: con tan poco historial la
tendencia no es honesta).

PURO (sin I/O) — compute_healthspan no lee ni escribe archivos; recibe
days/exercises/profile ya cargados por el caller (patrón cycle.py/journal.py:
motor separado de la persistencia). No hay persistencia propia: se computa on
-demand en el GET y aditivamente en run_sync (summary.healthspan).
"""
from __future__ import annotations

import datetime
import logging
import statistics
from typing import Any, Optional

from app.bodyage import compute_body_age
from app.trends import linreg_slope

logger = logging.getLogger("vitals.healthspan")

# Historial mínimo para emitir CUALQUIER serie/pace — con menos, la ventana de
# 90d ni siquiera se llena una vez con margen razonable (gate duro, roadmap D2).
MIN_HISTORY_DAYS = 120

# Ventana trailing (días) recomputada en cada punto mensual — idéntica a la
# ventana "reciente" que ya usa compute_body_age internamente para
# rhr/hrv/asleep (recent(k, n=14) opera sobre el slice que le pasemos).
WINDOW_DAYS = 90

# Paso entre puntos de la serie (mensual ≈ 30 días, aproximación calendario-
# agnóstica y suficiente para un pace anualizado con ruido razonable).
STEP_DAYS = 30


def _parse_date(s: Any) -> Optional[datetime.date]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def _chrono_age_at(birthdate: Optional[str], ref_date: datetime.date) -> Optional[float]:
    """Edad cronológica (años, con decimales) en `ref_date` dado el birthdate
    ISO. None si birthdate es inválido/ausente (nunca lanza)."""
    bd = _parse_date(birthdate)
    if bd is None:
        return None
    days = (ref_date - bd).days
    if days < 0:
        return None
    return days / 365.25


def _window_slices(days: list, window_days: int, step_days: int) -> list:
    """Genera los cortes trailing: para cada fecha de corte (con paso
    step_days, empezando en la MÁS ANTIGUA fecha que aún permite una ventana
    completa de window_days), devuelve (cutoff_date, days_slice) donde
    days_slice = todos los días con date <= cutoff_date (compute_body_age ya
    recorta internamente a los últimos 14 vía recent()).

    Requiere `days` ordenado por fecha ascendente (invariante del dataset del
    repo — health_compact.json siempre viene así). Tolerante a huecos: usa
    fechas reales, no índices posicionales."""
    valid = []
    for d in days or []:
        dt = _parse_date(d.get("date"))
        if dt is not None:
            valid.append((dt, d))
    if not valid:
        return []
    valid.sort(key=lambda x: x[0])

    first_date = valid[0][0]
    last_date = valid[-1][0]

    earliest_cutoff = first_date + datetime.timedelta(days=window_days)
    if earliest_cutoff > last_date:
        return []

    slices = []
    cutoff = earliest_cutoff
    while cutoff <= last_date:
        window_start = cutoff - datetime.timedelta(days=window_days)
        slice_days = [d for dt, d in valid if window_start < dt <= cutoff]
        if slice_days:
            slices.append((cutoff, slice_days))
        cutoff = cutoff + datetime.timedelta(days=step_days)

    # Asegura que el último punto sea SIEMPRE la ventana más reciente posible
    # (la fecha real del último día del dataset), aunque no caiga justo en el
    # paso de step_days — evita que la serie "se quede corta" y el usuario vea
    # un healthspan desactualizado respecto a su última sync.
    if slices and slices[-1][0] != last_date:
        window_start = last_date - datetime.timedelta(days=window_days)
        slice_days = [d for dt, d in valid if window_start < dt <= last_date]
        if slice_days:
            slices.append((last_date, slice_days))

    return slices


def compute_healthspan(days: list, exercises: list, profile: Optional[dict]) -> Optional[dict]:
    """Serie de body age por ventanas trailing de 90d (paso mensual) + pace of
    aging (pendiente anualizada del gap body_age-chrono_age) + delta_quarter.

    Devuelve None si:
    - profile no trae birthdate/waist/sex utilizables, o
    - el historial cubre menos de MIN_HISTORY_DAYS.

    Formato: {series: [{month, body_age, chrono_age, gap}], pace, delta_quarter}
    Nunca lanza — cualquier error interno degrada a None (mismo criterio que
    compute_cycle_state: nunca crashea)."""
    try:
        days = days or []
        exercises = exercises or []
        profile = profile or {}

        birthdate = profile.get("birthdate")
        waist = profile.get("waist_cm")
        sex = profile.get("sex") or "M"

        if not birthdate or not waist:
            return None

        valid_dates = [d for d in (_parse_date(x.get("date")) for x in days) if d is not None]
        if not valid_dates:
            return None
        span_days = (max(valid_dates) - min(valid_dates)).days
        if span_days < MIN_HISTORY_DAYS:
            return None

        sleep_target_min = profile.get("sleep_target_min") or 480
        try:
            sleep_penalty_h = (float(sleep_target_min) - 60) / 60.0
        except Exception:
            sleep_penalty_h = 7.0

        slices = _window_slices(days, WINDOW_DAYS, STEP_DAYS)
        if not slices:
            return None

        series = []
        for cutoff_date, slice_days in slices:
            chrono_age = _chrono_age_at(birthdate, cutoff_date)
            if chrono_age is None:
                continue
            # Ejercicios hasta la fecha de corte (compute_body_age filtra
            # internamente a los últimos 28 días vía su propio cutoff).
            cutoff_str = cutoff_date.isoformat()
            slice_exercises = [e for e in exercises if e.get("date", "") <= cutoff_str]
            try:
                ba = compute_body_age(
                    slice_days, slice_exercises, chrono_age, float(waist), sex,
                    sleep_penalty_h=sleep_penalty_h,
                )
            except Exception as exc:
                logger.warning("compute_body_age falló en ventana %s (omitida): %s", cutoff_str, exc)
                continue

            body_age = ba.get("body_age")
            if body_age is None:
                continue

            series.append({
                "month": cutoff_date.strftime("%Y-%m"),
                "date": cutoff_str,
                "body_age": body_age,
                "chrono_age": round(chrono_age, 1),
                "gap": round(body_age - chrono_age, 1),
            })

        if len(series) < 2:
            return None

        gaps = [pt["gap"] for pt in series]
        slope_per_step = linreg_slope(gaps)
        if slope_per_step is None:
            pace = None
        else:
            # slope_per_step es el cambio de gap POR PUNTO de la serie (≈
            # STEP_DAYS entre puntos, salvo el último que puede ser irregular
            # por el ajuste de "última ventana real" en _window_slices). Se
            # anualiza con el paso NOMINAL (STEP_DAYS) — aproximación
            # documentada: el punto final irregular introduce un pequeño sesgo
            # que se acepta por simplicidad (no-ML, auditable).
            slope_per_day = slope_per_step / STEP_DAYS
            pace = round(1.0 + slope_per_day * 365.25, 2)

        # delta_quarter: cambio del gap en los últimos ~90 días de la SERIE
        # (no del dataset crudo) — compara el punto más reciente contra el
        # punto ~3 atrás (3 pasos mensuales ≈ 90 días) si existe, si no contra
        # el primero disponible.
        if len(series) >= 4:
            delta_quarter = round(series[-1]["gap"] - series[-4]["gap"], 1)
        else:
            delta_quarter = round(series[-1]["gap"] - series[0]["gap"], 1)

        return {
            "series": series,
            "pace": pace,
            "delta_quarter": delta_quarter,
            "current_gap": series[-1]["gap"],
        }
    except Exception as exc:
        logger.warning("compute_healthspan falló (degradando a None): %s", exc)
        return None
