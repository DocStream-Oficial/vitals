"""
test_healthkit_hrv_sleep.py — Fase 1 de "HRV de sueño" (roadmap
dev-harness/sleep-hrv-fase1).

El plugin iOS ahora manda un campo ADITIVO `hrv_sleep: [{date, value, n}]` en el
payload. Esta fase es SOLO captura + medición: el motor NO lo consume todavía.
Estos tests fijan las tres garantías de "riesgo cero al motor":

  (a) `ingest()` persiste el payload CRUDO íntegro, incluido `hrv_sleep`.
  (b) `_normalize` IGNORA `hrv_sleep` — el dict de 13 claves es idéntico con y
      sin el campo (no llega a build_dataset).
  (c) `build_dataset` sobre el dict normalizado produce days/summary idénticos
      con y sin `hrv_sleep` en el payload de origen (recovery/bodyage intactos).
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from app.sources.healthkit import HealthKitSource
from app.scoring import build_dataset


# Payload mínimo con datos suficientes para que build_dataset produzca recovery,
# + el campo aditivo hrv_sleep con su conteo de muestras n.
_BASE_PAYLOAD = {
    "hrv":       [{"date": "2026-06-26", "value": 55.0},
                  {"date": "2026-06-27", "value": 58.0},
                  {"date": "2026-06-28", "value": 61.0}],
    "rhr":       [{"date": "2026-06-26", "value": 53},
                  {"date": "2026-06-27", "value": 52},
                  {"date": "2026-06-28", "value": 51}],
    "sleep": [
        {"date": "2026-06-26", "asleep": 400, "deep": 60, "rem": 90, "light": 250,
         "eff": 92, "bedtime": "00:30", "waketime": "07:10", "inbed": 430},
        {"date": "2026-06-27", "asleep": 410, "deep": 62, "rem": 92, "light": 256,
         "eff": 93, "bedtime": "00:20", "waketime": "07:12", "inbed": 440},
        {"date": "2026-06-28", "asleep": 420, "deep": 64, "rem": 94, "light": 262,
         "eff": 94, "bedtime": "00:10", "waketime": "07:14", "inbed": 448},
    ],
}

_HRV_SLEEP = [
    {"date": "2026-06-26", "value": 72.3, "n": 8},
    {"date": "2026-06-27", "value": 75.1, "n": 11},
    {"date": "2026-06-28", "value": 70.9, "n": 2},   # noche de baja densidad
]


@pytest.fixture
def hk_datadir(tmp_path):
    """settings.DATA_DIR -> tmp_path para no tocar data/ real (mismo patrón que
    tests/test_healthkit.py)."""
    from app import config
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        yield tmp_path


def test_raw_payload_persists_hrv_sleep_verbatim(hk_datadir):
    """(a) El payload crudo guardado incluye hrv_sleep TAL CUAL (con n)."""
    src = HealthKitSource()
    payload = dict(_BASE_PAYLOAD)
    payload["hrv_sleep"] = _HRV_SLEEP
    src.ingest(payload)

    raw = json.loads((hk_datadir / "healthkit_ingest.json").read_text(encoding="utf-8"))
    assert raw.get("hrv_sleep") == _HRV_SLEEP
    # y no contaminó la HRV diaria
    assert raw.get("hrv") == _BASE_PAYLOAD["hrv"]


def test_normalize_ignores_hrv_sleep(hk_datadir):
    """(b) _normalize produce el MISMO dict de 13 claves con y sin hrv_sleep."""
    src = HealthKitSource()
    without = src._normalize(dict(_BASE_PAYLOAD))
    with_field = src._normalize({**_BASE_PAYLOAD, "hrv_sleep": _HRV_SLEEP})
    assert without == with_field
    # hrv_sleep no aparece como clave del dict normalizado
    assert "hrv_sleep" not in with_field


def test_build_dataset_identical_with_and_without_hrv_sleep(hk_datadir):
    """(c) El motor no ve hrv_sleep: days/summary byte-idénticos."""
    src = HealthKitSource()
    data_without = src._normalize(dict(_BASE_PAYLOAD))
    data_with = src._normalize({**_BASE_PAYLOAD, "hrv_sleep": _HRV_SLEEP})

    ds_without = build_dataset(**data_without)
    ds_with = build_dataset(**data_with)

    assert ds_without["days"] == ds_with["days"]
    assert ds_without["summary"] == ds_with["summary"]


def test_ingest_without_hrv_sleep_unchanged(hk_datadir):
    """Compat: un payload SIN hrv_sleep (app vieja / pre-rebuild) se comporta
    exactamente igual — el crudo no gana la clave, el normalizado es el de siempre."""
    src = HealthKitSource()
    src.ingest(dict(_BASE_PAYLOAD))
    raw = json.loads((hk_datadir / "healthkit_ingest.json").read_text(encoding="utf-8"))
    assert "hrv_sleep" not in raw
