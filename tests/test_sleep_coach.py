"""
test_sleep_coach.py — Tests de app/sleep_coach.py (Fase 8C, paso C4).

Motor PURO — sin persistencia que testear, solo el cómputo. Cubre los casos
del roadmap: sin deuda, deuda grande (cap 60), strain alto, recovery bajo,
sin datos suficientes -> None.
"""
from __future__ import annotations

import datetime

import pytest

from app import sleep_coach


def _wake_days(n, wake="07:00", start="2026-06-01"):
    d0 = datetime.date.fromisoformat(start)
    return [
        {"date": (d0 + datetime.timedelta(days=i)).isoformat(), "waketime": wake, "asleep": 480}
        for i in range(n)
    ]


def _hhmm_to_min(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


# ── sin datos suficientes -> None ───────────────────────────────────────────

def test_no_days_returns_none():
    assert sleep_coach.recommend_bedtime([], {}, {}) is None


def test_too_few_wake_samples_returns_none():
    days = _wake_days(2)  # < _MIN_WAKE_SAMPLES (3)
    assert sleep_coach.recommend_bedtime(days, {}, {}) is None


def test_days_without_waketime_returns_none():
    days = [{"date": "2026-06-01", "asleep": 400}, {"date": "2026-06-02", "asleep": 420}]
    assert sleep_coach.recommend_bedtime(days, {}, {}) is None


# ── caso base: sin deuda, sin strain alto, sin recovery bajo ────────────────

def test_baseline_no_debt_no_adjustments():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec is not None
    assert rec["wake_assumed"] == "07:00"
    assert rec["extra_min"] == 0
    assert rec["need_min"] == 480
    assert rec["drivers"] == ["sleep_coach_driver_baseline"]
    # bedtime = wake(420min) - need(480) - latencia(15) = 420-480-15 = -75 -> 22:45
    expected_bedtime_min = (7 * 60 - 480 - 15) % 1440
    assert _hhmm_to_min(rec["bedtime"]) == expected_bedtime_min


# ── deuda de sueño: acumulada y capada ───────────────────────────────────────

def test_sleep_debt_pushes_bedtime_earlier():
    days = _wake_days(14, wake="07:00")
    # Últimos 7 días con déficit de 60 min c/u -> deuda = 420 -> cap a 60*0.3=... espera:
    # debt_adjust = min(debt*0.3, 60). Con 7 días de déficit 60min: debt=420, *0.3=126 -> cap 60.
    for d in days[-7:]:
        d["asleep"] = 420  # target 480 -> déficit 60/día
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec is not None
    assert rec["extra_min"] == 60  # cap alcanzado
    assert "sleep_coach_driver_debt" in rec["drivers"]


def test_sleep_debt_below_cap_uses_actual_value():
    days = _wake_days(14, wake="07:00")
    # 7 días con déficit de 20 min c/u -> deuda total 140 -> *0.3 = 42 (bajo el cap de 60)
    for d in days[-7:]:
        d["asleep"] = 460
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["extra_min"] == 42
    assert "sleep_coach_driver_debt" in rec["drivers"]


def test_surplus_sleep_does_not_reduce_debt_below_zero():
    """Días con SUPERÁVIT de sueño no generan deuda negativa (no restan)."""
    days = _wake_days(14, wake="07:00")
    for d in days[-7:]:
        d["asleep"] = 600  # surplus grande, no debe "regalar" minutos
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["extra_min"] == 0
    assert rec["drivers"] == ["sleep_coach_driver_baseline"]


# ── strain alto de hoy ───────────────────────────────────────────────────────

def test_high_strain_today_adds_20_min():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 16.0  # > 14
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["extra_min"] == 20
    assert "sleep_coach_driver_strain" in rec["drivers"]


def test_strain_at_threshold_does_not_trigger():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 14.0  # umbral exacto, no > 14
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert "sleep_coach_driver_strain" not in rec["drivers"]


# ── recovery bajo de hoy ─────────────────────────────────────────────────────

def test_low_recovery_today_adds_20_min():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 25  # < 34
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["extra_min"] == 20
    assert "sleep_coach_driver_recovery" in rec["drivers"]


def test_recovery_at_threshold_does_not_trigger():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 34  # umbral exacto, no < 34
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert "sleep_coach_driver_recovery" not in rec["drivers"]


# ── combinación de factores ──────────────────────────────────────────────────

def test_debt_strain_and_recovery_combine():
    days = _wake_days(14, wake="07:00")
    for d in days[-7:]:
        d["asleep"] = 420  # déficit 60/día -> debt_adjust cap 60
    days[-1]["strain"] = 18.0
    days[-1]["recovery"] = 20
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["extra_min"] == 60 + 20 + 20  # 100
    assert set(rec["drivers"]) == {
        "sleep_coach_driver_debt", "sleep_coach_driver_strain", "sleep_coach_driver_recovery",
    }


# ── mediana de wake time (robustez a outliers) ──────────────────────────────

def test_median_wake_robust_to_one_outlier():
    days = _wake_days(14, wake="07:00")
    days[5]["waketime"] = "13:45"  # outlier aislado no debe mover la mediana
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec["wake_assumed"] == "07:00"


def test_malformed_waketime_entries_are_ignored():
    days = _wake_days(5, wake="07:00")
    days[0]["waketime"] = "not-a-time"
    days[1]["waketime"] = None
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    assert rec is not None
    assert rec["wake_assumed"] == "07:00"


# ── sleep_target_min: cascada profile -> summary -> default ────────────────

def test_uses_profile_sleep_target_min():
    days = _wake_days(14, wake="07:00")
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 420})
    assert rec["need_min"] == 420


def test_falls_back_to_summary_sleep_target_min_if_no_profile():
    days = _wake_days(14, wake="07:00")
    rec = sleep_coach.recommend_bedtime(days, {"sleep_target_min": 450}, {})
    assert rec["need_min"] == 450


def test_falls_back_to_default_480_if_nothing_set():
    days = _wake_days(14, wake="07:00")
    rec = sleep_coach.recommend_bedtime(days, {}, {})
    assert rec["need_min"] == 480


# ── nunca lanza ───────────────────────────────────────────────────────────────

def test_never_raises_on_garbage_input():
    assert sleep_coach.recommend_bedtime(None, None, None) is None
    assert sleep_coach.recommend_bedtime([{"date": None}], "garbage", 12345) is None
    assert sleep_coach.recommend_bedtime([{"waketime": 12345}] * 5, {}, {}) is None
