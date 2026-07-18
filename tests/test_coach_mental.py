"""
test_coach_mental.py — Tests de app/coach_mental.py (prompt builder + apertura
+ cierre del Coach Deportivo / Sesión Master, roadmap coach-mental Paso 3).

Cubre:
- build_master_prompt: contiene brain + expediente + bloque fisiológico;
  marca de primera sesión cuando no hay expediente.
- Criterio de aceptación 5: una segunda Sesión Master incluye en su prompt
  los focos de la sesión anterior.
- ask_master / opening_message: fallback i18n cuando el LLM está caído
  (mockeado vía app.llm.generate), respuesta real cuando responde.
- close_session: JSON limpio, JSON envuelto en texto, basura total, y LLM
  caído (None) — en TODOS los casos la sesión se guarda (nunca se pierde).

NO toca coach_chat.py / coach.py (solo los consume).
"""
from __future__ import annotations

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Aísla mental_store y coach_store en tmp_path (nunca toca data/ real)."""
    from app import mental_store as ms
    from app import coach_store as cs
    monkeypatch.setattr(ms, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(ms, "_STORE_FILE", tmp_path / "mental_log.json")
    monkeypatch.setattr(cs, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(cs, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(cs, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(cs, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    from app import profile as pm
    monkeypatch.setattr(pm, "effective", lambda field: {"locale": "es"}.get(field))
    return {"mental_store": ms, "coach_store": cs}


_EMPTY_DATASET = {"days": []}


# ── build_master_prompt ───────────────────────────────────────────────────────

class TestBuildMasterPrompt:
    def test_contains_brain_content(self, isolated):
        from app import coach_mental as cm
        prompt = cm.build_master_prompt("¿cómo vengo?", _EMPTY_DATASET)
        assert "Coach Deportivo de Vitals" in prompt
        assert "Sesión Master" in prompt  # doctrina menciona la estructura de 4 actos

    def test_contains_physiological_context_marker(self, isolated):
        from app import coach_mental as cm
        prompt = cm.build_master_prompt("¿cómo vengo?", _EMPTY_DATASET)
        assert "=== CONTEXTO FISIOLÓGICO ===" in prompt

    def test_no_expediente_shows_first_session_marker(self, isolated):
        from app import coach_mental as cm
        prompt = cm.build_master_prompt("¿cómo vengo?", _EMPTY_DATASET)
        assert "PRIMERA SESIÓN" in prompt
        # El bloque real "=== EXPEDIENTE MENTAL ===\n..." (con perfil/sesiones)
        # no se emite; la doctrina SÍ menciona el nombre del bloque como
        # instrucción de lectura, así que se valida la ausencia del bloque
        # armado (mental_store.expediente_block), no del string suelto.
        assert cm._mental_store.expediente_block() == ""

    def test_with_profile_includes_expediente_block(self, isolated):
        from app import coach_mental as cm
        isolated["mental_store"].set_profile({"archetype": "El Sabio con bisturí"})
        prompt = cm.build_master_prompt("¿cómo vengo?", _EMPTY_DATASET)
        assert "=== EXPEDIENTE MENTAL ===" in prompt
        assert "El Sabio con bisturí" in prompt
        assert "PRIMERA SESIÓN" not in prompt

    def test_includes_question(self, isolated):
        from app import coach_mental as cm
        prompt = cm.build_master_prompt("¿qué trabajamos hoy?", _EMPTY_DATASET)
        assert "¿qué trabajamos hoy?" in prompt

    def test_includes_history(self, isolated):
        from app import coach_mental as cm
        history = [
            {"role": "user", "content": "vengo cansado"},
            {"role": "assistant", "content": "cuéntame más"},
        ]
        prompt = cm.build_master_prompt("¿y ahora?", _EMPTY_DATASET, history=history)
        assert "vengo cansado" in prompt
        assert "cuéntame más" in prompt

    def test_criterio_5_previous_session_focos_appear_in_prompt(self, isolated):
        """Criterio de aceptación 5: sembrar una sesión previa en el store ->
        sus focos aparecen en el prompt de la SIGUIENTE Sesión Master."""
        from app import coach_mental as cm
        isolated["mental_store"].append_session({
            "date": "2026-07-11",
            "resumen": "Habló de presión en el tercer set.",
            "focos": ["Respirar antes de cada punto importante"],
        })
        prompt = cm.build_master_prompt("¿cómo estuvo la semana?", _EMPTY_DATASET)
        assert "Respirar antes de cada punto importante" in prompt
        assert "FOCOS DE LA SEMANA PASADA" in prompt


# ── ask_master ────────────────────────────────────────────────────────────────

class TestAskMaster:
    def test_returns_llm_answer_when_available(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: "Respuesta del coach.")
        answer = cm.ask_master("hola", _EMPTY_DATASET)
        assert answer == "Respuesta del coach."

    def test_fallback_when_llm_down(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: None)
        answer = cm.ask_master("hola", _EMPTY_DATASET)
        assert answer  # nunca vacío
        assert "Coach Deportivo" in answer

    def test_passes_timeout_90_and_purpose(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        captured = {}

        def fake_generate(prompt, **kw):
            captured.update(kw)
            return "ok"

        monkeypatch.setattr(_llm, "generate", fake_generate)
        cm.ask_master("hola", _EMPTY_DATASET)
        assert captured.get("timeout") == 90
        assert captured.get("purpose") == "coach_mental"


# ── opening_message ────────────────────────────────────────────────────────────

class TestOpeningMessage:
    def test_returns_llm_answer_when_available(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: "¿Cómo vienes esta semana?")
        opening = cm.opening_message(_EMPTY_DATASET)
        assert opening == "¿Cómo vienes esta semana?"

    def test_fallback_when_llm_down_never_500(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: None)
        opening = cm.opening_message(_EMPTY_DATASET)
        assert opening  # nunca vacío -> nunca deja el chat sin apertura

    def test_opening_prompt_does_not_cobrar_previous_focos(self, isolated, monkeypatch):
        """La pregunta interna de apertura instruye NO cobrar focos todavía
        (Acto 1) — se verifica que la directiva viaja en el prompt."""
        from app import coach_mental as cm
        from app import llm as _llm
        captured = {}

        def fake_generate(prompt, **kw):
            captured["prompt"] = prompt
            return "apertura"

        monkeypatch.setattr(_llm, "generate", fake_generate)
        cm.opening_message(_EMPTY_DATASET)
        assert "NO los cobres aún" in captured["prompt"]


# ── close_session ────────────────────────────────────────────────────────────

def _seed_conversation(coach_store_mod, kind="mental_master"):
    conv = coach_store_mod.create_conversation(title="Sesión Master — 2026-07-18", kind=kind)
    coach_store_mod.append_message(conv["id"], "assistant", "¿cómo vienes?")
    coach_store_mod.append_turn(conv["id"], "cansado pero bien", "cuéntame más")
    return conv["id"]


class TestCloseSession:
    def test_clean_json_parses_correctly(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(
            _llm, "generate",
            lambda prompt, **kw: '{"resumen": "Sesión sobre presión en cancha.", "temas": ["presión"], "focos": ["respirar"]}',
        )
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["resumen"] == "Sesión sobre presión en cancha."
        assert result["temas"] == ["presión"]
        assert result["focos"] == ["respirar"]
        assert result["raw"] is False
        saved = isolated["mental_store"].list_sessions()
        assert len(saved) == 1
        assert saved[0]["conversation_id"] == cid

    def test_json_wrapped_in_text_parses_correctly(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(
            _llm, "generate",
            lambda prompt, **kw: 'Aquí está: {"resumen": "resumen ok", "temas": [], "focos": []} ¡Éxito!',
        )
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["resumen"] == "resumen ok"
        assert result["raw"] is False

    def test_garbage_output_degrades_to_raw_text_never_loses_session(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: "esto no es JSON para nada")
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["raw"] is True
        assert result["resumen"] == "esto no es JSON para nada"
        assert result["focos"] == []
        saved = isolated["mental_store"].list_sessions()
        assert len(saved) == 1  # la sesión SIEMPRE se guarda

    def test_llm_down_none_still_saves_session(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: None)
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["raw"] is True
        assert "LLM no disponible" in result["resumen"]
        saved = isolated["mental_store"].list_sessions()
        assert len(saved) == 1

    def test_focos_capped_at_2(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(
            _llm, "generate",
            lambda prompt, **kw: '{"resumen": "r", "temas": [], "focos": ["a", "b", "c", "d"]}',
        )
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["focos"] == ["a", "b"]

    def test_non_list_focos_degrades_to_empty_list(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        cid = _seed_conversation(isolated["coach_store"])
        monkeypatch.setattr(
            _llm, "generate",
            lambda prompt, **kw: '{"resumen": "r", "temas": [], "focos": "no es lista"}',
        )
        result = cm.close_session(cid, _EMPTY_DATASET)
        assert result["focos"] == []
        assert result["raw"] is False

    def test_close_session_never_raises_on_missing_conversation(self, isolated, monkeypatch):
        from app import coach_mental as cm
        from app import llm as _llm
        monkeypatch.setattr(_llm, "generate", lambda prompt, **kw: '{"resumen": "r", "temas": [], "focos": []}')
        result = cm.close_session("no-existe", _EMPTY_DATASET)  # no debe lanzar
        assert result["resumen"] == "r"
