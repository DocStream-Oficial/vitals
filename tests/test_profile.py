"""
test_profile.py — Tests de app/profile.py

Cubre:
- load/save round-trip
- is_onboarded (True / False / sin archivo)
- current_age (fórmula year - month/day)
- cascada effective(): profile.json → settings(.env) → default
- validación de PUT /api/profile (via TestClient)
- GET /api/profile devuelve perfil efectivo
- backward-compat: sin profile.json, effective() usa settings

NO testea i18n ni unidades (eso es 1B).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _write_profile(tmp_path: Path, data: dict) -> Path:
    """Escribe un profile.json en tmp_path y lo devuelve."""
    p = tmp_path / "profile.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _patch_profile_path(monkeypatch, tmp_path: Path):
    """Apunta _PROFILE_FILE de app.profile a tmp_path/profile.json."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)


# ── load / save round-trip ────────────────────────────────────────────────────

def test_load_returns_none_when_no_file(tmp_path, monkeypatch):
    """load_profile() → None cuando no existe profile.json."""
    _patch_profile_path(monkeypatch, tmp_path)
    from app.profile import load_profile
    assert load_profile() is None


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    """save_profile() → load_profile() devuelve el mismo dict."""
    _patch_profile_path(monkeypatch, tmp_path)
    from app.profile import save_profile, load_profile

    data = {
        "name": "Test Ñoño",
        "email": "test@example.com",
        "birthdate": "1990-05-10",
        "sex": "F",
        "waist_cm": 75.0,
        "height_cm": 165.0,
        "weight_kg": 62.5,
        "locale": "es",
        "units": "metric",
        "onboarded": True,
    }
    save_profile(data)
    loaded = load_profile()
    assert loaded is not None
    assert loaded["name"] == "Test Ñoño"
    assert loaded["email"] == "test@example.com"
    assert loaded["birthdate"] == "1990-05-10"
    assert loaded["sex"] == "F"
    assert loaded["waist_cm"] == 75.0
    assert loaded["onboarded"] is True


def test_save_is_atomic(tmp_path, monkeypatch):
    """save_profile() no deja archivo .tmp tras la escritura."""
    _patch_profile_path(monkeypatch, tmp_path)
    from app.profile import save_profile

    save_profile({"name": "Atomic", "onboarded": False})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == [], f"Quedaron archivos .tmp: {tmp_files}"


def test_load_returns_none_on_corrupt_json(tmp_path, monkeypatch):
    """load_profile() → None si el archivo tiene JSON inválido (nunca lanza)."""
    _patch_profile_path(monkeypatch, tmp_path)
    (tmp_path / "profile.json").write_text("NOT JSON{{{{", encoding="utf-8")
    from app.profile import load_profile
    assert load_profile() is None


# ── is_onboarded ─────────────────────────────────────────────────────────────

def test_is_onboarded_false_when_no_file(tmp_path, monkeypatch):
    _patch_profile_path(monkeypatch, tmp_path)
    from app.profile import is_onboarded
    assert is_onboarded() is False


def test_is_onboarded_false_when_flag_false(tmp_path, monkeypatch):
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"onboarded": False, "name": "X"})
    from app.profile import is_onboarded
    assert is_onboarded() is False


def test_is_onboarded_true_when_flag_true(tmp_path, monkeypatch):
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"onboarded": True, "name": "X"})
    from app.profile import is_onboarded
    assert is_onboarded() is True


# ── current_age ───────────────────────────────────────────────────────────────

def test_current_age_birthday_not_yet_this_year(tmp_path, monkeypatch):
    """Cumpleaños posterior al día de hoy → age = year_diff - 1."""
    _patch_profile_path(monkeypatch, tmp_path)
    today = datetime.date.today()
    # Fecha de nacimiento: mañana del año actual - 30 años (aún no ha cumplido)
    future_this_year = today.replace(year=today.year - 30) + datetime.timedelta(days=1)
    _write_profile(tmp_path, {"birthdate": future_this_year.isoformat(), "onboarded": False})

    from importlib import reload
    from app import profile as _pm
    # Parchar effective() para que devuelva nuestra fecha
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    age = _pm.current_age()
    assert age == 29  # no ha cumplido 30 aún


