"""
test_cycle.py — Tests de app/cycle.py (Fase 7: motor de ciclo + persistencia).

Cubre:
- load/save round-trip + atomicidad (patrón profile.py)
- _cycle_lengths / _median_cycle_length
- _infer_ovulation_from_temp con fixture sintético de temperatura
- compute_cycle_state: opt-in estricto, predicción, retraso, peri/menopausia,
  disclaimer, robustez ante datos ralos/desordenados.

Marcado -k engine: tests del motor puro (sin filesystem), corren con
`pytest tests/test_cycle.py -k engine`.
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import cycle


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_cycle_log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(cycle, "_CYCLE_LOG_FILE", tmp_path / "cycle_log.json")


def make_day(date, **kwargs):
    return {"date": date, **kwargs}


def date_seq(start: str, n: int) -> list[str]:
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


# ── persistencia: load/save round-trip ───────────────────────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_cycle_log_path(monkeypatch, tmp_path)
    log = cycle.load_cycle_log()
    assert log["periods"] == []
    assert log["symptoms"] == []
    assert log["ovulation_tests"] == []


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    _patch_cycle_log_path(monkeypatch, tmp_path)
    data = {
        "periods": [{"start": "2026-06-03", "end": "2026-06-07", "flow": "medium", "source": "manual"}],
        "symptoms": [{"date": "2026-06-15", "tags": ["cramps"], "note": "", "source": "manual"}],
        "ovulation_tests": [],
    }
    cycle.save_cycle_log(data)
    loaded = cycle.load_cycle_log()
    assert loaded["periods"] == data["periods"]
    assert loaded["symptoms"] == data["symptoms"]
    assert "updated" in loaded


def test_save_is_atomic_no_tmp_leftover(tmp_path, monkeypatch):
    _patch_cycle_log_path(monkeypatch, tmp_path)
    cycle.save_cycle_log({"periods": []})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_load_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    _patch_cycle_log_path(monkeypatch, tmp_path)
    (tmp_path / "cycle_log.json").write_text("NOT JSON{{{", encoding="utf-8")
    log = cycle.load_cycle_log()
    assert log["periods"] == []


def test_load_tolerant_to_partial_old_log(tmp_path, monkeypatch):
    """Log viejo con solo 'periods' (sin symptoms/ovulation_tests) -> cero migración."""
    _patch_cycle_log_path(monkeypatch, tmp_path)
    (tmp_path / "cycle_log.json").write_text(
        json.dumps({"periods": [{"start": "2026-01-01"}]}), encoding="utf-8"
    )
    log = cycle.load_cycle_log()
    assert log["periods"] == [{"start": "2026-01-01"}]
    assert log["symptoms"] == []
    assert log["ovulation_tests"] == []


# ── _cycle_lengths / _median_cycle_length (engine) ───────────────────────────

def test_cycle_lengths_engine_basic():
    periods = [
        {"start": "2026-01-01"},
        {"start": "2026-01-29"},  # 28 días
        {"start": "2026-02-26"},  # 28 días
    ]
    assert cycle._cycle_lengths(periods) == [28, 28]


def test_cycle_lengths_engine_handles_unsorted_input():
    """Robustez: periodos fuera de orden cronológico no rompen el cálculo."""
    periods = [
        {"start": "2026-02-26"},
        {"start": "2026-01-01"},
        {"start": "2026-01-29"},
    ]
    assert cycle._cycle_lengths(periods) == [28, 28]


def test_cycle_lengths_engine_ignores_malformed_entries():
    periods = [
        {"start": "2026-01-01"},
        {"start": "not-a-date"},
        {"end": "2026-01-10"},  # sin start
        "garbage",
        {"start": "2026-01-29"},
    ]
    assert cycle._cycle_lengths(periods) == [28]


def test_median_cycle_length_default_with_no_lengths():
    assert cycle._median_cycle_length([]) == cycle.DEFAULT_CYCLE_LEN


def test_median_cycle_length_engine_odd():
    assert cycle._median_cycle_length([26, 28, 30]) == 28


def test_median_cycle_length_engine_even():
    assert cycle._median_cycle_length([26, 28, 30, 32]) == 29


# ── _infer_ovulation_from_temp (engine) ───────────────────────────────────────

def test_infer_ovulation_engine_detects_sustained_shift():
    """Nadir folicular (~36.2) -> shift lúteo sostenido (~36.5, +0.3 >= TEMP_SHIFT_C)
    por >=3 días consecutivos -> debe detectar la fase lútea y confirmar."""
    last_period_start = datetime.date(2026, 6, 1)
    dates = date_seq("2026-06-01", 20)
    days = []
    for i, d in enumerate(dates):
        # Días 0-9: folicular baja (~36.2); día 10 en adelante: shift lúteo sostenido
        temp = 36.2 if i < 10 else 36.5
        days.append(make_day(d, skin_temp=temp))

    result = cycle._infer_ovulation_from_temp(days, last_period_start)
    assert result is not None
    assert result["confirmed"] is True
    assert "date" in result
    assert result["day_of_cycle"] >= 1


def test_infer_ovulation_engine_no_shift_returns_none():
    """Temperatura plana (sin shift) -> None, no falso positivo."""
    last_period_start = datetime.date(2026, 6, 1)
    dates = date_seq("2026-06-01", 15)
    days = [make_day(d, skin_temp=36.3) for d in dates]
    result = cycle._infer_ovulation_from_temp(days, last_period_start)
    assert result is None


def test_infer_ovulation_engine_insufficient_data_returns_none():
    last_period_start = datetime.date(2026, 6, 1)
    days = [make_day("2026-06-01", skin_temp=36.2)]
    result = cycle._infer_ovulation_from_temp(days, last_period_start)
    assert result is None


def test_infer_ovulation_engine_none_safe_missing_temp():
    """Días sin skin_temp en absoluto -> None, no crashea."""
    last_period_start = datetime.date(2026, 6, 1)
    dates = date_seq("2026-06-01", 15)
    days = [make_day(d) for d in dates]
    result = cycle._infer_ovulation_from_temp(days, last_period_start)
    assert result is None


def test_infer_ovulation_engine_no_period_start_returns_none():
    days = [make_day("2026-06-01", skin_temp=36.2)]
    assert cycle._infer_ovulation_from_temp(days, None) is None
    assert cycle._infer_ovulation_from_temp([], datetime.date(2026, 6, 1)) is None


# ── compute_cycle_state: opt-in estricto (criterio #1) ───────────────────────

def test_compute_cycle_state_none_when_toggle_off():
    profile = {"cycle_tracking": False}
    result = cycle.compute_cycle_state([], {"periods": []}, profile)
    assert result is None


def test_compute_cycle_state_none_when_profile_missing():
    result = cycle.compute_cycle_state([], {"periods": []}, None)
    assert result is None


def test_compute_cycle_state_none_when_key_absent():
    """Perfil sin el campo cycle_tracking en absoluto (perfil viejo) -> None (default off)."""
    result = cycle.compute_cycle_state([], {"periods": []}, {"name": "X"})
    assert result is None


def test_compute_cycle_state_enabled_true_when_toggle_on():
    profile = {"cycle_tracking": True}
    result = cycle.compute_cycle_state([], {"periods": []}, profile)
    assert result is not None
    assert result["enabled"] is True
    assert result["disclaimer"] == "cycle_disclaimer"


# ── compute_cycle_state: predicción (criterio #4) ────────────────────────────

def test_compute_cycle_state_predicts_next_period():
    """Con >=2 inicios registrados, predice próximo = último_inicio + mediana(longitudes)."""
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2026-05-01", "source": "manual"},
            {"start": "2026-05-29", "source": "manual"},  # 28 días
        ]
    }
    days = [make_day("2026-06-01")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["period"]["last_start"] == "2026-05-29"
    assert result["period"]["predicted_next"] == "2026-06-26"  # +28 días


def test_compute_cycle_state_detects_luteal_phase_with_temp_pattern():
    """Criterio #3: dataset con nadir folicular -> shift lúteo sostenido detecta
    fase lútea y estima ovulación retrospectiva."""
    profile = {"cycle_tracking": True}
    cycle_log = {"periods": [{"start": "2026-06-01", "source": "manual"}]}
    dates = date_seq("2026-06-01", 20)
    days = []
    for i, d in enumerate(dates):
        temp = 36.2 if i < 10 else 36.6
        days.append(make_day(d, skin_temp=temp))

    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["phase"] == "luteal"
    assert result["fertile_window"]["source"] == "temp+calendar"
    assert "inference" in result["sources_used"]


# ── compute_cycle_state: retraso (criterio #5) ───────────────────────────────

def test_compute_cycle_state_delayed_beyond_threshold():
    """hoy > predicho + DELAY_THRESHOLD_DAYS sin nuevo periodo -> delayed con n días."""
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2026-04-01", "source": "manual"},
            {"start": "2026-04-29", "source": "manual"},  # 28 días de mediana
        ]
    }
    # "Hoy" (último día del dataset) muy posterior al predicho (04-29 + 28 = 05-27)
    days = [make_day("2026-06-10")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["delay"]["is_delayed"] is True
    assert result["delay"]["days"] > cycle.DELAY_THRESHOLD_DAYS


def test_compute_cycle_state_not_delayed_within_threshold():
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2026-04-01", "source": "manual"},
            {"start": "2026-04-29", "source": "manual"},
        ]
    }
    # Predicho: 05-27; hoy 05-28 (+1 día, dentro del umbral de 2)
    days = [make_day("2026-05-28")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["delay"]["is_delayed"] is False
    assert result["delay"]["days"] == 0


# ── compute_cycle_state: peri/menopausia (criterio #6) ───────────────────────

def test_compute_cycle_state_insufficient_history_no_false_positive():
    """Usuaria joven regular con historial corto -> premenopausal/insufficient_history,
    SIN señales alarmistas (criterio: cero falsos positivos)."""
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2026-05-01", "source": "manual"},
            {"start": "2026-05-29", "source": "manual"},
        ]
    }
    days = [make_day("2026-06-01")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["menopause"]["stage"] == "insufficient_history"
    assert result["menopause"]["signals"] == []


def test_compute_cycle_state_menopause_amenorrhea_with_long_history():
    """Historial largo (>=180d entre 1er y último periodo) + amenorrea >=12 meses
    desde el último periodo -> menopause_possible."""
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2024-01-01", "source": "manual"},
            {"start": "2024-01-29", "source": "manual"},
            {"start": "2024-06-26", "source": "manual"},  # amplía el span >=180d
            {"start": "2024-08-01", "source": "manual"},
        ]
    }
    # Hoy: más de 12 meses después del último periodo registrado
    days = [make_day("2026-06-01")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["menopause"]["stage"] == "menopause_possible"
    assert "amenorrhea_12mo" in result["menopause"]["signals"]


def test_compute_cycle_state_perimenopause_length_variability():
    """Historial largo (>=180d) con variabilidad de longitud creciente ->
    perimenopause_possible."""
    profile = {"cycle_tracking": True}
    cycle_log = {
        "periods": [
            {"start": "2024-01-01", "source": "manual"},
            {"start": "2024-01-29", "source": "manual"},  # 28
            {"start": "2024-03-15", "source": "manual"},  # 46 (rango > 9)
            {"start": "2024-08-10", "source": "manual"},  # amplía span >=180d
        ]
    }
    days = [make_day("2024-09-01")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["menopause"]["stage"] == "perimenopause_possible"
    assert "length_variability" in result["menopause"]["signals"]


# ── disclaimer siempre presente (criterio #7) ────────────────────────────────

def test_compute_cycle_state_always_has_disclaimer_key():
    profile = {"cycle_tracking": True}
    result = cycle.compute_cycle_state([], {"periods": []}, profile)
    assert result["disclaimer"] == "cycle_disclaimer"

    cycle_log = {"periods": [{"start": "2026-05-01"}, {"start": "2026-05-29"}]}
    result2 = cycle.compute_cycle_state([make_day("2026-06-01")], cycle_log, profile)
    assert result2["disclaimer"] == "cycle_disclaimer"
    assert result2["fertile_window"] is not None


# ── robustez: datos ralos/nulos/desordenados (nunca crashea) ─────────────────

def test_compute_cycle_state_never_crashes_with_garbage_periods():
    profile = {"cycle_tracking": True}
    cycle_log = {"periods": [{"start": "not-a-date"}, "garbage", {}, None]}
    result = cycle.compute_cycle_state([make_day("2026-06-01")], cycle_log, profile)
    assert result is not None
    assert result["enabled"] is True


def test_compute_cycle_state_never_crashes_empty_days():
    profile = {"cycle_tracking": True}
    cycle_log = {"periods": [{"start": "2026-05-01"}, {"start": "2026-05-29"}]}
    result = cycle.compute_cycle_state([], cycle_log, profile)
    assert result is not None


def test_compute_cycle_state_never_crashes_malformed_profile():
    """profile no es dict-like esperado -> no lanza (degradado a None u OK)."""
    result = cycle.compute_cycle_state([], {}, {"cycle_tracking": True})
    assert result is not None  # sin periods -> estado mínimo, enabled True


def test_compute_cycle_state_single_period_low_confidence():
    """Un solo periodo registrado -> sin longitud de ciclo calculable aún (se
    necesitan >=2 inicios para una longitud real), predicción cae a mediana
    default con confianza/suficiencia baja (honesto, sin falsa seguridad)."""
    profile = {"cycle_tracking": True}
    cycle_log = {"periods": [{"start": "2026-05-01", "source": "manual"}]}
    days = [make_day("2026-05-10")]
    result = cycle.compute_cycle_state(days, cycle_log, profile)
    assert result["data_sufficiency"]["level"] == "low"
    assert result["period"]["confidence"] == "low"


def test_compute_cycle_state_no_periods_returns_minimal_enabled_state():
    profile = {"cycle_tracking": True}
    result = cycle.compute_cycle_state([make_day("2026-06-01")], {"periods": []}, profile)
    assert result["enabled"] is True
    assert result["cycle_day"] is None
    assert result["data_sufficiency"]["level"] == "low"
