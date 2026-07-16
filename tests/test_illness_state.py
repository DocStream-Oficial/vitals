"""
test_illness_state.py — Tests de app/illness_state.py (latch de la alerta
illness_early_warning, dev-harness/illness-latch).

Cubre:
- load_latch/save_latch: escritura atómica, none-safe (ausente/corrupto/vacío).
- apply_latch: rank (alert>watch>none), reset por día calendario, el caso
  "fresh=None pero hay alert persistido de HOY", empate de rank.
- Degradación: sin today_date, current_data_dir inaccesible.

Aislamiento: `state_mod` redirige _DATA_DIR/_LATCH_FILE a tmp_path (mismo
patrón que tests/test_coach_store.py) — NUNCA toca data/ real. Nota: el
autouse `_isolate_illness_state_data_dir` de tests/conftest.py YA aísla esto
para toda la suite; este fixture local es redundante a propósito (defensa en
profundidad + hace el archivo autocontenido/legible sin depender de conftest).

NO toca umbrales/lógica de rule_illness_early_warning (eso vive en
app/insights.py y no se toca aquí).
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def state_mod(tmp_path, monkeypatch):
    """Aísla illness_state en tmp_path (nunca toca data/ real)."""
    from app import illness_state as st
    monkeypatch.setattr(st, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(st, "_LATCH_FILE", tmp_path / "illness_latch.json")
    return st


def _alert(msg="pico de la mañana"):
    return {
        "id": "illness_early_warning",
        "severity": "alert",
        "category": "salud",
        "icon": "🌡️",
        "title": "Posible enfermedad",
        "summary": msg,
        "factors": ["Temp elevada", "HRV anómala"],
        "recommendation": "Descansa.",
    }


def _watch(msg="una señal"):
    return {
        "id": "illness_early_warning",
        "severity": "watch",
        "category": "salud",
        "icon": "🌡️",
        "title": "Vigilar",
        "summary": msg,
        "factors": ["Temp elevada"],
        "recommendation": "Observa.",
    }


# ── load_latch / save_latch ──────────────────────────────────────────────────

class TestLoadSave:
    def test_load_absent_returns_none(self, state_mod):
        assert state_mod.load_latch() is None

    def test_save_then_load_roundtrip(self, state_mod, tmp_path):
        state_mod.save_latch({"date": "2024-01-15", "severity": "alert", "insight": _alert()})
        loaded = state_mod.load_latch()
        assert loaded is not None
        assert loaded["date"] == "2024-01-15"
        assert loaded["severity"] == "alert"
        assert loaded["insight"]["summary"] == "pico de la mañana"
        # No debe sobrevivir el .tmp de la escritura atómica.
        assert not (tmp_path / "illness_latch.json.tmp").exists()
        assert (tmp_path / "illness_latch.json").exists()

    def test_load_corrupt_json_returns_none_no_crash(self, state_mod, tmp_path):
        (tmp_path / "illness_latch.json").write_text("{not valid json", encoding="utf-8")
        assert state_mod.load_latch() is None

    def test_load_empty_file_returns_none(self, state_mod, tmp_path):
        (tmp_path / "illness_latch.json").write_text("", encoding="utf-8")
        assert state_mod.load_latch() is None

    def test_load_unexpected_shape_returns_none(self, state_mod, tmp_path):
        (tmp_path / "illness_latch.json").write_text(json.dumps(["not", "a", "dict-with-date"]), encoding="utf-8")
        assert state_mod.load_latch() is None
        (tmp_path / "illness_latch.json").write_text(json.dumps({"severity": "alert"}), encoding="utf-8")
        assert state_mod.load_latch() is None  # sin "date"

    def test_save_never_raises_when_dir_uncreatable(self, state_mod, tmp_path, monkeypatch):
        # Simula un current_data_dir inaccesible: _latch_file() apunta a una
        # ruta cuyo padre es en realidad un ARCHIVO (mkdir fallará).
        blocker = tmp_path / "blocker_file"
        blocker.write_text("soy un archivo, no un dir", encoding="utf-8")
        monkeypatch.setattr(state_mod, "_LATCH_FILE", blocker / "illness_latch.json")
        # No debe lanzar.
        state_mod.save_latch({"date": "2024-01-15", "severity": "alert", "insight": _alert()})
        # Y load_latch tampoco.
        assert state_mod.load_latch() is None


# ── apply_latch: rank y lógica de fijado ─────────────────────────────────────

class TestApplyLatchRank:
    def test_fresh_alert_no_prior_state_persists_and_returns_alert(self, state_mod):
        fresh = _alert()
        result = state_mod.apply_latch(fresh, "2024-01-15")
        assert result is fresh
        persisted = state_mod.load_latch()
        assert persisted == {"date": "2024-01-15", "severity": "alert", "insight": fresh}

    def test_fresh_none_no_prior_state_returns_none_and_persists_none(self, state_mod):
        result = state_mod.apply_latch(None, "2024-01-15")
        assert result is None
        persisted = state_mod.load_latch()
        assert persisted == {"date": "2024-01-15", "severity": "none", "insight": None}

    def test_persisted_alert_beats_fresh_none_same_day(self, state_mod):
        """El corazón del criterio 7: alerta ya persistida de HOY sobrevive
        aunque la evaluación fresca de ESTE llamado sea None (HRV diluido)."""
        alert = _alert()
        state_mod.save_latch({"date": "2024-01-15", "severity": "alert", "insight": alert})
        result = state_mod.apply_latch(None, "2024-01-15")
        assert result == alert
        assert result["summary"] == "pico de la mañana"  # mensaje del PICO, no reconstruido

    def test_persisted_alert_beats_fresh_watch_same_day(self, state_mod):
        alert = _alert()
        state_mod.save_latch({"date": "2024-01-15", "severity": "alert", "insight": alert})
        result = state_mod.apply_latch(_watch("señal diluida"), "2024-01-15")
        assert result == alert

    def test_fresh_alert_beats_persisted_watch_same_day(self, state_mod):
        """Si la severidad SUBE dentro del día (watch -> alert), el nuevo pico gana."""
        watch = _watch()
        state_mod.save_latch({"date": "2024-01-15", "severity": "watch", "insight": watch})
        fresh_alert = _alert("empeoró a mediodía")
        result = state_mod.apply_latch(fresh_alert, "2024-01-15")
        assert result == fresh_alert
        assert state_mod.load_latch()["insight"] == fresh_alert

    def test_tie_rank_keeps_persisted_peak_stable(self, state_mod):
        """Empate de rank (watch vs watch): gana el persistido — el peak no
        flip-flopea dentro del mismo día por una segunda lectura equivalente."""
        first_watch = _watch("primera lectura")
        state_mod.save_latch({"date": "2024-01-15", "severity": "watch", "insight": first_watch})
        second_watch = _watch("segunda lectura, distinto texto")
        result = state_mod.apply_latch(second_watch, "2024-01-15")
        assert result == first_watch


class TestApplyLatchReset:
    def test_reset_on_new_day_ignores_yesterdays_alert(self, state_mod):
        """Criterio 6: persistido {date: ayer, alert}; hoy fresh=None ->
        apply_latch devuelve None (ayer se ignora), y el estado se
        re-persiste con la fecha de HOY (no acumula días viejos)."""
        yesterday_alert = _alert("ayer")
        state_mod.save_latch({"date": "2024-01-14", "severity": "alert", "insight": yesterday_alert})
        result = state_mod.apply_latch(None, "2024-01-15")
        assert result is None
        persisted = state_mod.load_latch()
        assert persisted["date"] == "2024-01-15"
        assert persisted["severity"] == "none"
        assert persisted["insight"] is None

    def test_reset_on_new_day_fresh_alert_persists_fresh_not_yesterday(self, state_mod):
        yesterday_alert = _alert("ayer")
        state_mod.save_latch({"date": "2024-01-14", "severity": "alert", "insight": yesterday_alert})
        today_alert = _alert("hoy, otro pico")
        result = state_mod.apply_latch(today_alert, "2024-01-15")
        assert result == today_alert
        assert result != yesterday_alert
        assert state_mod.load_latch()["date"] == "2024-01-15"


class TestApplyLatchNoneSafe:
    def test_no_today_date_degrades_to_fresh_without_persisting(self, state_mod):
        fresh = _alert()
        result = state_mod.apply_latch(fresh, None)
        assert result is fresh
        assert state_mod.load_latch() is None  # no debe haber persistido nada

    def test_corrupt_state_file_degrades_to_fresh(self, state_mod, tmp_path):
        (tmp_path / "illness_latch.json").write_text("{not valid json", encoding="utf-8")
        fresh = _alert()
        result = state_mod.apply_latch(fresh, "2024-01-15")
        assert result == fresh  # se comporta como si no hubiera latch previo

    def test_current_data_dir_inaccessible_never_crashes(self, state_mod, tmp_path, monkeypatch):
        blocker = tmp_path / "blocker_file"
        blocker.write_text("soy un archivo", encoding="utf-8")
        monkeypatch.setattr(state_mod, "_LATCH_FILE", blocker / "illness_latch.json")
        fresh = _alert()
        # No debe lanzar; degrada a devolver fresh (equivalente a sin latch).
        result = state_mod.apply_latch(fresh, "2024-01-15")
        assert result == fresh