def test_current_age_birthday_already_this_year(tmp_path, monkeypatch):
    """Cumpleaños ya pasó este año → age = year_diff."""
    _patch_profile_path(monkeypatch, tmp_path)
    today = datetime.date.today()
    # Fecha de nacimiento: ayer del año actual - 30 años (ya cumplió)
    past_this_year = today.replace(year=today.year - 30) - datetime.timedelta(days=1)
    _write_profile(tmp_path, {"birthdate": past_this_year.isoformat(), "onboarded": False})

    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    age = _pm.current_age()
    assert age == 30


def test_current_age_invalid_birthdate_returns_zero(tmp_path, monkeypatch):
    """current_age() con birthdate inválido → 0 (no propaga excepción)."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"birthdate": "not-a-date", "onboarded": False})
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    assert _pm.current_age() == 0


# ── effective() — cascada ─────────────────────────────────────────────────────

def test_effective_reads_from_profile(tmp_path, monkeypatch):
    """effective() devuelve el valor de profile.json cuando existe."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"birthdate": "2000-01-01", "sex": "F", "waist_cm": 70.0, "onboarded": True})
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    assert _pm.effective("birthdate") == "2000-01-01"
    assert _pm.effective("sex") == "F"
    assert _pm.effective("waist_cm") == 70.0


def test_effective_falls_back_to_settings_when_no_profile(tmp_path, monkeypatch):
    """Sin profile.json, effective() usa settings (simula la instancia del usuario con .env)."""
    _patch_profile_path(monkeypatch, tmp_path)
    # No creamos profile.json → load_profile() devuelve None
    from app import profile as _pm, config as _cfg
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_cfg.settings, "BIRTHDATE", "1985-03-15")
    monkeypatch.setattr(_cfg.settings, "WAIST_CM", 90.0)
    monkeypatch.setattr(_cfg.settings, "SEX", "M")
    assert _pm.effective("birthdate") == "1985-03-15"
    assert _pm.effective("waist_cm") == 90.0
    assert _pm.effective("sex") == "M"


def test_effective_falls_back_to_default_when_no_profile_no_env(tmp_path, monkeypatch):
    """Sin profile.json y con settings vacíos, effective() usa el default."""
    _patch_profile_path(monkeypatch, tmp_path)
    from app import profile as _pm, config as _cfg
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_cfg.settings, "BIRTHDATE", "")
    monkeypatch.setattr(_cfg.settings, "WAIST_CM", 82.0)
    monkeypatch.setattr(_cfg.settings, "SEX", "")
    # locale no tiene env → debe devolver default "es"
    assert _pm.effective("locale") == "es"
    assert _pm.effective("units") == "metric"


def test_effective_profile_overrides_settings(tmp_path, monkeypatch):
    """Si profile.json tiene un campo, tiene precedencia sobre settings."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"birthdate": "1995-06-20", "sex": "F", "waist_cm": 68.0, "onboarded": True})
    from app import profile as _pm, config as _cfg
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_cfg.settings, "BIRTHDATE", "1990-01-01")
    # profile tiene prioridad
    assert _pm.effective("birthdate") == "1995-06-20"
    assert _pm.effective("sex") == "F"


# ── GET /api/profile ──────────────────────────────────────────────────────────

def _get_api_client(tmp_path: Path, monkeypatch):
    """Devuelve un TestClient para los tests de endpoints de perfil."""
    real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
    if real_compact.exists():
        (tmp_path / "health_compact.json").write_text(real_compact.read_text())

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


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    yield from _get_api_client(tmp_path, monkeypatch)


def test_get_profile_returns_200_no_file(api_client):
    """GET /api/profile sin profile.json → 200 con defaults (nunca 500)."""
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    # Debe tener los campos clave del schema
    for key in ("name", "email", "birthdate", "sex", "waist_cm", "locale", "units", "onboarded", "age"):
        assert key in data, f"Falta clave '{key}' en GET /api/profile"


def test_get_profile_returns_profile_data(tmp_path, monkeypatch, api_client):
    """GET /api/profile con profile.json → devuelve los datos del archivo."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    _write_profile(tmp_path, {
        "name": "Ana García",
        "birthdate": "1992-08-15",
        "sex": "F",
        "waist_cm": 72.0,
        "locale": "es",
        "units": "metric",
        "onboarded": True,
    })
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Ana García"
    assert data["sex"] == "F"
    assert data["onboarded"] is True


