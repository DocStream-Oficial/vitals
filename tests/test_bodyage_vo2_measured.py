"""
test_bodyage_vo2_measured.py — Roadmap edad-corporal-credibilidad, Paso 1:
VO2 MEDIDO del reloj manda sobre la regresión NTNU cuando hay señal suficiente.

Cubre el criterio de aceptación 1 del roadmap:
- >=3 lecturas "vo2" en las últimas 60 entradas de `days` -> vo2max = media
  exacta (round 1), vo2max_source == "measured", fitness_age derivada de ESE
  valor.
- <3 lecturas -> comportamiento IDÉNTICO a un run sin vo2 en absoluto,
  vo2max_source == "estimated".
- lecturas fuera de las últimas 60 entradas no cuentan (recent() opera sobre
  ENTRADAS, no fechas calendario).
- confidence.vo2_readings presente siempre (incluso en 0).
"""
from __future__ import annotations

import datetime as _dt
import statistics

from app.bodyage import compute_body_age

BIRTHDATE_AGE = 40
WAIST = 82.0
SEX = "M"


def _date_seq(start, n):
    d0 = _dt.date.fromisoformat(start)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n)]


def _make_days(n, vo2_by_index=None, start="2025-01-01"):
    """n días con rhr/hrv/asleep constantes (sin ruido) para aislar el efecto
    de vo2. vo2_by_index: {idx: valor} inyecta la clave "vo2" solo en esos
    índices (0-based, orden de aparición en `days`)."""
    vo2_by_index = vo2_by_index or {}
    dates = _date_seq(start, n)
    days = []
    for i, d in enumerate(dates):
        day = {"date": d, "rhr": 55.0, "hrv": 45.0, "asleep": 420}
        if i in vo2_by_index:
            day["vo2"] = vo2_by_index[i]
        days.append(day)
    return days


def test_three_or_more_readings_yields_measured_mean():
    """>=3 lecturas de vo2 en las últimas 60 entradas -> vo2max = media exacta
    (round 1) de esas lecturas, vo2max_source == 'measured'."""
    n = 20
    readings = {17: 50.0, 18: 52.0, 19: 48.0}
    days = _make_days(n, vo2_by_index=readings)
    result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

    expected_mean = round(statistics.mean(readings.values()), 1)
    assert result["vo2max"] == expected_mean
    assert result["vo2max_source"] == "measured"
    assert result["confidence"]["vo2_readings"] == 3


def test_measured_vo2_drives_fitness_age():
    """fitness_age debe derivarse del vo2max MEDIDO, no del estimado por
    regresión — comprobado forzando un vo2 medido MUY distinto al estimado."""
    n = 10
    # Con age=40/waist=82/rhr=55 sin ejercicio, la regresión estima vo2~49.6
    # (fitness_age ya clampeada al piso 20) — el medido se elige DEBAJO de esa
    # zona de clamp para que el cambio sea observable en fitness_age.
    readings = {7: 39.0, 8: 40.0, 9: 41.0}
    days = _make_days(n, vo2_by_index=readings)
    result_measured = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)
    result_no_vo2 = compute_body_age(_make_days(n), [], BIRTHDATE_AGE, WAIST, SEX)

    assert result_measured["vo2max_source"] == "measured"
    assert result_measured["vo2max"] == 40.0
    assert result_measured["vo2max"] != result_no_vo2["vo2max"]
    assert result_measured["fitness_age"] != result_no_vo2["fitness_age"]
    # La estimación de la regresión sigue disponible (no se pierde), solo deja
    # de ser la que manda:
    assert result_measured["vo2max_estimated"] == result_no_vo2["vo2max"]


def test_two_readings_falls_back_to_estimated_identical():
    """<3 lecturas -> comportamiento IDÉNTICO a un run sin vo2 en absoluto."""
    n = 20
    readings = {17: 50.0, 18: 52.0}  # solo 2
    days_with_vo2 = _make_days(n, vo2_by_index=readings)
    days_without_vo2 = _make_days(n)

    result_with = compute_body_age(days_with_vo2, [], BIRTHDATE_AGE, WAIST, SEX)
    result_without = compute_body_age(days_without_vo2, [], BIRTHDATE_AGE, WAIST, SEX)

    assert result_with["vo2max_source"] == "estimated"
    assert result_with["vo2max"] == result_without["vo2max"]
    assert result_with["fitness_age"] == result_without["fitness_age"]
    assert result_with["confidence"]["vo2_readings"] == 2


def test_readings_outside_last_60_entries_dont_count():
    """recent() opera sobre las últimas 60 ENTRADAS de `days`, no fechas
    calendario — lecturas viejas fuera de esa ventana no cuentan aunque el
    dataset tenga >>60 días en total."""
    n = 100
    # 3 lecturas viejas, muy al principio (índices 0,1,2) -> fuera de los
    # últimos 60 (índices 40..99).
    readings = {0: 50.0, 1: 52.0, 2: 48.0}
    days = _make_days(n, vo2_by_index=readings)
    result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

    assert result["vo2max_source"] == "estimated"
    assert result["confidence"]["vo2_readings"] == 0


def test_confidence_vo2_readings_present_even_with_zero():
    """confidence.vo2_readings está presente siempre, incluso en 0."""
    days = _make_days(10)
    result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)
    assert result["confidence"]["vo2_readings"] == 0


def test_vo2max_estimated_key_present_always():
    """vo2max_estimated (el valor crudo de la regresión) está presente tanto
    en el camino 'estimated' como en el 'measured'."""
    days_no_vo2 = _make_days(10)
    result = compute_body_age(days_no_vo2, [], BIRTHDATE_AGE, WAIST, SEX)
    assert result["vo2max_estimated"] == result["vo2max"]  # estimated: son el mismo valor
