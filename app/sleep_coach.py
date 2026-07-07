"""
sleep_coach.py — Sleep Coach: hora de dormir recomendada para esta noche
(Fase 8C, paso C4).

Motor PURO (sin I/O), patrón `cycle.py`/`journal.py`: recibe `days` (dataset ya
cargado) + `summary` + `profile`, devuelve un dict o None. Nunca lanza —
cualquier dato ralo/insuficiente degrada a None (criterio "nunca crashea" del
resto del repo).

Fórmula (roadmap C4):
  wake = hora de despertar MEDIANA de los últimos 14 días con `waketime`.
  necesidad = sleep_target_min
              + min(deuda_7d * 0.3, 60)      # deuda acumulada, cap 60 min
              + (20 si strain de HOY > 14 else 0)
              + (20 si recovery de HOY < 34 else 0)
  bedtime = wake − necesidad − 15min (latencia de conciliación)

deuda_7d = suma de (sleep_target_min − asleep) de los últimos 7 días con dato,
           solo sumando déficits (días con superávit de sueño no restan deuda —
           consistente con cómo WHOOP/Oura comunican "sleep debt").
"""
from __future__ import annotations

import logging
import statistics
from typing import Any, Optional

from app.sleep_scores import (
    sleep_need_min as _sleep_need_min,
    _DEBT_WEIGHT,
    _DEBT_CAP_MIN,
    _STRAIN_HIGH_THRESHOLD,
    _STRAIN_ADJUST_MIN,
    _RECOVERY_LOW_THRESHOLD,
    _RECOVERY_ADJUST_MIN,
    _DEBT_WINDOW_DAYS,
    _sleep_debt_min,
)

logger = logging.getLogger("vitals.sleep_coach")

_LATENCY_MIN = 15

_WAKE_WINDOW_DAYS = 14
_MIN_WAKE_SAMPLES = 3  # por debajo de esto, la mediana no es confiable -> None


def _parse_hhmm(s: Any) -> Optional[int]:
    """'HH:MM' -> minutos desde medianoche (0-1439). None si no parseable."""
    if not isinstance(s, str) or ":" not in s:
        return None
    try:
        h, m = s.split(":", 1)
        h, m = int(h), int(m)
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
        return h * 60 + m
    except Exception:
        return None


def _median_wake_min(days: list) -> Optional[int]:
    """Mediana circular-friendly de la hora de despertar en minutos desde
    medianoche, sobre los últimos _WAKE_WINDOW_DAYS días con `waketime`.
    Nota: "circular" no se implementa (wake times normales no cruzan
    medianoche en la práctica — a diferencia de bed_min); simple mediana de
    minutos-del-día es correcta aquí."""
    recent = days[-_WAKE_WINDOW_DAYS:] if days else []
    wake_vals = []
    for d in recent:
        if not isinstance(d, dict):
            continue
        wm = _parse_hhmm(d.get("waketime"))
        if wm is not None:
            wake_vals.append(wm)
    if len(wake_vals) < _MIN_WAKE_SAMPLES:
        return None
    try:
        return int(round(statistics.median(wake_vals)))
    except Exception:
        return None


def _min_to_hhmm(total_min: float) -> str:
    """Minutos desde medianoche (puede ser negativo o >1440) -> 'HH:MM' de
    reloj de 24h, normalizado al día."""
    total = int(round(total_min)) % (24 * 60)
    return f"{total // 60:02d}{':'}{total % 60:02d}"


def recommend_bedtime(days: list, summary: Optional[dict] = None,
                       profile: Optional[dict] = None) -> Optional[dict]:
    """Recomienda la hora de dormir de esta noche.

    Devuelve:
        {bedtime: "HH:MM", wake_assumed: "HH:MM", extra_min: int,
         need_min: int, drivers: [claves i18n]}
    o None si no hay suficiente historial de wake time (<_MIN_WAKE_SAMPLES en
    los últimos _WAKE_WINDOW_DAYS días) — nunca lanza."""
    try:
        days = days or []
        summary = summary or {}
        profile = profile or {}

        if not days:
            return None

        wake_min = _median_wake_min(days)
        if wake_min is None:
            return None

        try:
            sleep_target_min = int(profile.get("sleep_target_min") or summary.get("sleep_target_min") or 480)
        except Exception:
            sleep_target_min = 480

        debt_min = _sleep_debt_min(days, sleep_target_min)
        debt_adjust = min(debt_min * _DEBT_WEIGHT, _DEBT_CAP_MIN)

        today = days[-1] if isinstance(days[-1], dict) else {}
        strain_today = today.get("strain")
        recovery_today = today.get("recovery")

        drivers: list[str] = []
        extra_min = 0.0

        if debt_adjust > 0:
            extra_min += debt_adjust
            drivers.append("sleep_coach_driver_debt")

        if strain_today is not None:
            try:
                if float(strain_today) > _STRAIN_HIGH_THRESHOLD:
                    extra_min += _STRAIN_ADJUST_MIN
                    drivers.append("sleep_coach_driver_strain")
            except (TypeError, ValueError):
                pass

        if recovery_today is not None:
            try:
                if float(recovery_today) < _RECOVERY_LOW_THRESHOLD:
                    extra_min += _RECOVERY_ADJUST_MIN
                    drivers.append("sleep_coach_driver_recovery")
            except (TypeError, ValueError):
                pass

        need_min = sleep_target_min + extra_min
        bedtime_min = wake_min - need_min - _LATENCY_MIN

        if not drivers:
            drivers.append("sleep_coach_driver_baseline")

        return {
            "bedtime": _min_to_hhmm(bedtime_min),
            "wake_assumed": _min_to_hhmm(wake_min),
            "extra_min": int(round(extra_min)),
            "need_min": int(round(need_min)),
            "drivers": drivers,
        }
    except Exception as exc:
        logger.warning("recommend_bedtime falló (degradando a None): %s", exc)
        return None
