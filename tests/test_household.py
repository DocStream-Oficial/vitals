"""
test_household.py — Tests de integración de Household multi-perfil (Fase 8D,
paso D3). EL RIESGO #1 del roadmap de Fase D: puede romper CUALQUIER path de
datos. Este archivo valida punta a punta (vía TestClient real, no solo
app/userctx.py en aislamiento — eso ya lo cubre test_userctx.py):

(a) aislamiento total entre 2 usuarios: perfil, journal, ciclo, coach — leer/
    escribir como user A nunca toca los datos de user B.
(b) migración automática desde layout viejo single-user, disparada por
    on_startup() real de la app.
(c) resolución de usuario: header > cookie > único usuario > default.
(d) endpoints GET/POST/DELETE /api/users.
(e) concurrencia: dos requests de usuarios DISTINTOS no se pisan los datos.
(f) instalación single-user (sin ningún usuario creado) sigue funcionando
    SIN header ni cookie — mismo comportamiento de siempre.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _make_client(tmp_path: Path) -> TestClient:
    """TestClient con settings.DATA_DIR Y userctx._DATA_DIR apuntando al
    MISMO tmp_path (simula una instancia real, donde ambos son el mismo
    data/) — a diferencia del resto de la suite (que aísla userctx del resto
    vía el autouse fixture de conftest.py para no interferir con tests que
    monkeypatchean rutas legacy por separado), aquí SÍ queremos que
    should_use_household_paths() reaccione a lo que main.py hace de verdad.

    IMPORTANTE: Starlette moderno solo dispara los eventos on_event
    ("startup"/"shutdown") si el TestClient se usa como context manager
    (`with TestClient(app) as c:`) — instanciarlo "pelón" (patrón que usa el
    resto de la suite preexistente, ver test_endpoints.py) NO corre
    on_startup(). Para probar la migración automática (que vive en
    on_startup) necesitamos el context manager real; se entra aquí y se sale
    en _stop_client().

    🔴 SEGURIDAD DE DATOS (bug real encontrado durante el desarrollo de este
    mismo archivo — casi escribe en el data/ real del usuario dos veces):
    parchear SOLO settings.DATA_DIR + userctx._DATA_DIR NO basta. Cada módulo
    de persistencia (profile.py, cycle.py, journal.py, labs.py, notify.py,
    auth.py, coach_headline.py, report.py, coach_store.py, sync.py, main.py)
    calcula su ruta LEGACY de fallback (_PROFILE_FILE, DATA_OUT, DATA_PATH,
    etc.) en IMPORT-TIME desde settings.DATA_DIR — es decir, ANTES de que
    cualquier patch.object corra en este test. should_use_household_paths()
    es False en los escenarios "single-user sin household creado" de este
    archivo, así que esos módulos caen a su ruta legacy — que sin este
    monkeypatch explícito de CADA constante, sigue apuntando al data/ real
    del repo. Por eso aquí se replican los MISMOS monkeypatches exactos que
    ya usa el resto de la suite preexistente (test_profile.py, etc.), archivo
    por archivo."""
    from app import config, userctx as _userctx
    from app import profile as _pm, cycle as _cyc, journal as _jrn, labs as _lbs
    from app import notify as _ntf, auth as _auth, coach_headline as _chl
    from app import report as _rpt, coach_store as _cst, plan_store as _pln
    from app import sync as _snc
    from app import api_keys as _apk
    import main as main_mod

    patchers = [
        patch.object(config.settings, "DATA_DIR", tmp_path),
        patch.object(config.settings, "TEMPLATES_DIR", Path(__file__).parent.parent / "templates"),
        patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"),
        patch.object(_userctx, "_DATA_DIR", tmp_path),
        patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"),
        patch.object(_pm, "_DATA_DIR", tmp_path),
        patch.object(_cyc, "_CYCLE_LOG_FILE", tmp_path / "cycle_log.json"),
        patch.object(_jrn, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json"),
        patch.object(_lbs, "_LABS_LOG_FILE", tmp_path / "labs_log.json"),
        patch.object(_ntf, "_NOTIFY_STATE_FILE", tmp_path / "notify_state.json"),
        patch.object(_auth, "TOKEN_PATH", tmp_path / "token.json"),
        patch.object(_chl, "_CACHE_PATH", tmp_path / "coach_headline.json"),
        patch.object(_rpt, "_CACHE_PATH", tmp_path / "reports.json"),
        patch.object(_cst, "_STORE_FILE", tmp_path / "coach_conversations.json"),
        patch.object(_cst, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json"),
        patch.object(_cst, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak"),
        patch.object(_cst, "_DATA_DIR", tmp_path),
        patch.object(_pln, "_PLAN_LOG_FILE", tmp_path / "plan_log.json"),
        patch.object(_apk, "_API_KEYS_FILE", tmp_path / "api_keys.json"),
        patch.object(_snc, "DATA_OUT", tmp_path / "health_compact.json"),
        # main.py hace `from app.scheduler import start_scheduler,
        # stop_scheduler` — patchear "app.scheduler.start_scheduler" NO afecta
        # la referencia ya importada en el namespace de main_mod. Hay que
        # patchear main_mod.start_scheduler directamente, si no el scheduler
        # REAL arranca y dispara un sync REAL contra Google Health con las
        # credenciales/perfil reales del usuario dentro del test (segundo bug
        # real encontrado durante el desarrollo de este archivo).
        patch.object(main_mod, "start_scheduler"),
        patch.object(main_mod, "stop_scheduler"),
    ]

    for p in patchers:
        p.start()

    client = TestClient(main_mod.app, raise_server_exceptions=True)
    client.__enter__()  # dispara on_startup() de verdad (ver docstring)
    client._patchers = patchers
    return client


def _stop_client(client: TestClient):
    client.__exit__(None, None, None)
    for p in reversed(getattr(client, "_patchers", [])):
        p.stop()


@pytest.fixture
def household_client(tmp_path):
    client = _make_client(tmp_path)
    yield client, tmp_path
    _stop_client(client)


# ── (d) endpoints /api/users ─────────────────────────────────────────────────

def test_api_users_empty_when_no_household(household_client):
    client, _ = household_client
    r = client.get("/api/users")
    assert r.status_code == 200
    body = r.json()
    assert body["users"] == []
    assert body["active"] is None


def test_api_users_post_creates_user(household_client):
    client, tmp_path = household_client
    r = client.post("/api/users", json={"name": "Mike"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["user"]["id"] == "mike"
    assert (tmp_path / "users" / "mike").is_dir()


def test_api_users_post_rejects_empty_name(household_client):
    client, _ = household_client
    r = client.post("/api/users", json={"name": ""})
    assert r.status_code == 422


def test_api_users_get_lists_after_creation(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})
    r = client.get("/api/users")
    body = r.json()
    ids = {u["id"] for u in body["users"]}
    assert ids == {"mike", "ana"}


def test_api_users_delete_requires_confirm(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    r = client.delete("/api/users/mike")
    assert r.status_code == 400


def test_api_users_delete_with_confirm_removes_from_registry(household_client):
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    r = client.delete("/api/users/mike?confirm=true")
    assert r.status_code == 200
    r2 = client.get("/api/users")
    assert r2.json()["users"] == []
    # sin delete_data=true, la carpeta de datos NO se borra
    assert (tmp_path / "users" / "mike").exists()


def test_api_users_delete_with_delete_data_removes_folder(household_client):
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.delete("/api/users/mike?confirm=true&delete_data=true")
    assert not (tmp_path / "users" / "mike").exists()


# ── (a) aislamiento total entre 2 usuarios ───────────────────────────────────

def test_isolation_profile_between_two_users(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    r1 = client.put("/api/profile", json={"name": "Mike Profile"}, headers={"X-Vitals-User": "mike"})
    assert r1.status_code == 200
    r2 = client.put("/api/profile", json={"name": "Ana Profile"}, headers={"X-Vitals-User": "ana"})
    assert r2.status_code == 200

    g1 = client.get("/api/profile", headers={"X-Vitals-User": "mike"})
    g2 = client.get("/api/profile", headers={"X-Vitals-User": "ana"})
    assert g1.json()["name"] == "Mike Profile"
    assert g2.json()["name"] == "Ana Profile"


def test_isolation_journal_between_two_users(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    r1 = client.put("/api/journal/2026-01-01", json={"habits": {"alcohol": True}},
                     headers={"X-Vitals-User": "mike"})
    assert r1.status_code == 200
    r2 = client.put("/api/journal/2026-01-01", json={"habits": {"alcohol": False, "meditation": True}},
                     headers={"X-Vitals-User": "ana"})
    assert r2.status_code == 200

    g1 = client.get("/api/journal?date=2026-01-01", headers={"X-Vitals-User": "mike"})
    g2 = client.get("/api/journal?date=2026-01-01", headers={"X-Vitals-User": "ana"})
    assert g1.json()["entry"] == {"alcohol": True}
    assert g2.json()["entry"] == {"alcohol": False, "meditation": True}


def test_isolation_plan_between_two_users(household_client):
    """Roadmap P1 F4 (paso 6): plan_store respeta X-Vitals-User — un plan
    iniciado por 'mike' no debe ser visible ni bloquear a 'ana' (riesgo #3
    del roadmap, mismo tipo que el aislamiento de journal)."""
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    r1 = client.post("/api/plan", json={"program_id": "sleep_reset"}, headers={"X-Vitals-User": "mike"})
    assert r1.status_code == 200

    # Ana NO ve el plan de Mike, y puede iniciar el SUYO propio sin 409.
    g_ana_before = client.get("/api/plan", headers={"X-Vitals-User": "ana"})
    assert g_ana_before.json() == {"active": False}

    r2 = client.post("/api/plan", json={"program_id": "aerobic_base"}, headers={"X-Vitals-User": "ana"})
    assert r2.status_code == 200

    g_mike = client.get("/api/plan", headers={"X-Vitals-User": "mike"})
    g_ana = client.get("/api/plan", headers={"X-Vitals-User": "ana"})
    assert g_mike.json()["program_id"] == "sleep_reset"
    assert g_ana.json()["program_id"] == "aerobic_base"


def test_isolation_cycle_between_two_users(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    # Activa cycle_tracking para Ana únicamente.
    client.put("/api/profile", json={"cycle_tracking": True}, headers={"X-Vitals-User": "ana"})
    r = client.post("/api/cycle/period", json={"start": "2026-01-01"}, headers={"X-Vitals-User": "ana"})
    assert r.status_code == 200

    # Mike no tiene cycle_tracking activado -> disabled, y sobre todo NO ve
    # el periodo de Ana aunque comparta instancia.
    r_mike = client.get("/api/cycle", headers={"X-Vitals-User": "mike"})
    assert r_mike.json().get("enabled") is False

    r_ana = client.get("/api/cycle", headers={"X-Vitals-User": "ana"})
    assert r_ana.json().get("enabled") is True


def test_isolation_coach_conversations_between_two_users(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    r1 = client.post("/api/coach/conversations", json={"title": "Mike chat"},
                      headers={"X-Vitals-User": "mike"})
    r2 = client.post("/api/coach/conversations", json={"title": "Ana chat"},
                      headers={"X-Vitals-User": "ana"})
    assert r1.status_code == 200 and r2.status_code == 200

    list_mike = client.get("/api/coach/conversations", headers={"X-Vitals-User": "mike"}).json()
    list_ana = client.get("/api/coach/conversations", headers={"X-Vitals-User": "ana"}).json()

    mike_titles = {c["title"] for c in list_mike}
    ana_titles = {c["title"] for c in list_ana}
    assert "Mike chat" in mike_titles
    assert "Ana chat" not in mike_titles
    assert "Ana chat" in ana_titles
    assert "Mike chat" not in ana_titles


def test_isolation_labs_between_two_users(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    client.post("/api/labs", json={"date": "2026-01-01", "marker": "glucose", "value": 90},
                headers={"X-Vitals-User": "mike"})
    r_ana = client.get("/api/labs", headers={"X-Vitals-User": "ana"})
    assert r_ana.json()["series"] == {}

    r_mike = client.get("/api/labs", headers={"X-Vitals-User": "mike"})
    assert "glucose" in r_mike.json()["series"]


def test_write_as_a_read_as_b_never_leaks(household_client):
    """El escenario EXACTO que pide el roadmap: 'escribir como user A, leer
    como B' y verificar que B NUNCA ve los datos de A."""
    client, _ = household_client
    client.post("/api/users", json={"name": "UserA"})
    client.post("/api/users", json={"name": "UserB"})

    client.put("/api/journal/2026-02-01", json={"habits": {"sick": True}},
               headers={"X-Vitals-User": "usera"})

    resp_b = client.get("/api/journal?date=2026-02-01", headers={"X-Vitals-User": "userb"})
    assert resp_b.json()["entry"] == {}  # B no ve el registro de A


