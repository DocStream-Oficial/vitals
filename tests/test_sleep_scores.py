"""
test_sleep_scores.py — Tests de app/sleep_scores.py (Roadmap P1, F5, paso 1).

Cubre:
- sleep_need_min(): mismo resultado que el need interno de sleep_coach con
  los mismos inputs (regresión del refactor).
- sleep_score(): 100 capado, None-safe.
- consistency_score(): 100 con σ baja, 0 con σ alta, None con <5 noches.
"""
from __future__ import annotations

import datetime

import pytest

from app import sleep_coach, sleep_scores


def _wake_days(n, wake="07:00", start="2026-06-01", bed_min=0):
    d0 = datetime.date.fromisoformat(start)
    return [
        {
            "date": (d0 + datetime.timedelta(days=i)).isoformat(),
            "waketime": wake, "asleep": 480, "bed_min": bed_min,
        }
        for i in range(n)
    ]


# ── sleep_need_min: paridad con sleep_coach.recommend_bedtime ──────────────

def test_need_matches_sleep_coach_baseline():
    days = _wake_days(14, wake="07:00")
    days[-1]["strain"] = 5.0
    days[-1]["recovery"] = 70
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    need = sleep_scores.sleep_need_min(days, {}, 480)
    assert need == rec["need_min"] == 480


def test_need_matches_sleep_coach_with_debt_strain_recovery():
    days = _wake_days(14, wake="07:00")
    for d in days[-7:]:
        d["asleep"] = 420  # déficit 60/día -> debt_adjust cap 60
    days[-1]["strain"] = 18.0
    days[-1]["recovery"] = 20
    rec = sleep_coach.recommend_bedtime(days, {}, {"sleep_target_min": 480})
    need = sleep_scores.sleep_need_min(days, {}, 480)
    assert need == rec["need_min"] == 480 + 60 + 20 + 20


def test_need_falls_back_to_summary_target():
    days = _wake_days(14, wake="07:00")
    need = sleep_scores.sleep_need_min(days, {"sleep_target_min": 450}, None)
    assert need == 450


def test_need_falls_back_to_default_480():
    days = _wake_days(14, wake="07:00")
    need = sleep_scores.sleep_need_min(days, {}, None)
    assert need == 480


def test_need_none_without_days():
    assert sleep_scores.sleep_need_min([], {}, 480) is None
    assert sleep_scores.sleep_need_min(None, {}, 480) is None


def test_need_never_raises_on_garbage():
    assert sleep_scores.sleep_need_min([{"date": None}], "garbage", "abc") is not None or True
    # nunca lanza, aunque el resultado degrade
    sleep_scores.sleep_need_min(None, None, None)


# ── sleep_score ──────────────────────────────────────────────────────────

def test_sleep_score_basic():
    assert sleep_scores.sleep_score(420, 480) == 88  # 420/480*100 = 87.5 -> round 88


def test_sleep_score_capped_at_100():
    assert sleep_scores.sleep_score(600, 480) == 100


def test_sleep_score_exact_100():
    assert sleep_scores.sleep_score(480, 480) == 100


def test_sleep_score_none_safe():
    assert sleep_scores.sleep_score(None, 480) is None
    assert sleep_scores.sleep_score(420, None) is None
    assert sleep_scores.sleep_score(420, 0) is None
    assert sleep_scores.sleep_score("garbage", 480) is None


# ── consistency_score ────────────────────────────────────────────────────

def test_consistency_perfect_with_zero_variance():
    days = _wake_days(14, wake="07:00", bed_min=0)
    assert sleep_scores.consistency_score(days) == 100


def test_consistency_zero_with_huge_variance():
    days = []
    d0 = datetime.date.fromisoformat("2026-06-01")
    wakes = ["05:00", "10:00", "06:00", "11:00", "07:00", "12:00", "08:00"]
    beds = [-200, 200, -180, 220, -150, 240, -100]
    for i in range(7):
        days.append({
            "date": (d0 + datetime.timedelta(days=i)).isoformat(),
            "waketime": wakes[i], "bed_min": beds[i],
        })
    score = sleep_scores.consistency_score(days)
    assert score == 0


def test_consistency_none_with_fewer_than_5_nights():
    days = _wake_days(4, wake="07:00", bed_min=0)
    assert sleep_scores.consistency_score(days) is None


def test_consistency_none_without_data():
    assert sleep_scores.consistency_score([]) is None
    assert sleep_scores.consistency_score(None) is None


def test_consistency_never_raises_on_garbage():
    assert sleep_scores.consistency_score([{"waketime": 12345, "bed_min": "x"}] * 10) is None
