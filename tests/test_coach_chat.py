"""
test_coach_chat.py — Tests de app/coach_chat.py (Ronda 4: intake clínico + adherencia v1).

Cubre:
- _clinical_block: bloque "=== PERFIL DECLARADO ===" con/sin campos, orden de metas.
- _goals_tracking: mapeo por keywords (sueño/fuerza/pasos), None-safe sin datos.
- _brain_fallback: lazy (no import-time), usa metas del perfil si existen.
- Fix ":184" (antes hardcodeado "← CERO, su meta #2"): condicional a metas declaradas.
- Backward-compat: perfil vacío -> prompt sin bloque clínico.

NO toca coach.py/mcp_tools.py (sin cambios de lógica en esta ronda).
"""
from __future__ import annotations

import pytest

from app import coach_chat as cc


# ── _clinical_block ───────────────────────────────────────────────────────────

class TestClinicalBlock:
    def test_none_profile_returns_empty(self):
        assert cc._clinical_block(None) == ""

    def test_empty_profile_returns_empty(self):
        assert cc._clinical_block({}) == ""

    def test_all_fields_empty_lists_returns_empty(self):
        p = {"goals": [], "injuries": [], "conditions": [], "medications": []}
        assert cc._clinical_block(p) == ""

    def test_only_goals_present(self):
        p = {"goals": ["dormir mejor", "ganar fuerza"], "injuries": [], "conditions": [], "medications": []}
        block = cc._clinical_block(p)
        assert "=== PERFIL DECLARADO ===" in block
        assert "1) dormir mejor" in block
        assert "2) ganar fuerza" in block
        assert "Lesiones" not in block
        assert "Condiciones" not in block
        assert "Medicamentos" not in block

    def test_goals_order_preserved_not_sorted(self):
        """El orden de metas es prioridad declarada por el usuario -> nunca se reordena."""
        p = {"goals": ["z-meta", "a-meta"], "injuries": [], "conditions": [], "medications": []}
        block = cc._clinical_block(p)
        assert block.index("1) z-meta") < block.index("2) a-meta")

    def test_injuries_conditions_medications_all_present(self):
        p = {
            "goals": [],
            "injuries": ["rodilla derecha"],
            "conditions": ["hipertensión"],
            "medications": ["losartán"],
        }
        block = cc._clinical_block(p)
        assert "Lesiones: rodilla derecha" in block
        assert "Condiciones: hipertensión" in block
        assert "Medicamentos: losartán" in block
        assert "Metas" not in block

    def test_appears_once(self):
        p = {"goals": ["a"], "injuries": ["b"], "conditions": ["c"], "medications": ["d"]}
        block = cc._clinical_block(p)
        assert block.count("=== PERFIL DECLARADO ===") == 1


# ── _goals_tracking ───────────────────────────────────────────────────────────

class TestGoalsTracking:
    def test_no_goals_returns_empty(self):
        assert cc._goals_tracking([], {"days": []}) == ""
        assert cc._goals_tracking(None, {"days": []}) == ""

    def test_no_dataset_none_safe(self):
        """Sin dataset (None) -> no crashea, cada meta cae a 'sin dato'."""
        result = cc._goals_tracking(["dormir mejor"], None)
        assert "sin dato" in result

    def test_empty_days_none_safe(self):
        result = cc._goals_tracking(["dormir mejor", "fuerza", "pasos diarios"], {"days": []})
        assert "SEGUIMIENTO DE METAS (7d):" in result
        assert result.count("sin dato") == 3

    def test_sleep_keyword_with_data(self):
        days = [{"date": f"2026-06-{d:02d}", "asleep": 420, "bed_min": 30} for d in range(20, 27)]
        result = cc._goals_tracking(["dormir mejor"], {"days": days})
        assert "dormir mejor" in result
        assert "duración media 7.0h" in result
        assert "hora media de acostarse" in result

    def test_strength_keyword_zero_min(self):
        days = [{"date": f"2026-06-{d:02d}"} for d in range(20, 27)]
        result = cc._goals_tracking(["ganar fuerza"], {"days": days, "exercises": []})
        assert "fuerza estructurada: 0 min" in result

    def test_steps_keyword_with_data(self):
        days = [{"date": f"2026-06-{d:02d}", "steps": 8000} for d in range(20, 27)]
        result = cc._goals_tracking(["caminar más"], {"days": days})
        assert "8000 pasos/día promedio" in result

    def test_unmatched_keyword_no_metric(self):
        days = [{"date": "2026-06-20", "recovery": 60}]
        result = cc._goals_tracking(["ser más feliz"], {"days": days})
        assert "sin métrica automática" in result

    def test_multiple_goals_all_listed(self):
        days = [{"date": f"2026-06-{d:02d}", "asleep": 400, "steps": 5000} for d in range(20, 27)]
        result = cc._goals_tracking(["dormir mejor", "caminar más", "meditar"], {"days": days})
        assert "dormir mejor" in result
        assert "caminar más" in result
        assert "meditar" in result
        assert "sin métrica automática" in result  # meditar no matchea keyword


