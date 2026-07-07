"""
test_ecg.py — Roadmap ECG, Paso 1 (backend): POST/GET /api/ecg, GET /api/ecg/{uuid},
storage aislado en app/ecg_store.py.

Cobertura (contrato del roadmap):
  - ingest idempotente por UUID (re-POST no duplica ni corrompe)
  - listado ligero (sin voltajes) ordenado por fecha desc
  - get por uuid (meta + voltages completos)
  - 404 en uuid inexistente
  - auth con/sin token (mismo patrón que /api/ingest)
  - JSON corrupto / payload no-dict -> controlado (nunca 500)
  - voltajes fuera de rango / NaN -> saneados, no rompen el guardado
  - aislamiento: 'ecg'/'voltages' no llegan a health_compact.json ni al dataset
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ecg_synthetic.json"


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


_DEFAULT_TEST_TOKEN = "s3cret-default"


@pytest.fixture
def client(tmp_path):
    """TestClient con DATA_DIR en tmp_path — nunca toca data/ecg/ real del usuario.

    Fase 8C (paso C6): INGEST_TOKEN es SIEMPRE obligatorio ahora (401 sin
    token válido) — esta fixture fija un token de prueba y lo manda
    automáticamente en cada request (headers del TestClient), para que los
    tests que NO están probando específicamente el flujo de auth (la mayoría
    de este archivo) sigan pasando sin tocar cada client.post(...) uno por
    uno. El flujo de auth en sí se cubre con `client_with_token` (token
    DISTINTO al de esta fixture) y los tests dedicados más abajo."""
    from app import config
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(config.settings, "INGEST_TOKEN", _DEFAULT_TEST_TOKEN), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        yield TestClient(
            main_mod.app, raise_server_exceptions=True,
            headers={"X-Vitals-Token": _DEFAULT_TEST_TOKEN},
        )


@pytest.fixture
def client_with_token(tmp_path):
    """Igual que `client` pero con INGEST_TOKEN configurado -> /api/ecg exige auth."""
    from app import config
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(config.settings, "INGEST_TOKEN", "s3cret-ecg"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        yield TestClient(main_mod.app, raise_server_exceptions=True)


# ── ecg_store (unit, sin HTTP) ───────────────────────────────────────────────

def test_store_save_and_list_and_get(tmp_path):
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        payload = _load_fixture()
        result = ecg_store.save_ecg(payload)
        assert result["status"] == "ok"
        assert result["uuid"] == payload["uuid"]

        listing = ecg_store.list_ecg()
        assert len(listing) == 1
        assert listing[0]["uuid"] == payload["uuid"]
        assert "voltages" not in listing[0]  # listado LIGERO

        full = ecg_store.get_ecg(payload["uuid"])
        assert full is not None
        assert full["voltages"] == payload["voltages"]
        assert full["classification"] == "sinusRhythm"


def test_store_meta_files_dont_contain_voltages_key(tmp_path):
    """El archivo .json de meta NUNCA debe tener la clave 'voltages' adentro."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        payload = _load_fixture()
        ecg_store.save_ecg(payload)
        meta_path = tmp_path / "ecg" / f"{payload['uuid']}.json"
        meta_raw = json.loads(meta_path.read_text(encoding="utf-8"))
        assert "voltages" not in meta_raw


def test_store_idempotent_by_uuid(tmp_path):
    """Re-guardar el mismo uuid sobreescribe, no duplica ni corrompe."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        payload = _load_fixture()
        ecg_store.save_ecg(payload)
        ecg_store.save_ecg(payload)
        ecg_store.save_ecg(payload)

        listing = ecg_store.list_ecg()
        assert len(listing) == 1  # no duplica

        # Cambiar la clasificación y re-guardar -> refleja el nuevo valor.
        updated = dict(payload)
        updated["classification"] = "atrialFibrillation"
        ecg_store.save_ecg(updated)
        listing2 = ecg_store.list_ecg()
        assert len(listing2) == 1
        assert listing2[0]["classification"] == "atrialFibrillation"


def test_store_no_tmp_files_left(tmp_path):
    """Escritura atómica: no debe quedar ningún .tmp residual tras guardar."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        ecg_store.save_ecg(_load_fixture())
        leftovers = list((tmp_path / "ecg").glob("*.tmp"))
        assert leftovers == []


