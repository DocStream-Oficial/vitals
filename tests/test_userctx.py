"""
test_userctx.py — Tests de app/userctx.py (Fase 8D, paso D3, household).

Cubre (roadmap D3 + riesgos §1):
(a) registro de usuarios: add/list/get/delete, slugs únicos.
(b) resolve_user: header > cookie > único usuario > default, fail-open ante
    uid desconocido.
(c) contextvar: set/get/reset, default fuera de contexto.
(d) migración desde layout viejo: idempotente, no pierde datos, no pisa un
    destino ya existente, instalación fresh sin legacy no crashea.
(e) user_dir/users_root apuntan a las rutas correctas.
"""
from __future__ import annotations

import json

import pytest

from app import userctx


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(userctx, "_DATA_DIR", tmp_path)


# ── (a) registro de usuarios ──────────────────────────────────────────────────

def test_list_users_empty_initially(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.list_users() == []


def test_add_user_then_list(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    u = userctx.add_user("Mike")
    assert u is not None
    assert u["id"] == "mike"
    assert u["name"] == "Mike"
    assert "color" in u
    users = userctx.list_users()
    assert len(users) == 1
    assert users[0]["id"] == "mike"


def test_add_user_creates_user_dir(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike")
    assert (tmp_path / "users" / "mike").is_dir()


def test_add_user_rejects_empty_name(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.add_user("") is None
    assert userctx.add_user("   ") is None


def test_add_user_dedupes_slug_on_collision(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    u1 = userctx.add_user("Ana")
    u2 = userctx.add_user("Ana")
    assert u1["id"] == "ana"
    assert u2["id"] == "ana_2"
    assert len(userctx.list_users()) == 2


def test_get_user_and_user_exists(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike")
    assert userctx.user_exists("mike") is True
    assert userctx.user_exists("nobody") is False
    assert userctx.get_user("mike")["name"] == "Mike"
    assert userctx.get_user("nobody") is None


def test_delete_user_removes_from_registry(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike")
    assert userctx.delete_user("mike") is True
    assert userctx.user_exists("mike") is False


def test_delete_user_idempotent_for_unknown(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.delete_user("nobody") is True


def test_delete_user_with_delete_data_removes_dir(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike")
    d = userctx.user_dir("mike")
    (d / "profile.json").write_text("{}", encoding="utf-8")
    assert d.exists()
    userctx.delete_user("mike", delete_data=True)
    assert not d.exists()


def test_delete_user_without_delete_data_keeps_dir(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike")
    d = userctx.user_dir("mike")
    userctx.delete_user("mike", delete_data=False)
    assert d.exists()  # datos preservados, solo se quita del registro


# ── (b) resolve_user ──────────────────────────────────────────────────────────

def test_resolve_user_header_wins_when_valid(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike", uid="mike")
    userctx.add_user("Ana", uid="ana")
    assert userctx.resolve_user(header_user="ana", cookie_user="mike") == "ana"


def test_resolve_user_falls_back_to_cookie_if_header_invalid(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike", uid="mike")
    userctx.add_user("Ana", uid="ana")
    assert userctx.resolve_user(header_user="nobody", cookie_user="ana") == "ana"


def test_resolve_user_single_user_wins_without_header_cookie(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike", uid="mike")
    assert userctx.resolve_user() == "mike"


def test_resolve_user_defaults_when_no_users_registered(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.resolve_user() == "default"


def test_resolve_user_defaults_with_multiple_users_and_no_header_cookie(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike", uid="mike")
    userctx.add_user("Ana", uid="ana")
    assert userctx.resolve_user() == "default"


def test_resolve_user_fail_open_ignores_unknown_uid(tmp_path, monkeypatch):
    """Riesgo D3 #1: uid desconocido en header NUNCA debe romper — cae en cascada."""
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Mike", uid="mike")
    result = userctx.resolve_user(header_user="typo_uid")
    assert result == "mike"  # cae al único usuario registrado


# ── (c) contextvar ─────────────────────────────────────────────────────────────

def test_current_uid_defaults_outside_context():
    assert userctx.current_uid() == "default"


def test_set_and_reset_current_uid():
    token = userctx.set_current_uid("ana")
    try:
        assert userctx.current_uid() == "ana"
    finally:
        userctx.reset_current_uid(token)
    assert userctx.current_uid() == "default"


def test_current_data_dir_follows_contextvar(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    token = userctx.set_current_uid("ana")
    try:
        assert userctx.current_data_dir() == tmp_path / "users" / "ana"
    finally:
        userctx.reset_current_uid(token)


# ── (e) user_dir / users_root ─────────────────────────────────────────────────

def test_user_dir_path(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.user_dir("mike") == tmp_path / "users" / "mike"


def test_users_root_path(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.users_root() == tmp_path / "users"


def test_user_dir_falls_back_to_default_for_empty_uid(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.user_dir("") == tmp_path / "users" / "default"
    assert userctx.user_dir(None) == tmp_path / "users" / "default"


# ── (d) migración desde layout viejo ──────────────────────────────────────────

def test_migration_noop_when_users_dir_already_exists(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    (tmp_path / "users").mkdir()
    (tmp_path / "profile.json").write_text('{"name":"old"}', encoding="utf-8")
    result = userctx.migrate_legacy_layout_if_needed()
    assert result is None
    # el archivo legacy NO se tocó (users/ ya existía -> no-op total)
    assert (tmp_path / "profile.json").exists()


def test_migration_fresh_install_is_total_noop_without_legacy(tmp_path, monkeypatch):
    """Bug real encontrado durante el desarrollo de D3: crear data/users/ (y
    registrar 'default') en una instalación fresh sin NINGÚN legacy activaba
    should_use_household_paths()=True para TODO request futuro, incluyendo el
    primer /api/sync real — que entonces escribiría en data/users/default/ en
    vez de data/ (rompiendo single-user-por-default). La instalación fresh
    debe quedar en modo legacy-compat total: sin users/, sin registro."""
    _patch_data_dir(monkeypatch, tmp_path)
    result = userctx.migrate_legacy_layout_if_needed()
    assert result is None
    assert not userctx.users_root().exists()
    assert userctx.list_users() == []


def test_migration_moves_legacy_files_to_default_user_dir(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    (tmp_path / "profile.json").write_text('{"name":"Mike"}', encoding="utf-8")
    (tmp_path / "journal_log.json").write_text('{"entries":{}}', encoding="utf-8")
    (tmp_path / "ecg").mkdir()
    (tmp_path / "ecg" / "sample.json").write_text("{}", encoding="utf-8")
    # Global files que NO deben migrar
    (tmp_path / "ingest_token.json").write_text('{"token":"abc"}', encoding="utf-8")

    result = userctx.migrate_legacy_layout_if_needed()
    assert result is not None
    assert "profile.json" in result

    default_dir = userctx.user_dir("default")
    assert (default_dir / "profile.json").exists()
    assert json.loads((default_dir / "profile.json").read_text())["name"] == "Mike"
    assert (default_dir / "journal_log.json").exists()
    assert (default_dir / "ecg" / "sample.json").exists()

    # los archivos viejos ya no están en la raíz (se MOVIERON, no copiaron)
    assert not (tmp_path / "profile.json").exists()
    assert not (tmp_path / "journal_log.json").exists()
    assert not (tmp_path / "ecg").exists()

    # global file INTACTO en la raíz (nunca migra)
    assert (tmp_path / "ingest_token.json").exists()

    # usuario default registrado
    assert userctx.user_exists("default")


def test_migration_is_idempotent_second_call_noop(tmp_path, monkeypatch):
    _patch_data_dir(monkeypatch, tmp_path)
    (tmp_path / "profile.json").write_text('{"name":"Mike"}', encoding="utf-8")
    userctx.migrate_legacy_layout_if_needed()
    # segunda llamada: users/ ya existe -> no-op, no debe lanzar ni duplicar
    result2 = userctx.migrate_legacy_layout_if_needed()
    assert result2 is None
    assert len(userctx.list_users()) == 1


def test_migration_does_not_overwrite_existing_partial_migration(tmp_path, monkeypatch):
    """Si una migración previa quedó a medias y el destino YA tiene el
    archivo, NO debe pisarlo (evita perder datos de una corrida anterior)."""
    _patch_data_dir(monkeypatch, tmp_path)
    # Simula estado "a medias": profile.json en la raíz Y en users/default/ ya
    # existe con contenido DISTINTO (versión que se quiere preservar).
    (tmp_path / "profile.json").write_text('{"name":"legacy_root"}', encoding="utf-8")
    default_dir = tmp_path / "users" / "default"
    default_dir.mkdir(parents=True)
    (default_dir / "profile.json").write_text('{"name":"already_migrated"}', encoding="utf-8")

    # users/ ya existe -> migrate_legacy_layout_if_needed es no-op inmediato
    # por diseño (guard de arriba). Verificamos ese comportamiento defensivo.
    result = userctx.migrate_legacy_layout_if_needed()
    assert result is None
    assert json.loads((default_dir / "profile.json").read_text())["name"] == "already_migrated"


def test_migration_preserves_data_never_deletes_without_moving(tmp_path, monkeypatch):
    """Verifica que la migración es un MOVE (no un delete+recreate) — total de
    bytes de datos preservado."""
    _patch_data_dir(monkeypatch, tmp_path)
    content = '{"entries": {"2026-01-01": {"alcohol": true}}}'
    (tmp_path / "journal_log.json").write_text(content, encoding="utf-8")
    userctx.migrate_legacy_layout_if_needed()
    migrated = (userctx.user_dir("default") / "journal_log.json").read_text()
    assert migrated == content


# ── (f) blindaje path-traversal en uid (auditoría Fase 8D, riesgo #1) ────────

def test_user_dir_sanitizes_traversal_uid(tmp_path, monkeypatch):
    """Un uid con componentes de traversal NUNCA puede escapar de data/users/.
    Regresión del bug: DELETE /api/users/%2e%2e?delete_data=true hacía
    rmtree(data/) (borrado total). user_dir() es el único chokepoint y debe
    neutralizar '..', '/', '\\'."""
    _patch_data_dir(monkeypatch, tmp_path)
    users_root = (tmp_path / "users").resolve()
    for evil in ["..", "../../etc", "../default", "a/../../b", "\\..\\..", "/etc/passwd"]:
        resolved = userctx.user_dir(evil).resolve()
        assert str(resolved).startswith(str(users_root)), (
            f"user_dir({evil!r}) escapó a {resolved}"
        )


def test_delete_user_traversal_uid_does_not_wipe_data(tmp_path, monkeypatch):
    """delete_user('..', delete_data=True) NO debe borrar la raíz de data/."""
    _patch_data_dir(monkeypatch, tmp_path)
    userctx.add_user("Alice")
    (tmp_path / "ingest_token.json").write_text("SECRET", encoding="utf-8")
    userctx.delete_user("..", delete_data=True)
    # data/ y sus archivos globales siguen intactos
    assert (tmp_path / "ingest_token.json").exists()
    assert (tmp_path / "users.json").exists()
    assert (tmp_path / "users").exists()


def test_add_user_rejects_traversal_explicit_uid(tmp_path, monkeypatch):
    """add_user con un uid explícito que contiene separadores/'..' se rechaza
    (el id resultante no coincidiría con su carpeta -> defensa en profundidad)."""
    _patch_data_dir(monkeypatch, tmp_path)
    assert userctx.add_user("Evil", uid="../evil") is None
    assert userctx.add_user("Evil2", uid="a/b") is None
    assert userctx.add_user("Good", uid="goodid") is not None
