"""
test_mental_store.py — Tests de app/mental_store.py (expediente longitudinal
del Coach Mental deportivo, roadmap coach-mental Fase 1, Paso 1).

Cubre:
- Round-trip de perfil (get/set), sin perfil -> {}.
- append_session + cap de 200 sesiones (evicta la más vieja).
- expediente_block: con y sin datos, formato, focos de la última sesión.
- Archivo corrupto -> degrada a vacío sin lanzar.
- Household-aware: con contexto de usuario activo, escribe en
  data/users/<uid>/mental_log.json (mismo patrón que coach_store.py).

NO toca scoring/bodyage/coach de salud.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def store_mod(tmp_path, monkeypatch):
    """Aísla mental_store en tmp_path (nunca toca data/ real) — mismo patrón
    que tests/test_coach_store.py::store_mod."""
    from app import mental_store as ms
    monkeypatch.setattr(ms, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(ms, "_STORE_FILE", tmp_path / "mental_log.json")
    return ms


# ── Perfil ───────────────────────────────────────────────────────────────────

class TestProfile:
    def test_get_profile_empty_when_no_file(self, store_mod):
        assert store_mod.get_profile() == {}

    def test_set_then_get_profile_round_trip(self, store_mod):
        profile = {
            "archetype": "El Sabio con bisturí",
            "calibraciones": ["UN corte directo por sesión"],
            "survey": {"motor": ["competir"]},
            "deporte": "padel",
        }
        store_mod.set_profile(profile)
        assert store_mod.get_profile() == profile

    def test_set_profile_non_dict_ignored_no_crash(self, store_mod):
        store_mod.set_profile("no soy un dict")  # no debe lanzar
        assert store_mod.get_profile() == {}

    def test_set_profile_overwrites_previous(self, store_mod):
        store_mod.set_profile({"archetype": "A"})
        store_mod.set_profile({"archetype": "B"})
        assert store_mod.get_profile() == {"archetype": "B"}


# ── Sesiones ─────────────────────────────────────────────────────────────────

class TestSessions:
    def test_list_sessions_empty_initially(self, store_mod):
        assert store_mod.list_sessions() == []

    def test_append_session_fills_defaults(self, store_mod):
        store_mod.append_session({"resumen": "Habló de tilt en el tercer set."})
        sessions = store_mod.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s["id"]
        assert s["date"]
        assert s["resumen"] == "Habló de tilt en el tercer set."
        assert s["focos"] == []
        assert s["temas"] == []
        assert s["raw"] is False

    def test_append_session_preserves_given_fields(self, store_mod):
        store_mod.append_session({
            "date": "2026-07-18",
            "conversation_id": "abc123",
            "resumen": "resumen",
            "focos": ["foco1", "foco2"],
            "temas": ["tema1"],
            "raw": True,
        })
        s = store_mod.list_sessions()[0]
        assert s["date"] == "2026-07-18"
        assert s["conversation_id"] == "abc123"
        assert s["focos"] == ["foco1", "foco2"]
        assert s["raw"] is True

    def test_append_session_non_dict_ignored_no_crash(self, store_mod):
        store_mod.append_session("no soy un dict")  # no debe lanzar
        assert store_mod.list_sessions() == []

    def test_list_sessions_respects_n_most_recent_at_end(self, store_mod):
        for i in range(5):
            store_mod.append_session({"resumen": f"sesión {i}"})
        sessions = store_mod.list_sessions(n=2)
        assert len(sessions) == 2
        assert sessions[-1]["resumen"] == "sesión 4"

    def test_cap_evicts_oldest(self, store_mod, monkeypatch):
        from app import mental_store as ms
        monkeypatch.setattr(ms, "_MAX_SESSIONS", 3)
        for i in range(5):
            store_mod.append_session({"resumen": f"sesión {i}"})
        sessions = store_mod.list_sessions()
        assert len(sessions) == 3
        # Se conservan las MÁS RECIENTES.
        assert [s["resumen"] for s in sessions] == ["sesión 2", "sesión 3", "sesión 4"]


# ── expediente_block ─────────────────────────────────────────────────────────

class TestExpedienteBlock:
    def test_empty_without_profile_or_sessions(self, store_mod):
        assert store_mod.expediente_block() == ""

    def test_includes_profile_fields(self, store_mod):
        store_mod.set_profile({
            "archetype": "El Sabio con bisturí",
            "calibraciones": ["UN corte directo por sesión", "Un solo foco semanal"],
            "survey": {"motor": ["competir - odio perder"]},
            "deporte": "padel",
        })
        block = store_mod.expediente_block()
        assert "=== EXPEDIENTE MENTAL ===" in block
        assert "El Sabio con bisturí" in block
        assert "UN corte directo por sesión" in block
        assert "competir - odio perder" in block
        assert "padel" in block

    def test_includes_recent_sessions_and_truncates_long_resumen(self, store_mod):
        long_resumen = "x" * 900
        store_mod.append_session({"date": "2026-07-01", "resumen": long_resumen, "focos": ["foco A"]})
        block = store_mod.expediente_block()
        assert "2026-07-01" in block
        assert "foco A" in block
        # Truncado a ~400 chars + elipsis, no los 900 completos.
        assert "x" * 900 not in block
        assert "x" * 400 in block

    def test_last_session_focos_appear_as_weekly_focus_line(self, store_mod):
        store_mod.append_session({"date": "2026-07-01", "resumen": "primera", "focos": ["foco viejo"]})
        store_mod.append_session({"date": "2026-07-08", "resumen": "segunda", "focos": ["foco nuevo"]})
        block = store_mod.expediente_block()
        assert "FOCOS DE LA SEMANA PASADA" in block
        assert "foco nuevo" in block
        # Solo el foco de la ÚLTIMA sesión va en la línea de "semana pasada".
        idx = block.index("FOCOS DE LA SEMANA PASADA")
        assert "foco viejo" not in block[idx:]

    def test_no_weekly_focus_line_when_last_session_has_no_focos(self, store_mod):
        store_mod.append_session({"date": "2026-07-01", "resumen": "sin focos", "focos": []})
        block = store_mod.expediente_block()
        assert "FOCOS DE LA SEMANA PASADA" not in block

    def test_respects_n_parameter(self, store_mod):
        for i in range(8):
            store_mod.append_session({"date": f"2026-07-0{i+1}", "resumen": f"sesión {i}"})
        block = store_mod.expediente_block(n=3)
        assert "sesión 7" in block
        assert "sesión 6" in block
        assert "sesión 5" in block
        assert "sesión 4" not in block


# ── None-safety / degradación ────────────────────────────────────────────────

class TestNoneSafety:
    def test_corrupt_store_file_recovers_empty(self, store_mod, tmp_path):
        (tmp_path / "mental_log.json").write_text("{not json", encoding="utf-8")
        assert store_mod.get_profile() == {}
        assert store_mod.list_sessions() == []
        assert store_mod.expediente_block() == ""

    def test_store_file_wrong_shape_recovers_empty(self, store_mod, tmp_path):
        (tmp_path / "mental_log.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert store_mod.get_profile() == {}
        assert store_mod.list_sessions() == []

    def test_store_file_empty_text_recovers_empty(self, store_mod, tmp_path):
        (tmp_path / "mental_log.json").write_text("", encoding="utf-8")
        assert store_mod.get_profile() == {}


# ── Household-aware ──────────────────────────────────────────────────────────

class TestHousehold:
    def test_writes_under_user_data_dir_when_household_active(self, tmp_path, monkeypatch):
        """Con contexto de usuario activo (household), el expediente vive en
        data/users/<uid>/mental_log.json — mismo mecanismo que coach_store.py.
        Sin esto, el expediente de dos usuarios se mezclaría (fuga de datos
        sensibles emocionales, riesgo #4 del roadmap)."""
        from app import mental_store as ms
        from app import userctx as uc

        monkeypatch.setattr(uc, "_DATA_DIR", tmp_path)
        # Legacy _STORE_FILE apunta a otra carpeta para detectar fuga si el
        # household-awareness fallara y cayera al legacy por accidente.
        monkeypatch.setattr(ms, "_DATA_DIR", tmp_path / "legacy_should_not_be_used")
        monkeypatch.setattr(ms, "_STORE_FILE", tmp_path / "legacy_should_not_be_used" / "mental_log.json")

        user = uc.add_user("Doc")
        assert user is not None
        token = uc.set_current_uid(user["id"])
        try:
            assert uc.should_use_household_paths() is True
            ms.set_profile({"archetype": "El Sabio"})
            ms.append_session({"resumen": "sesión household"})
        finally:
            uc.reset_current_uid(token)

        expected_file = tmp_path / "users" / user["id"] / "mental_log.json"
        assert expected_file.exists()
        data = json.loads(expected_file.read_text(encoding="utf-8"))
        assert data["profile"]["archetype"] == "El Sabio"
        assert len(data["sessions"]) == 1
        assert not (tmp_path / "legacy_should_not_be_used" / "mental_log.json").exists()

    def test_two_users_do_not_share_expediente(self, tmp_path, monkeypatch):
        from app import mental_store as ms
        from app import userctx as uc

        monkeypatch.setattr(uc, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(ms, "_DATA_DIR", tmp_path)
        monkeypatch.setattr(ms, "_STORE_FILE", tmp_path / "mental_log.json")

        user_a = uc.add_user("A")
        user_b = uc.add_user("B")

        token = uc.set_current_uid(user_a["id"])
        try:
            ms.append_session({"resumen": "SOLO de A"})
        finally:
            uc.reset_current_uid(token)

        token = uc.set_current_uid(user_b["id"])
        try:
            sessions_b = ms.list_sessions()
        finally:
            uc.reset_current_uid(token)

        assert sessions_b == []
