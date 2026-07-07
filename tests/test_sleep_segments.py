"""
test_sleep_segments.py — Tests de app/sleep_segments.py (F2 roadmap P0).
"""
from __future__ import annotations

from app.sleep_segments import validate_segments, awakenings


# ── validate_segments ─────────────────────────────────────────────────────────

def test_valid_segments_pass_through_sorted():
    raw = [
        {"s": 30, "e": 60, "st": "light"},
        {"s": 0, "e": 30, "st": "awake"},
        {"s": 60, "e": 120, "st": "deep"},
    ]
    result = validate_segments(raw)
    assert result is not None
    assert [s["s"] for s in result] == [0, 30, 60]


def test_none_raw_returns_none():
    assert validate_segments(None) is None


def test_empty_list_returns_none():
    assert validate_segments([]) is None


def test_not_a_list_returns_none():
    assert validate_segments("garbage") is None
    assert validate_segments({"s": 0, "e": 10, "st": "deep"}) is None


def test_missing_keys_returns_none():
    assert validate_segments([{"s": 0, "e": 10}]) is None
    assert validate_segments([{"s": 0, "st": "deep"}]) is None
    assert validate_segments([{"e": 10, "st": "deep"}]) is None


def test_non_dict_item_returns_none():
    assert validate_segments([{"s": 0, "e": 10, "st": "deep"}, "garbage"]) is None


def test_invalid_stage_returns_none():
    assert validate_segments([{"s": 0, "e": 10, "st": "napping"}]) is None


def test_e_not_greater_than_s_returns_none():
    assert validate_segments([{"s": 10, "e": 10, "st": "deep"}]) is None
    assert validate_segments([{"s": 10, "e": 5, "st": "deep"}]) is None


def test_negative_s_returns_none():
    assert validate_segments([{"s": -5, "e": 10, "st": "deep"}]) is None


def test_overlapping_segments_return_none():
    raw = [
        {"s": 0, "e": 30, "st": "light"},
        {"s": 20, "e": 50, "st": "deep"},
    ]
    assert validate_segments(raw) is None


def test_adjacent_segments_are_valid():
    """s del siguiente == e del anterior -> NO es traslape."""
    raw = [
        {"s": 0, "e": 30, "st": "light"},
        {"s": 30, "e": 60, "st": "deep"},
    ]
    result = validate_segments(raw)
    assert result is not None
    assert len(result) == 2


def test_non_integer_s_or_e_returns_none():
    assert validate_segments([{"s": 0.5, "e": 10, "st": "deep"}]) is None


def test_float_that_is_whole_number_is_accepted():
    result = validate_segments([{"s": 0.0, "e": 10.0, "st": "deep"}])
    assert result is not None
    assert result[0]["s"] == 0 and result[0]["e"] == 10


def test_bool_as_s_or_e_returns_none():
    """bool es subtipo de int en Python — debe rechazarse explícitamente."""
    assert validate_segments([{"s": True, "e": 10, "st": "deep"}]) is None


def test_none_string_returns_none():
    assert validate_segments("none") is None


# ── awakenings ─────────────────────────────────────────────────────────────────

def test_awakenings_counts_only_after_first_sleep():
    segments = [
        {"s": 0, "e": 10, "st": "awake"},   # inicial, no cuenta
        {"s": 10, "e": 100, "st": "light"},
        {"s": 100, "e": 110, "st": "awake"},  # cuenta
        {"s": 110, "e": 200, "st": "deep"},
        {"s": 200, "e": 205, "st": "awake"},  # cuenta
        {"s": 205, "e": 300, "st": "rem"},
    ]
    assert awakenings(segments) == 2


def test_awakenings_empty_or_none_is_zero():
    assert awakenings(None) == 0
    assert awakenings([]) == 0


def test_awakenings_all_asleep_no_wakes():
    segments = [{"s": 0, "e": 100, "st": "light"}, {"s": 100, "e": 200, "st": "deep"}]
    assert awakenings(segments) == 0


def test_awakenings_never_crashes_on_garbage():
    assert awakenings("garbage") == 0
    assert awakenings([1, 2, 3]) == 0
    assert awakenings([{"st": "awake"}, None]) == 0