def test_store_missing_uuid_rejected(tmp_path):
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        result = ecg_store.save_ecg({"date": "2026-07-01", "voltages": [1.0, 2.0]})
        assert result["status"] == "error"
        assert ecg_store.list_ecg() == []


def test_store_unsafe_uuid_rejected(tmp_path):
    """uuid con path traversal / separadores se rechaza (guard de seguridad de archivo)."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        result = ecg_store.save_ecg({"uuid": "../../etc/passwd", "voltages": []})
        assert result["status"] == "error"
        result2 = ecg_store.save_ecg({"uuid": "a/b", "voltages": []})
        assert result2["status"] == "error"


def test_store_get_nonexistent_uuid_returns_none(tmp_path):
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        assert ecg_store.get_ecg("no-existe") is None


def test_store_nan_and_inf_voltages_sanitized(tmp_path):
    """Voltajes con NaN/Inf/None/strings no numéricos se sanean a 0.0, no rompen el guardado."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        payload = {
            "uuid": "WEIRD-VOLTAGES",
            "date": "2026-07-01",
            "voltages": [1.0, float("nan"), float("inf"), float("-inf"), None, "abc", 2.5],
        }
        result = ecg_store.save_ecg(payload)
        assert result["status"] == "ok"
        full = ecg_store.get_ecg("WEIRD-VOLTAGES")
        assert full["voltages"] == [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.5]
        assert all(math.isfinite(v) for v in full["voltages"])


def test_store_empty_voltages_ok():
    """Payload sin voltajes -> lista vacía, no error (lectura 'sin dato' es válida)."""
    import tempfile
    from app import config, ecg_store
    with tempfile.TemporaryDirectory() as td:
        with patch.object(config.settings, "DATA_DIR", Path(td)):
            result = ecg_store.save_ecg({"uuid": "NO-VOLTAGES", "date": "2026-07-01"})
            assert result["status"] == "ok"
            full = ecg_store.get_ecg("NO-VOLTAGES")
            assert full["voltages"] == []


def test_store_list_sorted_by_date_desc(tmp_path):
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        ecg_store.save_ecg({"uuid": "OLD", "date": "2026-01-01", "voltages": []})
        ecg_store.save_ecg({"uuid": "NEW", "date": "2026-06-01", "voltages": []})
        ecg_store.save_ecg({"uuid": "MID", "date": "2026-03-01", "voltages": []})
        listing = ecg_store.list_ecg()
        assert [m["uuid"] for m in listing] == ["NEW", "MID", "OLD"]


def test_store_corrupt_meta_file_skipped(tmp_path):
    """Un archivo de meta corrupto se omite en el listado, no tumba list_ecg()."""
    from app import config, ecg_store
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        ecg_store.save_ecg({"uuid": "GOOD", "date": "2026-01-01", "voltages": []})
        ecg_dir = tmp_path / "ecg"
        (ecg_dir / "BROKEN.json").write_text("{not valid json", encoding="utf-8")
        listing = ecg_store.list_ecg()
        assert len(listing) == 1
        assert listing[0]["uuid"] == "GOOD"


# ── HTTP: POST /api/ecg ──────────────────────────────────────────────────────

