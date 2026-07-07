"""
sleep_scores.py — Scores de sueño visibles para el usuario (Roadmap P1, F5,
paso 1).

Motor PURO (sin I/O), mismo patrón que sleep_coach.py/cycle.py: nunca lanza,
degrada a None ante datos ralos/insuficientes.

Contiene:
  - `sleep_need_min()`: MISMA fórmula que vivía dentro de sleep_coach.py
    (extraída aquí para que F4/F6 la reusen sin duplicar código). sleep_coach.py
    la importa de vuelta (ver alias de re-export al final de ese módulo) para
    que su endpoint devuelva EXACTAMENTE lo mismo que antes — los tests
    existentes de sleep_coach NO cambian sus asserts.
  - `sleep_score()`: % de la necesidad de sueño cumplida anoche, capado a 100.
  - `consistency_score()`: 0-100 según qué tan consistente es la hora de
    dormir + de despertar en los últimos N días (desviación combinada).

Ninguna de estas funciones toca build_dataset ni el summary persistido — se
calculan on-read en el endpoint (baratas, <1ms), ver roadmap "Descartado:
persistir sleep_score por día".
"""
from __future__ import annotations

import statistics
from typing import Any, Optional

# ── Constantes de la fórmula del need (idénticas a las que vivían en
# sleep_coach.py antes de este refactor — ver docstring de recommend_bedtime) ──
_DEBT_WEIGHT = 0.3
_DEBT_CAP_MIN = 60
_STRAIN_HIGH_THRESHOLD = 14
_STRAIN_ADJUST_MIN = 20
_RECOVERY_LOW_THRESHOLD = 34
_RECOVERY_ADJUST_MIN = 20
_DEBT_WINDOW_DAYS = 7

# ── Constantes de consistency_score ──
_CONSISTENCY_WINDOW_DAYS = 14
_CONSISTENCY_MIN_NIGHTS = 5
_CONSISTENCY_SIGMA_PERFECT = 20   # σ <= 20min -> 100
_CONSISTENCY_SIGMA_ZERO = 120     # σ >= 120min -> 0


def _sleep_debt_min(days: list, sleep_target_min: int) -> float:
    """Deuda de sueño acumulada (min) de los últimos _DEBT_WINDOW_DAYS días con
    `asleep` — solo suma déficits (asleep < target), nunca resta superávit.
    (Copia exacta de la lógica que vivía en sleep_coach._sleep_debt_min.)"""
    recent = days[-_DEBT_WINDOW_DAYS:] if days else []
    debt = 0.0
    for d in recent:
        if not isinstance(d, dict):
            continue
        asleep = d.get("asleep")
        if asleep is None:
            continue
        try:
            asleep = float(asleep)
        except (TypeError, ValueError):
            continue
        deficit = sleep_target_min - asleep
        if deficit > 0:
            debt += deficit
    return debt


def sleep_need_min(days: list, summary: Optional[dict] = None,
                    target_min: Optional[int] = None) -> Optional[int]:
    """Necesidad de sueño de HOY en minutos (mismo cálculo que usaba
    sleep_coach.recommend_bedtime internamente antes de este refactor):

        need = target_min
               + min(deuda_7d * 0.3, 60)        # deuda acumulada, cap 60 min
               + (20 si strain de HOY > 14 else 0)
               + (20 si recovery de HOY < 34 else 0)

    `target_min` es el sleep_target_min efectivo (cascada profile -> summary
    -> 480 default) — el caller decide la cascada (mismo patrón que
    recommend_bedtime). Devuelve int redondeado, o None si `days` está vacío
    (sin días no hay "hoy" del que leer strain/recovery). Nunca lanza."""
    try:
        days = days or []
        summary = summary or {}
        if not days:
            return None

        try:
            sleep_target_min = int(target_min) if target_min is not None else int(
                summary.get("sleep_target_min") or 480
            )
        except Exception:
            sleep_target_min = 480

        debt_min = _sleep_debt_min(days, sleep_target_min)
        debt_adjust = min(debt_min * _DEBT_WEIGHT, _DEBT_CAP_MIN)

        today = days[-1] if isinstance(days[-1], dict) else {}
        strain_today = today.get("strain")
        recovery_today = today.get("recovery")

        extra_min = 0.0
        if debt_adjust > 0:
            extra_min += debt_adjust

        if strain_today is not None:
            try:
                if float(strain_today) > _STRAIN_HIGH_THRESHOLD:
                    extra_min += _STRAIN_ADJUST_MIN
            except (TypeError, ValueError):
                pass

        if recovery_today is not None:
            try:
                if float(recovery_today) < _RECOVERY_LOW_THRESHOLD:
                    extra_min += _RECOVERY_ADJUST_MIN
            except (TypeError, ValueError):
                pass

        return int(round(sleep_target_min + extra_min))
    except Exception:
        return None