# ── PUT /api/profile — validación ────────────────────────────────────────────

def test_put_profile_valid_saves(api_client):
    """PUT /api/profile con datos válidos → 200, devuelve perfil."""
    payload = {
        "name": "Carlos López",
        "birthdate": "1988-11-20",
        "sex": "M",
        "waist_cm": 85.0,
        "locale": "es",
        "units": "metric",
        "onboarded": True,
    }
    resp = api_client.put("/api/profile", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Carlos López"
    assert data["sex"] == "M"
    assert data["onboarded"] is True


def test_put_profile_invalid_birthdate(api_client):
    """PUT /api/profile con birthdate inválido → 422."""
    resp = api_client.put("/api/profile", json={
        "birthdate": "not-a-date",
        "sex": "M",
        "waist_cm": 80.0,
    })
    assert resp.status_code == 422
    data = resp.json()
    assert "errors" in data or "detail" in data


def test_put_profile_invalid_sex(api_client):
    """PUT /api/profile con sex != M/F → 422."""
    resp = api_client.put("/api/profile", json={"sex": "X"})
    assert resp.status_code == 422


def test_put_profile_negative_waist(api_client):
    """PUT /api/profile con waist_cm <= 0 → 422."""
    resp = api_client.put("/api/profile", json={
        "birthdate": "1990-01-01",
        "sex": "M",
        "waist_cm": -5.0,
    })
    assert resp.status_code == 422


def test_put_profile_invalid_locale(api_client):
    """PUT /api/profile con locale fuera de {es,en,fr,pt} → 422."""
    # 'xx' no está en el whitelist (que en 1B se amplió a es/en/fr/pt)
    resp = api_client.put("/api/profile", json={"locale": "xx"})
    assert resp.status_code == 422


def test_put_profile_invalid_units(api_client):
    """PUT /api/profile con units fuera de {metric,imperial} → 422."""
    resp = api_client.put("/api/profile", json={"units": "pounds"})
    assert resp.status_code == 422


def test_put_profile_partial_update_preserves_existing(tmp_path, monkeypatch, api_client):
    """PUT con campos parciales preserva los campos no enviados del perfil existente."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    _write_profile(tmp_path, {
        "name": "Juan",
        "birthdate": "1985-05-01",
        "sex": "M",
        "waist_cm": 90.0,
        "locale": "es",
        "units": "metric",
        "onboarded": True,
    })
    # Solo actualizar waist_cm
    resp = api_client.put("/api/profile", json={"waist_cm": 88.0})
    assert resp.status_code == 200
    data = resp.json()
    assert data["waist_cm"] == 88.0
    assert data["name"] == "Juan"  # preservado


# ── PUT /api/profile — steps_target (tarjeta de Pasos en Hoy) ───────────────

def test_put_valid_steps_target_saves(api_client):
    """PUT /api/profile con steps_target válido -> 200, se guarda."""
    resp = api_client.put("/api/profile", json={"steps_target": 10000})
    assert resp.status_code == 200
    assert resp.json()["steps_target"] == 10000


def test_put_steps_target_below_1000_rejected(api_client):
    """PUT /api/profile con steps_target < 1000 -> 422 controlado (nunca 500)."""
    resp = api_client.put("/api/profile", json={"steps_target": 999})
    assert resp.status_code == 422
    data = resp.json()
    assert "errors" in data or "detail" in data


def test_put_steps_target_above_50000_rejected(api_client):
    """PUT /api/profile con steps_target > 50000 -> 422 controlado (nunca 500)."""
    resp = api_client.put("/api/profile", json={"steps_target": 50001})
    assert resp.status_code == 422


def test_put_steps_target_boundaries_accepted(api_client):
    """Límites inclusivos 1000 y 50000 -> 200."""
    resp_lo = api_client.put("/api/profile", json={"steps_target": 1000})
    assert resp_lo.status_code == 200
    resp_hi = api_client.put("/api/profile", json={"steps_target": 50000})
    assert resp_hi.status_code == 200


def test_get_profile_default_steps_target_8000(api_client):
    """GET /api/profile sin profile.json (perfil viejo/sin el campo) -> default 8000."""
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    assert resp.json()["steps_target"] == 8000


def test_put_steps_target_non_numeric_rejected(api_client):
    """steps_target no numérico -> 422 de pydantic, nunca 500."""
    resp = api_client.put("/api/profile", json={"steps_target": "muchos"})
    assert resp.status_code == 422


# ── GET / debe inyectar __PROFILE__ ──────────────────────────────────────────

def test_root_injects_profile_placeholder(api_client):
    """GET / no debe tener __PROFILE__ crudo en el HTML final."""
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert "__PROFILE__" not in resp.text, "__PROFILE__ no fue sustituido en el template"


def test_root_has_profile_var(api_client):
    """GET / contiene 'var PROFILE' en el HTML (inyección exitosa)."""
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert "var PROFILE" in resp.text, "Falta 'var PROFILE' en el HTML"


# ── effective_sources() — Fase 6A ────────────────────────────────────────────

def test_effective_sources_no_profile_defaults_google_health(tmp_path, monkeypatch):
    """Sin profile.json -> ["google_health"] (default)."""
    _patch_profile_path(monkeypatch, tmp_path)
    from app import profile as _pm
    assert _pm.effective_sources() == ["google_health"]


def test_effective_sources_old_profile_only_source_string(tmp_path, monkeypatch):
    """Backward-compat DURA: perfil viejo con solo 'source' (string), sin 'sources'
    -> effective_sources() == [ese valor]. Este es el caso del perfil real del usuario."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"source": "google_health", "onboarded": True})
    from app import profile as _pm
    assert _pm.effective_sources() == ["google_health"]


def test_effective_sources_old_profile_non_default_source(tmp_path, monkeypatch):
    """Perfil viejo con source != default -> se respeta ese valor."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"source": "oura", "onboarded": True})
    from app import profile as _pm
    assert _pm.effective_sources() == ["oura"]


def test_effective_sources_new_profile_uses_sources_list(tmp_path, monkeypatch):
    """Perfil con 'sources' (lista) -> se usa tal cual, ignora 'source'."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {
        "source": "oura",  # huérfano, debe ser ignorado
        "sources": ["healthkit", "google_health"],
        "onboarded": True,
    })
    from app import profile as _pm
    assert _pm.effective_sources() == ["healthkit", "google_health"]


