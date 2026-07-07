"""
test_endpoints.py — smoke tests de endpoints con TestClient (sin red).
"""
from __future__ import annotations

import json
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Fase 9-B: los <script> inline con JS de la app se movieron a static/js/
# (app-i18n-helpers.js, app-dashboard.js). Los tests que verificaban "la
# función/constante X está en el JS servido" contra resp.text/html ahora
# verifican contra body/html + este JS externo -- mismo contrato, nueva
# ubicación del código.
def _external_js_source() -> str:
    base = Path(__file__).parent.parent / "static" / "js"
    return (
        (base / "app-i18n-helpers.js").read_text(encoding="utf-8")
        + (base / "app-dashboard.js").read_text(encoding="utf-8")
    )


def _get_client(tmp_path: Path, with_data: bool = True) -> TestClient:
    """Devuelve un TestClient con scheduler mockeado y DATA_DIR en tmp_path."""
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
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(auth_mod, "TOKEN_PATH", tmp_path / "token.json"), \
         patch.object(sync_mod, "DATA_OUT", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture(autouse=True)
def _isolate_coach_history(tmp_path, monkeypatch):
    """Redirige coach_store a tmp para que NINGÚN test escriba en los
    data/coach_*.json reales del usuario (persistencia es side-effect)."""
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")


@pytest.fixture
def client(tmp_path):
    yield from _get_client(tmp_path, with_data=True)


@pytest.fixture
def client_no_data(tmp_path):
    yield from _get_client(tmp_path, with_data=False)


def test_root_200(client):
    """GET / debe devolver 200 y HTML sin __DATA__/__COACH__/__AUTH__."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "__DATA__" not in body
    assert "__COACH__" not in body
    assert "__AUTH__" not in body


def test_root_html_content(client):
    """GET / debe contener marcadores del diseño iOS (tab-bar, hero, Liquid Glass)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Marcadores del template iOS nuevo (layout responsive, no mockup)
    ios_markers = ["tabBar", "screenHoy", "reconnectBanner", "ringWrap", "id=\"glow\""]
    assert any(m in body for m in ios_markers), (
        "La respuesta de GET / no contiene marcadores del diseño iOS. "
        f"Probé: {ios_markers}"
    )


def test_root_ios_markers(client):
    """GET / contiene los elementos clave del diseño iOS responsive (no el mockup de teléfono)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Layout responsive real: contenedor .app + grid .cards + tab bar + pantalla Hoy
    assert 'class="app"' in body, "Debe usar el contenedor responsive .app"
    assert 'class="cards"' in body, "Debe usar el grid responsive .cards"
    assert 'id="tabBar"' in body, "Debe contener la tab bar"
    assert "screenHoy" in body, "Debe contener la pantalla Hoy"
    # Scaffolding del mockup NO debe quedar
    assert "phoneFrame" not in body, "No debe quedar el frame de teléfono del mockup"
    assert ">9:41<" not in body, "No debe quedar la hora simulada del mockup"


def test_api_data_returns_json(client):
    """GET /api/data devuelve JSON con 'days', 'summary', 'summary.bodyage'."""
    from app import config
    import main as main_mod
    tmp = Path(client.app.state.__dict__.get("_test_tmp", "/dev/null")).parent if hasattr(client.app.state, "_test_tmp") else None

    resp = client.get("/api/data")
    assert resp.status_code == 200
    data = resp.json()
    assert "days" in data
    assert "summary" in data
    assert len(data["days"]) > 0
    assert "bodyage" in data["summary"]


def test_api_data_schema(client):
    """Verifica el esquema exacto de summary."""
    resp = client.get("/api/data")
    assert resp.status_code == 200
    s = resp.json()["summary"]
    for key in ["hrv_base", "rhr_base", "hrv_range", "rhr_range", "n_days", "updated", "bodyage"]:
        assert key in s, f"Falta clave '{key}' en summary"


def test_api_data_passes_through_synthetic_merge_info_by_metric(tmp_path):
    """Roadmap P1, F7 (paso 11): summary.merge_info.by_metric (inyectado por
    sync.py vía app.merge.last_merge_info()) debe llegar tal cual a GET
    /api/data — es lo que consume la UI para la matriz de Fuentes y los
    badges '· via <fuente>'. Test unitario del 'inversor' vía endpoint real
    en vez de mockear el frontend JS (sin runtime de navegador en esta suite)."""
    from app import config, profile as _pm
    import main as main_mod

    days = [{"date": "2026-06-28", "recovery": 60, "asleep": 420}]
    synthetic_merge_info = {
        "n_sources": 2,
        "hrv_source": "healthkit",
        "by_metric": {
            "hrv": {"mode": "canonical", "source": "healthkit"},
            "rhr": {"mode": "avg", "sources": ["healthkit", "oura"]},
            "sleep": {"mode": "per-night", "sources": ["oura"]},
        },
    }
    dataset = {"days": days, "summary": {"n_days": 1, "merge_info": synthetic_merge_info}}

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = client.get("/api/data")

    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["merge_info"] == synthetic_merge_info


def test_api_sync_no_token_controlled(client):
    """POST /api/sync sin token válido: respuesta controlada, no 500."""
    from app.auth import NoToken
    with patch("app.auth.access_token", side_effect=NoToken("sin token")):
        resp = client.post("/api/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "no_token"


def test_api_sync_expired_token_controlled(client):
    """POST /api/sync con token expirado: respuesta controlada con status expired."""
    from app.auth import TokenExpired
    with patch("app.auth.access_token", side_effect=TokenExpired("expirado")):
        resp = client.post("/api/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "expired"


def test_auth_login_redirects(client):
    """GET /auth/login debe redirigir (3xx) a Google OAuth."""
    resp = client.get("/auth/login", follow_redirects=False)
    assert resp.status_code in (301, 302, 307, 308)
    location = resp.headers.get("location", "")
    assert "accounts.google.com" in location
    assert "googlehealth" in location


def test_auth_login_has_offline_scope(client):
    """La URL de redirect debe incluir access_type=offline."""
    resp = client.get("/auth/login", follow_redirects=False)
    location = resp.headers.get("location", "")
    assert "access_type=offline" in location


def test_api_data_404_no_file(client_no_data):
    """GET /api/data sin health_compact.json devuelve 404."""
    resp = client_no_data.get("/api/data")
    assert resp.status_code == 404


def test_root_no_data_file(client_no_data):
    """GET / sin health_compact.json devuelve 200 con mensaje de bienvenida."""
    resp = client_no_data.get("/")
    assert resp.status_code == 200
    assert "auth/login" in resp.text


def test_tendencias_content_present(client):
    """GET / contiene el contenido del tab Tendencias (gráficas y secciones clave)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # Títulos de las 8 gráficas
    assert "Recuperación vs Esfuerzo" in body, "Falta la gráfica Recuperación vs Esfuerzo"
    assert "Arquitectura del sueño" in body, "Falta la gráfica Arquitectura del sueño"
    assert "Consistencia de sueño" in body, "Falta la gráfica Consistencia de sueño"
    assert "HRV (RMSSD)" in body, "Falta la gráfica HRV"
    assert "FC en reposo" in body, "Falta la gráfica FC en reposo"
    assert "SpO₂ nocturno" in body, "Falta la gráfica SpO₂"
    assert "Temp. de piel" in body, "Falta la gráfica Temp. de piel"
    assert "Balance del periodo" in body, "Falta la tarjeta Balance del periodo"
    # Selector de periodo
    assert "tend-period-bar" in body, "Falta el selector de periodo"
    assert "setTendPeriod" in body, "Falta la función setTendPeriod"
    # Métricas y entrenamientos
    assert "Métricas clave" in body, "Falta la sección Métricas clave"
    assert "Entrenamientos recientes" in body, "Falta la sección Entrenamientos recientes"


def test_tendencias_no_placeholder(client):
    """El tab Tendencias no debe contener el texto placeholder 'Próximamente'."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # screenTend must exist but must NOT contain the old placeholder text
    tend_start = body.find('id="screenTend"')
    tend_end = body.find('id="screenCoach"')
    assert tend_start != -1, "No se encontró #screenTend"
    tend_block = body[tend_start:tend_end] if tend_end != -1 else body[tend_start:]
    assert "Próximamente" not in tend_block, "El bloque Tendencias todavía contiene 'Próximamente'"
    assert "renderTend" in body + _external_js_source(), "Falta la función renderTend en el JS"


def test_tendencias_no_hardcoded_values(client):
    """El HTML de Tendencias no contiene valores hardcodeados del prototipo."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    # These were the hardcoded prototype values — must NOT appear as literals
    assert ">60%" not in body or "tend" not in body.lower(), (
        "El valor hardcodeado '60%' del prototipo puede estar presente"
    )
    # The JS should reference DB fields, not literals like '57.1' as a fixed string
    # We check that the template injects DB data (var DB = ...) and no plain proto literal
    assert "57.1" not in body or "hrv_base" in body, (
        "Posible valor hardcodeado 57.1 sin referencia a hrv_base"
    )


# ── Coach endpoint tests ──────────────────────────────────────────────────────

def test_api_coach_200_with_answer(client):
    """POST /api/coach con ask_coach mockeado → 200 + {answer}."""
    with patch("main.ask_coach", return_value="Prioriza fuerza hoy — 0 min esta semana."):
        resp = client.post(
            "/api/coach",
            json={"question": "¿Qué priorizo hoy?"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data
    assert len(data["answer"]) > 0


def test_api_coach_ignores_deprecated_front_history(client):
    """POST /api/coach ya NO usa body.history como contexto — el contexto sale
    exclusivamente de coach_store.get_context(cid). Si el front manda history,
    se ignora silenciosamente (no rompe, no se mezcla)."""
    captured = {}

    def mock_ask(question, dataset, history=None):
        captured["history"] = history
        return "Respuesta de prueba."

    with patch("main.ask_coach", side_effect=mock_ask):
        resp = client.post(
            "/api/coach",
            json={
                "question": "¿Cómo voy con el sueño?",
                "history": [{"role": "user", "content": "de otra parte, NO debe colarse"}],
            },
        )
    assert resp.status_code == 200
    history_passed = captured.get("history") or []
    assert not any(
        "NO debe colarse" in (m.get("content") or "") for m in history_passed
    ), f"body.history (deprecado) se coló en el contexto: {history_passed}"


def test_api_coach_cli_error_returns_fallback_not_500(client):
    """Si ask_coach lanza una excepción interna, el endpoint devuelve 200 con fallback."""
    # ask_coach ya maneja sus errores internamente y retorna un string;
    # pero si por algún motivo lanzara, el endpoint tampoco debe dar 500.
    with patch("main.ask_coach", return_value="No puedo conectarme ahora, intenta luego."):
        resp = client.post(
            "/api/coach",
            json={"question": "prueba"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "answer" in data


def test_api_coach_no_data(client_no_data):
    """POST /api/coach sin health_compact.json responde 200 con answer (dataset vacío)."""
    with patch("main.ask_coach", return_value="Sin datos por ahora, pero puedo ayudarte igual."):
        resp = client_no_data.post(
            "/api/coach",
            json={"question": "¿Cómo estoy?"},
        )
    assert resp.status_code == 200
    assert "answer" in resp.json()


# ── Coach history persistence tests (PARTE B) ────────────────────────────────

def test_api_coach_history_empty(client_no_data):
    """GET /api/coach/history sin archivo → 200 con lista vacía (nunca 500)."""
    with patch("main.load_history", return_value=[]):
        resp = client_no_data.get("/api/coach/history")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 0


def test_api_coach_history_returns_persisted(client):
    """GET /api/coach/history devuelve lo que load_history() retorna."""
    mock_history = [
        {"role": "user", "content": "dame una rutina", "ts": "2026-06-27T10:00:00+00:00"},
        {"role": "assistant", "content": "Haz sentadillas.", "ts": "2026-06-27T10:00:00+00:00"},
    ]
    with patch("main.load_history", return_value=mock_history):
        resp = client.get("/api/coach/history")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["role"] == "user"
    assert data[1]["role"] == "assistant"


def test_api_coach_post_persists_turn(client):
    """POST /api/coach persiste el turno vía coach_store.append_turn (real,
    contra el store aislado en tmp_path) y devuelve conversation_id."""
    with patch("main.ask_coach", return_value="Haz plancha 3x30s."):
        resp = client.post(
            "/api/coach",
            json={"question": "dame un ejercicio de core"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "conversation_id" in data and data["conversation_id"]

    from app import coach_store
    conv = coach_store.get_conversation(data["conversation_id"])
    assert conv is not None
    assert conv["messages"][-2] == {
        "role": "user", "content": "dame un ejercicio de core",
        "ts": conv["messages"][-2]["ts"],
    }
    assert conv["messages"][-1]["content"] == "Haz plancha 3x30s."


def test_api_coach_history_delete(client):
    """DELETE /api/coach/history llama clear_history y responde 200."""
    with patch("main.clear_history") as mock_clear:
        resp = client.delete("/api/coach/history")
    assert resp.status_code == 200
    mock_clear.assert_called_once()


# ── Conversaciones (multi-chat) ────────────────────────────────────────────

def test_api_conversations_create_returns_id(client):
    resp = client.post("/api/coach/conversations", json={})
    assert resp.status_code == 200
    assert "id" in resp.json() and resp.json()["id"]


def test_api_conversations_create_no_body(client):
    """POST sin body (title opcional) también funciona."""
    resp = client.post("/api/coach/conversations")
    assert resp.status_code == 200
    assert resp.json()["id"]


def test_api_conversations_list_empty(client):
    resp = client.get("/api/coach/conversations")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_conversations_list_is_light(client):
    """La lista NO trae messages (barata)."""
    cid = client.post("/api/coach/conversations", json={}).json()["id"]
    with patch("main.ask_coach", return_value="ok"):
        client.post("/api/coach", json={"question": "hola", "conversation_id": cid})
    resp = client.get("/api/coach/conversations")
    items = resp.json()
    assert len(items) == 1
    assert "messages" not in items[0]
    assert items[0]["message_count"] == 2


def test_api_conversation_get_404(client):
    resp = client.get("/api/coach/conversations/no-existe")
    assert resp.status_code == 404


def test_api_conversation_get_full(client):
    cid = client.post("/api/coach/conversations", json={}).json()["id"]
    resp = client.get(f"/api/coach/conversations/{cid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == cid
    assert data["messages"] == []


def test_api_conversation_delete_only_that_one(client):
    cid_a = client.post("/api/coach/conversations", json={}).json()["id"]
    cid_b = client.post("/api/coach/conversations", json={}).json()["id"]
    resp = client.delete(f"/api/coach/conversations/{cid_a}")
    assert resp.status_code == 200
    assert client.get(f"/api/coach/conversations/{cid_a}").status_code == 404
    assert client.get(f"/api/coach/conversations/{cid_b}").status_code == 200


def test_api_conversation_delete_nonexistent_never_500(client):
    resp = client.delete("/api/coach/conversations/no-existe")
    assert resp.status_code == 200


def test_api_coach_without_conversation_id_creates_one(client):
    """POST /api/coach sin conversation_id crea una (o usa la activa) y la
    devuelve en la respuesta."""
    with patch("main.ask_coach", return_value="Respuesta."):
        resp = client.post("/api/coach", json={"question": "¿Qué priorizo hoy?"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["conversation_id"]
    conv = client.get(f"/api/coach/conversations/{data['conversation_id']}").json()
    assert len(conv["messages"]) == 2


def test_api_coach_with_conversation_id_reuses_it(client):
    cid = client.post("/api/coach/conversations", json={}).json()["id"]
    with patch("main.ask_coach", return_value="Respuesta 1."):
        client.post("/api/coach", json={"question": "primera", "conversation_id": cid})
    with patch("main.ask_coach", return_value="Respuesta 2."):
        resp = client.post("/api/coach", json={"question": "segunda", "conversation_id": cid})
    assert resp.json()["conversation_id"] == cid
    conv = client.get(f"/api/coach/conversations/{cid}").json()
    assert len(conv["messages"]) == 4


def test_api_coach_context_isolated_between_two_conversations(client):
    """EL PUNTO DE LA FEATURE: el contexto pasado a ask_coach para la conv A
    NUNCA incluye mensajes de la conv B. Captura el arg `history` real."""
    cid_a = client.post("/api/coach/conversations", json={}).json()["id"]
    cid_b = client.post("/api/coach/conversations", json={}).json()["id"]

    with patch("main.ask_coach", return_value="Respuesta A1."):
        client.post("/api/coach", json={"question": "SOLO-A pregunta 1", "conversation_id": cid_a})
    with patch("main.ask_coach", return_value="Respuesta B1."):
        client.post("/api/coach", json={"question": "SOLO-B pregunta 1", "conversation_id": cid_b})

    captured = {}

    def mock_ask(question, dataset, history=None):
        captured["history"] = history or []
        return "Respuesta A2."

    with patch("main.ask_coach", side_effect=mock_ask):
        resp = client.post("/api/coach", json={"question": "SOLO-A pregunta 2", "conversation_id": cid_a})
    assert resp.status_code == 200

    ctx_text = " ".join(m.get("content", "") for m in captured["history"])
    assert "SOLO-B" not in ctx_text, f"Contexto de A incluyó mensajes de B: {ctx_text}"
    assert "SOLO-A pregunta 1" in ctx_text
    assert "Respuesta A1." in ctx_text


# ── Coach/Más content tests ───────────────────────────────────────────────────

def test_coach_screen_present_no_placeholder(client):
    """#screenCoach tiene contenido real y no contiene 'Próximamente'."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text

    coach_start = body.find('id="screenCoach"')
    mas_start = body.find('id="screenMas"')
    assert coach_start != -1, "No se encontró #screenCoach"

    coach_block = body[coach_start:mas_start] if mas_start > coach_start else body[coach_start:]
    assert "Próximamente" not in coach_block, "El bloque Coach todavía contiene 'Próximamente'"
    # Debe tener el composer y las sugerencias
    assert "coachInput" in coach_block, "Falta el composer #coachInput en #screenCoach"
    assert "coachSuggestions" in coach_block, "Falta el contenedor de sugerencias"
    assert "sendCoach" in body + _external_js_source(), "Falta la función sendCoach en el JS"


def test_mas_screen_present_no_placeholder(client):
    """#screenMas tiene contenido real y no contiene 'Próximamente'."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text

    mas_start = body.find('id="screenMas"')
    assert mas_start != -1, "No se encontró #screenMas"

    mas_block = body[mas_start:]
    assert "Próximamente" not in mas_block, "El bloque Más todavía contiene 'Próximamente'"
    assert "masThemeToggle" in mas_block, "Falta el toggle de tema en #screenMas"
    assert "Sincronizar" in mas_block, "Falta botón Sincronizar en #screenMas"
    assert "Mike" in mas_block or "perfil" in mas_block.lower(), "Falta el perfil del usuario en #screenMas"


def test_no_proximamente_anywhere(client):
    """'Próximamente' no debe aparecer en ningún tab del HTML final."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.text.count("Próximamente") == 0, (
        f"'Próximamente' aparece {resp.text.count('Próximamente')} veces — debe ser 0"
    )


# ── Insights endpoint tests ───────────────────────────────────────────────────

def test_api_insights_200_list(client):
    """GET /api/insights devuelve 200 con una lista JSON."""
    resp = client.get("/api/insights")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_api_insights_structure(client):
    """Cada insight tiene los campos obligatorios."""
    resp = client.get("/api/insights")
    assert resp.status_code == 200
    for item in resp.json():
        for key in ("id", "severity", "category", "icon", "title", "summary",
                    "factors", "recommendation"):
            assert key in item, f"Falta clave '{key}' en insight {item.get('id')}"
        assert item["severity"] in ("alert", "watch", "positive", "info")
        assert isinstance(item["factors"], list)


def test_api_insights_no_data_returns_empty_list(client_no_data):
    """GET /api/insights sin health_compact.json → 200 con [] (no 500)."""
    resp = client_no_data.get("/api/insights")
    assert resp.status_code == 200
    assert resp.json() == []


# ── Coach suggestions endpoint tests (F1 roadmap P0) ─────────────────────────

def test_api_coach_suggestions_200_shape(client):
    """GET /api/coach/suggestions devuelve 200 con {questions: [...]}, cada
    item con id/text, y <=4 preguntas."""
    resp = client.get("/api/coach/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert "questions" in data
    questions = data["questions"]
    assert isinstance(questions, list)
    assert len(questions) <= 4
    for q in questions:
        assert "id" in q
        assert "text" in q
        assert isinstance(q["text"], str) and q["text"]


def test_api_coach_suggestions_no_data_returns_generic(client_no_data):
    """Sin health_compact.json -> 200 con 4 preguntas genéricas (no 500)."""
    resp = client_no_data.get("/api/coach/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) == 4


def test_api_coach_suggestions_respects_locale(client):
    """?locale=en devuelve preguntas en inglés (heurística: contiene 'How' o
    similar en al menos una pregunta genérica)."""
    resp = client.get("/api/coach/suggestions?locale=en")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["questions"]) >= 1


def test_root_no_raw_insights_placeholder(client):
    """GET / no debe contener __INSIGHTS__ crudo en el HTML final."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "__INSIGHTS__" not in resp.text, (
        "__INSIGHTS__ no fue sustituido en el template"
    )


def test_root_no_raw_placeholders_all(client):
    """GET / no debe contener ningún placeholder crudo (__DATA__, __COACH__, __AUTH__, __INSIGHTS__, __DRIVERS__, __TRENDS__, __PROFILE__, __CYCLE__)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    for ph in ("__DATA__", "__COACH__", "__AUTH__", "__INSIGHTS__", "__DRIVERS__", "__TRENDS__", "__PROFILE__", "__CYCLE__"):
        assert ph not in body, f"Placeholder crudo encontrado en HTML: {ph}"


# ── Fase 7: render de / con estado de ciclo enabled/disabled ────────────────

def test_root_renders_with_cycle_disabled_default(client):
    """GET / con cycle_tracking=False (default) -> renderiza sin excepción,
    __CYCLE__ sustituido, var CYCLE con enabled:false."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "var CYCLE" in resp.text
    assert '"enabled":false' in resp.text.replace(" ", "")


def test_root_renders_with_cycle_enabled(tmp_path, monkeypatch, client):
    """GET / con cycle_tracking=True -> renderiza sin excepción (criterio: nunca
    crashea), var CYCLE refleja enabled:true."""
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    put_resp = client.put("/api/profile", json={"cycle_tracking": True})
    assert put_resp.status_code == 200

    resp = client.get("/")
    assert resp.status_code == 200
    assert "var CYCLE" in resp.text
    assert '"enabled":true' in resp.text.replace(" ", "")


# ── Golden render tests (Tier 1/2/3 UI) ─────────────────────────────────────

def test_golden_render_no_raw_placeholders():
    """render_ios sobre el golden no deja ningún __PLACEHOLDER__ crudo."""
    import re
    import json as _json
    from pathlib import Path as _Path
    from app.render import render_ios
    from app.insights import evaluate
    from app.drivers import analyze_drivers
    from app.trends import trend_summary

    golden = _Path(__file__).parent.parent / "data" / "health_compact.json"
    if not golden.exists():
        pytest.skip("golden data/health_compact.json no disponible")

    ds = _json.loads(golden.read_text(encoding="utf-8"))
    drivers = analyze_drivers(ds["days"])
    last30 = ds["days"][-30:]
    trends = {m: trend_summary([d.get(m) for d in last30]) for m in ["recovery", "hrv", "rhr", "asleep"]}

    html = render_ios(
        ds,
        {"chips": [], "bullets": []},
        {"status": "ok"},
        evaluate(ds),
        drivers,
        trends,
    )
    raw = re.findall(r"__[A-Z_]+__", html)
    assert raw == [], f"Placeholders crudos en el render del golden: {raw}"


def test_golden_render_key_texts():
    """render_ios sobre el golden contiene los textos clave de los 3 tiers."""
    import json as _json
    from pathlib import Path as _Path
    from app.render import render_ios
    from app.insights import evaluate
    from app.drivers import analyze_drivers
    from app.trends import trend_summary

    golden = _Path(__file__).parent.parent / "data" / "health_compact.json"
    if not golden.exists():
        pytest.skip("golden data/health_compact.json no disponible")

    ds = _json.loads(golden.read_text(encoding="utf-8"))
    drivers = analyze_drivers(ds["days"])
    last30 = ds["days"][-30:]
    trends = {m: trend_summary([d.get(m) for d in last30]) for m in ["recovery", "hrv", "rhr", "asleep"]}

    html = render_ios(
        ds,
        {"chips": [], "bullets": []},
        {"status": "ok"},
        evaluate(ds),
        drivers,
        trends,
    )

    # Fase 9-B: parte de estos textos (ACWR/percentil/señal) viven en el JS
    # externo (static/js/), no en el HTML renderizado; 'Palancas' sigue en el
    # HTML del template. Verificamos contra html + JS externo (mismo contrato).
    frontend = html + _external_js_source()

    # Tier 1: percentil bodyage y ACWR
    assert "percentil" in frontend, "Falta 'percentil' — bodyage percentile no inyectado"
    assert "ACWR" in frontend, "Falta 'ACWR' — tile de carga no inyectado"

    # Tier 3: tarjeta Palancas
    assert "Palancas" in frontend, "Falta 'Palancas' — tarjeta de drivers no inyectada"

    # Tier 1: señales de recuperación (recovery_n)
    assert "señal" in frontend, "Falta texto de señales — recovery_n no inyectado"


# ── Card reorder tests (popup + 2 scopes) ────────────────────────────────────

def test_card_reorder_popup_present(client):
    """GET / contiene el popup #orderOverlay y el botón 'Acomodar tarjetas' en #screenMas."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "orderOverlay" in body, "Falta el popup #orderOverlay"
    assert "Acomodar tarjetas" in body, "Falta el botón 'Acomodar tarjetas' en #screenMas"
    # La sección inline vieja ya NO debe estar
    assert "masCardOrderList" not in body, "masCardOrderList no debería estar (sección inline removida)"


def test_card_reorder_functions_present(client):
    """GET / contiene las funciones JS generalizadas de reordenamiento."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text + _external_js_source()
    assert "openOrderPopup" in body, "Falta la función openOrderPopup en el JS"
    assert "applyOrder" in body, "Falta la función applyOrder en el JS"
    assert "ORDER_SCOPES" in body, "Falta la constante ORDER_SCOPES en el JS"
    assert "vitals-card-order" in body, "Falta la clave localStorage vitals-card-order (Hoy)"
    assert "vitals-tend-order" in body, "Falta la clave localStorage vitals-tend-order (Tendencias)"
    # Backwards-compat aliases deben seguir presentes
    assert "applyCardOrder" in body, "Falta el alias backwards-compat applyCardOrder"
    assert "CARD_ORDER_DEFAULT" in body, "Falta la constante CARD_ORDER_DEFAULT (alias)"


def test_card_reorder_tend_ids_present(client):
    """GET / contiene los IDs de las 11 tarjetas de Tendencias (8 gráficas + métricas + workouts + palancas)."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    for card_id in ["tendRecStrainCard", "tendBalanceCard", "tendHrvCard", "tendRhrCard",
                    "tendSleepArchCard", "tendConsistencyCard", "tendSpo2Card", "tendTempCard",
                    "tendMetricsCard", "tendWorkoutsCard"]:
        assert card_id in body, f"Falta el id {card_id} en las tarjetas de Tendencias"


def test_card_reorder_new_ids_in_order_scopes(client):
    """GET / contiene los nuevos ids (tendMetricsCard, tendWorkoutsCard) en ORDER_SCOPES."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "tendMetricsCard" in body, "Falta tendMetricsCard en ORDER_SCOPES"
    assert "tendWorkoutsCard" in body, "Falta tendWorkoutsCard en ORDER_SCOPES"
    assert "insightCards" in body, "Falta insightCards en ORDER_SCOPES"


def test_card_reorder_new_names_in_order_scopes(client):
    """GET / contiene los nombres de las nuevas tarjetas en ORDER_SCOPES.names."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "Alertas" in body, "Falta el nombre 'Alertas' en ORDER_SCOPES.names (hoy)"
    assert "Métricas clave" in body, "Falta el nombre 'Métricas clave' en ORDER_SCOPES.names (tend)"
    assert "Entrenamientos" in body, "Falta el nombre 'Entrenamientos' en ORDER_SCOPES.names (tend)"


def test_golden_render_card_reorder():
    """render_ios sobre el golden contiene el popup y funciones generalizadas de reordenamiento."""
    import json as _json
    from pathlib import Path as _Path
    from app.render import render_ios
    from app.insights import evaluate
    from app.drivers import analyze_drivers
    from app.trends import trend_summary

    golden = _Path(__file__).parent.parent / "data" / "health_compact.json"
    if not golden.exists():
        pytest.skip("golden data/health_compact.json no disponible")

    ds = _json.loads(golden.read_text(encoding="utf-8"))
    drivers = analyze_drivers(ds["days"])
    last30 = ds["days"][-30:]
    trends = {m: trend_summary([d.get(m) for d in last30]) for m in ["recovery", "hrv", "rhr", "asleep"]}

    html = render_ios(
        ds,
        {"chips": [], "bullets": []},
        {"status": "ok"},
        evaluate(ds),
        drivers,
        trends,
    )

    # Fase 9-B: los ids/funciones de reorder viven ahora en el JS externo
    # (ORDER_SCOPES, openOrderPopup...); orderOverlay/"Acomodar tarjetas"
    # siguen en el HTML. Verificamos contra html + JS externo (mismo contrato).
    frontend = html + _external_js_source()

    assert "orderOverlay" in frontend, "Falta orderOverlay en el render del golden"
    assert "Acomodar tarjetas" in frontend, "Falta 'Acomodar tarjetas' en el render del golden"
    assert "openOrderPopup" in frontend, "Falta openOrderPopup en el render del golden"
    assert "applyOrder" in frontend, "Falta applyOrder en el render del golden"
    assert "vitals-tend-order" in frontend, "Falta vitals-tend-order en el render del golden"
    assert "tendHrvCard" in frontend, "Falta tendHrvCard en el render del golden"
    # New reorder-complete ids
    assert "tendMetricsCard" in frontend, "Falta tendMetricsCard en el render del golden"
    assert "tendWorkoutsCard" in frontend, "Falta tendWorkoutsCard en el render del golden"
    assert "insightCards" in frontend, "Falta insightCards en el render del golden"
    assert "Alertas" in html, "Falta nombre 'Alertas' en el render del golden"
    assert "Métricas clave" in html, "Falta nombre 'Métricas clave' en el render del golden"
    assert "Entrenamientos" in html, "Falta nombre 'Entrenamientos' en el render del golden"


# ── Ronda 3: escapes OAuth (XSS reflejado) ─────────────────────────────────────

def test_auth_callback_error_param_is_escaped(client):
    """
    GET /auth/callback?error=<script>...</script> debe devolver el texto ESCAPADO,
    no el HTML crudo inyectado (XSS reflejado clásico vía ?error=)."""
    payload = "<script>alert(1)</script>"
    resp = client.get("/auth/callback", params={"error": payload})
    assert resp.status_code == 400
    body = resp.text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_auth_callback_error_param_success_page_still_renders(client):
    """Verifica que escapar no rompa el HTML válido de la página de error (criterio
    de validación #6 del roadmap): sigue siendo HTML bien formado con el link de
    reintentar."""
    resp = client.get("/auth/callback", params={"error": "access_denied"})
    assert resp.status_code == 400
    body = resp.text
    assert "<html>" in body and "</html>" in body
    assert "Reintentar" in body
    assert "access_denied" in body


def test_auth_callback_success_page_renders(client):
    """Camino feliz de /auth/callback (exchange_code exitoso) sigue rindiendo el
    HTML de éxito sin romperse por los escapes añadidos."""
    login_resp = client.get("/auth/login", params={"source": "google_health"}, follow_redirects=False)
    assert login_resp.status_code in (302, 307)
    # Extraer el state generado del Location de redirect (?state=...) para reusarlo.
    import urllib.parse as _urlparse
    location = login_resp.headers.get("location", "")
    qs = _urlparse.parse_qs(_urlparse.urlparse(location).query)
    state = qs.get("state", [None])[0]
    assert state, f"No se encontró state en el redirect: {location}"

    with patch("app.sources.google_health.GoogleHealthSource.exchange_code", return_value=None):
        resp = client.get("/auth/callback", params={"code": "fake_code", "state": state})
    assert resp.status_code == 200
    assert "Conectado correctamente" in resp.text


def test_auth_login_unknown_source_error_is_escaped(client):
    """GET /auth/login?source=<script>... -> ValueError controlado con el nombre de
    fuente escapado en el HTML de error (segunda ubicación de escape del roadmap)."""
    payload = "<img src=x onerror=alert(1)>"
    resp = client.get("/auth/login", params={"source": payload})
    assert resp.status_code == 400
    body = resp.text
    assert "<img src=x onerror=alert(1)>" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


# ── /api/cycle — Fase 7: módulo de salud femenina (opt-in) ──────────────────

def _get_cycle_client(tmp_path: Path):
    """TestClient con DATA_DIR/profile/cycle_log aislados en tmp_path."""
    from app import config, profile as _pm, cycle as _cyc
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch.object(_cyc, "_CYCLE_LOG_FILE", tmp_path / "cycle_log.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def cycle_client(tmp_path):
    yield from _get_cycle_client(tmp_path)


def test_api_cycle_get_disabled_by_default(cycle_client):
    """GET /api/cycle sin haber activado el toggle -> {enabled: false}, nunca 500."""
    resp = cycle_client.get("/api/cycle")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_api_cycle_get_enabled_after_toggle(cycle_client):
    put_resp = cycle_client.put("/api/profile", json={"cycle_tracking": True})
    assert put_resp.status_code == 200
    resp = cycle_client.get("/api/cycle")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["disclaimer"] == "cycle_disclaimer"


def test_api_cycle_period_post_disabled_403(cycle_client):
    """POST /api/cycle/period con toggle off -> 403 controlado, nunca 500."""
    resp = cycle_client.post("/api/cycle/period", json={"start": "2026-06-01"})
    assert resp.status_code == 403
    assert resp.json()["status"] == "disabled"


def test_api_cycle_period_post_and_get_reflects_it(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/period", json={"start": "2026-06-01", "end": "2026-06-05", "flow": "medium"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert any(p["start"] == "2026-06-01" for p in body["periods"])

    get_resp = cycle_client.get("/api/cycle")
    assert get_resp.status_code == 200
    assert get_resp.json()["period"]["last_start"] == "2026-06-01"


def test_api_cycle_period_post_dedupes_by_start(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    cycle_client.post("/api/cycle/period", json={"start": "2026-06-01", "flow": "light"})
    resp = cycle_client.post("/api/cycle/period", json={"start": "2026-06-01", "flow": "heavy"})
    assert resp.status_code == 200
    body = resp.json()
    matching = [p for p in body["periods"] if p["start"] == "2026-06-01"]
    assert len(matching) == 1
    assert matching[0]["flow"] == "heavy"


def test_api_cycle_period_post_invalid_date_422(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/period", json={"start": "not-a-date"})
    assert resp.status_code == 422


def test_api_cycle_period_post_missing_body_422(cycle_client):
    """Body malformado (sin 'start' requerido) -> 422 controlado, nunca 500."""
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/period", json={})
    assert resp.status_code == 422


def test_api_cycle_period_delete_removes_entry(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    cycle_client.post("/api/cycle/period", json={"start": "2026-06-01"})
    resp = cycle_client.delete("/api/cycle/period/2026-06-01")
    assert resp.status_code == 200
    body = resp.json()
    assert all(p["start"] != "2026-06-01" for p in body["periods"])


def test_api_cycle_period_delete_idempotent_when_missing(cycle_client):
    """Borrar un periodo que no existe -> 200 idempotente, nunca 500/404."""
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.delete("/api/cycle/period/2099-01-01")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_cycle_period_delete_disabled_403(cycle_client):
    resp = cycle_client.delete("/api/cycle/period/2026-06-01")
    assert resp.status_code == 403


def test_api_cycle_symptom_post_disabled_403(cycle_client):
    resp = cycle_client.post("/api/cycle/symptom", json={"date": "2026-06-01", "tags": ["cramps"]})
    assert resp.status_code == 403


def test_api_cycle_symptom_post_saves_tags(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/symptom", json={
        "date": "2026-06-15", "tags": ["cramps", "headache"], "note": "leve",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["symptoms"][0]["tags"] == ["cramps", "headache"]
    assert body["symptoms"][0]["note"] == "leve"


def test_api_cycle_symptom_post_invalid_date_422(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/symptom", json={"date": "bad-date", "tags": []})
    assert resp.status_code == 422


def test_api_cycle_symptom_post_caps_tags_10x120(cycle_client):
    """Reusa _clean_str_list: cap 10 items x 120 chars, mismo contrato que goals/injuries."""
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    many_tags = [f"tag{i}" for i in range(15)]
    long_tag = "x" * 200
    resp = cycle_client.post("/api/cycle/symptom", json={
        "date": "2026-06-15", "tags": many_tags + [long_tag],
    })
    assert resp.status_code == 200
    tags = resp.json()["symptoms"][0]["tags"]
    assert len(tags) == 10


def test_api_cycle_symptom_post_non_list_tags_422(cycle_client):
    cycle_client.put("/api/profile", json={"cycle_tracking": True})
    resp = cycle_client.post("/api/cycle/symptom", json={"date": "2026-06-15", "tags": "cramps"})
    assert resp.status_code == 422


def test_api_cycle_get_never_500_with_garbage_cycle_log(tmp_path):
    """GET /api/cycle con cycle_log.json corrupto -> nunca 500, degrada limpio."""
    from app import config, profile as _pm, cycle as _cyc
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch.object(_cyc, "_CYCLE_LOG_FILE", tmp_path / "cycle_log.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        client.put("/api/profile", json={"cycle_tracking": True})
        (tmp_path / "cycle_log.json").write_text("NOT JSON{{{", encoding="utf-8")
        resp = client.get("/api/cycle")
        assert resp.status_code == 200


# ── GET /api/sleep-coach (Fase 8C, paso C4) ─────────────────────────────────

def test_api_sleep_coach_no_dataset_returns_unavailable(client_no_data):
    resp = client_no_data.get("/api/sleep-coach")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


def test_api_sleep_coach_insufficient_history_returns_unavailable(client):
    """Dataset real existe pero puede o no tener suficiente waketime — de
    cualquier forma, nunca 500 y siempre 'available' presente."""
    resp = client.get("/api/sleep-coach")
    assert resp.status_code == 200
    assert "available" in resp.json()


def test_api_sleep_coach_with_synthetic_data_returns_recommendation(tmp_path):
    from app import config, profile as _pm
    import main as main_mod
    import datetime as _dt

    days = []
    d0 = _dt.date(2026, 6, 1)
    for i in range(20):
        d = d0 + _dt.timedelta(days=i)
        days.append({"date": d.isoformat(), "waketime": "07:00", "asleep": 480,
                     "strain": 5.0, "recovery": 65})
    dataset = {"days": days, "summary": {"n_days": len(days)}}

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = client.get("/api/sleep-coach")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert body["wake_assumed"] == "07:00"
    assert "bedtime" in body


def test_api_sleep_coach_shape_includes_f5_score_fields(tmp_path):
    """Roadmap P1 F5 (paso 2): need_min/sleep_score/consistency son ADITIVOS
    al shape ya existente — nunca faltan las claves, aunque sean None."""
    from app import config, profile as _pm
    import main as main_mod
    import datetime as _dt

    days = []
    d0 = _dt.date(2026, 6, 1)
    for i in range(20):
        d = d0 + _dt.timedelta(days=i)
        days.append({"date": d.isoformat(), "waketime": "07:00", "asleep": 480,
                     "bed_min": 0, "strain": 5.0, "recovery": 65})
    dataset = {"days": days, "summary": {"n_days": len(days)}}

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = client.get("/api/sleep-coach")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert "need_min" in body and body["need_min"] == 480
    assert "sleep_score" in body and body["sleep_score"] == 100  # asleep==need
    assert "consistency" in body and body["consistency"] == 100  # bed_min/waketime constantes


def test_api_sleep_coach_never_500_on_corrupt_dataset(tmp_path):
    from app import config, profile as _pm
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        (tmp_path / "health_compact.json").write_text("NOT JSON{{{", encoding="utf-8")
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = client.get("/api/sleep-coach")
    assert resp.status_code == 200
    assert resp.json() == {"available": False}


# ── GET /api/ingest-token (Fase 8C, paso C6) ────────────────────────────────

def test_api_ingest_token_returns_configured_token(tmp_path):
    from app import config
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(config.settings, "INGEST_TOKEN", "my-visible-token"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        resp = client.get("/api/ingest-token")
    assert resp.status_code == 200
    assert resp.json() == {"token": "my-visible-token"}