def test_post_ecg_ok_with_valid_token(client):
    """Fase 8C (paso C6): ya no existe el modo 'sin token configurado' — la
    fixture `client` fija un token de prueba y lo manda automáticamente."""
    payload = _load_fixture()
    resp = client.post("/api/ecg", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_post_ecg_401_when_ingest_token_empty(tmp_path):
    """Fase 8C (paso C6): INGEST_TOKEN vacío en el server -> 401 SIEMPRE, ya
    NO existe el modo permisivo 'sin auth' de fases anteriores."""
    from app import config
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(config.settings, "INGEST_TOKEN", ""), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        bare_client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = bare_client.post("/api/ecg", json=_load_fixture())
    assert resp.status_code == 401
    assert resp.json()["status"] == "unauthorized"


def test_post_ecg_idempotent_via_http(client):
    payload = _load_fixture()
    r1 = client.post("/api/ecg", json=payload)
    r2 = client.post("/api/ecg", json=payload)
    assert r1.status_code == 200 and r2.status_code == 200
    listing = client.get("/api/ecg").json()
    assert len(listing) == 1


def test_post_ecg_invalid_json_controlled(client):
    resp = client.post(
        "/api/ecg", content=b"{not valid json", headers={"content-type": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_post_ecg_non_dict_payload_controlled(client):
    resp = client.post("/api/ecg", json=[1, 2, 3])
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_post_ecg_missing_uuid_controlled(client):
    resp = client.post("/api/ecg", json={"date": "2026-07-01"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_post_ecg_requires_token_when_configured(client_with_token):
    payload = _load_fixture()
    resp = client_with_token.post("/api/ecg", json=payload)  # sin header
    assert resp.status_code == 401


def test_post_ecg_wrong_token_rejected(client_with_token):
    payload = _load_fixture()
    resp = client_with_token.post(
        "/api/ecg", json=payload, headers={"X-Vitals-Token": "wrong"}
    )
    assert resp.status_code == 401


def test_post_ecg_correct_token_accepted(client_with_token):
    payload = _load_fixture()
    resp = client_with_token.post(
        "/api/ecg", json=payload, headers={"X-Vitals-Token": "s3cret-ecg"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ── HTTP: GET /api/ecg (listado) ─────────────────────────────────────────────

def test_get_ecg_list_empty(client):
    resp = client.get("/api/ecg")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_ecg_list_no_voltages_key(client):
    client.post("/api/ecg", json=_load_fixture())
    resp = client.get("/api/ecg")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "voltages" not in data[0]
    assert data[0]["uuid"] == "SYNTH-ECG-0001"


# ── HTTP: GET /api/ecg/{uuid} ────────────────────────────────────────────────

def test_get_ecg_by_uuid_full(client):
    payload = _load_fixture()
    client.post("/api/ecg", json=payload)
    resp = client.get(f"/api/ecg/{payload['uuid']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["voltages"] == payload["voltages"]
    assert data["classification"] == "sinusRhythm"
    assert data["avg_hr"] == 62


def test_get_ecg_by_uuid_404(client):
    resp = client.get("/api/ecg/does-not-exist")
    assert resp.status_code == 404


def test_get_ecg_by_uuid_path_traversal_404(client):
    """Un uuid con separadores de ruta nunca debe escapar data/ecg/ — 404 controlado."""
    resp = client.get("/api/ecg/..%2F..%2Fetc%2Fpasswd")
    assert resp.status_code in (404, 400)


# ── Tamaño: un ECG de 15k puntos entra y sale bien ──────────────────────────

def test_large_ecg_15k_points_roundtrip(client):
    voltages = [float(i % 100) for i in range(15_000)]
    payload = {
        "uuid": "BIG-ECG",
        "date": "2026-07-01",
        "classification": "sinusRhythm",
        "avg_hr": 58,
        "sampling_frequency": 512,
        "sample_count": 15000,
        "symptoms_status": "none",
        "voltages": voltages,
    }
    resp = client.post("/api/ecg", json=payload)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    full = client.get("/api/ecg/BIG-ECG").json()
    assert len(full["voltages"]) == 15_000

    # el listado sigue siendo ligero incluso con una lectura grande guardada
    listing = client.get("/api/ecg").json()
    assert "voltages" not in listing[0]


# ── AISLAMIENTO DEL MOTOR: el ECG nunca debe tocar el dataset/coach ─────────

def test_ecg_post_does_not_touch_health_compact(client, tmp_path):
    """POST /api/ecg NO debe crear ni modificar health_compact.json."""
    compact_path = tmp_path / "health_compact.json"
    assert not compact_path.exists()
    client.post("/api/ecg", json=_load_fixture())
    assert not compact_path.exists()


def test_ecg_store_module_has_no_engine_imports():
    """Grep estático de líneas import/from: ecg_store.py no importa scoring/bodyage/
    merge/coach_chat/mcp_tools/load (solo se permiten menciones en docstrings/comentarios,
    que documentan a propósito el aislamiento)."""
    src_path = Path(__file__).parent.parent / "app" / "ecg_store.py"
    forbidden = ("scoring", "bodyage", "merge", "coach_chat", "mcp_tools", "build_dataset")
    import_lines = [
        line for line in src_path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    for line in import_lines:
        for name in forbidden:
            assert name not in line, f"ecg_store.py importa '{name}' en: {line}"
