"""
test_coach_mental_endpoints.py — Tests de endpoints del Coach Deportivo
(app/routes/coach_mental.py + bifurcación en app/routes/coach.py::api_coach),
roadmap coach-mental Fase 1, Paso 4. Mismo harness que tests/test_endpoints.py.

Cubre (criterios de aceptación 1-4 del roadmap):
1. PUT/GET /api/coach/mental/profile — round-trip; sin perfil -> {}.
2. POST /api/coach/mental/session — crea conversación kind=mental_master,
   título "Sesión Master — YYYY-MM-DD", apertura vía LLM mockeado (y
   fallback estático si el LLM está caído, nunca 500).
3. POST /api/coach bifurca a ask_master en conversación master y a ask_coach
   en conversación normal (parchea ambos, verifica cuál se llamó).
4. POST /api/coach/mental/session/{cid}/close guarda en el expediente;
   404 controlado en cid inexistente o kind normal.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _get_client(tmp_path: Path):
    from app import config
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    """Aísla coach_store + mental_store en tmp_path (nunca toca data/ real) —
    mismo patrón que test_endpoints.py::_isolate_coach_history."""
    from app import coach_store
    from app import mental_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    monkeypatch.setattr(mental_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(mental_store, "_STORE_FILE", tmp_path / "mental_log.json")


@pytest.fixture
def client(tmp_path):
    yield from _get_client(tmp_path)


# ── Criterio 1: perfil ────────────────────────────────────────────────────────

class TestProfileEndpoint:
    def test_get_profile_empty_when_no_seed(self, client):
        resp = client.get("/api/coach/mental/profile")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_put_then_get_round_trip(self, client):
        payload = {
            "archetype": "El Sabio con bisturí",
            "calibraciones": ["UN corte directo por sesión"],
            "survey": {"motor": ["competir - odio perder"]},
            "deporte": "padel",
        }
        put_resp = client.put("/api/coach/mental/profile", json=payload)
        assert put_resp.status_code == 200

        get_resp = client.get("/api/coach/mental/profile")
        assert get_resp.status_code == 200
        assert get_resp.json() == payload

    def test_put_non_dict_body_returns_400(self, client):
        resp = client.put("/api/coach/mental/profile", json=["no", "es", "dict"])
        assert resp.status_code == 400

    def test_put_overwrites_previous_profile(self, client):
        client.put("/api/coach/mental/profile", json={"archetype": "A"})
        client.put("/api/coach/mental/profile", json={"archetype": "B"})
        resp = client.get("/api/coach/mental/profile")
        assert resp.json() == {"archetype": "B"}


# ── Criterio 2: abrir Sesión Master ─────────────────────────────────────────

class TestSessionStart:
    def test_start_creates_master_conversation_with_opening(self, client):
        with patch("main.ask_master") as _unused, \
             patch("app.coach_mental.opening_message", return_value="¿Cómo llegas esta semana?"):
            resp = client.post("/api/coach/mental/session")
        assert resp.status_code == 200
        data = resp.json()
        assert data["opening"] == "¿Cómo llegas esta semana?"
        cid = data["conversation_id"]
        assert cid

        from app import coach_store
        conv = coach_store.get_conversation(cid)
        assert conv["kind"] == "mental_master"
        assert conv["title"].startswith("Sesión Master — ")
        assert len(conv["messages"]) == 1
        assert conv["messages"][0]["role"] == "assistant"
        assert conv["messages"][0]["content"] == "¿Cómo llegas esta semana?"

    def test_start_sets_conversation_active(self, client):
        with patch("app.coach_mental.opening_message", return_value="hola"):
            resp = client.post("/api/coach/mental/session")
        cid = resp.json()["conversation_id"]
        from app import coach_store
        assert coach_store.get_active_id() == cid

    def test_start_never_500_when_llm_down(self, client):
        """opening_message ya degrada internamente a fallback i18n — el
        endpoint nunca debe dar 500 aunque el LLM esté caído."""
        with patch("app.llm.generate", return_value=None):
            resp = client.post("/api/coach/mental/session")
        assert resp.status_code == 200
        assert resp.json()["opening"]  # nunca vacío

    def test_start_appears_in_conversations_list_with_kind(self, client):
        with patch("app.coach_mental.opening_message", return_value="hola"):
            resp = client.post("/api/coach/mental/session")
        cid = resp.json()["conversation_id"]
        list_resp = client.get("/api/coach/conversations")
        items = list_resp.json()
        match = next(i for i in items if i["id"] == cid)
        assert match["kind"] == "mental_master"


# ── Criterio 3: bifurcación en POST /api/coach ─────────────────────────────

class TestCoachBifurcation:
    def test_master_conversation_calls_ask_master_not_ask_coach(self, client):
        with patch("app.coach_mental.opening_message", return_value="hola"):
            start_resp = client.post("/api/coach/mental/session")
        cid = start_resp.json()["conversation_id"]

        with patch("main.ask_master", return_value="Respuesta del Coach Deportivo.") as mock_master, \
             patch("main.ask_coach", return_value="NO debería llamarse") as mock_coach:
            resp = client.post("/api/coach", json={"question": "¿cómo vengo?", "conversation_id": cid})

        assert resp.status_code == 200
        assert resp.json()["answer"] == "Respuesta del Coach Deportivo."
        mock_master.assert_called_once()
        mock_coach.assert_not_called()

    def test_normal_conversation_calls_ask_coach_not_ask_master(self, client):
        create_resp = client.post("/api/coach/conversations", json={})
        cid = create_resp.json()["id"]

        with patch("main.ask_coach", return_value="Respuesta normal.") as mock_coach, \
             patch("main.ask_master", return_value="NO debería llamarse") as mock_master:
            resp = client.post("/api/coach", json={"question": "¿qué priorizo?", "conversation_id": cid})

        assert resp.status_code == 200
        assert resp.json()["answer"] == "Respuesta normal."
        mock_coach.assert_called_once()
        mock_master.assert_not_called()

    def test_no_conversation_id_defaults_to_chat_path(self, client):
        """Sin conversation_id explícito y sin activa previa, el prompt sigue
        el camino normal (ask_coach) — cero regresión del flujo existente."""
        with patch("main.ask_coach", return_value="ok") as mock_coach, \
             patch("main.ask_master") as mock_master:
            resp = client.post("/api/coach", json={"question": "hola"})
        assert resp.status_code == 200
        mock_coach.assert_called_once()
        mock_master.assert_not_called()


# ── Criterio 4: cerrar Sesión Master ────────────────────────────────────────

class TestSessionClose:
    def test_close_saves_to_expediente_and_returns_focos_resumen(self, client):
        with patch("app.coach_mental.opening_message", return_value="hola"):
            start_resp = client.post("/api/coach/mental/session")
        cid = start_resp.json()["conversation_id"]

        with patch(
            "app.llm.generate",
            return_value='{"resumen": "Buena sesión sobre presión.", "temas": ["presión"], "focos": ["respirar"]}',
        ):
            resp = client.post(f"/api/coach/mental/session/{cid}/close")

        assert resp.status_code == 200
        data = resp.json()
        assert data["saved"] is True
        assert data["resumen"] == "Buena sesión sobre presión."
        assert data["focos"] == ["respirar"]

        from app import mental_store
        saved = mental_store.list_sessions()
        assert len(saved) == 1
        assert saved[0]["conversation_id"] == cid

    def test_close_nonexistent_cid_returns_404(self, client):
        resp = client.post("/api/coach/mental/session/no-existe/close")
        assert resp.status_code == 404

    def test_close_normal_conversation_kind_returns_404(self, client):
        create_resp = client.post("/api/coach/conversations", json={})
        cid = create_resp.json()["id"]
        resp = client.post(f"/api/coach/mental/session/{cid}/close")
        assert resp.status_code == 404

    def test_close_never_loses_session_when_llm_down(self, client):
        with patch("app.coach_mental.opening_message", return_value="hola"):
            start_resp = client.post("/api/coach/mental/session")
        cid = start_resp.json()["conversation_id"]

        with patch("app.llm.generate", return_value=None):
            resp = client.post(f"/api/coach/mental/session/{cid}/close")

        assert resp.status_code == 200
        assert resp.json()["saved"] is True
        from app import mental_store
        assert len(mental_store.list_sessions()) == 1
