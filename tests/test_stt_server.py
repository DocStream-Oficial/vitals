"""
tests/test_stt_server.py — Tests del server STT standalone
(scripts/voice/stt_server.py, roadmap coach-voz Paso 1).

whisper se mockea vía sys.modules ANTES de llamar load_model() (import
diferido dentro de la función — ver su docstring), así el módulo se puede
importar y testear sin GPU ni modelo real. NUNCA se descarga nada.

NADA en app/ importa scripts/voice/stt_server.py — es standalone; este
archivo lo importa directo agregando scripts/voice/ a sys.path.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

_SCRIPTS_VOICE = Path(__file__).parent.parent / "scripts" / "voice"


@pytest.fixture
def stt_module(monkeypatch):
    """Importa scripts/voice/stt_server.py fresco en cada test (nunca cae
    en un sys.modules cacheado de un test anterior) y arranca sin modelo
    cargado (_model = None) para que cada test controle su propio estado."""
    monkeypatch.syspath_prepend(str(_SCRIPTS_VOICE))
    sys.modules.pop("stt_server", None)
    import stt_server
    stt_server._model = None
    yield stt_server
    sys.modules.pop("stt_server", None)
    sys.modules.pop("whisper", None)


def _fake_whisper(transcribe_result=None, raise_on_load=False, raise_on_transcribe=False):
    """Módulo whisper falso: load_model(name) -> objeto con .transcribe()."""
    fake = types.ModuleType("whisper")
    model = MagicMock()
    if raise_on_transcribe:
        model.transcribe.side_effect = RuntimeError("boom")
    else:
        model.transcribe.return_value = transcribe_result or {
            "text": "hola", "segments": [{"end": 1.5}],
        }

    def _load_model(name):
        if raise_on_load:
            raise RuntimeError("no pude cargar el modelo")
        return model

    fake.load_model = _load_model
    return fake, model


# ── GET /health ──────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_without_model_loaded(self, stt_module):
        client = TestClient(stt_module.app)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["model_loaded"] is False
        assert data["model"] == stt_module.WHISPER_MODEL

    def test_health_with_model_loaded(self, stt_module, monkeypatch):
        fake, _ = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        resp = client.get("/health")
        assert resp.json()["model_loaded"] is True


# ── load_model() ──────────────────────────────────────────────────────────────

class TestLoadModel:
    def test_load_model_failure_leaves_model_none(self, stt_module, monkeypatch):
        fake, _ = _fake_whisper(raise_on_load=True)
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        assert stt_module._model is None

    def test_load_model_success_sets_model(self, stt_module, monkeypatch):
        fake, model = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        assert stt_module._model is model


# ── POST /transcribe ────────────────────────────────────────────────────────

class TestTranscribe:
    def test_transcribe_happy_path(self, stt_module, monkeypatch):
        fake, model = _fake_whisper(
            transcribe_result={"text": " hola mundo ", "segments": [{"end": 2.3}]}
        )
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        resp = client.post(
            "/transcribe", content=b"fake-audio-bytes",
            headers={"Content-Type": "audio/webm"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == "hola mundo"
        assert data["duration_s"] == 2.3
        model.transcribe.assert_called_once()

    def test_transcribe_passes_lang_query_param(self, stt_module, monkeypatch):
        fake, model = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        client.post(
            "/transcribe?lang=en", content=b"fake-audio-bytes",
            headers={"Content-Type": "audio/mp4"},
        )
        _, kwargs = model.transcribe.call_args
        assert kwargs.get("language") == "en"

    def test_transcribe_defaults_lang_to_es(self, stt_module, monkeypatch):
        fake, model = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        client.post(
            "/transcribe", content=b"fake-audio-bytes",
            headers={"Content-Type": "audio/mp4"},
        )
        _, kwargs = model.transcribe.call_args
        assert kwargs.get("language") == "es"

    def test_transcribe_empty_body_returns_400(self, stt_module, monkeypatch):
        fake, _ = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        resp = client.post(
            "/transcribe", content=b"", headers={"Content-Type": "audio/webm"},
        )
        assert resp.status_code == 400

    def test_transcribe_without_model_returns_503(self, stt_module):
        client = TestClient(stt_module.app)
        resp = client.post(
            "/transcribe", content=b"fake-audio-bytes",
            headers={"Content-Type": "audio/webm"},
        )
        assert resp.status_code == 503

    def test_transcribe_whisper_raises_returns_503(self, stt_module, monkeypatch):
        fake, model = _fake_whisper(raise_on_transcribe=True)
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        resp = client.post(
            "/transcribe", content=b"fake-audio-bytes",
            headers={"Content-Type": "audio/mp4"},
        )
        assert resp.status_code == 503

    def test_transcribe_too_large_returns_413(self, stt_module, monkeypatch):
        fake, _ = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        big = b"x" * (stt_module.MAX_BODY_BYTES + 1)
        resp = client.post(
            "/transcribe", content=big, headers={"Content-Type": "audio/wav"},
        )
        assert resp.status_code == 413

    def test_transcribe_unknown_content_type_uses_bin_extension(self, stt_module, monkeypatch):
        """Content-Type desconocido no debe romper el flujo (whisper/ffmpeg
        huelen el formato real por contenido, no por extensión)."""
        fake, model = _fake_whisper()
        monkeypatch.setitem(sys.modules, "whisper", fake)
        stt_module.load_model()
        client = TestClient(stt_module.app)
        resp = client.post(
            "/transcribe", content=b"fake-audio-bytes",
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 200


# ── helpers internos ────────────────────────────────────────────────────────

class TestHelpers:
    def test_ext_for_known_content_types(self, stt_module):
        assert stt_module._ext_for_content_type("audio/mp4") == ".mp4"
        assert stt_module._ext_for_content_type("audio/webm") == ".webm"
        assert stt_module._ext_for_content_type("audio/webm;codecs=opus") == ".webm"

    def test_ext_for_unknown_or_missing_content_type(self, stt_module):
        assert stt_module._ext_for_content_type("application/octet-stream") == ".bin"
        assert stt_module._ext_for_content_type(None) == ".bin"

    def test_duration_from_result_no_segments(self, stt_module):
        assert stt_module._duration_from_result({}) == 0.0
        assert stt_module._duration_from_result({"segments": []}) == 0.0

    def test_duration_from_result_last_segment_end(self, stt_module):
        result = {"segments": [{"end": 1.0}, {"end": 5.5}]}
        assert stt_module._duration_from_result(result) == 5.5