def test_effective_sources_empty_list_falls_back_to_source(tmp_path, monkeypatch):
    """'sources' presente pero vacío -> cae a [effective('source')], no lista vacía."""
    _patch_profile_path(monkeypatch, tmp_path)
    _write_profile(tmp_path, {"source": "whoop", "sources": [], "onboarded": True})
    from app import profile as _pm
    assert _pm.effective_sources() == ["whoop"]


def test_effective_sources_single_source_list():
    """Sanidad: perfil con sources=['google_health'] (post-migración natural) -> passthrough."""
    pass  # cubierto arriba; placeholder para claridad de intención, sin aserciones extra.


def test_defaults_has_sources_key():
    """_DEFAULTS debe declarar 'sources': ['google_health'] (Paso 1 del roadmap)."""
    from app.profile import _DEFAULTS
    assert _DEFAULTS["sources"] == ["google_health"]
    assert _DEFAULTS["source"] == "google_health"  # deprecated pero presente por compat


# ── POST/DELETE /api/sources/{name} — Fase 6A ────────────────────────────────

def test_post_sources_connects_new_source(api_client):
    resp = api_client.post("/api/sources/oura")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "oura" in body["sources"]


def test_post_sources_idempotent_no_duplicate(api_client):
    api_client.post("/api/sources/oura")
    resp = api_client.post("/api/sources/oura")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"].count("oura") == 1


