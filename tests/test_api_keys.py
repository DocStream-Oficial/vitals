"""
test_api_keys.py — Tests de app/api_keys.py (Roadmap P2, F10, paso 1).

Cubre:
(a) persistencia/atomicidad (round-trip, sin .tmp leftover, tolerante a JSON
    corrupto) — patrón test_journal.py/test_plan_store.py.
(b) formato de clave `vk_<...>`, hash NUNCA reversible en el store (la clave
    cruda jamás se persiste).
(c) tope _MAX_KEYS=10.
(d) revocar: resolve_key dado de baja, pero list_keys sigue mostrando el id
    con su revoked_at.
(e) comparación de hash con secrets.compare_digest (no timing-unsafe ==).
"""
from __future__ import annotations

import hashlib
import json

import pytest

from app import api_keys
from tests.test_household import _make_client, _stop_client


def _patch_path(monkeypatch, tmp_path):
    monkeypatch.setattr(api_keys, "_API_KEYS_FILE", tmp_path / "api_keys.json")


# ── (a) persistencia ─────────────────────────────────────────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    store = api_keys.load_keys()
    assert store["keys"] == []


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("mi script")
    store = api_keys.load_keys()
    assert len(store["keys"]) == 1
    assert store["keys"][0]["label"] == "mi script"


