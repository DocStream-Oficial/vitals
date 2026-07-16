"""
test_illness_latch_endpoint.py — Paso 3 del roadmap dev-harness/illness-latch:
verifica el CABLEADO real de los 2 callers vivos (main.py:409 vía GET /,
app/routes/insights.py:45 vía GET /api/insights) con latch=True end-to-end
por TestClient — no solo evaluate() en aislamiento (eso ya lo cubre
tests/test_insights.py).

Escribe un health_compact.json SINTÉTICO propio (mismos valores que
tests/test_insights.py::_incident_days) en un tmp_path aislado — NUNCA
toca data/ real ni depende del dataset real del usuario. El data dir + illness_state quedan aislados en tmp_path (mismo
patrón que _isolate_coach_history de test_endpoints.py; illness_state
además tiene aislamiento GLOBAL autouse en conftest.py).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


_INCIDENT_TEMPS_PREV = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                         0.38, 0.42, -2.01, -2.58, 0.28]


def _date_seq(n):
    return [f"2024-01-{i+1:02d}" for i in range(n)]


def _incident_dataset(today_hrv: float) -> dict:
    """Mismo incidente reconstruido que tests/test_insights.py::_incident_days:
    13 días previos (temp variable, sd alta) + hoy con HRV variable — para
    simular la 2ª lectura del mismo día con la HRV ya diluida por el
    re-promedio intradía."""
    dates = _date_seq(14)
    days = [
        {"date": d, "skin_temp": t, "rhr": 51.6, "hrv": 57.0, "resp": 15.0}
        for d, t in zip(dates[:13], _INCIDENT_TEMPS_PREV)
    ]
    days.append({
        "date": dates[13], "skin_temp": 0.24, "rhr": 53.0, "hrv": today_hrv,
        "resp": 15.23, "spo2": 95.68,
    })
    summary = {"hrv_base_recent": 58.8, "hrv_sd": 7.71, "rhr_base": 51.6}
    return {"days": days, "summary": summary, "exercises": []}


@pytest.fixture
def client_with_incident(tmp_path):
    """TestClient con DATA_DIR aislado en tmp_path y health_compact.json
    escrito con el dataset del incidente (HRV en su pico -> alert)."""
    (tmp_path / "health_compact.json").write_text(
        json.dumps(_incident_dataset(today_hrv=79.77)), encoding="utf-8"
    )

    from app import config
    from app import auth as auth_mod
    from app import sync as sync_mod
    from app import coach_store as coach_store_mod
    from app import illness_state as illness_state_mod
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(auth_mod, "TOKEN_PATH", tmp_path / "token.json"), \
         patch.object(sync_mod, "DATA_OUT", tmp_path / "health_compact.json"), \
         patch.object(coach_store_mod, "_DATA_DIR", tmp_path), \
         patch.object(coach_store_mod, "_STORE_FILE", tmp_path / "coach_conversations.json"), \
         patch.object(coach_store_mod, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json"), \
         patch.object(coach_store_mod, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak"), \
         patch.object(illness_state_mod, "_DATA_DIR", tmp_path), \
         patch.object(illness_state_mod, "_LATCH_FILE", tmp_path / "illness_latch.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client, tmp_path


def test_api_insights_latches_alert_across_intraday_dilution(client_with_incident):
    """Criterios 5 y 7 vía HTTP real: GET /api/insights (app/routes/insights.py:45,
    latch=True) devuelve alert en la 1ª llamada (HRV en su pico), persiste
    illness_latch.json, y SIGUE devolviendo alert en la 2ª llamada del mismo
    día con la HRV ya diluida (health_compact.json reescrito entre llamadas,
    igual que un sync real re-promediando el día)."""
    client, tmp_path = client_with_incident

    resp1 = client.get("/api/insights")
    assert resp1.status_code == 200
    body1 = resp1.json()
    illness1 = next((r for r in body1 if r["id"] == "illness_early_warning"), None)
    assert illness1 is not None and illness1["severity"] == "alert"

    # Confirmar que SÍ persistió (no es casualidad del cómputo fresco).
    latch_file = tmp_path / "illness_latch.json"
    assert latch_file.exists()
    persisted = json.loads(latch_file.read_text(encoding="utf-8"))
    assert persisted["date"] == "2024-01-14"
    assert persisted["severity"] == "alert"

    # Simula el re-promedio intradía: mismo día, HRV diluida -> re-escribe
    # health_compact.json (igual que haría un sync real).
    (tmp_path / "health_compact.json").write_text(
        json.dumps(_incident_dataset(today_hrv=66.07)), encoding="utf-8"
    )

    resp2 = client.get("/api/insights")
    assert resp2.status_code == 200
    body2 = resp2.json()
    illness2 = next((r for r in body2 if r["id"] == "illness_early_warning"), None)
    assert illness2 is not None and illness2["severity"] == "alert", (
        "GET /api/insights debe seguir devolviendo alert (latcheado) aunque "
        "la HRV fresca de la 2ª llamada ya se haya diluido"
    )
    assert illness2["summary"] == illness1["summary"], (
        "debe ser el insight COMPLETO del pico de la 1ª llamada"
    )


def test_dashboard_root_also_latches_alert(client_with_incident):
    """Criterio 5: main.py:409 (GET /, dashboard) también pasa latch=True.
    No re-verifica toda la lógica del latch (ya cubierta arriba) — solo que
    el caller vivo del dashboard está cableado y no rompe la ruta."""
    client, tmp_path = client_with_incident
    resp = client.get("/")
    assert resp.status_code == 200
    # El GET / persiste el latch igual que /api/insights (mismo evaluate()).
    latch_file = tmp_path / "illness_latch.json"
    assert latch_file.exists()
    persisted = json.loads(latch_file.read_text(encoding="utf-8"))
    assert persisted["date"] == "2024-01-14"
    assert persisted["severity"] == "alert"
