"""
test_hrv_trail.py — probe de "HRV matutina" (app/hrv_trail.py).

Garantías: (a) registra la HRV por fuente + canónica del último día; (b) es
best-effort — NUNCA lanza (fetched/merged malformados, dir inaccesible); (c) el
log se acota a _MAX_SNAPSHOTS; (d) no toca el motor (no importa scoring).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app import hrv_trail


@pytest.fixture
def datadir(tmp_path):
    from app import config
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        yield tmp_path


_FETCHED = {
    "healthkit":     {"hrv": {"2026-07-20": 65.0, "2026-07-21": 76.5}},
    "google_health": {"hrv": {"2026-07-20": 61.2, "2026-07-21": 70.1}},
}
_MERGED = {"hrv": {"2026-07-20": 65.0, "2026-07-21": 76.5}}   # canónica = healthkit


def test_records_per_source_and_canonical(datadir):
    hrv_trail.record_snapshot(_FETCHED, _MERGED)
    log = json.loads((datadir / "hrv_morning_trail.json").read_text(encoding="utf-8"))
    assert isinstance(log, list) and len(log) == 1
    by_date = log[0]["by_date"]
    assert by_date["2026-07-21"]["canonical"] == 76.5
    assert by_date["2026-07-21"]["healthkit"] == 76.5
    assert by_date["2026-07-21"]["google_health"] == 70.1
    assert "ts" in log[0]


def test_appends_across_calls(datadir):
    hrv_trail.record_snapshot(_FETCHED, _MERGED)
    hrv_trail.record_snapshot(_FETCHED, _MERGED)
    log = json.loads((datadir / "hrv_morning_trail.json").read_text(encoding="utf-8"))
    assert len(log) == 2


def test_never_raises_on_garbage(datadir):
    # Ninguna de estas debe lanzar (best-effort).
    hrv_trail.record_snapshot(None, None)
    hrv_trail.record_snapshot({}, {})
    hrv_trail.record_snapshot({"x": None}, {"hrv": None})
    hrv_trail.record_snapshot("no-dict", "no-dict")
    # merged sin hrv -> no escribe archivo (canon vacío -> return temprano)
    assert not (datadir / "hrv_morning_trail.json").exists()


def test_caps_log_length(datadir):
    with patch.object(hrv_trail, "_MAX_SNAPSHOTS", 3):
        for _ in range(6):
            hrv_trail.record_snapshot(_FETCHED, _MERGED)
    log = json.loads((datadir / "hrv_morning_trail.json").read_text(encoding="utf-8"))
    assert len(log) == 3