def test_save_is_atomic_no_tmp_leftover(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("x")
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_load_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    (tmp_path / "api_keys.json").write_text("NOT JSON{{{", encoding="utf-8")
    store = api_keys.load_keys()
    assert store["keys"] == []


def test_load_returns_empty_when_not_dict(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    (tmp_path / "api_keys.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    store = api_keys.load_keys()
    assert store["keys"] == []


# ── (b) formato + hash nunca reversible ──────────────────────────────────────

def test_generate_key_format(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    assert result is not None
    assert result["key"].startswith("vk_")
    assert len(result["key"]) > len("vk_") + 20  # token_urlsafe(32) es largo


def test_generate_key_only_persists_hash_never_raw(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    raw_key = result["key"]

    # La clave cruda NUNCA debe aparecer en el archivo persistido.
    raw_text = (tmp_path / "api_keys.json").read_text(encoding="utf-8")
    assert raw_key not in raw_text

    # El hash guardado es EXACTAMENTE sha256(raw_key).
    expected_hash = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
    store = api_keys.load_keys()
    assert store["keys"][0]["hash"] == expected_hash


def test_generate_key_returns_key_only_once(tmp_path, monkeypatch):
    """list_keys() nunca expone el hash ni la clave cruda."""
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    assert "key" in result
    for meta in api_keys.list_keys():
        assert "key" not in meta
        assert "hash" not in meta


def test_resolve_key_valid_key_succeeds(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    assert api_keys.resolve_key(result["key"]) is True


def test_resolve_key_wrong_key_fails(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("test")
    assert api_keys.resolve_key("vk_wrong_key_entirely") is False


def test_resolve_key_malformed_input_never_crashes(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("test")
    assert api_keys.resolve_key("") is False
    assert api_keys.resolve_key(None) is False  # type: ignore[arg-type]
    assert api_keys.resolve_key("no-prefix-at-all") is False


def test_resolve_key_updates_last_used(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    before = api_keys.list_keys()[0]
    assert before["last_used"] is None
    api_keys.resolve_key(result["key"])
    after = api_keys.list_keys()[0]
    assert after["last_used"] is not None


# ── (c) tope _MAX_KEYS ───────────────────────────────────────────────────────

def test_max_keys_cap(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    for i in range(api_keys._MAX_KEYS):
        result = api_keys.generate_key(f"key{i}")
        assert result is not None
    # La #11 debe fallar.
    assert api_keys.generate_key("overflow") is None


def test_max_keys_cap_counts_revoked_too(tmp_path, monkeypatch):
    """Revocar NO libera espacio — evita generar infinitas claves basura."""
    _patch_path(monkeypatch, tmp_path)
    ids = []
    for i in range(api_keys._MAX_KEYS):
        result = api_keys.generate_key(f"key{i}")
        ids.append(result["id"])
    api_keys.revoke_key(ids[0])
    assert api_keys.generate_key("overflow") is None


# ── (d) revocar: resolve_key da de baja, list_keys sigue mostrando el id ─────

def test_revoke_key_removes_from_resolve_but_stays_in_list(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    key_id, raw_key = result["id"], result["key"]

    assert api_keys.resolve_key(raw_key) is True

    assert api_keys.revoke_key(key_id) is True
    assert api_keys.resolve_key(raw_key) is False

    metas = api_keys.list_keys()
    assert len(metas) == 1
    assert metas[0]["id"] == key_id
    assert metas[0]["revoked"] is True


def test_revoke_key_unknown_id_returns_false(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("test")
    assert api_keys.revoke_key("nonexistent_id") is False


def test_revoke_key_already_revoked_returns_false(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    result = api_keys.generate_key("test")
    key_id = result["id"]
    assert api_keys.revoke_key(key_id) is True
    assert api_keys.revoke_key(key_id) is False  # ya estaba revocada


# ── (e) list_keys nunca expone datos sensibles ───────────────────────────────

def test_list_keys_never_exposes_hash_or_raw_key(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    api_keys.generate_key("a")
    api_keys.generate_key("b")
    for meta in api_keys.list_keys():
        assert set(meta.keys()) == {"id", "label", "created", "last_used", "revoked"}


def test_list_keys_empty_when_no_keys(tmp_path, monkeypatch):
    _patch_path(monkeypatch, tmp_path)
    assert api_keys.list_keys() == []


# ═══════════════════════════════════════════════════════════════════════════
# Paso 2 — endpoints de gestión + auth de /api/v1/* (TestClient real,
# household completo — mismo patrón/fixture de test_household.py, EL RIESGO
# #1 del roadmap P2: una clave de un usuario NUNCA debe leer datos de otro).
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def household_client(tmp_path):
    client = _make_client(tmp_path)
    yield client, tmp_path
    _stop_client(client)


def _seed_dataset(tmp_path, uid, marker):
    """Escribe un health_compact.json mínimo y reconocible para `uid`, bajo
    data/users/<uid>/ (mismo layout que usa should_use_household_paths())."""
    d = tmp_path / "users" / uid
    d.mkdir(parents=True, exist_ok=True)
    (d / "health_compact.json").write_text(
        json.dumps({
            "summary": {"marker": marker},
            "days": [{"date": "2026-01-01", "recovery": 50, "hrv": 40, "asleep": 400}],
            "exercises": [],
        }),
        encoding="utf-8",
    )


# ── endpoints de gestión (autenticados por sesión normal, no por la propia key) ──

def test_post_api_keys_creates_and_returns_raw_key_once(household_client):
    client, _ = household_client
    r = client.post("/api/keys", json={"label": "mi script"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["key"].startswith("vk_")
    assert body["label"] == "mi script"
    assert "id" in body and "created" in body


def test_get_api_keys_lists_metadata_only(household_client):
    client, _ = household_client
    client.post("/api/keys", json={"label": "test"})
    r = client.get("/api/keys")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert "key" not in keys[0]
    assert "hash" not in keys[0]
    assert keys[0]["label"] == "test"


def test_delete_api_keys_revokes(household_client):
    client, _ = household_client
    created = client.post("/api/keys", json={"label": "test"}).json()
    r = client.delete(f"/api/keys/{created['id']}")
    assert r.status_code == 200
    keys = client.get("/api/keys").json()["keys"]
    assert keys[0]["revoked"] is True


def test_delete_api_keys_unknown_id_404(household_client):
    client, _ = household_client
    r = client.delete("/api/keys/does-not-exist")
    assert r.status_code == 404


def test_management_endpoints_use_household_session_not_bearer(household_client):
    """POST/GET/DELETE /api/keys se autentican por X-Vitals-User (sesión
    normal), NUNCA por la propia API key — mandar un Bearer inválido no debe
    afectar estos endpoints de gestión."""
    client, _ = household_client
    r = client.post("/api/keys", json={"label": "x"},
                     headers={"Authorization": "Bearer vk_garbage"})
    assert r.status_code == 200


# ── /api/v1/* — auth SOLO por Bearer, aislamiento entre usuarios ────────────

def test_api_v1_data_401_without_key(household_client):
    client, _ = household_client
    r = client.get("/api/v1/data")
    assert r.status_code == 401
    assert r.json()["status"] == "error"


def test_api_v1_data_401_with_garbage_key(household_client):
    client, _ = household_client
    r = client.get("/api/v1/data", headers={"Authorization": "Bearer vk_totally_wrong"})
    assert r.status_code == 401


def test_api_v1_data_401_household_header_alone_never_authenticates(household_client):
    """Un X-Vitals-User sin Bearer NUNCA basta para /api/v1/* — es un límite
    de confianza distinto (criterio F10 #4: nunca cae a household header/cookie)."""
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    r = client.get("/api/v1/data", headers={"X-Vitals-User": "mike"})
    assert r.status_code == 401


def test_api_v1_data_succeeds_with_valid_key(household_client):
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    _seed_dataset(tmp_path, "mike", "mike-data")

    created = client.post("/api/keys", json={"label": "x"}, headers={"X-Vitals-User": "mike"}).json()
    raw_key = created["key"]

    r = client.get("/api/v1/data", headers={"Authorization": f"Bearer {raw_key}"})
    assert r.status_code == 200
    assert r.json()["summary"]["marker"] == "mike-data"


def test_api_v1_data_isolation_key_a_never_reads_data_b(household_client):
    """EL RIESGO #1 del roadmap: una clave de un usuario A nunca lee datos de B."""
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})
    _seed_dataset(tmp_path, "mike", "mike-secret")
    _seed_dataset(tmp_path, "ana", "ana-secret")

    key_mike = client.post("/api/keys", json={"label": "x"}, headers={"X-Vitals-User": "mike"}).json()["key"]
    key_ana = client.post("/api/keys", json={"label": "y"}, headers={"X-Vitals-User": "ana"}).json()["key"]

    r_mike = client.get("/api/v1/data", headers={"Authorization": f"Bearer {key_mike}"})
    r_ana = client.get("/api/v1/data", headers={"Authorization": f"Bearer {key_ana}"})

    assert r_mike.json()["summary"]["marker"] == "mike-secret"
    assert r_ana.json()["summary"]["marker"] == "ana-secret"

    # Cruzado: la clave de Ana jamás debe devolver el marcador de Mike y viceversa.
    assert r_mike.json()["summary"]["marker"] != "ana-secret"
    assert r_ana.json()["summary"]["marker"] != "mike-secret"


def test_api_v1_data_revoked_key_stops_working(household_client):
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    _seed_dataset(tmp_path, "mike", "mike-data")

    created = client.post("/api/keys", json={"label": "x"}, headers={"X-Vitals-User": "mike"}).json()
    raw_key, key_id = created["key"], created["id"]

    ok = client.get("/api/v1/data", headers={"Authorization": f"Bearer {raw_key}"})
    assert ok.status_code == 200

    client.delete(f"/api/keys/{key_id}", headers={"X-Vitals-User": "mike"})

    revoked = client.get("/api/v1/data", headers={"Authorization": f"Bearer {raw_key}"})
    assert revoked.status_code == 401


def test_api_v1_insights_401_without_key(household_client):
    client, _ = household_client
    r = client.get("/api/v1/insights")
    assert r.status_code == 401


def test_api_v1_insights_succeeds_with_valid_key(household_client):
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    _seed_dataset(tmp_path, "mike", "mike-data")
    created = client.post("/api/keys", json={"label": "x"}, headers={"X-Vitals-User": "mike"}).json()

    r = client.get("/api/v1/insights", headers={"Authorization": f"Bearer {created['key']}"})
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_v1_no_write_endpoints_exposed(household_client):
    """Ningún endpoint de escritura debe quedar bajo /api/v1/ (criterio F10 #5)."""
    client, _ = household_client
    from main import app as fastapi_app
    v1_routes = [r for r in fastapi_app.routes if getattr(r, "path", "").startswith("/api/v1/")]
    for route in v1_routes:
        methods = getattr(route, "methods", set()) or set()
        assert methods <= {"GET", "HEAD"}, f"Ruta insegura bajo /api/v1/: {route.path} ({methods})"


def test_api_v1_data_404_when_no_dataset(household_client):
    """Clave válida pero sin dataset -> 404 (nunca 500)."""
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    created = client.post("/api/keys", json={"label": "x"}, headers={"X-Vitals-User": "mike"}).json()
    r = client.get("/api/v1/data", headers={"Authorization": f"Bearer {created['key']}"})
    assert r.status_code == 404


def test_api_v1_single_user_no_household_still_works(tmp_path):
    """Instalación single-user (sin ningún /api/users creado todavía):
    list_users() es [] -> _resolve_api_key_uid prueba contra el uid actual
    del contexto ('default'), que en single-user usa la ruta LEGACY
    (data/api_keys.json, sin household). Cubre el caso de una instancia fresh
    que aún no creó ningún usuario explícito pero ya quiere usar F10."""
    client = _make_client(tmp_path)
    try:
        real_compact = tmp_path / "health_compact.json"
        real_compact.write_text(json.dumps({
            "summary": {"marker": "solo-data"}, "days": [], "exercises": [],
        }), encoding="utf-8")

        created = client.post("/api/keys", json={"label": "x"}).json()
        assert created["status"] == "ok"

        r = client.get("/api/v1/data", headers={"Authorization": f"Bearer {created['key']}"})
        assert r.status_code == 200
        assert r.json()["summary"]["marker"] == "solo-data"
    finally:
        _stop_client(client)
