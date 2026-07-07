"""
load.py — Carga de entrenamiento fisiológica (solo stdlib).

Feature C del Roadmap Tier 2:

  hr_max(age)           → HRmax = 211 − 0.64·edad  (Nes 2013, mismo linaje NTNU que VO₂máx)
  trimp_session(...)    → TRIMP de Banister por sesión (M/F)
  acwr(daily_loads)     → ratio agudo:crónico 7d:28d sobre la serie diaria
  acwr_zone(ratio)      → "detraining"|"optimo"|"precaucion"|"alto"

Ronda 3 (motor honesto):
  STRENGTH_RE            → regex compartido de detección de fuerza (superset de los
                            3 regex antes duplicados en coach.py/coach_chat.py).
  strength_minutes(...)  → minutos de fuerza estructurada en una lista de ejercicios,
                            opcionalmente filtrados a un set de fechas.

Notas de diseño:
- TRIMP usa dur_min·HRr·factor, con HRr clampeado a [0,1].
- ACWR se computa sobre serie de strain (no TRIMP) porque strain es diario y denso;
  TRIMP es campo de calidad por sesión (solo ~18 días en el golden).
- acwr() devuelve None si chronic<=0 o <14 días con datos reales en los últimos 28.
"""
from __future__ import annotations

import math
import re
from typing import Optional


def hr_max(age: float) -> float:
    """HRmax = 211 − 0.64·edad (Nes 2013, NTNU)."""
    return 211.0 - 0.64 * age


def trimp_session(
    dur_min: Optional[float],
    avg_hr: Optional[float],
    hr_rest: Optional[float],
    age: Optional[float],
    sex: str = "M",
) -> Optional[float]:
    """
    TRIMP de Banister por sesión.

    HRr = clamp((avg_hr − hr_rest) / (hr_max − hr_rest), 0, 1)
    factor_M = 0.64 · e^(1.92·HRr)
    factor_F = 0.86 · e^(1.67·HRr)
    TRIMP = dur_min · HRr · factor

    Devuelve None si falta algún dato obligatorio.
    Devuelve 0.0 si avg_hr <= hr_rest (esfuerzo nulo, HRr=0).
    """
    if dur_min is None or avg_hr is None or hr_rest is None or age is None:
        return None

    hmax = hr_max(age)
    denom = hmax - hr_rest

    # Garantía: denom > 0 siempre que hr_rest < hr_max (fisiológicamente cierto)
    if denom <= 0:
        return None

    hrr = (avg_hr - hr_rest) / denom
    # Clamp [0, 1]
    hrr = max(0.0, min(1.0, hrr))

    sex_upper = (sex or "M").upper()
    if sex_upper == "F":
        factor = 0.86 * math.exp(1.67 * hrr)
    else:
        factor = 0.64 * math.exp(1.92 * hrr)

    return dur_min * hrr * factor


def acwr_zone(ratio: Optional[float]) -> Optional[str]:
    """
    Zona de riesgo según ACWR:
      < 0.8  → "detraining"
      0.8–1.3 → "optimo"
      1.3–1.5 → "precaucion"
      > 1.5  → "alto"
    Devuelve None si ratio es None.
    """
    if ratio is None:
        return None
    if ratio < 0.8:
        return "detraining"
    if ratio <= 1.3:
        return "optimo"
    if ratio <= 1.5:
        return "precaucion"
    return "alto"


def acwr(daily_loads_last28: list) -> Optional[float]:
    """
    Ratio agudo:crónico sobre los últimos 28 días de carga (strain).

    daily_loads_last28: lista de hasta 28 valores (float|None), ordenados cronológicamente,
                        del más antiguo al más reciente. Puede contener None.

    Acute  = Σ(últimos 7 valores no-None)
    Chronic = Σ(todos los hasta 28 valores no-None) / 4

    Devuelve None si:
      - chronic <= 0
      - hay <14 entradas con dato real (None cuenta como sin dato)
    """
    # Tomar hasta 28 elementos
    window = list(daily_loads_last28[-28:])

    # Contar días con dato real (no-None, >0 o ==0 aún cuenta como dato)
    # — None significa "sin dato ese día"; 0 es un dato válido (sin actividad)
    real_days = [v for v in window if v is not None]
    if len(real_days) < 14:
        return None

    # Acute: últimos 7 no-None
    acute_vals = [v for v in window[-7:] if v is not None]
    acute = sum(acute_vals)

    # Chronic: suma de todos los no-None en ventana / 4
    chronic = sum(real_days) / 4.0

    if chronic <= 0:
        return None

    return acute / chronic


# ── Detección de fuerza estructurada (Ronda 3) ─────────────────────────────────
# Superset de los 3 regex que antes vivían duplicados (coach.py:195 y
# coach_chat.py:174 usaban "weight|strength|fuerza|gym|resistance"; coach.py:84
# usaba solo "strength" in type). Se matchea sobre str(type) + " " + str(name)
# para capturar tanto el tipo normalizado del wearable como el nombre libre del
# workout (p.ej. "Musculación" en HealthKit).
STRENGTH_RE = re.compile(r"(weight|strength|fuerza|pesas|gym|resistance|musculac)", re.I)


def strength_minutes(exercises: list, dates: Optional[set] = None) -> int:
    """
    Suma los minutos (`dur_min`) de los ejercicios de `exercises` que matchean
    STRENGTH_RE en `str(type) + " " + str(name)`.

    Args:
        exercises: lista de dicts de workout (contrato de Source.fetch()/merge).
        dates: si se da, solo se cuentan ejercicios cuyo `date` esté en este set
               (filtro de ventana, p.ej. "últimos 7 días con dato").

    Devuelve 0 si no hay ejercicios de fuerza (nunca None: "0 minutos" es un
    resultado válido y distinto de "sin datos", que las reglas manejan aparte).
    """
    total = 0
    for e in exercises or []:
        if dates is not None and e.get("date") not in dates:
            continue
        haystack = f"{e.get('type', '')} {e.get('name', '')}"
        if STRENGTH_RE.search(haystack):
            total += e.get("dur_min", 0) or 0
    return total
