"""
test_plan_store.py — Tests de app/plan_store.py (Roadmap P1, F4, paso 5).

Cubre:
(a) persistencia/atomicidad (round-trip, tolerante a corrupto) — patrón
    test_journal.py.
(b) mutaciones: start_plan/abandon_plan/manual_check, un-solo-activo.
(c) plan_status: día N correcto, adherencia auto (sleep/cardio/strength/
    habit), manual override, bordes (día 1, plan completado, sin dato).
"""
from __future__ import annotations

import datetime
import json

import pytest

from app import plan_store


def _patch_plan_log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(plan_store, "_PLAN_LOG_FILE", tmp_path / "plan_log.json")


def _dates(start: str, n: int) -> list:
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


# ── (a) persistencia ─────────────────────────────────────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    log = plan_store.load_plan_log()
    assert log == {"active": None, "history": []}


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    log = {"active": {"program_id": "sleep_reset", "started_date": "2026-01-01", "checks": {}}, "history": []}
    plan_store.save_plan_log(log)
    loaded = plan_store.load_plan_log()
    assert loaded["active"]["program_id"] == "sleep_reset"


def test_load_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    (tmp_path / "plan_log.json").write_text("NOT JSON{{{", encoding="utf-8")
    log = plan_store.load_plan_log()
    assert log == {"active": None, "history": []}