def test_post_sources_adds_without_removing_existing(api_client):
    api_client.post("/api/sources/healthkit")
    resp = api_client.post("/api/sources/oura")
    body = resp.json()
    assert "healthkit" in body["sources"]
    assert "oura" in body["sources"]


def test_post_sources_unknown_name_404(api_client):
    resp = api_client.post("/api/sources/fitbit_direct")
    assert resp.status_code == 404


def test_delete_sources_disconnects(api_client):
    api_client.post("/api/sources/oura")
    resp = api_client.delete("/api/sources/oura")
    assert resp.status_code == 200
    body = resp.json()
    assert "oura" not in body["sources"]


def test_delete_sources_idempotent_when_not_connected(api_client):
    """DELETE de una fuente que no estaba conectada -> no error, idempotente."""
    resp = api_client.delete("/api/sources/whoop")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"


def test_delete_sources_unknown_name_404(api_client):
    resp = api_client.delete("/api/sources/fitbit_direct")
    assert resp.status_code == 404


def test_post_then_get_profile_reflects_effective_sources(tmp_path, monkeypatch, api_client):
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    api_client.post("/api/sources/healthkit")
    assert _pm.effective_sources() == ["google_health", "healthkit"]


# ── GET /api/sources — Fase 6B ───────────────────────────────────────────────

def test_get_sources_no_connections_all_false(api_client):
    """Sin fuentes conectadas explícitamente (solo el default google_health en
    _DEFAULTS), las otras 3 deben reportar connected:false."""
    resp = api_client.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"google_health", "oura", "whoop", "healthkit"}
    assert body["oura"]["connected"] is False
    assert body["whoop"]["connected"] is False
    assert body["healthkit"]["connected"] is False
    for name, info in body.items():
        assert "connected" in info
        assert "status" in info


def test_get_sources_reflects_connected_sources(api_client):
    """Con 2 fuentes conectadas via POST /api/sources/{name}, GET /api/sources
    debe reflejar connected:true solo en esas 2."""
    api_client.post("/api/sources/oura")
    api_client.post("/api/sources/healthkit")
    resp = api_client.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["oura"]["connected"] is True
    assert body["healthkit"]["connected"] is True
    assert body["google_health"]["connected"] is True  # default, siempre presente
    assert body["whoop"]["connected"] is False


# ── Intake clínico (Ronda 4): goals/injuries/conditions/medications ─────────

def test_defaults_has_clinical_fields_empty_lists():
    """_DEFAULTS declara los 4 campos clínicos como [] (Paso 1 del roadmap)."""
    from app.profile import _DEFAULTS
    for field in ("goals", "injuries", "conditions", "medications"):
        assert _DEFAULTS[field] == []


