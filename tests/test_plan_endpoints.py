"""
test_plan_endpoints.py — Tests de los endpoints de programas/plan
(Roadmap P1, F4, paso 6): GET /api/programs, GET/POST/DELETE /api/plan,
POST /api/plan/check.

Patrón de fixture: idéntico a _get_journal_client de test_journal.py
(DATA_DIR/profile/journal/plan_store aislados en tmp_path).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import plan_store


def _get_plan_client(tmp_path: Path, with_dataset: bool = False):
    from app import config, profile as _pm
    import main as main_mod

    if with_dataset:
        today = datetime.date.today()
        days = []
        for i in range(20):
            d = today - datetime.timedelta(days=19 - i)
            days.append({"date": d.isoformat(), "waketime": "07:00", "asleep": 480, "recovery": 65, "strain": 5.0})
        dataset = {"days": days, "summary": {"n_days": len(days)}}
        (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch.object(plan_store, "_PLAN_LOG_FILE", tmp_path / "plan_log.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def plan_client(tmp_path):
    yield from _get_plan_client(tmp_path, with_dataset=True)


@pytest.fixture
def plan_client_no_data(tmp_path):
    yield from _get_plan_client(tmp_path, with_dataset=False)


# ── GET /api/programs ────────────────────────────────────────────────────

def test_api_programs_get_returns_4_programs(plan_client):
    resp = plan_client.get("/api/programs")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4
    ids = {p["id"] for p in body}
    assert ids == {"sleep_reset", "aerobic_base", "strength_3x", "stress_reset"}


# ── GET /api/plan (sin plan activo) ──────────────────────────────────────

def test_api_plan_get_no_active_plan(plan_client):
    resp = plan_client.get("/api/plan")
    assert resp.status_code == 200
    assert resp.json() == {"active": False}


# ── POST /api/plan ────────────────────────────────────────────────────────

def test_api_plan_post_starts_program(plan_client):
    resp = plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["active"]["program_id"] == "sleep_reset"


def test_api_plan_post_unknown_program_422(plan_client):
    resp = plan_client.post("/api/plan", json={"program_id": "no_existe"})
    assert resp.status_code == 422


def test_api_plan_post_conflict_409_with_active(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.post("/api/plan", json={"program_id": "aerobic_base"})
    assert resp.status_code == 409


def test_api_plan_post_missing_body_422(plan_client):
    resp = plan_client.post("/api/plan", json={})
    assert resp.status_code == 422


# ── GET /api/plan (con plan activo) ──────────────────────────────────────

def test_api_plan_get_with_active_plan(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.get("/api/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["program_id"] == "sleep_reset"
    assert body["day_number"] >= 1
    assert "today_task" in body
    assert "adherence_pct" in body


# ── DELETE /api/plan ──────────────────────────────────────────────────────

def test_api_plan_delete_abandons_active(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.delete("/api/plan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    status = plan_client.get("/api/plan").json()
    assert status == {"active": False}


def test_api_plan_delete_idempotent_without_active(plan_client):
    resp = plan_client.delete("/api/plan")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_plan_post_after_delete_succeeds(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    plan_client.delete("/api/plan")
    resp = plan_client.post("/api/plan", json={"program_id": "aerobic_base"})
    assert resp.status_code == 200
    assert resp.json()["active"]["program_id"] == "aerobic_base"


# ── POST /api/plan/check ──────────────────────────────────────────────────

def test_api_plan_check_marks_today(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.post("/api/plan/check", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    today_str = datetime.date.today().isoformat()
    assert body["active"]["checks"][today_str] == "manual"


def test_api_plan_check_specific_date(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.post("/api/plan/check", json={"date": "2020-01-01"})
    assert resp.status_code == 200
    assert resp.json()["active"]["checks"]["2020-01-01"] == "manual"


def test_api_plan_check_invalid_date_422(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client.post("/api/plan/check", json={"date": "not-a-date"})
    assert resp.status_code == 422


def test_api_plan_check_future_date_422(plan_client):
    plan_client.post("/api/plan", json={"program_id": "sleep_reset"})
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    resp = plan_client.post("/api/plan/check", json={"date": future})
    assert resp.status_code == 422


def test_api_plan_check_without_active_plan_404(plan_client):
    resp = plan_client.post("/api/plan/check", json={})
    assert resp.status_code == 404


# ── sin dataset (no debe 500) ─────────────────────────────────────────────

def test_api_plan_get_never_500_without_dataset(plan_client_no_data):
    plan_client_no_data.post("/api/plan", json={"program_id": "sleep_reset"})
    resp = plan_client_no_data.get("/api/plan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True  # plan existe aunque no haya dataset todavía


def test_api_programs_never_500_without_dataset(plan_client_no_data):
    resp = plan_client_no_data.get("/api/programs")
    assert resp.status_code == 200
