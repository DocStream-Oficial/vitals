"""
tests/test_vo2_gate.py — Roadmap vo2-sin-inventar, Paso 1: cobertura dedicada
de los criterios de aceptación 1, 2 y 3 (conteo por mediciones, ventana de
validez de 180 días, y la función `gate_unmeasured`).

No depende de datos reales de ningún usuario — todos los datasets son
sintéticos y deterministas (fechas fijas, sin random).
"""
from __future__ import annotations

import datetime as _dt

from app.bodyage import compute_body_age, gate_unmeasured, MEASUREMENT_VALIDITY_DAYS

BIRTHDATE_AGE = 40
WAIST = 82.0
SEX = "M"


def _date_seq(start, n):
    d0 = _dt.date.fromisoformat(start)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n)]


def _make_days(n, vo2_by_index=None, start="2025-01-01"):
    vo2_by_index = vo2_by_index or {}
    dates = _date_seq(start, n)
    days = []
    for i, d in enumerate(dates):
        day = {"date": d, "rhr": 55.0, "hrv": 45.0, "asleep": 420}
        if i in vo2_by_index:
            day["vo2"] = vo2_by_index[i]
        days.append(day)
    return days


# ── Criterio 1: conteo por MEDICIONES, no por entradas ──────────────────────

class TestCriterio1Dedup:
    def test_60_entries_same_value_counts_as_one_measurement(self):
        """Caso Fitbit re-emitido: 60 días con el MISMO valor vo2 -> 1 sola
        medición deduplicada, no 60."""
        n = 60
        readings = {i: 29.57 for i in range(n)}
        days = _make_days(n, vo2_by_index=readings)
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["confidence"]["vo2_measurements"] == 1
        assert result["confidence"]["vo2_readings"] == 60
        assert result["vo2max_source"] == "measured"
        assert result["vo2max"] == 29.6  # round(29.57, 1)

    def test_three_distinct_values_counts_as_three_measurements(self):
        n = 10
        readings = {5: 40.0, 6: 42.0, 7: 44.0}
        days = _make_days(n, vo2_by_index=readings)
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["confidence"]["vo2_measurements"] == 3
        assert result["confidence"]["vo2_readings"] == 3
        assert result["vo2max"] == 42.0  # media de los 3 valores únicos

    def test_dedup_keeps_most_recent_date_of_the_group(self):
        """Riesgo #5 del roadmap: el grupo deduplicado conserva la fecha MÁS
        RECIENTE como vo2_last_measured_date."""
        n = 10
        readings = {3: 37.15, 5: 37.15, 8: 37.15}  # mismo valor 3 veces
        days = _make_days(n, vo2_by_index=readings)
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["confidence"]["vo2_measurements"] == 1
        assert result["vo2_last_measured_date"] == days[8]["date"]


# ── Criterio 2: ventana de validez de 180 días (relativa al último day) ─────

class TestCriterio2ValidityWindow:
    def test_measurement_181_days_back_does_not_count(self):
        """>180 días de distancia respecto al último day -> NO cuenta."""
        n = 200
        last_index = n - 1
        reading_index = last_index - 181
        days = _make_days(n, vo2_by_index={reading_index: 45.0})
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["vo2max_source"] == "estimated"
        assert result["confidence"]["vo2_measurements"] == 0
        assert result["confidence"]["vo2_readings"] == 0

    def test_measurement_180_days_back_counts(self):
        """Exactamente en el borde (180 días) -> SÍ cuenta ('más de 180 días'
        implica que 180 exacto todavía es válido)."""
        n = 200
        last_index = n - 1
        reading_index = last_index - MEASUREMENT_VALIDITY_DAYS
        days = _make_days(n, vo2_by_index={reading_index: 45.0})
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["vo2max_source"] == "measured"
        assert result["confidence"]["vo2_measurements"] == 1
        assert result["vo2max"] == 45.0

    def test_measurement_179_days_back_counts(self):
        n = 200
        last_index = n - 1
        reading_index = last_index - 179
        days = _make_days(n, vo2_by_index={reading_index: 45.0})
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["vo2max_source"] == "measured"
        assert result["confidence"]["vo2_measurements"] == 1

    def test_window_is_relative_to_last_day_not_today(self):
        """El motor debe ser puro: la ventana se calcula contra la fecha del
        ÚLTIMO day del dataset, nunca date.today(). Fechas fijas en 2020 (muy
        lejos de "hoy" real) con la medición dentro de los últimos 180 días
        DEL DATASET deben seguir contando como measured."""
        n = 200
        days = _make_days(n, vo2_by_index={n - 1: 45.0}, start="2020-01-01")
        result = compute_body_age(days, [], BIRTHDATE_AGE, WAIST, SEX)

        assert result["vo2max_source"] == "measured"