def test_get_profile_old_profile_defaults_clinical_empty(tmp_path, monkeypatch, api_client):
    """Perfil viejo (real, sin los 4 campos nuevos) -> GET /api/profile los expone
    como [] (cero migración, backward-compat dura)."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    _write_profile(tmp_path, {
        "name": "Doc", "birthdate": "1985-01-01", "sex": "M",
        "waist_cm": 85.0, "onboarded": True,
    })
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goals"] == []
    assert data["injuries"] == []
    assert data["conditions"] == []
    assert data["medications"] == []


def test_put_profile_clinical_fields_saved_and_trimmed(api_client):
    """PUT con listas de strings -> se guardan trimeadas, GET las refleja."""
    resp = api_client.put("/api/profile", json={
        "goals": ["  dormir mejor  ", "ganar fuerza"],
        "injuries": ["rodilla derecha"],
        "conditions": [],
        "medications": ["ibuprofeno 400mg"],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["goals"] == ["dormir mejor", "ganar fuerza"]
    assert data["injuries"] == ["rodilla derecha"]
    assert data["conditions"] == []
    assert data["medications"] == ["ibuprofeno 400mg"]


def test_put_profile_clinical_order_preserved(api_client):
    """El orden de 'goals' es significativo (prioridad) -> debe preservarse tal cual."""
    resp = api_client.put("/api/profile", json={"goals": ["sueño", "fuerza", "longevidad"]})
    assert resp.status_code == 200
    assert resp.json()["goals"] == ["sueño", "fuerza", "longevidad"]


def test_put_profile_clinical_empty_strings_filtered(api_client):
    """Items vacíos o solo-espacios se descartan."""
    resp = api_client.put("/api/profile", json={"goals": ["  ", "", "dormir"]})
    assert resp.status_code == 200
    assert resp.json()["goals"] == ["dormir"]


def test_put_profile_clinical_caps_items_and_length(api_client):
    """Máx 10 items, cada uno cortado a 120 chars."""
    long_item = "x" * 200
    many = [f"meta {i}" for i in range(15)]
    resp = api_client.put("/api/profile", json={"goals": many, "injuries": [long_item]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["goals"]) == 10
    assert data["goals"] == many[:10]
    assert len(data["injuries"][0]) == 120


def test_put_profile_clinical_non_list_rejected_422(api_client):
    """goals como string (no lista) -> 422 controlado, nunca 500."""
    resp = api_client.put("/api/profile", json={"goals": "dormir mejor"})
    assert resp.status_code == 422
    assert "errors" in resp.json()


def test_put_profile_clinical_non_string_items_rejected_422(api_client):
    """Lista con items no-string (ej. números/dicts) -> 422 controlado."""
    resp = api_client.put("/api/profile", json={"injuries": [1, 2, 3]})
    assert resp.status_code == 422


def test_put_profile_clinical_dict_rejected_422(api_client):
    """medications como dict -> 422 controlado."""
    resp = api_client.put("/api/profile", json={"medications": {"a": "b"}})
    assert resp.status_code == 422


def test_put_profile_clinical_partial_update_preserves_others(tmp_path, monkeypatch, api_client):
    """PUT que solo toca 'goals' no borra 'injuries' ya guardadas."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    api_client.put("/api/profile", json={"injuries": ["hombro"]})
    resp = api_client.put("/api/profile", json={"goals": ["dormir"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["goals"] == ["dormir"]
    assert data["injuries"] == ["hombro"]


# ── cycle_tracking toggle (Fase 7: salud femenina, opt-in) ───────────────────

def test_defaults_has_cycle_tracking_false():
    """_DEFAULTS declara cycle_tracking=False (rollout invisible por default)."""
    from app.profile import _DEFAULTS
    assert _DEFAULTS["cycle_tracking"] is False


def test_get_profile_default_cycle_tracking_false(api_client):
    """Perfil viejo/sin el campo -> GET /api/profile expone cycle_tracking=False."""
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    assert resp.json()["cycle_tracking"] is False


@pytest.mark.parametrize("sex", ["M", "F"])
def test_put_cycle_tracking_true_works_with_any_sex(api_client, sex):
    """Toggle inclusivo: funciona con cualquier sex, nunca forzado."""
    resp = api_client.put("/api/profile", json={"sex": sex, "cycle_tracking": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cycle_tracking"] is True
    assert data["sex"] == sex


def test_put_cycle_tracking_false_works(api_client):
    resp = api_client.put("/api/profile", json={"cycle_tracking": False})
    assert resp.status_code == 200
    assert resp.json()["cycle_tracking"] is False


def test_put_cycle_tracking_non_bool_rejected_422(api_client):
    """cycle_tracking no coercible a bool -> 422 de pydantic, nunca 500.
    (pydantic v2 en modo lax SÍ coerce strings como 'yes'/'true' a bool -- eso
    es válido; probamos con un valor que ni siquiera pydantic puede interpretar.)"""
    resp = api_client.put("/api/profile", json={"cycle_tracking": "not-a-bool-at-all"})
    assert resp.status_code == 422


def test_put_cycle_tracking_preserves_other_fields(tmp_path, monkeypatch, api_client):
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    api_client.put("/api/profile", json={"name": "Ana"})
    resp = api_client.put("/api/profile", json={"cycle_tracking": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["cycle_tracking"] is True
    assert data["name"] == "Ana"


def test_get_sources_one_source_auth_state_raises_does_not_500(api_client):
    """Si auth_state() de una fuente lanza, esa fuente reporta status:'error' pero
    las otras 3 siguen respondiendo con su estado real — nunca 500 completo."""
    from app.sources.whoop import WhoopSource
    with patch.object(WhoopSource, "auth_state", side_effect=RuntimeError("boom")):
        resp = api_client.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["whoop"]["status"] == "error"
    assert body["whoop"]["connected"] is False
    # las demás no se ven afectadas
    assert body["google_health"]["status"] != "error"
    assert body["oura"]["status"] != "error"
    assert body["healthkit"]["status"] != "error"


# ── notifications (Fase 8C, paso C3) ────────────────────────────────────────

def test_get_profile_default_notifications(api_client):
    """Perfil viejo/sin el campo -> GET /api/profile expone notifications con
    defaults (sin canales configurados, morning_brief/alerts en True)."""
    resp = api_client.get("/api/profile")
    assert resp.status_code == 200
    n = resp.json()["notifications"]
    assert n["ntfy_url"] == ""
    assert n["telegram_bot_token"] == ""
    assert n["telegram_chat_id"] == ""
    assert n["morning_brief"] is True
    assert n["alerts"] is True


def test_put_notifications_partial_update_merges(api_client):
    """PUT notifications con solo un subcampo -> MERGE parcial, no borra los
    demás (togglear morning_brief no debe borrar un ntfy_url ya guardado)."""
    resp1 = api_client.put("/api/profile", json={"notifications": {"ntfy_url": "https://ntfy.sh/mi-topic"}})
    assert resp1.status_code == 200
    assert resp1.json()["notifications"]["ntfy_url"] == "https://ntfy.sh/mi-topic"

    resp2 = api_client.put("/api/profile", json={"notifications": {"morning_brief": False}})
    assert resp2.status_code == 200
    data = resp2.json()["notifications"]
    assert data["morning_brief"] is False
    assert data["ntfy_url"] == "https://ntfy.sh/mi-topic"  # preservado


def test_put_notifications_non_dict_rejected_422(api_client):
    resp = api_client.put("/api/profile", json={"notifications": "not-a-dict"})
    assert resp.status_code == 422


def test_put_notifications_bad_subfield_types_rejected_422(api_client):
    resp = api_client.put("/api/profile", json={"notifications": {"ntfy_url": 12345}})
    assert resp.status_code == 422
    resp2 = api_client.put("/api/profile", json={"notifications": {"morning_brief": "yes-please"}})
    assert resp2.status_code == 422


def test_put_notifications_unknown_subfield_ignored(api_client):
    """Claves desconocidas dentro de notifications se ignoran silenciosamente
    (forward-compat), nunca 422 ni 500."""
    resp = api_client.put("/api/profile", json={"notifications": {"future_field": "x"}})
    assert resp.status_code == 200


def test_put_notifications_preserves_other_top_level_fields(tmp_path, monkeypatch, api_client):
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    api_client.put("/api/profile", json={"name": "Ana"})
    resp = api_client.put("/api/profile", json={"notifications": {"alerts": False}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Ana"
    assert data["notifications"]["alerts"] is False
