"""
test_llm.py — Tests de app/llm.py (F3 roadmap P0: backend LLM intercambiable).

Cubre:
- generate() despacha a claude_cli por default (comportamiento EXACTO al
  subprocess original: stdin, shell=False, timeout, encoding utf-8).
- generate() despacha a openai_compat vía urllib (mock de urlopen): éxito,
  HTTP no-2xx, timeout, JSON malformado, choices vacío/shape rara -> None.
- Backend desconocido -> warning + fallback a claude_cli.
- Nunca lanza (red de seguridad total).
"""
from __future__ import annotations

import json
import subprocess
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from app import llm
from app.config import settings


# ── claude_cli ────────────────────────────────────────────────────────────────

class _OkResult:
    returncode = 0
    stdout = "Respuesta del CLI.\n"
    stderr = ""


class _FailResult:
    returncode = 1
    stdout = ""
    stderr = "algo truena"


class _EmptyResult:
    returncode = 0
    stdout = "   "
    stderr = ""


def test_claude_cli_success(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _OkResult())
    result = llm.generate("hola", timeout=10, purpose="coach")
    assert result == "Respuesta del CLI."


def test_claude_cli_nonzero_exit_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FailResult())
    assert llm.generate("hola", timeout=10) is None


def test_claude_cli_empty_output_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _EmptyResult())
    assert llm.generate("hola", timeout=10) is None


def test_claude_cli_file_not_found_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")

    def _boom(*a, **kw):
        raise FileNotFoundError("no CLI")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert llm.generate("hola", timeout=10) is None


def test_claude_cli_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")

    def _boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=10)

    monkeypatch.setattr(subprocess, "run", _boom)
    assert llm.generate("hola", timeout=10) is None


def test_claude_cli_uses_stdin_never_shell_true(monkeypatch):
    """Verifica que el prompt va por input= (STDIN) y shell=True NUNCA se usa
    (invariante de seguridad del roadmap)."""
    monkeypatch.setattr(settings, "COACH_BACKEND", "claude_cli")
    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _OkResult()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    llm.generate("pregunta secreta", timeout=10)
    assert captured["kwargs"].get("input") == "pregunta secreta"
    assert captured["kwargs"].get("shell") is not True
    assert "pregunta secreta" not in captured["cmd"]


def test_default_backend_is_claude_cli():
    assert settings.COACH_BACKEND == "claude_cli"


def test_unknown_backend_falls_back_to_claude_cli(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "totally_bogus_backend")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _OkResult())
    result = llm.generate("hola", timeout=10)
    assert result == "Respuesta del CLI."


# ── openai_compat ─────────────────────────────────────────────────────────────

class _FakeHttpResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_openai_compat_success(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "COACH_MODEL", "llama3.1")
    monkeypatch.setattr(settings, "COACH_API_KEY", "")

    body = json.dumps({"choices": [{"message": {"content": "Hola desde Ollama"}}]})

    def _fake_urlopen(req, timeout=None):
        return _FakeHttpResponse(200, body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    result = llm.generate("hola", timeout=10, purpose="coach")
    assert result == "Hola desde Ollama"


def test_openai_compat_sends_auth_header_only_if_key_present(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "COACH_MODEL", "llama3.1")
    monkeypatch.setattr(settings, "COACH_API_KEY", "sk-test-123")

    body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeHttpResponse(200, body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    llm.generate("hola", timeout=10)
    # urllib normaliza las cabeceras a Capitalized-Case
    assert captured["headers"].get("Authorization") == "Bearer sk-test-123"


def test_openai_compat_no_key_omits_auth_header(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")
    monkeypatch.setattr(settings, "COACH_MODEL", "llama3.1")
    monkeypatch.setattr(settings, "COACH_API_KEY", "")

    body = json.dumps({"choices": [{"message": {"content": "ok"}}]})
    captured = {}

    def _fake_urlopen(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _FakeHttpResponse(200, body)

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    llm.generate("hola", timeout=10)
    assert "Authorization" not in captured["headers"]


def test_openai_compat_http_500_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        return _FakeHttpResponse(500, "{}")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_connection_refused_returns_none(monkeypatch):
    """Servidor caído (URLError, p.ej. ConnectionRefusedError) -> None, nunca
    excepción propagada."""
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_timeout_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=1) is None


def test_openai_compat_malformed_json_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        return _FakeHttpResponse(200, "NOT JSON{{{")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_empty_choices_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        return _FakeHttpResponse(200, json.dumps({"choices": []}))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_missing_message_content_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _fake_urlopen(req, timeout=None):
        return _FakeHttpResponse(200, json.dumps({"choices": [{"message": {}}]}))

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_empty_base_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "")
    assert llm.generate("hola", timeout=10) is None


def test_openai_compat_unexpected_exception_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "COACH_BACKEND", "openai_compat")
    monkeypatch.setattr(settings, "COACH_API_BASE", "http://localhost:11434/v1")

    def _boom(req, timeout=None):
        raise RuntimeError("algo inesperado")

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    assert llm.generate("hola", timeout=10) is None
