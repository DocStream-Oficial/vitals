"""
test_profile_impact.py — Roadmap edad-corporal-credibilidad, Paso 4:
atribución de cambios de perfil.

Cubre el criterio de aceptación 5 del roadmap:
- PUT /api/profile que cambia waist_cm/sex/birthdate y mueve body_age >=2
  años -> escribe profile_impact.json en el dir del usuario.
- Edits que mueven <2 años -> NO generan nota.
- PUT sigue devolviendo 200 aunque el dataset no exista (best-effort total).
- run_sync() inyecta summary.bodyage.profile_note desde un profile_impact.json
  fresco; uno con >14 días de antigüedad se borra y NO se inyecta.
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ── PUT /api/profile — helpers ──────────────────────────────────────────────

def _birthdate_for_age(age_years: int) -> str:
    """Birthdate ISO que da EXACTAMENTE `age_years` hoy (cumpleaños ya pasado
    este año, para que la fórmula de current_age/​_age_from_birthdate no
    dependa de en qué día del año se corra la suite)."""
    today = datetime.date.today()
    return (today.replace(year=today.year - age_years) - datetime.timedelta(days=1)).isoformat()


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app import config, profile as _pm
    import main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                        Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)

    with patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


def _write_profile(tmp_path: Path, data: dict) -> None:
    (tmp_path / "profile.json").write_text(json.dumps(data), encoding="utf-8")


def _write_dataset(tmp_path: Path) -> None:
    """health_compact.json mínimo con 1 día — suficiente para que
    compute_body_age tenga algo que procesar (recent() tolera <14 días)."""
    dataset = {"days": [{"date": "2025-06-01", "rhr": 55.0}], "exercises": [], "summary": {}}
    (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")


# ── PUT: cambio grande -> escribe profile_impact.json ──────────────────────

def test_put_waist_change_moves_body_age_writes_impact(tmp_path, monkeypatch, api_client):
    bd = _birthdate_for_age(45)
    _write_profile(tmp_path, {
        "birthdate": bd, "sex": "F", "waist_cm": 60.0, "onboarded": True,
    })
    _write_dataset(tmp_path)

    resp = api_client.put("/api/profile", json={"waist_cm": 90.0})
    assert resp.status_code == 200

    impact_path = tmp_path / "profile_impact.json"
    assert impact_path.exists(), "esperaba profile_impact.json tras un cambio que mueve >=2 años"
    impact = json.loads(impact_path.read_text())
    assert impact["fields"] == ["waist_cm"]
    assert impact["old_body_age"] == 20
    assert impact["new_body_age"] == 41
    assert impact["delta_years"] == 21
    assert impact["date"] == datetime.date.today().isoformat()


def test_put_small_waist_change_does_not_write_impact(tmp_path, monkeypatch, api_client):
    bd = _birthdate_for_age(45)
    _write_profile(tmp_path, {
        "birthdate": bd, "sex": "F", "waist_cm": 60.0, "onboarded": True,
    })
    _write_dataset(tmp_path)

    resp = api_client.put("/api/profile", json={"waist_cm": 61.0})
    assert resp.status_code == 200

    impact_path = tmp_path / "profile_impact.json"
    assert not impact_path.exists(), "un cambio <2 años NO debe generar nota"


def test_put_no_relevant_field_change_does_not_write_impact(tmp_path, monkeypatch, api_client):
    """PUT que no toca waist_cm/sex/birthdate (ej. solo locale) -> sin nota."""
    bd = _birthdate_for_age(45)
    _write_profile(tmp_path, {
        "birthdate": bd, "sex": "F", "waist_cm": 60.0, "onboarded": True,
    })
    _write_dataset(tmp_path)

    resp = api_client.put("/api/profile", json={"locale": "en"})
    assert resp.status_code == 200
    assert not (tmp_path / "profile_impact.json").exists()


def test_put_returns_200_even_without_dataset(tmp_path, monkeypatch, api_client):
    """Best-effort total: sin health_compact.json (dataset ausente), el PUT
    sigue devolviendo 200 y simplemente no escribe profile_impact.json."""
    bd = _birthdate_for_age(45)
    _write_profile(tmp_path, {
        "birthdate": bd, "sex": "F", "waist_cm": 60.0, "onboarded": True,
    })
    # SIN _write_dataset(tmp_path) -> no hay health_compact.json

    resp = api_client.put("/api/profile", json={"waist_cm": 90.0})
    assert resp.status_code == 200
    assert not (tmp_path / "profile_impact.json").exists()


def test_put_returns_200_with_corrupt_dataset(tmp_path, monkeypatch, api_client):
    """Best-effort total: health_compact.json corrupto -> el PUT sigue 200,
    sin nota (nunca 500 por culpa de este bloque aditivo)."""
    bd = _birthdate_for_age(45)
    _write_profile(tmp_path, {
        "birthdate": bd, "sex": "F", "waist_cm": 60.0, "onboarded": True,
    })
    (tmp_path / "health_compact.json").write_text("NOT JSON{{{", encoding="utf-8")

    resp = api_client.put("/api/profile", json={"waist_cm": 90.0})
    assert resp.status_code == 200
    assert not (tmp_path / "profile_impact.json").exists()


def test_put_first_time_profile_no_old_values_no_crash(tmp_path, monkeypatch, api_client):
    """Primer PUT de la vida (sin profile.json previo) -> old_values son todos
    None, 'cambiaron' respecto a los nuevos, pero al no haber old_waist/old_bd
    utilizables el bloque se sale limpio (sin nota, sin crash, 200)."""
    _write_dataset(tmp_path)
    resp = api_client.put("/api/profile", json={
        "birthdate": "1990-01-01", "sex": "M", "waist_cm": 85.0,
    })
    assert resp.status_code == 200
    assert not (tmp_path / "profile_impact.json").exists()


# ── sync.py: inyección de profile_note ──────────────────────────────────────

def _sample_source_data():
    return {
        "sleep": {}, "rhr": {"2026-06-28": 55.0}, "hrv": {"2026-06-28": 45.0},
        "resp": {}, "vo2": {}, "steps": {}, "azm": {}, "spo2": {}, "skin": {},
        "exercises": [],
    }


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Aislamiento mínimo (mismo patrón que tests/test_sync.py::sync_env) para
    correr run_sync() sin tocar CLI/red ni el data/ real."""
    from app import config
    from app import profile as _pm
    import subprocess as _subprocess
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)

    class _NoCliResult:
        returncode = 1
        stdout = ""
        stderr = "claude CLI no disponible en tests"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _NoCliResult())

    import importlib
    import app.sync as sync_mod
    import app.coach_headline as headline_mod
    import app.report as report_mod
    import app.journal as journal_mod
    importlib.reload(sync_mod)
    importlib.reload(headline_mod)
    importlib.reload(report_mod)
    importlib.reload(journal_mod)
    monkeypatch.setattr(headline_mod, "_CACHE_PATH", tmp_path / "coach_headline.json")
    monkeypatch.setattr(report_mod, "_CACHE_PATH", tmp_path / "reports.json")
    monkeypatch.setattr(journal_mod, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json")
    monkeypatch.setattr(report_mod, "maybe_regenerate_reports", lambda *a, **kw: None)
    yield tmp_path, sync_mod
    importlib.reload(sync_mod)
    importlib.reload(headline_mod)
    importlib.reload(report_mod)
    importlib.reload(journal_mod)


def test_sync_injects_fresh_profile_note(sync_env):
    from unittest.mock import MagicMock
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "birthdate": "1985-01-01", "waist_cm": 85.0,
        "sex": "M", "onboarded": True,
    }))
    impact = {
        "date": datetime.date.today().isoformat(),
        "fields": ["waist_cm"], "old_body_age": 30, "new_body_age": 35, "delta_years": 5,
    }
    (tmp_path / "profile_impact.json").write_text(json.dumps(impact), encoding="utf-8")

    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    with patch("app.sync.get_source", return_value=fake_source):
        dataset = sync_mod.run_sync(45)

    assert dataset["summary"]["bodyage"]["profile_note"] == impact


def test_sync_expires_and_deletes_old_profile_note(sync_env):
    from unittest.mock import MagicMock
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "birthdate": "1985-01-01", "waist_cm": 85.0,
        "sex": "M", "onboarded": True,
    }))
    old_date = (datetime.date.today() - datetime.timedelta(days=20)).isoformat()
    impact = {
        "date": old_date,
        "fields": ["waist_cm"], "old_body_age": 30, "new_body_age": 35, "delta_years": 5,
    }
    impact_path = tmp_path / "profile_impact.json"
    impact_path.write_text(json.dumps(impact), encoding="utf-8")

    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    with patch("app.sync.get_source", return_value=fake_source):
        dataset = sync_mod.run_sync(45)

    assert "profile_note" not in (dataset["summary"].get("bodyage") or {})
    assert not impact_path.exists(), "una nota >14 días debe borrarse"