def sleep_score(asleep: Any, need: Any) -> Optional[int]:
    """% de la necesidad de sueño cumplida anoche, capado a 100.

        score = round(min(100, asleep / need * 100))

    None si `asleep` o `need` faltan, no son numéricos, o `need` <= 0 (evita
    división por cero) — "sin dato" nunca crashea ni se disfraza de 0%."""
    if asleep is None or need is None:
        return None
    try:
        asleep = float(asleep)
        need = float(need)
    except (TypeError, ValueError):
        return None
    if need <= 0:
        return None
    return int(round(min(100.0, asleep / need * 100.0)))


def _combined_stdev(days: list, n: int) -> Optional[float]:
    """Desviación estándar combinada de bed_min y waketime (en minutos) sobre
    las últimas `n` noches con AMBOS datos presentes. Se combina promediando
    las dos desviaciones (bed_min, waketime) — ambas miden lo mismo
    conceptualmente ("consistencia de horario"), y promediarlas evita que una
    sola métrica ruidosa domine el score. None si hay <_CONSISTENCY_MIN_NIGHTS
    noches con dato completo."""
    recent = days[-n:] if days else []
    bed_vals, wake_vals = [], []
    for d in recent:
        if not isinstance(d, dict):
            continue
        bed_min = d.get("bed_min")
        wake_min = d.get("waketime")
        # waketime viene como "HH:MM" en el dataset (ver sleep_coach._parse_hhmm);
        # bed_min ya es numérico (offset en minutos vs medianoche).
        wake_parsed = None
        if isinstance(wake_min, str) and ":" in wake_min:
            try:
                h, m = wake_min.split(":", 1)
                h, m = int(h), int(m)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    wake_parsed = h * 60 + m
            except Exception:
                wake_parsed = None
        if bed_min is None or wake_parsed is None:
            continue
        try:
            bed_vals.append(float(bed_min))
        except (TypeError, ValueError):
            continue
        wake_vals.append(float(wake_parsed))

    if len(bed_vals) < _CONSISTENCY_MIN_NIGHTS:
        return None

    try:
        bed_sd = statistics.pstdev(bed_vals)
        wake_sd = statistics.pstdev(wake_vals)
        return (bed_sd + wake_sd) / 2.0
    except Exception:
        return None


def consistency_score(days: list, n: int = _CONSISTENCY_WINDOW_DAYS) -> Optional[int]:
    """Score de consistencia de sueño 0-100 desde la desviación combinada de
    hora de acostarse (bed_min) y hora de despertar (waketime) en los últimos
    `n` días.

    Fórmula (lineal, documentada en el roadmap P1 F5):
        σ <= 20 min  -> 100
        σ >= 120 min -> 0
        entre medio  -> interpolación lineal

    Requiere >=5 noches con AMBOS datos (bed_min y waketime) presentes; si no
    -> None ("sin dato suficiente" ≠ "0", nunca se inventa un score malo por
    falta de datos). Nunca lanza."""
    try:
        sigma = _combined_stdev(days, n)
        if sigma is None:
            return None
        if sigma <= _CONSISTENCY_SIGMA_PERFECT:
            return 100
        if sigma >= _CONSISTENCY_SIGMA_ZERO:
            return 0
        span = _CONSISTENCY_SIGMA_ZERO - _CONSISTENCY_SIGMA_PERFECT
        frac = (sigma - _CONSISTENCY_SIGMA_PERFECT) / span
        return int(round(100 * (1 - frac)))
    except Exception:
        return None