# ── Criterio 3: gate_unmeasured() ───────────────────────────────────────────

_GATED_FIELDS = (
    "vo2max", "fitness_age", "body_age", "category", "vo2max_percentile",
    "vo2max_label", "fitness_age_display", "body_age_display", "age_floored",
    "penalty", "body_age_stable", "body_age_stable_display", "pace",
)

_CONSERVED_FIELDS = (
    "vo2max_estimated", "vo2_last_measured_date", "age", "rhr", "hrv",
    "sleep_h", "confidence",
)


def _full_ba_dict(vo2max_source: str) -> dict:
    """Dict con la FORMA completa de summary.bodyage tras sync.py (instantáneo
    + stable + pace), para probar el gate con todos los campos relevantes
    presentes."""
    return {
        "vo2max": 52.7, "fitness_age": 25, "body_age": 27, "category": "Excelente",
        "rhr": 55.0, "hrv": 45.0, "sleep_h": 7.2, "pa_index": 10,
        "waist": 82.0, "age": 49, "penalty": 1.0,
        "confidence": {"rhr_days": 14, "hrv_days": 14, "sleep_days": 14,
                       "exercise_sessions": 3, "vo2_readings": 0,
                       "vo2_measurements": 0, "level": "high"},
        "vo2max_percentile": 83, "vo2max_label": "Excelente",
        "body_age_raw": 27.4,
        "vo2max_source": vo2max_source,
        "vo2max_estimated": 52.7,
        "vo2_last_measured_date": "2025-08-01",
        "fitness_age_display": 34, "body_age_display": 34, "age_floored": True,
        "body_age_stable": 28, "n_days_stable": 30, "stable_confidence": "ok",
        "body_age_stable_display": 34,
        "pace": 1.1,
    }


class TestCriterio3GateUnmeasured:
    def test_unmeasured_nulls_all_derived_fields(self):
        ba = _full_ba_dict("estimated")
        gated = gate_unmeasured(ba)

        for k in _GATED_FIELDS:
            assert gated[k] is None, f"'{k}' debería ser None tras el gate"
        assert gated["unavailable_reason"] == "no_vo2_measurement"

    def test_unmeasured_conserves_diagnostic_fields(self):
        ba = _full_ba_dict("estimated")
        gated = gate_unmeasured(ba)

        for k in _CONSERVED_FIELDS:
            assert gated[k] == ba[k], f"'{k}' debería conservarse tal cual"

    def test_unmeasured_does_not_mutate_input(self):
        ba = _full_ba_dict("estimated")
        ba_copy_before = dict(ba)
        gate_unmeasured(ba)
        assert ba == ba_copy_before

    def test_measured_returns_dict_unchanged_identity(self):
        ba = _full_ba_dict("measured")
        gated = gate_unmeasured(ba)
        assert gated is ba  # identidad: ni copia ni mutación
        assert "unavailable_reason" not in gated

    def test_falsy_input_returned_as_is(self):
        assert gate_unmeasured(None) is None
        assert gate_unmeasured({}) == {}