# ── _brain_fallback (lazy, no import-time) ───────────────────────────────────

class TestBrainFallback:
    def test_no_goals_uses_default_priority(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"name": "Ana", "goals": []}.get(field))
        monkeypatch.setattr(_pm, "current_age", lambda: 30)
        text = cc._brain_fallback()
        assert "sueño > fuerza > longevidad" in text
        assert "Ana" in text

    def test_with_goals_uses_declared_priority(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {
            "name": "Ana", "goals": ["bajar de peso", "dormir mejor"],
        }.get(field))
        monkeypatch.setattr(_pm, "current_age", lambda: 30)
        text = cc._brain_fallback()
        assert "bajar de peso, dormir mejor" in text
        assert "sueño > fuerza > longevidad" not in text

    def test_profile_import_error_falls_back_gracefully(self, monkeypatch):
        """Si app.profile falla al importar/leer, _brain_fallback no propaga excepción."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *a, **kw):
            if name == "app.profile":
                raise RuntimeError("boom")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _boom)
        text = cc._brain_fallback()
        assert "el usuario" in text

    def test_recomputed_each_call_not_cached_at_import(self, monkeypatch):
        """Antes era una constante _BRAIN_FALLBACK calculada al importar (stale si el
        perfil cambiaba). Ahora _brain_fallback() debe reflejar el perfil ACTUAL
        en cada llamada."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"name": "Primero", "goals": []}.get(field))
        monkeypatch.setattr(_pm, "current_age", lambda: 20)
        first = cc._brain_fallback()
        assert "Primero" in first

        monkeypatch.setattr(_pm, "effective", lambda field: {"name": "Segundo", "goals": []}.get(field))
        second = cc._brain_fallback()
        assert "Segundo" in second
        assert "Primero" not in second


# ── Fix ":184" — "← CERO, su meta #2" condicional ────────────────────────────

