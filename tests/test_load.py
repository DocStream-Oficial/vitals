"""
test_load.py — Tests del helper compartido de detección de fuerza (Ronda 3).

Cubre:
- STRENGTH_RE matchea las variantes esperadas (weight/strength/fuerza/pesas/gym/
  resistance/musculac) case-insensitive, sobre type y sobre name.
- strength_minutes(): suma dur_min de ejercicios que matchean, ignora los que no.
- strength_minutes() con filtro de `dates`.
- None-safety: exercises=None/[], dur_min None, campos faltantes.
"""
from __future__ import annotations

from app.load import STRENGTH_RE, strength_minutes


# ── STRENGTH_RE ────────────────────────────────────────────────────────────────

def test_strength_re_matches_expected_variants():
    for word in ("weight", "strength", "fuerza", "pesas", "gym", "resistance", "musculac"):
        assert STRENGTH_RE.search(word), f"no matcheó: {word}"


def test_strength_re_case_insensitive():
    assert STRENGTH_RE.search("STRENGTH")
    assert STRENGTH_RE.search("Fuerza")
    assert STRENGTH_RE.search("GYM")


def test_strength_re_no_match_cardio():
    assert not STRENGTH_RE.search("running")
    assert not STRENGTH_RE.search("Tennis")
    assert not STRENGTH_RE.search("swim")


# ── strength_minutes ───────────────────────────────────────────────────────────

def test_strength_minutes_sums_matching_by_type():
    exercises = [
        {"date": "2026-06-28", "type": "strength_training", "name": "Workout", "dur_min": 45},
        {"date": "2026-06-28", "type": "running", "name": "Run", "dur_min": 30},
    ]
    assert strength_minutes(exercises) == 45


def test_strength_minutes_sums_matching_by_name():
    """Matchea sobre name aunque type no lo indique (p.ej. HealthKit 'Musculación')."""
    exercises = [
        {"date": "2026-06-28", "type": "traditional_strength_training", "name": "Musculación", "dur_min": 50},
        {"date": "2026-06-28", "type": "other", "name": "Pesas libres", "dur_min": 20},
    ]
    assert strength_minutes(exercises) == 70


def test_strength_minutes_sums_multiple_sessions():
    exercises = [
        {"date": "2026-06-27", "type": "gym", "name": "", "dur_min": 40},
        {"date": "2026-06-28", "type": "gym", "name": "", "dur_min": 35},
    ]
    assert strength_minutes(exercises) == 75


def test_strength_minutes_zero_when_no_strength_sessions():
    exercises = [
        {"date": "2026-06-28", "type": "running", "name": "Run", "dur_min": 30},
        {"date": "2026-06-28", "type": "swimming", "name": "Swim", "dur_min": 40},
    ]
    assert strength_minutes(exercises) == 0


def test_strength_minutes_empty_list():
    assert strength_minutes([]) == 0


def test_strength_minutes_none_exercises():
    assert strength_minutes(None) == 0


def test_strength_minutes_none_dur_min_treated_as_zero():
    exercises = [{"date": "2026-06-28", "type": "gym", "name": "", "dur_min": None}]
    assert strength_minutes(exercises) == 0


def test_strength_minutes_missing_fields_no_crash():
    exercises = [{"date": "2026-06-28"}]  # sin type/name/dur_min
    assert strength_minutes(exercises) == 0


def test_strength_minutes_filters_by_dates():
    exercises = [
        {"date": "2026-06-20", "type": "gym", "name": "", "dur_min": 40},  # fuera de ventana
        {"date": "2026-06-28", "type": "gym", "name": "", "dur_min": 30},  # dentro
    ]
    result = strength_minutes(exercises, dates={"2026-06-28"})
    assert result == 30


def test_strength_minutes_dates_none_means_no_filter():
    exercises = [{"date": "2026-06-20", "type": "gym", "name": "", "dur_min": 40}]
    assert strength_minutes(exercises, dates=None) == 40


def test_strength_minutes_dates_empty_set_excludes_all():
    exercises = [{"date": "2026-06-28", "type": "gym", "name": "", "dur_min": 40}]
    assert strength_minutes(exercises, dates=set()) == 0
