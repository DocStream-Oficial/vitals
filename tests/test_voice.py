"""
tests/test_voice.py — Tests de app/voice.py (cliente stdlib STT/TTS +
caché de audio, roadmap coach-voz Paso 2).

Cubre:
- transcribe()/synthesize(): urllib mockeado (feliz/timeout/HTTP
  error/JSON roto/cuerpo vacío), nunca lanzan.
- Recorte de texto a 1200 chars en el último fin de oración.
- Caché de audio: round-trip, cap de 50 archivos (evicta el más viejo),
  regex de audio_id (incluye intento de path traversal), aislamiento
  household entre dos usuarios (patrón de tests/test_mental_store.py).
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from app import voice
from app.config import settings


# ── fixture: aísla voice._DATA_DIR (nunca toca data/ real) ────────────────────

@pytest.fixture
def voice_mod(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "_DATA_DIR", tmp_path)
    return voice


class _FakeHttpResponse:
    def __init__(self, status, body_bytes):
        self.status = status
        self._body = body_bytes

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── transcribe() ─────────────────────────────────────────────────────────────

class TestTranscribe:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")
        body = json.dumps({"text": " hola mundo ", "duration_s": 2.1}).encode("utf-8")

        def _fake_urlopen(req, timeout=None):
            assert req.get_method() == "POST"
            assert req.data == b"fake-bytes"
            return _FakeHttpResponse(200, body)

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        result = voice.transcribe(b"fake-bytes", "audio/webm")
        assert result == "hola mundo"

    def test_empty_bytes_returns_none_without_calling_urlopen(self, monkeypatch):
        called = {"n": 0}

        def _fake_urlopen(req, timeout=None):
            called["n"] += 1
            return _FakeHttpResponse(200, b"{}")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"", "audio/webm") is None
        assert called["n"] == 0

    def test_empty_stt_url_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "")
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_http_error_status_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(503, b'{"error":"down"}')

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_connection_refused_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_timeout_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("timed out")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_malformed_json_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(200, b"NOT JSON{{{")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_missing_text_field_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(200, b'{"duration_s": 1.0}')

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_unexpected_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")

        def _boom(req, timeout=None):
            raise RuntimeError("algo inesperado")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        assert voice.transcribe(b"bytes", "audio/webm") is None

    def test_sends_content_type_header(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_STT_URL", "http://127.0.0.1:8102")
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["headers"] = dict(req.header_items())
            return _FakeHttpResponse(200, b'{"text": "ok"}')

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        voice.transcribe(b"bytes", "audio/mp4")
        assert captured["headers"].get("Content-type") == "audio/mp4"


# ── synthesize() ─────────────────────────────────────────────────────────────

class TestSynthesize:
    def test_happy_path(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")
        monkeypatch.setattr(settings, "VITALS_TTS_SPEAKER", "alfred")
        wav_bytes = b"RIFF....WAVEfmt "

        def _fake_urlopen(req, timeout=None):
            payload = json.loads(req.data.decode("utf-8"))
            assert payload["text"] == "Hola, ¿cómo vas?"
            assert payload["speaker"] == "alfred"
            assert payload["language"] == "es"
            return _FakeHttpResponse(200, wav_bytes)

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        result = voice.synthesize("Hola, ¿cómo vas?", locale="es")
        assert result == wav_bytes

    def test_empty_text_returns_none_without_calling_urlopen(self, monkeypatch):
        called = {"n": 0}

        def _fake_urlopen(req, timeout=None):
            called["n"] += 1
            return _FakeHttpResponse(200, b"x")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.synthesize("", locale="es") is None
        assert called["n"] == 0

    def test_empty_tts_url_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "")
        assert voice.synthesize("hola", locale="es") is None

    def test_http_error_status_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")

        def _fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(500, b"")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.synthesize("hola", locale="es") is None

    def test_connection_refused_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")

        def _fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.synthesize("hola", locale="es") is None

    def test_empty_body_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")

        def _fake_urlopen(req, timeout=None):
            return _FakeHttpResponse(200, b"")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        assert voice.synthesize("hola", locale="es") is None

    def test_unexpected_exception_returns_none(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")

        def _boom(req, timeout=None):
            raise RuntimeError("algo inesperado")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        assert voice.synthesize("hola", locale="es") is None

    def test_locale_passed_as_language(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeHttpResponse(200, b"wav")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        voice.synthesize("hello", locale="en")
        assert captured["payload"]["language"] == "en"

    def test_truncates_long_text_before_sending(self, monkeypatch):
        monkeypatch.setattr(settings, "VITALS_TTS_URL", "http://127.0.0.1:8100")
        long_text = "Frase uno. " * 200  # muy por encima de 1200 chars
        captured = {}

        def _fake_urlopen(req, timeout=None):
            captured["payload"] = json.loads(req.data.decode("utf-8"))
            return _FakeHttpResponse(200, b"wav")

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        voice.synthesize(long_text, locale="es")
        assert len(captured["payload"]["text"]) <= 1200


# ── _truncate_at_sentence_end() ────────────────────────────────────────────

class TestTruncateAtSentenceEnd:
    def test_short_text_unchanged(self):
        assert voice._truncate_at_sentence_end("Hola.") == "Hola."

    def test_empty_text(self):
        assert voice._truncate_at_sentence_end("") == ""

    def test_truncates_at_last_sentence_end_within_limit(self):
        text = ("A" * 500) + ". " + ("B" * 800) + ". " + ("C" * 500)
        out = voice._truncate_at_sentence_end(text, limit=1200)
        assert len(out) <= 1200
        assert out.endswith(".")
        assert "C" not in out

    def test_hard_cut_when_no_sentence_end_in_range(self):
        text = "A" * 2000  # sin ningún punto/exclamación/interrogación
        out = voice._truncate_at_sentence_end(text, limit=1200)
        assert len(out) == 1200


# ── Caché de audio ──────────────────────────────────────────────────────────

class TestAudioCache:
    def test_save_and_retrieve_round_trip(self, voice_mod):
        audio_id = voice_mod.save_audio(b"RIFF-fake-wav-bytes")
        assert audio_id
        path = voice_mod.audio_path(audio_id)
        assert path is not None
        assert path.exists()
        assert path.read_bytes() == b"RIFF-fake-wav-bytes"

    def test_save_empty_bytes_returns_none(self, voice_mod):
        assert voice_mod.save_audio(b"") is None

    def test_audio_path_invalid_id_returns_none_without_touching_fs(self, voice_mod, tmp_path):
        # Path traversal: no debe matchear el regex -> None ANTES de construir
        # ninguna ruta o tocar el filesystem.
        assert voice_mod.audio_path("../../profile") is None
        assert voice_mod.audio_path("") is None
        assert voice_mod.audio_path(None) is None
        # Ni siquiera se creó el directorio de caché.
        assert not (tmp_path / "voice_audio").exists()

    def test_audio_path_nonexistent_valid_id_returns_none(self, voice_mod):
        assert voice_mod.audio_path("aaaaaaaaaaaa") is None

    def test_audio_path_rejects_id_too_short_or_too_long(self, voice_mod):
        assert voice_mod.audio_path("short") is None  # <8 chars
        assert voice_mod.audio_path("x" * 40) is None  # >32 chars

    def test_cap_evicts_oldest(self, voice_mod, monkeypatch):
        monkeypatch.setattr(voice_mod, "_MAX_AUDIO_FILES", 3)
        ids = []
        for i in range(5):
            aid = voice_mod.save_audio(f"wav-{i}".encode("utf-8"))
            ids.append(aid)
        d = voice_mod._voice_audio_dir()
        remaining = sorted(p.name for p in d.glob("*.wav"))
        assert len(remaining) == 3
        # Los 2 primeros (más viejos) fueron evictados.
        assert f"{ids[0]}.wav" not in remaining
        assert f"{ids[1]}.wav" not in remaining
        assert f"{ids[4]}.wav" in remaining

    def test_household_writes_under_user_data_dir(self, tmp_path, monkeypatch):
        """Mismo patrón que tests/test_mental_store.py::TestHousehold — con
        contexto de usuario activo, el audio vive en
        data/users/<uid>/voice_audio/, aislado del legacy."""
        from app import userctx as uc

        monkeypatch.setattr(uc, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(voice, "_DATA_DIR", tmp_path / "legacy_should_not_be_used")

        user = uc.add_user("Doc")
        assert user is not None
        token = uc.set_current_uid(user["id"])
        try:
            assert uc.should_use_household_paths() is True
            audio_id = voice.save_audio(b"household-wav")
        finally:
            uc.reset_current_uid(token)

        expected_file = tmp_path / "users" / user["id"] / "voice_audio" / f"{audio_id}.wav"
        assert expected_file.exists()
        assert not (tmp_path / "legacy_should_not_be_used").exists()

    def test_two_users_do_not_share_audio_cache(self, tmp_path, monkeypatch):
        from app import userctx as uc

        monkeypatch.setattr(uc, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(voice, "_DATA_DIR", tmp_path)

        user_a = uc.add_user("A")
        user_b = uc.add_user("B")

        token = uc.set_current_uid(user_a["id"])
        try:
            audio_id = voice.save_audio(b"solo de A")
        finally:
            uc.reset_current_uid(token)

        token = uc.set_current_uid(user_b["id"])
        try:
            # El id de A no es servible desde el contexto de B.
            assert voice.audio_path(audio_id) is None
        finally:
            uc.reset_current_uid(token)