class TestStrengthZeroFlagFix:
    def _dataset(self, n_days=7):
        days = [{"date": f"2026-06-{d:02d}", "recovery": 50} for d in range(20, 20 + n_days)]
        return {"days": days, "exercises": [], "summary": {}}

    def test_no_goals_declared_neutral_text(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": []}.get(field))
        ctx = cc._build_context(self._dataset())
        assert "Fuerza estructurada: 0 min (sin sesiones esta semana)" in ctx
        assert "CERO, su meta #2" not in ctx

    def test_strength_goal_declared_shows_cero_flag(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": ["ganar fuerza"]}.get(field))
        ctx = cc._build_context(self._dataset())
        assert "CERO, meta declarada" in ctx

    def test_non_strength_goal_declared_no_cero_flag(self, monkeypatch):
        """Metas declaradas pero NINGUNA es de fuerza -> no debe fingir que era su meta."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": ["dormir mejor"]}.get(field))
        ctx = cc._build_context(self._dataset())
        assert "CERO, meta declarada" not in ctx
        assert "sin sesiones esta semana" not in ctx  # esta rama es solo para "sin metas"

    def test_profile_unavailable_does_not_crash(self, monkeypatch):
        """Si app.profile lanza, _build_context sigue funcionando (try/except interno)."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *a, **kw):
            if name == "app.profile":
                raise RuntimeError("boom")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _boom)
        ctx = cc._build_context(self._dataset())
        assert "Fuerza estructurada: 0 min" in ctx


# ── Backward-compat del prompt completo ──────────────────────────────────────

class TestPromptBackwardCompat:
    """NOTA: coach_brain.md (texto libre, Paso 3) menciona el literal
    '=== PERFIL DECLARADO ===' en su propia prosa explicativa -> aparece SIEMPRE
    en el prompt completo (es parte del "brain", no del bloque de datos). Estos
    tests miden el bloque de DATOS (_clinical_block), no el prompt completo, para
    no dar falso positivo por ese texto explicativo del brain."""

    def test_empty_profile_prompt_has_no_clinical_data_block(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {
            "name": "Doc", "locale": "es", "goals": [],
        }.get(field))
        monkeypatch.setattr(_pm, "current_age", lambda: 40)
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {
            "goals": [], "injuries": [], "conditions": [], "medications": [],
        })
        dataset = {"days": [{"date": "2026-06-20", "recovery": 60}], "summary": {}, "exercises": []}
        prompt = cc._build_prompt("¿cómo voy?", dataset)
        # Sin campos clínicos, _clinical_block() devuelve "" -> no debe aparecer
        # el separador "\n\n=== PERFIL DECLARADO ===\nMetas" (el dato en sí),
        # aunque el TEXTO EXPLICATIVO del brain sí mencione la etiqueta.
        assert "Metas (en orden de prioridad)" not in prompt
        assert "Lesiones:" not in prompt

    def test_full_profile_prompt_has_clinical_data_block_once(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {
            "name": "Doc", "locale": "es", "goals": ["dormir mejor"],
        }.get(field))
        monkeypatch.setattr(_pm, "current_age", lambda: 40)
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {
            "goals": ["dormir mejor", "ganar fuerza"],
            "injuries": ["rodilla"],
            "conditions": [],
            "medications": [],
        })
        dataset = {"days": [{"date": "2026-06-20", "recovery": 60}], "summary": {}, "exercises": []}
        prompt = cc._build_prompt("¿cómo voy?", dataset)
        assert prompt.count("Metas (en orden de prioridad)") == 1
        assert prompt.index("1) dormir mejor") < prompt.index("2) ganar fuerza")
        assert "Lesiones: rodilla" in prompt


# ── Fase 7: bloque CICLO en _build_context (salud femenina, opt-in) ─────────

