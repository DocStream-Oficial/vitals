"""
test_labs.py — Tests de app/labs.py (Fase 8D, paso D1).

Cubre:
(a) persistencia/atomicidad (round-trip, sin .tmp leftover, tolerante a JSON
    corrupto) — patrón test_journal.py/test_cycle.py.
(b) CRUD: add_entry, delete_entry, series_by_marker, latest_by_marker.
(c) rangos de referencia por sexo (ref_range, is_out_of_range).
(d) import_csv: caso feliz (con y sin header), filas malformadas reportadas
    sin abortar el import completo.
(e) contexto del coach (coach_context_lines).
(f) endpoints FastAPI vía TestClient: GET/POST/DELETE /api/labs, import.
"""
from __future__ import annotations

import json

import pytest

from app import labs


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_labs_log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(labs, "_LABS_LOG_FILE", tmp_path / "labs_log.json")


# ── (a) persistencia: load/save round-trip + atomicidad ──────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    log = labs.load_labs()
    assert log["entries"] == []


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    data = {"entries": [{"id": "abc", "date": "2026-01-01", "marker": "glucose", "value": 90}]}
    labs.save_labs(data)
    loaded = labs.load_labs()
    assert loaded["entries"] == data["entries"]
    assert "updated" in loaded


def test_save_is_atomic_no_tmp_leftover(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.save_labs({"entries": []})
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    (tmp_path / "labs_log.json").write_text("NOT JSON{{{", encoding="utf-8")
    log = labs.load_labs()
    assert log["entries"] == []


def test_load_returns_empty_when_not_dict(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    (tmp_path / "labs_log.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    log = labs.load_labs()
    assert log["entries"] == []


def test_load_tolerant_to_partial_old_log(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    (tmp_path / "labs_log.json").write_text(json.dumps({"entries": []}), encoding="utf-8")
    log = labs.load_labs()
    assert log["entries"] == []
    assert log["updated"] is None


# ── (b) CRUD ─────────────────────────────────────────────────────────────────

def test_add_entry_then_series_by_marker(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("2026-01-01", "glucose", 95, sex="M")
    assert e is not None
    assert e["marker"] == "glucose"
    assert e["value"] == 95.0
    assert e["unit"] == "mg/dL"
    series = labs.series_by_marker()
    assert "glucose" in series
    assert len(series["glucose"]) == 1


def test_add_entry_rejects_unknown_marker(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("2026-01-01", "not_a_marker", 10)
    assert e is None


def test_add_entry_rejects_bad_date(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("not-a-date", "glucose", 90)
    assert e is None


def test_add_entry_rejects_non_numeric_value(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("2026-01-01", "glucose", "not-a-number")
    assert e is None


def test_series_by_marker_sorted_by_date(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.add_entry("2026-03-01", "glucose", 100)
    labs.add_entry("2026-01-01", "glucose", 90)
    labs.add_entry("2026-02-01", "glucose", 95)
    series = labs.series_by_marker()
    dates = [e["date"] for e in series["glucose"]]
    assert dates == ["2026-01-01", "2026-02-01", "2026-03-01"]


def test_delete_entry_removes_by_id(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("2026-01-01", "glucose", 90)
    assert labs.delete_entry(e["id"]) is True
    series = labs.series_by_marker()
    assert series.get("glucose", []) == []


def test_delete_entry_idempotent_for_unknown_id(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    assert labs.delete_entry("does-not-exist") is True


def test_latest_by_marker_returns_most_recent(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.add_entry("2026-01-01", "glucose", 90)
    labs.add_entry("2026-06-01", "glucose", 110)
    latest = labs.latest_by_marker()
    assert latest["glucose"]["value"] == 110.0
    assert latest["glucose"]["date"] == "2026-06-01"


# ── (c) rangos de referencia por sexo ────────────────────────────────────────

def test_ref_range_sex_dependent_marker():
    low_m, high_m = labs.ref_range("hdl", sex="M")
    low_f, high_f = labs.ref_range("hdl", sex="F")
    assert low_m == 40
    assert low_f == 50
    assert high_m is None and high_f is None


def test_ref_range_sex_independent_marker_same_for_both():
    m = labs.ref_range("glucose", sex="M")
    f = labs.ref_range("glucose", sex="F")
    assert m == f == (70, 99)


def test_ref_range_unknown_marker_returns_none_none():
    assert labs.ref_range("bogus_marker") == (None, None)


def test_is_out_of_range_detects_high_and_low():
    assert labs.is_out_of_range(250, 0, 200) is True
    assert labs.is_out_of_range(50, 70, 99) is True
    assert labs.is_out_of_range(85, 70, 99) is False


def test_is_out_of_range_none_safe_limits():
    assert labs.is_out_of_range(500, None, None) is False
    assert labs.is_out_of_range("not-a-number", 0, 100) is False


def test_add_entry_persists_ref_resolved_at_capture_time(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    e = labs.add_entry("2026-01-01", "testosterone", 300, sex="M")
    assert e["ref_low"] == 264
    assert e["ref_high"] == 916
    e2 = labs.add_entry("2026-01-01", "testosterone", 30, sex="F")
    assert e2["ref_low"] == 8
    assert e2["ref_high"] == 60


# ── (d) import CSV ────────────────────────────────────────────────────────────

def test_import_csv_happy_path_with_header(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    csv_text = (
        "date,marker,value,unit\n"
        "2026-01-01,glucose,90,mg/dL\n"
        "2026-02-01,hba1c,5.2,%\n"
    )
    result = labs.import_csv(csv_text, sex="M")
    assert len(result["imported"]) == 2
    assert result["rejected"] == []


def test_import_csv_happy_path_no_header(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    csv_text = "2026-01-01,glucose,90\n2026-02-01,ldl,80\n"
    result = labs.import_csv(csv_text)
    assert len(result["imported"]) == 2
    assert result["rejected"] == []


def test_import_csv_rejects_malformed_rows_without_aborting(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    csv_text = (
        "date,marker,value\n"
        "2026-01-01,glucose,90\n"       # ok
        "not-a-date,glucose,90\n"       # fecha inválida
        "2026-01-02,unknown_marker,10\n"  # marcador desconocido
        "2026-01-03,glucose,not-a-number\n"  # valor no numérico
        "2026-01-04,ldl,85\n"           # ok
    )
    result = labs.import_csv(csv_text)
    assert len(result["imported"]) == 2
    assert len(result["rejected"]) == 3
    reasons = " ".join(r["reason"] for r in result["rejected"])
    assert "fecha inválida" in reasons
    assert "marcador desconocido" in reasons
    assert "no numérico" in reasons


def test_import_csv_empty_text_returns_empty_result(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    result = labs.import_csv("")
    assert result == {"imported": [], "rejected": []}


# ── (e) contexto del coach ────────────────────────────────────────────────────

def test_coach_context_lines_empty_without_labs(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    assert labs.coach_context_lines() == []


def test_coach_context_lines_includes_out_of_range_flag(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.add_entry("2026-01-01", "glucose", 150, sex="M")  # fuera de rango (70-99)
    lines = labs.coach_context_lines(locale="es")
    assert len(lines) == 1
    assert "Glucosa" in lines[0]
    assert "⚠" in lines[0]


def test_coach_context_lines_no_flag_when_in_range(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.add_entry("2026-01-01", "glucose", 85, sex="M")
    lines = labs.coach_context_lines(locale="es")
    assert "⚠" not in lines[0]


def test_coach_context_lines_uses_latest_per_marker(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    labs.add_entry("2026-01-01", "glucose", 90)
    labs.add_entry("2026-06-01", "glucose", 200)
    lines = labs.coach_context_lines(locale="es")
    assert len(lines) == 1
    assert "200" in lines[0]


# ── catálogo ───────────────────────────────────────────────────────────────

def test_catalog_has_20_markers_all_locales():
    for locale in ("es", "en", "fr", "pt"):
        cat = labs.catalog(locale=locale, sex="M")
        assert len(cat) == 20
        for m in cat:
            assert m["label"] and not m["label"].startswith("marker_")


# ── (f) endpoints FastAPI ──────────────────────────────────────────────────

@pytest.fixture()
def labs_client(tmp_path, monkeypatch):
    _patch_labs_log_path(monkeypatch, tmp_path)
    import main
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def test_endpoint_get_labs_empty(labs_client):
    r = labs_client.get("/api/labs")
    assert r.status_code == 200
    body = r.json()
    assert "catalog" in body and "series" in body
    assert len(body["catalog"]) == 20


def test_endpoint_post_then_get_labs(labs_client):
    r = labs_client.post("/api/labs", json={"date": "2026-01-01", "marker": "glucose", "value": 92})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    r2 = labs_client.get("/api/labs")
    assert "glucose" in r2.json()["series"]


def test_endpoint_post_labs_rejects_bad_marker(labs_client):
    r = labs_client.post("/api/labs", json={"date": "2026-01-01", "marker": "nope", "value": 1})
    assert r.status_code == 422


def test_endpoint_post_labs_rejects_bad_date(labs_client):
    r = labs_client.post("/api/labs", json={"date": "not-a-date", "marker": "glucose", "value": 1})
    assert r.status_code == 422


def test_endpoint_delete_labs(labs_client):
    r = labs_client.post("/api/labs", json={"date": "2026-01-01", "marker": "glucose", "value": 92})
    entry_id = r.json()["entry"]["id"]
    r2 = labs_client.delete(f"/api/labs/{entry_id}")
    assert r2.status_code == 200
    assert r2.json()["status"] == "ok"


def test_endpoint_import_csv(labs_client):
    csv_text = "date,marker,value\n2026-01-01,glucose,90\n2026-01-02,ldl,80\n"
    r = labs_client.post("/api/labs/import", content=csv_text, headers={"content-type": "text/csv"})
    assert r.status_code == 200
    body = r.json()
    assert len(body["imported"]) == 2
