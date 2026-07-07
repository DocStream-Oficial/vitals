"""
test_install.py — Tests de install.py (instalador seamless, un comando).

Cubre (ROADMAP-vitals-install-seamless.md, paso S2):
- venv_python_path: resuelve bin/python (posix) vs Scripts/python.exe (nt).
- build_env_content: respeta valores dados, autogenera INGEST_TOKEN cuando
  falta, activa VITALS_DEMO en modo demo, no duplica claves, preserva las
  que el usuario no tocó.
- parse_args: defaults y cada flag.
- is_already_installed: True sólo si .venv Y .env existen.

Importa install.py como módulo top-level (vive en la raíz del repo). No crea
venvs reales ni toca el .env real del repo — todo I/O pasa por tmp_path, y
subprocess/venv quedan mockeados donde aplica.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

install = importlib.import_module("install")


# ──────────────────────────────────────────────────────────────────────────
# venv_python_path
# ──────────────────────────────────────────────────────────────────────────


def test_venv_python_path_posix():
    root = Path("/home/user/vitals-app")
    result = install.venv_python_path(root, "posix")
    assert result == root / ".venv" / "bin" / "python"


def test_venv_python_path_nt():
    root = Path("C:/Users/user/vitals-app")
    result = install.venv_python_path(root, "nt")
    assert result == root / ".venv" / "Scripts" / "python.exe"


def test_venv_python_path_returns_path_object():
    result = install.venv_python_path(Path("."), "posix")
    assert isinstance(result, Path)


# ──────────────────────────────────────────────────────────────────────────
# build_env_content
# ──────────────────────────────────────────────────────────────────────────

EXAMPLE_TEXT = """\
# comentario inicial
# VITALS_DEMO=1

CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com
CLIENT_SECRET=YOUR_CLIENT_SECRET
REDIRECT_URI=http://localhost:8700/auth/callback

BIRTHDATE=YYYY-MM-DD
WAIST_CM=80
SEX=M
PREFER_PLATFORM=AUTO

SYNC_HOUR=9

