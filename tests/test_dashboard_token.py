"""
test_dashboard_token.py — DASHBOARD_TOKEN opt-in (R2 pre-publicación).

Cubre el criterio de aceptación 3 del ROADMAP: con DASHBOARD_TOKEN vacío
(default) el comportamiento es byte-idéntico a hoy; con token seteado, exige
cookie/Bearer para el dashboard, exenta ingest/ecg-POST/v1, y expone
/api/ingest-token detrás del auth (cierra el hallazgo 🟠 de la auditoría).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _get_client(tmp_path: Path, dashboard_token: str = "", with_data: bool = True):
    if with_data:
        real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
        if real_compact.exists():
            (tmp_path / "health_compact.json").write_text(real_compact.read_text())

    from app import config
    from app import auth as auth_mod
    from app import sync as sync_mod
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(config.settings, "DASHBOARD_TOKEN", dashboard_token), \
         patch.object(config.settings, "INGEST_TOKEN", "test-ingest-token"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(auth_mod, "TOKEN_PATH", tmp_path / "token.json"), \
         patch.object(sync_mod, "DATA_OUT", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture(autouse=True)
def _isolate_coach_history(tmp_path, monkeypatch):
    """Mismo aislamiento que test_endpoints.py: nunca tocar data/coach_*.json reales."""
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")


@pytest.fixture
def client_token_off(tmp_path):
    yield from _get_client(tmp_path, dashboard_token="")


@pytest.fixture
def client_token_on(tmp_path):
    yield from _get_client(tmp_path, dashboard_token="testtok123")


# ── Token OFF (default) — byte-idéntico a hoy ───────────────────────────────

def test_token_off_root_200_no_cookie(client_token_off):
    resp = client_token_off.get("/")
    assert resp.status_code == 200


def test_token_off_api_data_200_no_cookie(client_token_off):
    resp = client_token_off.get("/api/data")
    assert resp.status_code == 200


def test_token_off_ingest_token_endpoint_200_no_cookie(client_token_off):
    """Sin DASHBOARD_TOKEN, /api/ingest-token sigue abierto (comportamiento
    preexistente sin cambio) — el cierre del hallazgo 🟠 solo aplica con
    DASHBOARD_TOKEN seteado."""
    resp = client_token_off.get("/api/ingest-token")
    assert resp.status_code == 200


# ── Token ON — criterio 3(a): GET / sin cookie/header -> 401 o redirect ────

def test_token_on_root_without_auth_redirects_to_login(client_token_on):
    resp = client_token_on.get("/", follow_redirects=False, headers={"Accept": "text/html"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_token_on_root_without_auth_redirects_even_without_accept_header(client_token_on):
    """GET / SIEMPRE redirige a /login sin auth, incluso sin header Accept
    (ej. un curl de smoke-test plano) — es la ruta de dashboard/navegador por
    excelencia, no debe depender de que el cliente mande Accept: text/html."""
    resp = client_token_on.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


def test_token_on_api_without_auth_returns_401_json(client_token_on):
    resp = client_token_on.get("/api/data", follow_redirects=False)
    assert resp.status_code == 401
    assert resp.json() == {"detail": "dashboard token required"}


# ── Token ON — criterio 3(f): /api/ingest-token detrás del auth ────────────

def test_token_on_ingest_token_endpoint_requires_auth(client_token_on):
    resp = client_token_on.get("/api/ingest-token")
    assert resp.status_code == 401


def test_token_on_ingest_token_endpoint_with_bearer_200(client_token_on):
    resp = client_token_on.get(
        "/api/ingest-token", headers={"Authorization": "Bearer testtok123"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"token": "test-ingest-token"}


# ── Token ON — criterio 3(b): login correcto -> cookie HttpOnly -> GET / 200

def test_token_on_login_correct_token_sets_cookie_and_redirects(client_token_on):
    resp = client_token_on.post(
        "/login", data={"token": "testtok123"}, follow_redirects=False
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"
    set_cookie = resp.headers.get("set-cookie", "")
    assert "vitals_dash=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "samesite=lax" in set_cookie.lower()

    # La cookie debe autorizar el siguiente GET /
    resp2 = client_token_on.get("/", cookies={"vitals_dash": "testtok123"})
    assert resp2.status_code == 200


# ── Token ON — criterio 3(c): token incorrecto -> 401 sin cookie ───────────

def test_token_on_login_incorrect_token_401_no_cookie(client_token_on):
    resp = client_token_on.post(
        "/login", data={"token": "wrong"}, follow_redirects=False
    )
    assert resp.status_code == 401
    assert "set-cookie" not in {k.lower() for k in resp.headers.keys()}


# ── Token ON — criterio 3(d): /api/ingest con X-Vitals-Token sigue OK ──────

def test_token_on_api_ingest_exempt_with_ingest_token_no_cookie(client_token_on):
    resp = client_token_on.post(
        "/api/ingest",
        json={"days": []},
        headers={"X-Vitals-Token": "test-ingest-token"},
    )
    # Sin cookie de dashboard, pero con su propio X-Vitals-Token -> nunca 401
    # por el dashboard-auth (puede ser 200/otros por lógica interna de ingest,
    # pero JAMÁS el 401 de "dashboard token required").
    assert resp.status_code != 401 or resp.json() != {"detail": "dashboard token required"}


def test_token_on_api_ingest_wrong_ingest_token_still_401_from_ingest_auth(client_token_on):
    """El endpoint sigue exigiendo SU propio X-Vitals-Token — el dashboard-auth
    lo exenta, no lo reemplaza."""
    resp = client_token_on.post(
        "/api/ingest",
        json={"days": []},
        headers={"X-Vitals-Token": "wrong-token"},
    )
    assert resp.status_code == 401
    assert resp.json() == {"status": "unauthorized"}


# ── Token ON — criterio 3(e): /api/v1/data con Bearer key sigue igual ──────

def test_token_on_api_v1_data_exempt_from_dashboard_auth(client_token_on):
    """/api/v1/ tiene su propia auth (API keys) — sin dashboard cookie ni
    Bearer del DASHBOARD_TOKEN, debe fallar por SU propia auth (401 de API
    key), nunca por el dashboard-auth genérico."""
    resp = client_token_on.get("/api/v1/data")
    assert resp.status_code == 401
    # No debe ser el mensaje genérico del dashboard-auth.
    assert resp.json() != {"detail": "dashboard token required"}


# ── Token ON — criterio 3(g): Authorization Bearer como alternativa a cookie

def test_token_on_bearer_header_authorizes_api(client_token_on):
    resp = client_token_on.get(
        "/api/data", headers={"Authorization": "Bearer testtok123"}
    )
    assert resp.status_code == 200


def test_token_on_bearer_header_authorizes_root(client_token_on):
    resp = client_token_on.get(
        "/", headers={"Authorization": "Bearer testtok123"}
    )
    assert resp.status_code == 200


# ── Rutas exentas siempre accesibles (estáticos PWA, login) ────────────────

def test_token_on_login_page_accessible_without_auth(client_token_on):
    resp = client_token_on.get("/login")
    assert resp.status_code == 200
    assert "form" in resp.text.lower()


def test_token_on_service_worker_accessible_without_auth(client_token_on):
    resp = client_token_on.get("/service-worker.js")
    assert resp.status_code == 200


def test_token_on_manifest_accessible_without_auth(client_token_on):
    resp = client_token_on.get("/manifest.webmanifest")
    assert resp.status_code == 200


# ── compare_digest en bytes: no debe crashear con header no-ASCII ──────────

def test_token_on_non_ascii_bearer_header_does_not_crash(tmp_path):
    """Lección 5D-B: comparar en str con no-ASCII puede lanzar TypeError (DoS).
    Un valor no-ASCII en el header Authorization debe devolver 401 limpio,
    nunca 500. Se llama al middleware directo (como test_healthkit.py hace con
    api_ingest) porque el cliente HTTP real (httpx) rechaza mandar headers
    no-ASCII, pero un servidor ASGI real sí puede entregar ese str."""
    import asyncio
    from app import config

    import main as main_mod

    class _Headers(dict):
        def get(self, k, default=None):
            if k == "Authorization":
                return "Bearer t\xf6ken-\xf1o\xf1o"
            return default

    class _Cookies(dict):
        def get(self, k, default=None):
            return default

    class _URL:
        path = "/api/data"

    class _Req:
        headers = _Headers()
        cookies = _Cookies()
        url = _URL()
        method = "GET"

    async def _call_next(request):
        raise AssertionError("no debe llegar a call_next: debe fallar el auth antes")

    with patch.object(config.settings, "DASHBOARD_TOKEN", "testtok123"):
        resp = asyncio.run(main_mod._dashboard_auth_middleware(_Req(), _call_next))
    assert resp.status_code == 401


def test_token_on_non_ascii_cookie_does_not_crash(tmp_path):
    import asyncio
    from app import config

    import main as main_mod

    class _Headers(dict):
        def get(self, k, default=None):
            return default

    class _Cookies(dict):
        def get(self, k, default=None):
            if k == "vitals_dash":
                return "t\xf6ken-\xf1o\xf1o"
            return default

    class _URL:
        path = "/api/data"

    class _Req:
        headers = _Headers()
        cookies = _Cookies()
        url = _URL()
        method = "GET"

    async def _call_next(request):
        raise AssertionError("no debe llegar a call_next: debe fallar el auth antes")

    with patch.object(config.settings, "DASHBOARD_TOKEN", "testtok123"):
        resp = asyncio.run(main_mod._dashboard_auth_middleware(_Req(), _call_next))
    assert resp.status_code == 401