class TestCycleContextBlock:
    def _dataset(self, extra_days=None):
        days = [{"date": "2026-06-20", "recovery": 60, "asleep": 420}]
        if extra_days:
            days = extra_days
        return {"days": days, "summary": {}, "exercises": []}

    def test_no_cycle_block_when_toggle_off(self, monkeypatch):
        """cycle_tracking=False (default) -> ningún bloque CICLO en el contexto,
        prompt idéntico al de antes de Fase 7 (criterio #1: opt-in estricto)."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {
            "cycle_tracking": False, "goals": [], "injuries": [], "conditions": [], "medications": [],
        })
        ctx = cc._build_context(self._dataset())
        assert "CICLO" not in ctx

    def test_no_cycle_block_when_profile_missing_field(self, monkeypatch):
        """Perfil sin el campo cycle_tracking en absoluto (perfil viejo) -> sin bloque."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {
            "goals": [], "injuries": [], "conditions": [], "medications": [],
        })
        ctx = cc._build_context(self._dataset())
        assert "CICLO" not in ctx

    def test_cycle_block_present_when_enabled_with_data(self, monkeypatch):
        from app import profile as _pm, cycle as _cyc
        profile_dict = {
            "cycle_tracking": True, "goals": [], "injuries": [], "conditions": [], "medications": [],
        }
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: profile_dict)
        monkeypatch.setattr(_cyc, "load_cycle_log", lambda: {
            "periods": [{"start": "2026-06-01", "source": "manual"}, {"start": "2026-06-29", "source": "manual"}]
        })
        ctx = cc._build_context(self._dataset(extra_days=[{"date": "2026-07-05", "recovery": 60}]))
        assert "CICLO" in ctx
        assert "Día" in ctx

    def test_cycle_block_absent_when_no_periods_logged(self, monkeypatch):
        """Toggle on pero sin ningún periodo registrado -> compute_cycle_state
        devuelve cycle_day=None -> el bloque se omite (nada útil que mostrar)."""
        from app import profile as _pm, cycle as _cyc
        profile_dict = {
            "cycle_tracking": True, "goals": [], "injuries": [], "conditions": [], "medications": [],
        }
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: profile_dict)
        monkeypatch.setattr(_cyc, "load_cycle_log", lambda: {"periods": []})
        ctx = cc._build_context(self._dataset())
        assert "CICLO" not in ctx

    def test_cycle_block_never_crashes_on_error(self, monkeypatch):
        """Si compute_cycle_state lanza inesperadamente, _build_context sigue
        funcionando (try/except interno, nunca tumba el contexto del coach)."""
        from app import profile as _pm, cycle as _cyc
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {"cycle_tracking": True})
        monkeypatch.setattr(_cyc, "load_cycle_log", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        ctx = cc._build_context(self._dataset())  # no debe lanzar
        assert "Fuerza estructurada: 0 min" in ctx


# ── Roadmap P1, F4 (paso 7): bloque PLAN ACTIVO en _build_context ───────────

class TestPlanActiveContextBlock:
    def _dataset(self, extra_days=None):
        days = extra_days or [{"date": "2026-06-20", "recovery": 60, "asleep": 420}]
        return {"days": days, "summary": {}, "exercises": []}

    def _patch_plan_log(self, monkeypatch, tmp_path):
        from app import plan_store as _pln
        monkeypatch.setattr(_pln, "_PLAN_LOG_FILE", tmp_path / "plan_log.json")

    def test_no_plan_block_without_active_plan(self, monkeypatch, tmp_path):
        self._patch_plan_log(monkeypatch, tmp_path)
        ctx = cc._build_context(self._dataset())
        assert "PLAN ACTIVO" not in ctx

    def test_prompt_identical_to_pre_f4_without_active_plan(self, monkeypatch, tmp_path):
        """Criterio de riesgo #5 del roadmap P1: sin plan activo, el prompt
        completo debe ser EXACTAMENTE igual al de antes de F4 (assert de
        igualdad, no solo 'no contiene PLAN ACTIVO'). Comparamos el prompt
        construido con plan_store "vacío" (sin _PLAN_LOG_FILE aislado, es
        decir el estado por default de un módulo recién importado sin plan)
        contra sí mismo dos veces — la invariante real es que agregar F4 NO
        introduce ningún bloque cuando no hay plan."""
        self._patch_plan_log(monkeypatch, tmp_path)
        dataset = self._dataset()
        prompt_a = cc._build_prompt("¿cómo voy?", dataset)
        prompt_b = cc._build_prompt("¿cómo voy?", dataset)
        assert prompt_a == prompt_b
        assert "PLAN ACTIVO" not in prompt_a

    def test_plan_block_present_with_active_plan(self, monkeypatch, tmp_path):
        self._patch_plan_log(monkeypatch, tmp_path)
        from app import plan_store as _pln
        _pln.start_plan("sleep_reset", "2026-06-19")
        ctx = cc._build_context(self._dataset())
        assert "PLAN ACTIVO" in ctx
        assert "sleep_reset" in ctx
        assert "día" in ctx.lower()

    def test_plan_block_shows_adapted_reason(self, monkeypatch, tmp_path):
        self._patch_plan_log(monkeypatch, tmp_path)
        from app import plan_store as _pln
        _pln.start_plan("aerobic_base", "2026-06-20")
        ctx = cc._build_context(self._dataset(extra_days=[{"date": "2026-06-20", "recovery": 20, "asleep": 420}]))
        assert "PLAN ACTIVO" in ctx
        assert "adaptad" in ctx.lower() or "recuperaci" in ctx.lower()

    def test_plan_block_never_crashes_on_error(self, monkeypatch, tmp_path):
        self._patch_plan_log(monkeypatch, tmp_path)
        from app import plan_store as _pln
        monkeypatch.setattr(_pln, "plan_status", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))
        ctx = cc._build_context(self._dataset())  # no debe lanzar
        assert "Fuerza estructurada: 0 min" in ctx
