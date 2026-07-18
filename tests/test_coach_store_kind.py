"""
test_coach_store_kind.py — Tests del campo aditivo `kind` en app/coach_store.py
(roadmap coach-mental, Paso 2 — "EL PASO MÁS RIESGOSO").

Cubre:
- create_conversation(kind=...) guarda la clave; sin kind, NO se añade
  (conversaciones de chat normal quedan byte-idénticas a como eran antes).
- get_kind: devuelve el kind guardado, "chat" por default, "chat" para
  conversaciones viejas SIN la clave (simulando un store de producción
  pre-existente), y "chat" para cid None/inexistente.
- list_conversations trae `kind` en la metadata ligera.
- append_message: agrega un mensaje suelto, respeta el cap, no toca el
  título, no-op sobre cid inexistente.

NO edita tests existentes (test_coach_store.py intacto) — archivo nuevo.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def store_mod(tmp_path, monkeypatch):
    """Aísla coach_store en tmp_path — mismo patrón que test_coach_store.py."""
    from app import coach_store as cs
    monkeypatch.setattr(cs, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(cs, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(cs, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(cs, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    return cs


class TestCreateWithKind:
    def test_create_with_kind_persists_it(self, store_mod):
        conv = store_mod.create_conversation(title="Sesión Master — 2026-07-18", kind="mental_master")
        assert conv["kind"] == "mental_master"
        full = store_mod.get_conversation(conv["id"])
        assert full["kind"] == "mental_master"

    def test_create_without_kind_does_not_add_key(self, store_mod):
        conv = store_mod.create_conversation(title="chat normal")
        assert "kind" not in conv
        full = store_mod.get_conversation(conv["id"])
        assert "kind" not in full

    def test_create_default_no_kind_arg_identical_to_before(self, store_mod):
        conv = store_mod.create_conversation()
        assert "kind" not in conv
        assert conv["title"]  # default title sigue funcionando


class TestGetKind:
    def test_get_kind_returns_stored_kind(self, store_mod):
        conv = store_mod.create_conversation(kind="mental_master")
        assert store_mod.get_kind(conv["id"]) == "mental_master"

    def test_get_kind_defaults_to_chat_when_no_kind_key(self, store_mod):
        conv = store_mod.create_conversation()
        assert store_mod.get_kind(conv["id"]) == "chat"

    def test_get_kind_defaults_to_chat_for_legacy_store_without_key(self, store_mod, tmp_path):
        """Simula un coach_conversations.json de producción escrito ANTES de
        este paso — sin la clave `kind` en absoluto."""
        legacy_store = {
            "version": 2,
            "active_id": "abc",
            "conversations": [
                {"id": "abc", "title": "vieja", "created": "t1", "updated": "t1", "messages": []},
            ],
        }
        (tmp_path / "coach_conversations.json").write_text(json.dumps(legacy_store), encoding="utf-8")
        assert store_mod.get_kind("abc") == "chat"

    def test_get_kind_none_cid_returns_chat(self, store_mod):
        assert store_mod.get_kind(None) == "chat"

    def test_get_kind_nonexistent_cid_returns_chat(self, store_mod):
        assert store_mod.get_kind("no-existe") == "chat"


class TestListConversationsKind:
    def test_list_conversations_includes_kind_master(self, store_mod):
        store_mod.create_conversation(kind="mental_master")
        items = store_mod.list_conversations()
        assert items[0]["kind"] == "mental_master"

    def test_list_conversations_includes_kind_chat_default(self, store_mod):
        store_mod.create_conversation()
        items = store_mod.list_conversations()
        assert items[0]["kind"] == "chat"

    def test_list_conversations_legacy_without_key_defaults_chat(self, store_mod, tmp_path):
        legacy_store = {
            "version": 2,
            "active_id": None,
            "conversations": [
                {"id": "x1", "title": "vieja", "created": "t1", "updated": "t1", "messages": []},
            ],
        }
        (tmp_path / "coach_conversations.json").write_text(json.dumps(legacy_store), encoding="utf-8")
        items = store_mod.list_conversations()
        assert items[0]["kind"] == "chat"


class TestAppendMessage:
    def test_append_message_adds_single_message(self, store_mod):
        conv = store_mod.create_conversation(kind="mental_master")
        store_mod.append_message(conv["id"], "assistant", "¿Cómo viene la semana?")
        full = store_mod.get_conversation(conv["id"])
        assert len(full["messages"]) == 1
        assert full["messages"][0]["role"] == "assistant"
        assert full["messages"][0]["content"] == "¿Cómo viene la semana?"
        assert "ts" in full["messages"][0]

    def test_append_message_does_not_touch_title(self, store_mod):
        conv = store_mod.create_conversation(title="Sesión Master — 2026-07-18", kind="mental_master")
        store_mod.append_message(conv["id"], "assistant", "apertura")
        full = store_mod.get_conversation(conv["id"])
        assert full["title"] == "Sesión Master — 2026-07-18"

    def test_append_message_updates_updated_ts(self, store_mod):
        conv = store_mod.create_conversation()
        original_updated = conv["updated"]
        store_mod.append_message(conv["id"], "assistant", "hola")
        full = store_mod.get_conversation(conv["id"])
        assert full["updated"] >= original_updated

    def test_append_message_nonexistent_cid_is_noop_no_crash(self, store_mod):
        store_mod.append_message("no-existe", "assistant", "hola")  # no debe lanzar

    def test_append_message_none_cid_is_noop_no_crash(self, store_mod):
        store_mod.append_message(None, "assistant", "hola")  # no debe lanzar

    def test_append_message_respects_per_conversation_cap(self, store_mod, monkeypatch):
        from app import coach_store as cs
        monkeypatch.setattr(cs, "_MAX_MSGS_PER_CONV", 5)
        conv = store_mod.create_conversation()
        for i in range(8):
            store_mod.append_message(conv["id"], "assistant", f"msg{i}")
        full = store_mod.get_conversation(conv["id"])
        assert len(full["messages"]) == 5
        assert full["messages"][-1]["content"] == "msg7"

    def test_append_message_mixed_with_append_turn(self, store_mod):
        """La apertura (append_message) + turnos normales (append_turn)
        conviven en la misma conversación sin pisarse."""
        conv = store_mod.create_conversation(kind="mental_master")
        store_mod.append_message(conv["id"], "assistant", "apertura")
        store_mod.append_turn(conv["id"], "pregunta 1", "respuesta 1")
        full = store_mod.get_conversation(conv["id"])
        assert len(full["messages"]) == 3
        assert full["messages"][0]["content"] == "apertura"
        assert full["messages"][1]["content"] == "pregunta 1"
        assert full["messages"][2]["content"] == "respuesta 1"