def test_save_is_atomic_no_tmp_leftover(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.save_plan_log({"active": None, "history": []})
    leftovers = list(tmp_path.glob("*.tmp"))
    assert not leftovers


# ── (b) mutaciones ────────────────────────────────────────────────────────

def test_start_plan_creates_active(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    active = plan_store.start_plan("sleep_reset", "2026-01-01")
    assert active is not None
    assert active["program_id"] == "sleep_reset"
    assert active["started_date"] == "2026-01-01"
    assert active["checks"] == {}
    assert plan_store.has_active_plan() is True


def test_start_plan_unknown_program_returns_none(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    assert plan_store.start_plan("bogus_program") is None
    assert plan_store.has_active_plan() is False


def test_start_plan_with_active_returns_none_single_active_at_a_time(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    result = plan_store.start_plan("aerobic_base", "2026-01-02")
    assert result is None
    log = plan_store.load_plan_log()
    assert log["active"]["program_id"] == "sleep_reset"  # el primero sigue activo


def test_abandon_plan_moves_to_history(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    result = plan_store.abandon_plan("2026-01-05")
    assert result is True
    log = plan_store.load_plan_log()
    assert log["active"] is None
    assert len(log["history"]) == 1
    assert log["history"][0]["program_id"] == "sleep_reset"
    assert log["history"][0]["ended_reason"] == "abandoned"


def test_abandon_plan_without_active_returns_false(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    assert plan_store.abandon_plan() is False


def test_start_plan_after_abandon_succeeds(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    plan_store.abandon_plan("2026-01-05")
    active = plan_store.start_plan("aerobic_base", "2026-01-06")
    assert active is not None
    assert active["program_id"] == "aerobic_base"


def test_manual_check_sets_check(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    result = plan_store.manual_check("2026-01-02")
    assert result is not None
    assert result["checks"]["2026-01-02"] == "manual"


def test_manual_check_default_today(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    result = plan_store.manual_check()
    today_str = datetime.date.today().isoformat()
    assert result["checks"][today_str] == "manual"


def test_manual_check_without_active_returns_none(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    assert plan_store.manual_check("2026-01-01") is None


def test_manual_check_invalid_date_returns_none(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    assert plan_store.manual_check("not-a-date") is None


# ── (c) plan_status ───────────────────────────────────────────────────────

def test_plan_status_none_without_active_plan(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    assert plan_store.plan_status({"days": []}) is None


def test_plan_status_day_1_zero_evaluable_days(tmp_path, monkeypatch):
    """Plan iniciado HOY: día 1, 0 días evaluables -> adherence_pct None."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    today = datetime.date.today().isoformat()
    plan_store.start_plan("sleep_reset", today)
    dataset = {"days": [{"date": today, "recovery": 60}], "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status is not None
    assert status["day_number"] == 1
    assert status["is_completed"] is False
    assert status["n_evaluable_days"] == 0
    assert status["adherence_pct"] is None
    assert status["today_task"] is not None


def test_plan_status_day_number_clamped_when_dataset_date_before_start(tmp_path, monkeypatch):
    """Bug real encontrado en humo manual: si la última fecha del dataset
    (today_str) es ANTERIOR a started_date (ej. dataset demo con fecha
    sintética vs plan iniciado con la fecha real de hoy), day_index sale
    negativo — day_number debe quedar clamped a 1, nunca <=0."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    future_start = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    plan_store.start_plan("sleep_reset", future_start)
    dataset = {"days": [{"date": "2020-01-01"}], "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status is not None
    assert status["day_number"] == 1
    assert status["today_task"] is None  # day_index < 0 -> sin tarea de hoy


def test_plan_status_completed_when_past_duration(tmp_path, monkeypatch):
    """day_index >= duración -> status completed, sin crash."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("sleep_reset", start)  # duración 14
    far_future = (datetime.date.fromisoformat(start) + datetime.timedelta(days=30)).isoformat()
    dataset = {"days": [{"date": far_future}], "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status is not None
    assert status["is_completed"] is True
    assert status["today_task"] is None
    assert status["day_number"] == 14  # capado a duration


def test_plan_status_sleep_adherence_met(tmp_path, monkeypatch):
    """kind=sleep (día índice 0 de sleep_reset): asleep >= need - 30min ->
    cumplido auto. Usamos ventana de 2 días (start + 1) para que SOLO el día
    0 (kind=sleep) sea evaluable — el día 1 del catálogo es kind=habit."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("sleep_reset", start)
    dates = _dates(start, 2)
    days = [{"date": d, "asleep": 480, "waketime": "07:00"} for d in dates]
    dataset = {"days": days, "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status is not None
    assert status["n_evaluable_days"] == 1  # solo el día 0 (start) transcurrió, día 1 es "hoy"
    assert status["adherence_pct"] == 100


def test_plan_status_sleep_adherence_not_met(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("sleep_reset", start)
    dates = _dates(start, 2)
    days = [{"date": d, "asleep": 200, "waketime": "07:00"} for d in dates]  # muy poco sueño
    dataset = {"days": days, "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status["adherence_pct"] == 0


def test_plan_status_manual_check_overrides_auto(tmp_path, monkeypatch):
    """Un check manual en un día que NO cumple auto debe contar como cumplido."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("sleep_reset", start)
    dates = _dates(start, 3)
    plan_store.manual_check(dates[0])  # día 0: manual, aunque no haya dato de sueño
    days = [{"date": d} for d in dates]  # sin asleep -> auto fallaría
    dataset = {"days": days, "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status["n_met_days"] == 1  # solo el manual cuenta
    assert status["adherence_pct"] == 50  # 1 de 2 evaluables


def test_plan_status_strength_adherence(tmp_path, monkeypatch):
    """strength_3x día 0 (kind=strength, min=40) — sesión de 45 min cumple."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("strength_3x", start)
    dates = _dates(start, 2)
    days = [{"date": d} for d in dates]
    exercises = [{"date": dates[0], "type": "strength", "name": "Weights", "dur_min": 45}]
    dataset = {"days": days, "exercises": exercises, "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status["n_evaluable_days"] == 1
    assert status["adherence_pct"] == 100


def test_plan_status_habit_adherence(tmp_path, monkeypatch):
    """stress_reset día 0 (kind=habit, breathwork) — journal marca sí."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    from app import journal as _journal_mod
    monkeypatch.setattr(_journal_mod, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json")

    start = "2026-01-01"
    plan_store.start_plan("stress_reset", start)
    dates = _dates(start, 2)
    _journal_mod.set_entry(dates[0], {"breathwork": True})
    days = [{"date": d} for d in dates]
    dataset = {"days": days, "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status["n_evaluable_days"] == 1
    assert status["adherence_pct"] == 100


def test_plan_status_habit_adherence_empty_journal(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    from app import journal as _journal_mod
    monkeypatch.setattr(_journal_mod, "_JOURNAL_LOG_FILE", tmp_path / "journal_log_empty.json")

    start = "2026-01-01"
    plan_store.start_plan("stress_reset", start)
    dates = _dates(start, 2)
    days = [{"date": d} for d in dates]
    dataset = {"days": days, "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status["adherence_pct"] == 0  # sin journal -> no cumplido, nunca crash


def test_plan_status_missing_dataset_dates_count_as_not_met(tmp_path, monkeypatch):
    """Fechas ausentes por completo en el dataset -> no cumplidas, sin crash."""
    _patch_plan_log_path(monkeypatch, tmp_path)
    start = "2026-01-01"
    plan_store.start_plan("sleep_reset", start)
    dataset = {"days": [{"date": (datetime.date.fromisoformat(start) + datetime.timedelta(days=5)).isoformat()}],
               "exercises": [], "summary": {}}
    status = plan_store.plan_status(dataset)
    assert status is not None
    assert status["adherence_pct"] == 0


def test_plan_status_never_raises_on_garbage_dataset(tmp_path, monkeypatch):
    _patch_plan_log_path(monkeypatch, tmp_path)
    plan_store.start_plan("sleep_reset", "2026-01-01")
    assert plan_store.plan_status(None) is not None or plan_store.plan_status(None) is None
    assert plan_store.plan_status({"days": "garbage"}) is not None or True
    assert plan_store.plan_status({}) is not None