def test_isolation_coach_suggestions_respects_household_dataset(household_client):
    """Riesgo #8 del roadmap P0 (F1): /api/coach/suggestions debe leer el
    dataset del uid ACTIVO (X-Vitals-User), no un dataset global compartido.
    Mike tiene un health_compact.json con 7 noches cortas (dispara sleep_debt,
    alert) -> su primera sugerencia debe ser la de deuda de sueño; Ana no
    tiene dataset -> cae al pool genérico. Ningún cruce entre ambos."""
    client, tmp_path = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    import datetime as _dt
    days = []
    start = _dt.date(2026, 1, 1)
    for i in range(7):
        d = (start + _dt.timedelta(days=i)).isoformat()
        days.append({"date": d, "asleep": 340, "recovery": 55})
    dataset = {"summary": {}, "days": days, "exercises": []}
    mike_dir = tmp_path / "users" / "mike"
    mike_dir.mkdir(parents=True, exist_ok=True)
    (mike_dir / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")

    r_mike = client.get("/api/coach/suggestions", headers={"X-Vitals-User": "mike"})
    assert r_mike.status_code == 200
    mike_qs = r_mike.json()["questions"]
    assert mike_qs[0]["id"] == "sleep_debt"

    r_ana = client.get("/api/coach/suggestions", headers={"X-Vitals-User": "ana"})
    assert r_ana.status_code == 200
    ana_qs = r_ana.json()["questions"]
    assert ana_qs[0]["id"] != "sleep_debt", "Ana no debe heredar la señal de sleep_debt de Mike"
    assert all(q["id"].startswith("generic_") for q in ana_qs)


# ── (c) resolución de usuario ────────────────────────────────────────────────

def test_resolution_cookie_persists_selection(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    client.put("/api/profile", json={"name": "Ana Name"}, headers={"X-Vitals-User": "ana"})
    client.cookies.set("vitals_user", "ana")
    r = client.get("/api/profile")  # sin header, solo cookie
    assert r.json()["name"] == "Ana Name"


def test_resolution_single_user_no_header_needed(household_client):
    client, _ = household_client
    client.post("/api/users", json={"name": "OnlyUser"})
    client.put("/api/profile", json={"name": "Solo"})  # sin header ni cookie
    r = client.get("/api/profile")
    assert r.json()["name"] == "Solo"


# ── (f) instalación single-user (sin household) sigue funcionando ───────────

def test_single_user_installation_works_without_any_user_created(household_client):
    """Sin NINGÚN usuario creado (data/users/ no existe) — el sistema debe
    comportarse EXACTAMENTE como antes de Fase 8D: profile.json en la raíz de
    data/, sin importar header/cookie."""
    client, tmp_path = household_client
    r = client.put("/api/profile", json={"name": "Legacy Single User"})
    assert r.status_code == 200
    assert (tmp_path / "profile.json").exists()
    assert not (tmp_path / "users").exists()

    r2 = client.get("/api/profile")
    assert r2.json()["name"] == "Legacy Single User"


# ── (b) migración automática desde layout viejo ──────────────────────────────

def test_migration_runs_on_startup_with_legacy_data(tmp_path):
    """Coloca un layout viejo (profile.json suelto) ANTES de instanciar el
    TestClient — on_startup() real debe migrarlo a data/users/default/ y la
    app debe seguir sirviendo esos datos sin que el caller haga nada especial."""
    (tmp_path / "profile.json").write_text(
        json.dumps({"name": "Legacy Doc", "onboarded": True}), encoding="utf-8"
    )

    client = _make_client(tmp_path)
    try:
        # Tras el arranque, el layout viejo debe haberse movido.
        assert not (tmp_path / "profile.json").exists()
        assert (tmp_path / "users" / "default" / "profile.json").exists()

        r = client.get("/api/profile")
        assert r.status_code == 200
        assert r.json()["name"] == "Legacy Doc"

        r_users = client.get("/api/users")
        ids = {u["id"] for u in r_users.json()["users"]}
        assert "default" in ids
    finally:
        _stop_client(client)


def test_migration_is_idempotent_across_restarts(tmp_path):
    """Dos 'arranques' (dos TestClient) sobre el mismo tmp_path: el segundo no
    debe re-migrar ni duplicar nada."""
    (tmp_path / "profile.json").write_text(json.dumps({"name": "Doc"}), encoding="utf-8")

    client1 = _make_client(tmp_path)
    _stop_client(client1)

    client2 = _make_client(tmp_path)
    try:
        r = client2.get("/api/users")
        users = r.json()["users"]
        assert len([u for u in users if u["id"] == "default"]) == 1
    finally:
        _stop_client(client2)


# ── (e) concurrencia: dos usuarios sincronizando "al mismo tiempo" ───────────

def test_concurrent_writes_two_users_do_not_clobber_each_other(household_client):
    """Simula (secuencialmente, sin threads reales — basta para validar
    AISLAMIENTO de escritura, que es lo que puede pisarse) escrituras
    intercaladas de dos usuarios sobre el mismo endpoint."""
    client, _ = household_client
    client.post("/api/users", json={"name": "Mike"})
    client.post("/api/users", json={"name": "Ana"})

    for i in range(5):
        client.put(f"/api/journal/2026-03-{i+1:02d}", json={"habits": {"alcohol": i % 2 == 0}},
                   headers={"X-Vitals-User": "mike"})
        client.put(f"/api/journal/2026-03-{i+1:02d}", json={"habits": {"meditation": i % 2 == 1}},
                   headers={"X-Vitals-User": "ana"})

    mike_j = client.get("/api/journal?date=2026-03-01", headers={"X-Vitals-User": "mike"}).json()
    ana_j = client.get("/api/journal?date=2026-03-01", headers={"X-Vitals-User": "ana"}).json()
    assert mike_j["entry"] == {"alcohol": True}
    assert ana_j["entry"] == {"meditation": False}