INGEST_TOKEN=
"""


def test_build_env_content_preserves_placeholders_when_no_answers():
    content = install.build_env_content(EXAMPLE_TEXT, {})
    assert "CLIENT_ID=YOUR_CLIENT_ID.apps.googleusercontent.com" in content
    assert "WAIST_CM=80" in content
    assert "SEX=M" in content
    # comentarios preservados
    assert "# comentario inicial" in content


def test_build_env_content_respects_given_values():
    answers = {
        "CLIENT_ID": "my-client-id",
        "CLIENT_SECRET": "my-secret",
        "BIRTHDATE": "1990-05-15",
        "WAIST_CM": "90",
        "SEX": "F",
    }
    content = install.build_env_content(EXAMPLE_TEXT, answers)
    assert "CLIENT_ID=my-client-id" in content
    assert "CLIENT_SECRET=my-secret" in content
    assert "BIRTHDATE=1990-05-15" in content
    assert "WAIST_CM=90" in content
    assert "SEX=F" in content
    # no tocado -> se preserva el placeholder original
    assert "PREFER_PLATFORM=AUTO" in content


def test_build_env_content_autogenerates_ingest_token_when_missing():
    content = install.build_env_content(EXAMPLE_TEXT, {})
    lines = [l for l in content.splitlines() if l.startswith("INGEST_TOKEN=")]
    assert len(lines) == 1
    token = lines[0].split("=", 1)[1]
    assert len(token) > 20  # secrets.token_urlsafe(32) produce ~43 chars


def test_build_env_content_does_not_regenerate_ingest_token_when_given():
    answers = {"INGEST_TOKEN": "existing-token-value"}
    content = install.build_env_content(EXAMPLE_TEXT, answers)
    assert "INGEST_TOKEN=existing-token-value" in content
    assert content.count("INGEST_TOKEN=") == 1


def test_build_env_content_two_calls_produce_different_tokens_when_unset():
    # Cada llamada sin token dado autogenera uno nuevo — el caller (install.py
    # main flow) es responsable de no re-invocar esto sobre un .env existente,
    # pero la función misma no debe fingir determinismo.
    content_a = install.build_env_content(EXAMPLE_TEXT, {})
    content_b = install.build_env_content(EXAMPLE_TEXT, {})
    token_a = [l for l in content_a.splitlines() if l.startswith("INGEST_TOKEN=")][0]
    token_b = [l for l in content_b.splitlines() if l.startswith("INGEST_TOKEN=")][0]
    assert token_a != token_b


def test_build_env_content_sets_vitals_demo_when_demo_flag():
    content = install.build_env_content(EXAMPLE_TEXT, {"_demo": True})
    assert "VITALS_DEMO=1" in content
    # no debe quedar la version comentada Y la activa duplicadas
    assert content.count("VITALS_DEMO=1") == 1


def test_build_env_content_no_vitals_demo_line_when_not_demo():
    content = install.build_env_content(EXAMPLE_TEXT, {})
    active_lines = [
        l for l in content.splitlines() if l.strip() == "VITALS_DEMO=1"
    ]
    assert active_lines == []


def test_build_env_content_does_not_duplicate_keys():
    content = install.build_env_content(EXAMPLE_TEXT, {"SEX": "F"})
    assert content.count("SEX=") == 1
    assert content.count("CLIENT_ID=") == 1


def test_build_env_content_handles_example_without_ingest_token():
    # .env.example minimo sin INGEST_TOKEN -> no debe reventar ni inventar la clave.
    minimal = "CLIENT_ID=x\nCLIENT_SECRET=y\n"
    content = install.build_env_content(minimal, {})
    assert "INGEST_TOKEN" not in content


def test_build_env_content_ends_with_newline():
    content = install.build_env_content(EXAMPLE_TEXT, {})
    assert content.endswith("\n")


# ──────────────────────────────────────────────────────────────────────────
# parse_args
# ──────────────────────────────────────────────────────────────────────────


def test_parse_args_defaults():
    args = install.parse_args([])
    assert args.demo is False
    assert args.no_launch is False
    assert args.yes is False
    assert args.port == install.DEFAULT_PORT


def test_parse_args_demo_flag():
    args = install.parse_args(["--demo"])
    assert args.demo is True


def test_parse_args_no_launch_flag():
    args = install.parse_args(["--no-launch"])
    assert args.no_launch is True


def test_parse_args_yes_flag_long_and_short():
    assert install.parse_args(["--yes"]).yes is True
    assert install.parse_args(["-y"]).yes is True


def test_parse_args_port_flag():
    args = install.parse_args(["--port", "9000"])
    assert args.port == 9000


def test_parse_args_combined_flags():
    args = install.parse_args(["--demo", "--no-launch", "--port", "1234"])
    assert args.demo is True
    assert args.no_launch is True
    assert args.port == 1234


def test_parse_args_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc_info:
        install.parse_args(["--help"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "install.py" in captured.out
    assert "--demo" in captured.out
    assert "--no-launch" in captured.out


# ──────────────────────────────────────────────────────────────────────────
# is_already_installed
# ──────────────────────────────────────────────────────────────────────────


def test_is_already_installed_false_when_nothing_exists(tmp_path):
    assert install.is_already_installed(tmp_path) is False


def test_is_already_installed_false_when_only_venv(tmp_path):
    (tmp_path / ".venv").mkdir()
    assert install.is_already_installed(tmp_path) is False


def test_is_already_installed_false_when_only_env(tmp_path):
    (tmp_path / ".env").write_text("X=1")
    assert install.is_already_installed(tmp_path) is False


def test_is_already_installed_true_when_both_exist(tmp_path):
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".env").write_text("X=1")
    assert install.is_already_installed(tmp_path) is True


# ──────────────────────────────────────────────────────────────────────────
# build_env_answers — modo demo/yes no pregunta nada (sin bloquear en input())
# ──────────────────────────────────────────────────────────────────────────


def test_build_env_answers_demo_mode_no_prompt(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no deberia llamar a input() en modo demo")

    monkeypatch.setattr("builtins.input", _boom)
    answers = install.build_env_answers(demo=True, yes=False)
    assert answers["_demo"] is True


def test_build_env_answers_yes_mode_no_prompt(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("no deberia llamar a input() en modo --yes")

    monkeypatch.setattr("builtins.input", _boom)
    answers = install.build_env_answers(demo=False, yes=True)
    assert answers.get("_demo") is False


def test_build_env_answers_interactive_mode_uses_input(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *_: "")
    answers = install.build_env_answers(demo=False, yes=False)
    # todo vacio (Enter) -> ningun campo opcional queda seteado
    assert "CLIENT_ID" not in answers


def test_prompt_survives_closed_stdin(monkeypatch):
    # Simula stdin cerrado: input() lanza EOFError -> _prompt no debe colgarse,
    # debe caer al default.
    def _eof(*a, **k):
        raise EOFError()

    monkeypatch.setattr("builtins.input", _eof)
    result = install._prompt("cualquier cosa", default="fallback")
    assert result == "fallback"


# ──────────────────────────────────────────────────────────────────────────
# write_env_file — no pisa un .env existente
# ──────────────────────────────────────────────────────────────────────────


def test_write_env_file_does_not_overwrite_existing_env(tmp_path, monkeypatch):
    (tmp_path / ".env.example").write_text(EXAMPLE_TEXT)
    existing = "CLIENT_ID=already-set\n"
    (tmp_path / ".env").write_text(existing)

    monkeypatch.setattr("builtins.input", lambda *_: "")
    install.write_env_file(tmp_path, demo=False, yes=True)

    assert (tmp_path / ".env").read_text() == existing


def test_write_env_file_creates_env_from_example_when_missing(tmp_path):
    (tmp_path / ".env.example").write_text(EXAMPLE_TEXT)
    install.write_env_file(tmp_path, demo=True, yes=True)

    content = (tmp_path / ".env").read_text()
    assert "VITALS_DEMO=1" in content
    assert "INGEST_TOKEN=" in content


# ──────────────────────────────────────────────────────────────────────────
# ensure_venv — reusa un .venv existente sin invocar subprocess
# ──────────────────────────────────────────────────────────────────────────


def test_ensure_venv_reuses_existing_venv(tmp_path, monkeypatch):
    venv_dir = tmp_path / ".venv"
    if __import__("os").name == "nt":
        py = venv_dir / "Scripts" / "python.exe"
    else:
        py = venv_dir / "bin" / "python"
    py.parent.mkdir(parents=True)
    py.touch()

    def _boom(*a, **k):
        raise AssertionError("no deberia invocar subprocess si .venv ya existe")

    monkeypatch.setattr(install.subprocess, "run", _boom)
    result = install.ensure_venv(tmp_path)
    assert result == py


def test_ensure_venv_creates_when_missing_mocked_subprocess(tmp_path, monkeypatch):
    calls = []

    class _FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, capture_output, text):
        calls.append(cmd)
        # Simula que `python -m venv` crea el interprete esperado.
        py = install.venv_python_path(tmp_path, __import__("os").name)
        py.parent.mkdir(parents=True, exist_ok=True)
        py.touch()
        return _FakeResult()

    monkeypatch.setattr(install.subprocess, "run", _fake_run)
    result = install.ensure_venv(tmp_path)

    assert len(calls) == 1
    assert calls[0][0] == sys.executable
    assert "venv" in calls[0]
    assert result.exists()


def test_ensure_venv_fails_with_actionable_message_on_pip_error(tmp_path, monkeypatch, capsys):
    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "boom: permission denied"

    def _fake_run(cmd, capture_output, text):
        return _FakeResult()

    monkeypatch.setattr(install.subprocess, "run", _fake_run)
    with pytest.raises(SystemExit):
        install.ensure_venv(tmp_path)
    captured = capsys.readouterr()
    assert "boom: permission denied" in captured.err


def test_install_dependencies_fails_with_actionable_message(tmp_path, monkeypatch, capsys):
    (tmp_path / "requirements.txt").write_text("fastapi==0.128.8\n")

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "could not find a version that satisfies the requirement"

    def _fake_run(cmd, capture_output, text):
        return _FakeResult()

    monkeypatch.setattr(install.subprocess, "run", _fake_run)
    with pytest.raises(SystemExit):
        install.install_dependencies(Path("/fake/venv/python"), tmp_path)
    captured = capsys.readouterr()
    assert "could not find a version" in captured.err


def test_install_dependencies_missing_requirements_file(tmp_path, capsys):
    with pytest.raises(SystemExit):
        install.install_dependencies(Path("/fake/venv/python"), tmp_path)
    captured = capsys.readouterr()
    assert "requirements.txt" in captured.err


# ──────────────────────────────────────────────────────────────────────────
# check_python_version
# ──────────────────────────────────────────────────────────────────────────


def test_check_python_version_passes_on_current_interpreter():
    # La suite misma corre en un Python que ya cumple el minimo del proyecto.
    install.check_python_version()


def test_check_python_version_fails_below_minimum(monkeypatch, capsys):
    monkeypatch.setattr(install.sys, "version_info", (3, 8, 0))
    with pytest.raises(SystemExit):
        install.check_python_version()
    captured = capsys.readouterr()
    assert "3.9" in captured.err
