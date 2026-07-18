"""
tests/test_coach_voice_endpoints.py — Tests de endpoints de notas de voz del
Coach (app/routes/coach_voice.py, roadmap coach-voz Paso 3). Mismo harness
que tests/test_coach_mental_endpoints.py.

Cubre (criterios de aceptación 1-4 del roadmap):
1. POST /api/coach/voice: transcribe (mockeado), bifurca por kind de
   conversación (normal -> ask_coach, master -> ask_master), persiste el
   turno, sintetiza (mockeado), devuelve {transcript, answer,
   conversation_id, audio_id, voice}.
2. Degradación con gracia: STT caído -> error_key sin persistir turno; TTS
   caído -> turno persistido, audio_id=None, voice=False.
3. GET /api/coach/voice/audio/{audio_id} sirve el WAV; id fuera de formato
   (path traversal) -> 404 sin tocar filesystem.
4. Body vacío -> 400; body >15MB -> 413.
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
    """Aísla coach_store + mental_store + voice (caché de audio) en
    tmp_path — mismo patrón que test_coach_mental_endpoints.py."""
    from app import coach_store, mental_store, voice
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    monkeypatch.setattr(mental_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(mental_store, "_STORE_FILE", tmp_path / "mental_log.json")
    monkeypatch.setattr(voice, "_DATA_DIR", tmp_path)


@pytest.fixture
def client(tmp_path):
    yield from _get_client(tmp_path)


_WAV_BYTES = b"RIFF-fake-wav-bytes"


# ── Criterio 1: flujo feliz + bifurcación normal/master ─────────────────────

class TestHappyPath:
    def test_normal_conversation_transcribes_answers_and_synthesizes(self, client):
        with patch("app.voice.transcribe", return_value="¿cómo vengo hoy?") as mock_stt, \
             patch("main.ask_coach", return_value="Vienes bien.") as mock_coach, \
             patch("main.ask_master") as mock_master, \
             patch("app.voice.synthesize", return_value=_WAV_BYTES) as mock_tts:
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/webm"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["transcript"] == "¿cómo vengo hoy?"
        assert data["answer"] == "Vienes bien."
        assert data["conversation_id"]
        assert data["voice"] is True
        assert data["audio_id"]
        mock_stt.assert_called_once()
        mock_coach.assert_called_once()
        mock_master.assert_not_called()
        mock_tts.assert_called_once()

        # El turno quedó persistido con el TRANSCRIPT como pregunta.
        from app import coach_store
        conv = coach_store.get_conversation(data["conversation_id"])
        assert conv["messages"][-2]["content"] == "¿cómo vengo hoy?"
        assert conv["messages"][-1]["content"] == "Vienes bien."

    def test_master_conversation_calls_ask_master_not_ask_coach(self, client):
        # Arranca una Sesión Master real (kind=mental_master).
        with patch("app.coach_mental.opening_message", return_value="hola"):
            start_resp = client.post("/api/coach/mental/session")
        cid = start_resp.json()["conversation_id"]

        with patch("app.voice.transcribe", return_value="vengo tenso"), \
             patch("main.ask_master", return_value="Respiremos.") as mock_master, \
             patch("main.ask_coach", return_value="NO debería llamarse") as mock_coach, \
             patch("app.voice.synthesize", return_value=_WAV_BYTES):
            resp = client.post(
                f"/api/coach/voice?conversation_id={cid}", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/mp4"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "Respiremos."
        mock_master.assert_called_once()
        mock_coach.assert_not_called()

    def test_audio_id_servible_via_get_endpoint(self, client):
        with patch("app.voice.transcribe", return_value="hola"), \
             patch("main.ask_coach", return_value="hola de vuelta"), \
             patch("app.voice.synthesize", return_value=_WAV_BYTES):
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/webm"},
            )
        audio_id = resp.json()["audio_id"]
        get_resp = client.get(f"/api/coach/voice/audio/{audio_id}")
        assert get_resp.status_code == 200
        assert get_resp.content == _WAV_BYTES
        assert get_resp.headers["content-type"] == "audio/wav"

    def test_no_conversation_id_creates_or_reuses_active(self, client):
        with patch("app.voice.transcribe", return_value="hola"), \
             patch("main.ask_coach", return_value="hola de vuelta"), \
             patch("app.voice.synthesize", return_value=None):
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/webm"},
            )
        assert resp.status_code == 200
        assert resp.json()["conversation_id"]


# ── Criterio 2: degradación con gracia ──────────────────────────────────────

class TestDegradation:
    def test_stt_down_returns_error_key_without_persisting_turn(self, client):
        from app import coach_store
        before = coach_store.list_conversations()

        with patch("app.voice.transcribe", return_value=None), \
             patch("main.ask_coach") as mock_coach, \
             patch("main.ask_master") as mock_master:
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/webm"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["error_key"] == "voice_stt_down"
        assert data["message"]
        mock_coach.assert_not_called()
        mock_master.assert_not_called()

        after = coach_store.list_conversations()
        assert after == before  # sin turno persistido, sin conversación nueva

    def test_tts_down_persists_turn_with_no_audio(self, client):
        with patch("app.voice.transcribe", return_value="hola"), \
             patch("main.ask_coach", return_value="respuesta sin audio") as mock_coach, \
             patch("app.voice.synthesize", return_value=None) as mock_tts:
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={"Content-Type": "audio/webm"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["answer"] == "respuesta sin audio"
        assert data["audio_id"] is None
        assert data["voice"] is False
        mock_coach.assert_called_once()
        mock_tts.assert_called_once()

        # El turno SÍ se persistió (el coach respondió, solo falta el audio).
        from app import coach_store
        conv = coach_store.get_conversation(data["conversation_id"])
        assert conv["messages"][-2]["content"] == "hola"
        assert conv["messages"][-1]["content"] == "respuesta sin audio"


# ── Criterio 3: GET audio — path traversal / 404 ────────────────────────────

class TestAudioEndpointSafety:
    def test_path_traversal_id_returns_404(self, client):
        resp = client.get("/api/coach/voice/audio/..%2F..%2Fprofile")
        assert resp.status_code == 404

    def test_nonexistent_valid_id_returns_404(self, client):
        resp = client.get("/api/coach/voice/audio/aaaaaaaaaaaa")
        assert resp.status_code == 404

    def test_audio_from_other_user_not_servible(self, client, tmp_path, monkeypatch):
        """Aislamiento household: el audio de un usuario no es servible desde
        el contexto de otro (mismo criterio 4 del roadmap)."""
        from app import userctx as uc
        monkeypatch.setattr(uc, "_DATA_DIR", tmp_path)

        user_a = uc.add_user("A")
        user_b = uc.add_user("B")

        with patch("app.voice.transcribe", return_value="hola"), \
             patch("main.ask_coach", return_value="respuesta"), \
             patch("app.voice.synthesize", return_value=_WAV_BYTES):
            resp = client.post(
                "/api/coach/voice", content=b"fake-audio-bytes",
                headers={
                    "Content-Type": "audio/webm",
                    "X-Vitals-User": user_a["id"],
                },
            )
        audio_id = resp.json()["audio_id"]
        assert audio_id

        get_resp = client.get(
            f"/api/coach/voice/audio/{audio_id}",
            headers={"X-Vitals-User": user_b["id"]},
        )
        assert get_resp.status_code == 404


# ── Criterio 4: validación de body ──────────────────────────────────────────

class TestBodyValidation:
    def test_empty_body_returns_400(self, client):
        resp = client.post(
            "/api/coach/voice", content=b"", headers={"Content-Type": "audio/webm"},
        )
        assert resp.status_code == 400

    def test_body_too_large_returns_413(self, client):
        big = b"x" * (15 * 1024 * 1024 + 1)
        resp = client.post(
            "/api/coach/voice", content=big, headers={"Content-Type": "audio/webm"},
        )
        assert resp.status_code == 413
