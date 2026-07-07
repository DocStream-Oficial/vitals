"""
test_export.py — Tests de GET /api/export (Ronda 4 — producto).

Cubre:
- fmt=json: attachment con Content-Disposition correcto, JSON parseable == dataset.
- fmt=csv: parseable con csv.reader, columnas = unión de claves (no solo el 1er día),
  campos de sueño prefijados 'sleep_', formula-injection neutralizada.
- Sin datos -> 404. fmt inválido -> 400. Nunca 500.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from unittest.mock import patch

import pytest


def _get_api_client(tmp_path: Path, monkeypatch):
    from app import config, profile as _pm, coach_store
    import main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                        Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")

    with patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    yield from _get_api_client(tmp_path, monkeypatch)


def _write_dataset(tmp_path: Path, days: list, exercises: list = None):
    data = {
        "days": days,
        "exercises": exercises or [],
        "summary": {"n_days": len(days)},
    }
    (tmp_path / "health_compact.json").write_text(json.dumps(data), encoding="utf-8")
    return data


# ── Sin datos / fmt inválido ──────────────────────────────────────────────────

def test_export_no_data_404(api_client):
    resp = api_client.get("/api/export?fmt=json")
    assert resp.status_code == 404


def test_export_invalid_fmt_400(tmp_path, api_client):
    _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": 60}])
    resp = api_client.get("/api/export?fmt=xml")
    assert resp.status_code == 400


def test_export_default_fmt_is_json(tmp_path, api_client):
    _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": 60}])
    resp = api_client.get("/api/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/json")


# ── fmt=json ──────────────────────────────────────────────────────────────────

def test_export_json_attachment_headers(tmp_path, api_client):
    _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": 60}])
    resp = api_client.get("/api/export?fmt=json")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "vitals-export-" in cd
    assert cd.endswith(".json\"") or cd.endswith(".json")
    assert resp.headers.get("cache-control") == "no-store"


def test_export_json_content_matches_dataset(tmp_path, api_client):
    data = _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": 60, "hrv": 55}])
    resp = api_client.get("/api/export?fmt=json")
    body = json.loads(resp.text)
    assert body == data


# ── fmt=csv ───────────────────────────────────────────────────────────────────

def test_export_csv_attachment_headers(tmp_path, api_client):
    _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": 60}])
    resp = api_client.get("/api/export?fmt=csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".csv" in cd


def test_export_csv_no_days_404(tmp_path, api_client):
    _write_dataset(tmp_path, [])
    resp = api_client.get("/api/export?fmt=csv")
    assert resp.status_code == 404


def test_export_csv_parseable_with_csv_reader(tmp_path, api_client):
    _write_dataset(tmp_path, [
        {"date": "2026-06-20", "recovery": 60, "asleep": 420},
        {"date": "2026-06-21", "recovery": 70, "asleep": 400},
    ])
    resp = api_client.get("/api/export?fmt=csv")
    reader = csv.reader(io.StringIO(resp.text))
    rows = list(reader)
    assert len(rows) == 3  # header + 2 días
    header = rows[0]
    assert "date" in header
    assert "sleep_asleep" in header  # asleep -> prefijo sleep_
    assert "recovery" in header


def test_export_csv_columns_are_union_not_first_day(tmp_path, api_client):
    """El primer día tiene MENOS claves que el último -> fieldnames debe ser la
    UNIÓN, no solo las claves del primer día (riesgo #4 del roadmap)."""
    _write_dataset(tmp_path, [
        {"date": "2026-06-20"},  # pocas claves
        {"date": "2026-06-21", "recovery": 70, "hrv": 55, "steps": 8000},  # más claves
    ])
    resp = api_client.get("/api/export?fmt=csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    fieldnames = reader.fieldnames
    assert "recovery" in fieldnames
    assert "hrv" in fieldnames
    assert "steps" in fieldnames
    rows = list(reader)
    assert len(rows) == 2
    # El primer día no tenía 'recovery' -> debe venir como "" (no KeyError/crash)
    assert rows[0]["recovery"] == ""
    assert rows[1]["recovery"] == "70"


def test_export_csv_sleep_fields_prefixed(tmp_path, api_client):
    _write_dataset(tmp_path, [{
        "date": "2026-06-20", "asleep": 420, "deep": 90, "rem": 80,
        "eff": 92, "bedtime": "23:30", "bed_min": 30, "sleep_perf": 85,
        "recovery": 60,
    }])
    resp = api_client.get("/api/export?fmt=csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    fieldnames = reader.fieldnames
    for f in ("asleep", "deep", "rem", "eff", "bedtime", "bed_min"):
        assert f"sleep_{f}" in fieldnames
    # 'sleep_perf' ya trae el prefijo semántico en su propio nombre -> NO se
    # re-prefija (evitaría la columna fea 'sleep_sleep_perf').
    assert "sleep_perf" in fieldnames
    assert "sleep_sleep_perf" not in fieldnames
    assert "recovery" in fieldnames  # no-sueño, sin prefijo
    assert "date" in fieldnames  # nunca se prefija aunque esté en _SLEEP_FIELDS-adyacente


def test_export_csv_none_values_become_empty_string(tmp_path, api_client):
    _write_dataset(tmp_path, [{"date": "2026-06-20", "recovery": None, "hrv": 55}])
    resp = api_client.get("/api/export?fmt=csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    row = next(reader)
    assert row["recovery"] == ""
    assert row["hrv"] == "55"


def test_export_csv_formula_injection_neutralized(tmp_path, api_client):
    """Un valor de texto que empiece con =+-@ se neutraliza con prefijo ' para
    que Excel/Sheets no lo interprete como fórmula."""
    _write_dataset(
        tmp_path,
        [{"date": "2026-06-20", "recovery": 60}],
        exercises=[{"date": "2026-06-20", "name": "=cmd|'/c calc'!A1", "dur_min": 30}],
    )
    # exercises está fuera del CSV v1 (documentado) -> probamos el guard directo
    from main import _csv_safe
    assert _csv_safe("=cmd|'/c calc'!A1") == "'=cmd|'/c calc'!A1"
    assert _csv_safe("+1+1") == "'+1+1"
    assert _csv_safe("-1") == "'-1"
    assert _csv_safe("@SUM(A1)") == "'@SUM(A1)"
    assert _csv_safe("normal text") == "normal text"
    assert _csv_safe(60) == 60
    assert _csv_safe(None) is None


def test_export_csv_exercises_excluded_v1(tmp_path, api_client):
    """exercises queda fuera del CSV v1 (documentado en el roadmap) — el CSV solo
    trae columnas derivadas de `days`."""
    _write_dataset(
        tmp_path,
        [{"date": "2026-06-20", "recovery": 60}],
        exercises=[{"date": "2026-06-20", "name": "Running", "dur_min": 30}],
    )
    resp = api_client.get("/api/export?fmt=csv")
    reader = csv.DictReader(io.StringIO(resp.text))
    assert "name" not in (reader.fieldnames or [])
    assert "dur_min" not in (reader.fieldnames or [])


# ── F2 roadmap P0: hipnograma — segments en JSON sí, en CSV no ───────────────

_SEGS = [
    {"s": 0, "e": 80, "st": "deep"},
    {"s": 80, "e": 200, "st": "light"},
    {"s": 200, "e": 210, "st": "awake"},
    {"s": 210, "e": 400, "st": "rem"},
]


def test_export_json_includes_segments(tmp_path, api_client):
    """fmt=json incluye `segments` tal cual (el JSON es el dataset completo)."""
    days = [
        {"date": "2026-06-28", "asleep": 400, "recovery": 70, "segments": _SEGS},
        {"date": "2026-06-29", "asleep": 380, "recovery": 65},
    ]
    _write_dataset(tmp_path, days)
    resp = api_client.get("/api/export?fmt=json")
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["days"][0]["segments"] == _SEGS
    assert "segments" not in data["days"][1]


def test_export_csv_excludes_segments(tmp_path, api_client):
    """fmt=csv EXCLUYE segments (una lista como celda rompe el CSV plano):
    ni columna 'segments' ni 'sleep_segments', y el resto de columnas intactas."""
    days = [
        {"date": "2026-06-28", "asleep": 400, "recovery": 70, "segments": _SEGS},
        {"date": "2026-06-29", "asleep": 380, "recovery": 65},
    ]
    _write_dataset(tmp_path, days)
    resp = api_client.get("/api/export?fmt=csv")
    assert resp.status_code == 200
    reader = csv.reader(io.StringIO(resp.content.decode("utf-8")))
    rows = list(reader)
    header = rows[0]
    assert "segments" not in header
    assert "sleep_segments" not in header
    # Las columnas normales siguen presentes y con datos.
    assert "sleep_asleep" in header
    assert "recovery" in header
    asleep_idx = header.index("sleep_asleep")
    assert rows[1][asleep_idx] == "400"
    # Ninguna celda contiene la lista serializada.
    flat = resp.content.decode("utf-8")
    assert "'st'" not in flat and '"st"' not in flat
