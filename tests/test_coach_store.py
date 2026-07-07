"""
test_coach_store.py — Tests de app/coach_store.py v2 (conversaciones multi-chat).

Cubre:
- Migración v1 (plano) -> v2 (conversaciones): sin pérdida, .v1.bak conservado,
  idempotencia (2ª carga no re-migra ni duplica).
- Perfil sin historial -> arranca vacío sin crash.
- API v2: create/get/list/delete/append_turn/get_context/set_active.
- AISLAMIENTO DE CONTEXTO: get_context(A) nunca incluye mensajes de B.
- Caps: por-conversación (200 msgs) y total (50 convs, evicta más vieja, nunca
  la activa).
- None-safe / nunca lanza.

NO toca scoring/bodyage/merge.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def store_mod(tmp_path, monkeypatch):
    """Aísla coach_store en tmp_path (nunca toca data/ real)."""
    from app import coach_store as cs
    monkeypatch.setattr(cs, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(cs, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(cs, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(cs, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    return cs


# ── Migración ──────────────────────────────────────────────────────────────

class TestMigration:
    def test_no_legacy_no_v2_starts_empty(self, store_mod, tmp_path):
        """Perfil sin historial -> arranca vacío, sin crash."""
        assert store_mod.list_conversations() == []
        assert store_mod.get_active_id() is None
        assert not (tmp_path / "coach_history.json.v1.bak").exists()

    def test_migration_wraps_legacy_messages_no_loss(self, store_mod, tmp_path):
        legacy = [
            {"role": "user", "content": "¿Cómo dormí?", "ts": "2026-01-01T10:00:00+00:00"},
            {"role": "assistant", "content": "Dormiste 7h.", "ts": "2026-01-01T10:00:01+00:00"},
            {"role": "user", "content": "¿Y mi HRV?", "ts": "2026-01-02T09:00:00+00:00"},
            {"role": "assistant", "content": "HRV 45ms.", "ts": "2026-01-02T09:00:01+00:00"},
        ]
        (tmp_path / "coach_history.json").write_text(json.dumps(legacy), encoding="utf-8")

        convs = store_mod.list_conversations()
        assert len(convs) == 1
        full = store_mod.get_conversation(convs[0]["id"])
        assert full is not None
        assert len(full["messages"]) == len(legacy)
        assert full["messages"] == legacy
        assert store_mod.get_active_id() == convs[0]["id"]

        # El viejo se conserva como .v1.bak, NO se destruye.
        assert (tmp_path / "coach_history.json.v1.bak").exists()
        assert not (tmp_path / "coach_history.json").exists()
        backed_up = json.loads((tmp_path / "coach_history.json.v1.bak").read_text())
        assert backed_up == legacy

    def test_migration_title_from_first_user_message(self, store_mod, tmp_path):
        legacy = [
            {"role": "user", "content": "¿Qué tal mi sueño esta semana largo mensaje de prueba?", "ts": "t1"},
            {"role": "assistant", "content": "Bien.", "ts": "t2"},
        ]
        (tmp_path / "coach_history.json").write_text(json.dumps(legacy), encoding="utf-8")
        convs = store_mod.list_conversations()
        assert convs[0]["title"].startswith("¿Qué tal mi sueño")

    def test_migration_empty_legacy_list_starts_empty_conversations(self, store_mod, tmp_path):
        (tmp_path / "coach_history.json").write_text("[]", encoding="utf-8")
        assert store_mod.list_conversations() == []

    def test_migration_corrupt_legacy_json_starts_empty_no_crash(self, store_mod, tmp_path):
        (tmp_path / "coach_history.json").write_text("{not valid json", encoding="utf-8")
        assert store_mod.list_conversations() == []

    def test_migration_idempotent_second_load_no_remigrate_no_duplicate(self, store_mod, tmp_path):
        legacy = [
            {"role": "user", "content": "hola", "ts": "t1"},
            {"role": "assistant", "content": "hola!", "ts": "t2"},
        ]
        (tmp_path / "coach_history.json").write_text(json.dumps(legacy), encoding="utf-8")

        convs_1 = store_mod.list_conversations()
        assert len(convs_1) == 1

        # Simular reinicio del proceso: llamar de nuevo a operaciones que disparan
        # _migrate_if_needed(). No debe re-migrar ni duplicar, incluso si alguien
        # repone un coach_history.json plano después (v2 ya existe -> no-op).
        (tmp_path / "coach_history.json").write_text(json.dumps(legacy), encoding="utf-8")
        convs_2 = store_mod.list_conversations()
        assert len(convs_2) == 1
        assert convs_2[0]["id"] == convs_1[0]["id"]
        assert convs_2[0]["message_count"] == 2


# ── API v2 básica ─────────────────────────────────────────────────────────

class TestConversationsCRUD:
    def test_create_conversation_returns_id(self, store_mod):
        conv = store_mod.create_conversation()
        assert conv["id"]
        assert conv["messages"] == []

    def test_list_conversations_empty_is_empty_list(self, store_mod):
        assert store_mod.list_conversations() == []

    def test_get_conversation_missing_returns_none(self, store_mod):
        assert store_mod.get_conversation("no-existe") is None

    def test_list_conversations_light_no_messages_field(self, store_mod):
        conv = store_mod.create_conversation()
        store_mod.append_turn(conv["id"], "hola", "hola!")
        items = store_mod.list_conversations()
        assert "messages" not in items[0]
        assert items[0]["message_count"] == 2

    def test_list_order_by_updated_desc(self, store_mod):
        c1 = store_mod.create_conversation()
        c2 = store_mod.create_conversation()
        store_mod.append_turn(c1["id"], "primero", "resp1")
        store_mod.append_turn(c2["id"], "segundo", "resp2")
        items = store_mod.list_conversations()
        assert items[0]["id"] == c2["id"]

    def test_delete_conversation_removes_only_that_one(self, store_mod):
        c1 = store_mod.create_conversation()
        c2 = store_mod.create_conversation()
        store_mod.delete_conversation(c1["id"])
        remaining = store_mod.list_conversations()
        assert len(remaining) == 1
        assert remaining[0]["id"] == c2["id"]

    def test_delete_nonexistent_never_raises(self, store_mod):
        store_mod.delete_conversation("no-existe")  # no debe lanzar

    def test_append_turn_creates_conversation_if_cid_none(self, store_mod):
        cid = store_mod.append_turn(None, "pregunta", "respuesta")
        assert cid
        conv = store_mod.get_conversation(cid)
        assert len(conv["messages"]) == 2

    def test_append_turn_sets_title_from_first_message(self, store_mod):
        cid = store_mod.append_turn(None, "¿Cómo va mi recuperación?", "Bien.")
        conv = store_mod.get_conversation(cid)
        assert "recuperación" in conv["title"]

    def test_set_active_and_get_active_id(self, store_mod):
        conv = store_mod.create_conversation()
        store_mod.set_active(conv["id"])
        assert store_mod.get_active_id() == conv["id"]

    def test_clear_all_removes_everything(self, store_mod):
        store_mod.create_conversation()
        store_mod.create_conversation()
        store_mod.clear_all()
        assert store_mod.list_conversations() == []
        assert store_mod.get_active_id() is None


# ── Aislamiento de contexto (el punto de la feature) ───────────────────────

class TestContextIsolation:
    def test_context_of_a_never_includes_b(self, store_mod):
        conv_a = store_mod.create_conversation()
        conv_b = store_mod.create_conversation()

        store_mod.append_turn(conv_a["id"], "pregunta EXCLUSIVA de A", "respuesta A1")
        store_mod.append_turn(conv_b["id"], "pregunta EXCLUSIVA de B", "respuesta B1")
        store_mod.append_turn(conv_a["id"], "segunda pregunta de A", "respuesta A2")

        ctx_a = store_mod.get_context(conv_a["id"], n=10)
        ctx_b = store_mod.get_context(conv_b["id"], n=10)

        text_a = " ".join(m["content"] for m in ctx_a)
        text_b = " ".join(m["content"] for m in ctx_b)

        assert "EXCLUSIVA de B" not in text_a
        assert "respuesta B1" not in text_a
        assert "EXCLUSIVA de A" not in text_b
        assert "respuesta A1" not in text_b
        assert "respuesta A2" not in text_b

        # Y sí contiene lo suyo.
        assert "EXCLUSIVA de A" in text_a
        assert "EXCLUSIVA de B" in text_b

    def test_get_context_respects_n(self, store_mod):
        conv = store_mod.create_conversation()
        for i in range(8):
            store_mod.append_turn(conv["id"], f"q{i}", f"a{i}")
        ctx = store_mod.get_context(conv["id"], n=4)
        assert len(ctx) == 4
        assert ctx[-1]["content"] == "a7"

    def test_get_context_nonexistent_conversation_returns_empty(self, store_mod):
        assert store_mod.get_context("no-existe", n=10) == []

    def test_get_context_none_cid_returns_empty(self, store_mod):
        assert store_mod.get_context(None, n=10) == []


# ── Caps ────────────────────────────────────────────────────────────────────

class TestCaps:
    def test_per_conversation_cap_200_messages(self, store_mod, monkeypatch):
        from app import coach_store as cs
        monkeypatch.setattr(cs, "_MAX_MSGS_PER_CONV", 10)
        conv = store_mod.create_conversation()
        for i in range(10):
            store_mod.append_turn(conv["id"], f"q{i}", f"a{i}")  # 20 msgs -> cap a 10
        full = store_mod.get_conversation(conv["id"])
        assert len(full["messages"]) == 10
        # Se conservan los MÁS RECIENTES.
        assert full["messages"][-1]["content"] == "a9"

    def test_total_conversations_cap_evicts_oldest_not_active(self, store_mod, monkeypatch):
        from app import coach_store as cs
        monkeypatch.setattr(cs, "_MAX_CONVERSATIONS", 3)

        ids = []
        for i in range(3):
            c = store_mod.create_conversation()
            store_mod.append_turn(c["id"], f"q{i}", f"a{i}")
            ids.append(c["id"])

        # La más vieja es ids[0]. La marcamos activa para probar que NO se evicta
        # aunque sea la más vieja por updated.
        store_mod.set_active(ids[0])

        # Crear una 4ta -> dispara cap de 3.
        c4 = store_mod.create_conversation()
        store_mod.append_turn(c4["id"], "q3", "a3")

        remaining_ids = {c["id"] for c in store_mod.list_conversations()}
        assert ids[0] in remaining_ids  # activa: protegida de eviction
        assert len(remaining_ids) == 3


# ── None-safety / nunca lanza ───────────────────────────────────────────────

class TestNoneSafety:
    def test_corrupt_store_file_recovers_empty(self, store_mod, tmp_path):
        (tmp_path / "coach_conversations.json").write_text("{not json", encoding="utf-8")
        assert store_mod.list_conversations() == []

    def test_store_file_wrong_shape_recovers_empty(self, store_mod, tmp_path):
        (tmp_path / "coach_conversations.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert store_mod.list_conversations() == []

    def test_load_history_compat_empty_when_no_active(self, store_mod):
        assert store_mod.load_history() == []

    def test_load_history_compat_returns_active_conversation_messages(self, store_mod):
        conv = store_mod.create_conversation()
        store_mod.append_turn(conv["id"], "hola", "hola!")
        assert store_mod.load_history() == store_mod.get_conversation(conv["id"])["messages"]

    def test_clear_compat_alias(self, store_mod):
        store_mod.create_conversation()
        store_mod.clear()
        assert store_mod.list_conversations() == []
