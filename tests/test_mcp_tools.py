"""
test_mcp_tools.py — Tests de las funciones puras de mcp_tools.

IMPORTANTE: NO importa vitals_mcp.py (requiere Python 3.10+ / mcp SDK).
            Solo importa app.mcp_tools (puro, corre en Python 3.9).

Cubre:
- Cada función sobre el dataset REAL (data/health_compact.json).
- Dataset vacío {} y None → no crashea, devuelve algo razonable.
- insights_list no trae &lt; (HTML-escapes normalizados).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── Importar SOLO mcp_tools (nunca vitals_mcp) ───────────────────────────────
from app import mcp_tools as m


# ── Fixture: aislar el historial del coach (no escribir en el real) ───────────

@pytest.fixture(autouse=True)
def _isolate_coach_history(tmp_path, monkeypatch):
    """ask() persiste el turno como side-effect; redirigir a tmp para no
    contaminar el data/coach_history.json real del usuario."""
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")


# ── Fixture: dataset real ─────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def real_ds():
    """Carga el dataset real de data/health_compact.json."""
    return m._load_dataset()


@pytest.fixture(scope="session")
def real_ds_last_complete(real_ds):
    """Fase 8A (paso A2): variante de `real_ds` recortada al ÚLTIMO día
    COMPLETO (con recovery/asleep/hrv no-None), en vez de days[-1] crudo.

    `real_ds["days"][-1]` es el día de HOY tal como lo dejó el último sync —
    en la práctica casi siempre incompleto (el sync diario corre a
    SYNC_HOUR:00 y hasta entonces recovery/sueño/hrv de hoy son None; ver
    app/scheduler.py). today_snapshot() SIEMPRE lee days[-1] literal (por
    diseño: "snapshot del día más reciente"), así que los tests que exigen
    recovery/sueño/hrv presentes deben alimentarlo con un dataset cuyo último
    día sí los tenga — de lo contrario el test queda a merced de la hora del
    día y del estado del sync real del usuario, exactamente el acoplamiento que
    este fixture elimina.

    Busca hacia atrás desde el final; si NINGÚN día está completo (dataset
    real degenerado), cae de vuelta a real_ds tal cual (los tests que dependan
    de datos completos fallarán con un mensaje claro, no un skip silencioso)."""
    days = (real_ds or {}).get("days") or []
    for idx in range(len(days) - 1, -1, -1):
        day = days[idx]
        if (day.get("recovery") is not None
                and day.get("asleep") is not None
                and day.get("hrv") is not None):
            trimmed = dict(real_ds)
            trimmed["days"] = days[: idx + 1]
            return trimmed
    return real_ds


# ── _load_dataset ─────────────────────────────────────────────────────────────

class TestLoadDataset:
    def test_returns_dict(self, real_ds):
        assert isinstance(real_ds, dict)

    def test_has_days(self, real_ds):
        assert "days" in real_ds
        assert len(real_ds["days"]) > 0

    def test_has_summary(self, real_ds):
        assert "summary" in real_ds

    def test_missing_file_returns_empty(self, monkeypatch, tmp_path):
        """Si el archivo no existe, devuelve {} sin excepción."""
        monkeypatch.setattr(m, "_DATASET_FILE", tmp_path / "no_existe.json")
        result = m._load_dataset()
        assert result == {}

    def test_empty_file_returns_empty(self, monkeypatch, tmp_path):
        """Archivo vacío → {}."""
        f = tmp_path / "empty.json"
        f.write_text("", encoding="utf-8")
        monkeypatch.setattr(m, "_DATASET_FILE", f)
        result = m._load_dataset()
        assert result == {}

    def test_invalid_json_returns_empty(self, monkeypatch, tmp_path):
        """JSON inválido → {}."""
        f = tmp_path / "bad.json"
        f.write_text("{broken", encoding="utf-8")
        monkeypatch.setattr(m, "_DATASET_FILE", f)
        result = m._load_dataset()
        assert result == {}


# ── today_snapshot ────────────────────────────────────────────────────────────

class TestTodaySnapshot:
    def test_real_data_returns_dict(self, real_ds):
        snap = m.today_snapshot(real_ds)
        assert isinstance(snap, dict)
        assert "status" not in snap, "No debe tener status cuando hay datos"

    def test_real_data_has_fecha(self, real_ds):
        snap = m.today_snapshot(real_ds)
        assert "fecha" in snap
        assert snap["fecha"] != "sin fecha"

    def test_real_data_has_recovery_and_estado(self, real_ds_last_complete):
        """Usa el último día COMPLETO (fixture real_ds_last_complete), no
        days[-1] crudo — days[-1] real es 'hoy' y casi siempre está incompleto
        hasta que corre el sync diario (ver docstring del fixture). Robusto a
        la fecha/hora en que corra la suite."""
        snap = m.today_snapshot(real_ds_last_complete)
        assert "recovery" in snap
        assert snap["recovery_estado"] in ("alta", "media", "baja")

    def test_real_data_has_sueno(self, real_ds_last_complete):
        snap = m.today_snapshot(real_ds_last_complete)
        assert "sueno_h" in snap
        assert snap["sueno_h"] > 0

    def test_real_data_has_hrv(self, real_ds_last_complete):
        snap = m.today_snapshot(real_ds_last_complete)
        assert "hrv_ms" in snap

    def test_real_data_has_strain(self, real_ds):
        snap = m.today_snapshot(real_ds)
        assert "strain" in snap

    def test_fuerza_semana_is_int(self, real_ds):
        snap = m.today_snapshot(real_ds)
        assert isinstance(snap.get("fuerza_semana_min"), int)

    def test_empty_dict_returns_sin_datos(self):
        result = m.today_snapshot({})
        assert result.get("status") == "sin datos"

    def test_none_returns_sin_datos(self):
        result = m.today_snapshot(None)
        assert result.get("status") == "sin datos"

    def test_empty_days_returns_sin_datos(self):
        result = m.today_snapshot({"days": [], "summary": {}})
        assert result.get("status") == "sin datos"

    def test_json_serializable(self, real_ds):
        snap = m.today_snapshot(real_ds)
        # No debe lanzar excepción
        encoded = json.dumps(snap, ensure_ascii=False)
        assert len(encoded) > 0


# ── trends ────────────────────────────────────────────────────────────────────

class TestTrends:
    def test_real_data_returns_dict(self, real_ds):
        t = m.trends(real_ds)
        assert isinstance(t, dict)
        assert "status" not in t

    def test_real_data_has_recovery_7d_30d(self, real_ds):
        t = m.trends(real_ds)
        assert "recovery_pct_7d" in t
        assert "recovery_pct_30d" in t

    def test_real_data_has_hrv_fields(self, real_ds):
        t = m.trends(real_ds)
        assert "hrv_ms_7d" in t
        assert "hrv_ms_30d" in t

    def test_real_data_has_sueno(self, real_ds):
        t = m.trends(real_ds)
        assert "sueno_h_7d" in t
        assert "sueno_h_30d" in t

    def test_real_data_noches_menos_7h_is_int(self, real_ds):
        t = m.trends(real_ds)
        assert isinstance(t.get("noches_menos_7h"), int)

    def test_empty_returns_sin_datos(self):
        assert m.trends({}).get("status") == "sin datos"

    def test_none_returns_sin_datos(self):
        assert m.trends(None).get("status") == "sin datos"

    def test_json_serializable(self, real_ds):
        t = m.trends(real_ds)
        json.dumps(t, ensure_ascii=False)


# ── insights_list ─────────────────────────────────────────────────────────────

class TestInsightsList:
    def test_real_data_returns_list(self, real_ds):
        ins = m.insights_list(real_ds)
        assert isinstance(ins, list)

    def test_real_data_max_5_insights(self, real_ds):
        ins = m.insights_list(real_ds)
        assert len(ins) <= 5

    def test_each_insight_has_required_keys(self, real_ds):
        ins = m.insights_list(real_ds)
        for item in ins:
            assert "id" in item
            assert "severity" in item
            assert "title" in item

    def test_no_html_escapes_in_factors(self, real_ds):
        """Los textos NO deben contener &lt; ni &gt; ni &amp; (HTML-escapes)."""
        ins = m.insights_list(real_ds)
        for item in ins:
            factors = item.get("factors", [])
            for f in factors:
                assert "&lt;" not in f, f"HTML-escape '&lt;' encontrado en factor: {f!r}"
                assert "&gt;" not in f, f"HTML-escape '&gt;' encontrado en factor: {f!r}"
                assert "&amp;" not in f, f"HTML-escape '&amp;' encontrado en factor: {f!r}"
            # También chequear summary y recommendation
            for field in ("summary", "recommendation", "title"):
                txt = item.get(field, "")
                assert "&lt;" not in txt

    def test_empty_returns_empty_list(self):
        assert m.insights_list({}) == []

    def test_none_returns_empty_list(self):
        assert m.insights_list(None) == []

    def test_normalize_html_escapes(self):
        """_normalize_insight convierte &lt; → <."""
        raw = {
            "id": "test",
            "severity": "watch",
            "title": "HRV &lt; base",
            "summary": "Valor &lt; umbral",
            "factors": ["HRV &lt; 50 ms", "normal text"],
            "recommendation": "Cuida el sueño &amp; la carga",
        }
        result = m._normalize_insight(raw)
        assert result["title"] == "HRV < base"
        assert result["summary"] == "Valor < umbral"
        assert result["factors"][0] == "HRV < 50 ms"
        assert result["recommendation"] == "Cuida el sueño & la carga"

    def test_json_serializable(self, real_ds):
        ins = m.insights_list(real_ds)
        json.dumps(ins, ensure_ascii=False)


# ── bodyage_summary ───────────────────────────────────────────────────────────

class TestBodyageSummary:
    def test_real_data_returns_dict(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        assert isinstance(ba, dict)
        assert "status" not in ba

    def test_real_data_has_body_age(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        assert "body_age" in ba
        assert ba["body_age"] is not None

    def test_real_data_has_vo2max(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        assert "vo2max" in ba

    def test_real_data_has_category(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        assert "category" in ba

    def test_real_data_has_drivers(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        assert "drivers" in ba
        assert isinstance(ba["drivers"], dict)

    def test_empty_returns_sin_datos(self):
        assert m.bodyage_summary({}).get("status") == "sin datos"

    def test_none_returns_sin_datos(self):
        assert m.bodyage_summary(None).get("status") == "sin datos"

    def test_no_bodyage_in_summary(self):
        ds = {"days": [{"date": "2024-01-01"}], "summary": {}}
        result = m.bodyage_summary(ds)
        assert result.get("status") == "sin datos"

    def test_json_serializable(self, real_ds):
        ba = m.bodyage_summary(real_ds)
        json.dumps(ba, ensure_ascii=False)


# ── morning_brief ─────────────────────────────────────────────────────────────

class TestMorningBrief:
    def test_real_data_returns_str(self, real_ds):
        brief = m.morning_brief(real_ds)
        assert isinstance(brief, str)
        assert len(brief) > 50

    def test_real_data_has_buenos_dias(self, real_ds):
        brief = m.morning_brief(real_ds)
        assert "Buenos dias" in brief or "Vitals" in brief

    def test_real_data_has_prioridad(self, real_ds):
        brief = m.morning_brief(real_ds)
        assert "PRIORIDAD:" in brief

    def test_real_data_has_hoy_section(self, real_ds):
        brief = m.morning_brief(real_ds)
        assert "HOY:" in brief

    def test_no_html_escapes(self, real_ds):
        brief = m.morning_brief(real_ds)
        assert "&lt;" not in brief
        assert "&gt;" not in brief

    def test_empty_returns_string(self):
        result = m.morning_brief({})
        assert isinstance(result, str)
        assert "sin datos" in result.lower() or "sincronización" in result.lower() or "sincronizacion" in result.lower()

    def test_none_returns_string(self):
        result = m.morning_brief(None)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_llm_call(self, real_ds, monkeypatch):
        """morning_brief es determinista; no llama a subprocess / coach."""
        import subprocess
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or None)
        m.morning_brief(real_ds)
        assert len(calls) == 0, "morning_brief no debe invocar subprocess (sin LLM)"


# ── ask ───────────────────────────────────────────────────────────────────────

class TestAsk:
    def test_returns_string(self, real_ds, monkeypatch):
        """ask() llama a coach_chat.ask_coach — mockeamos el subprocess."""
        import subprocess

        mock_result = type("R", (), {"returncode": 0, "stdout": "Respuesta del coach.", "stderr": ""})()
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: mock_result)

        result = m.ask("¿Cómo estoy hoy?", ds=real_ds)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_ds_loads_dataset(self, monkeypatch):
        """Cuando ds=None, carga el dataset automáticamente."""
        captured = {}

        def mock_ask_coach(q, ds, history=None):
            captured["ds"] = ds
            return "ok"

        monkeypatch.setattr("app.coach_chat.ask_coach", mock_ask_coach)
        m.ask("pregunta", ds=None)
        # Debe haber cargado algo (puede ser {} si falla, pero no debe crashear)
        assert "ds" in captured

    def test_exception_returns_fallback(self, real_ds, monkeypatch):
        """Si coach_chat falla, ask() retorna mensaje de fallback, no excepción."""
        def broken_coach(q, ds, history=None):
            raise RuntimeError("CLI no disponible")

        monkeypatch.setattr("app.coach_chat.ask_coach", broken_coach)
        result = m.ask("pregunta", ds=real_ds)
        assert isinstance(result, str)
        assert len(result) > 0


# ── bedtime_brief (Ronda 4) ───────────────────────────────────────────────────

class TestBedtimeBrief:
    def _days(self, bed_mins, asleep=420, recovery=60):
        return [
            {"date": f"2026-06-{20+i:02d}", "bed_min": bm, "asleep": asleep, "recovery": recovery}
            for i, bm in enumerate(bed_mins)
        ]

    def test_none_ds_returns_amable_message(self):
        result = m.bedtime_brief(None)
        assert isinstance(result, str)
        assert "sin datos" in result.lower() or "sincroniz" in result.lower()

    def test_empty_ds_returns_amable_message(self):
        result = m.bedtime_brief({})
        assert isinstance(result, str)
        assert "sin datos" in result.lower() or "sincroniz" in result.lower()

    def test_no_days_returns_amable_message(self):
        result = m.bedtime_brief({"days": []})
        assert "sin datos" in result.lower() or "sincroniz" in result.lower()

    def test_is_short_2_to_4_lines(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": []}.get(field))
        ds = {"days": self._days([30, 45, 20, 10, 60, 15, 25])}
        result = m.bedtime_brief(ds)
        lines = [l for l in result.split("\n") if l.strip()]
        assert 2 <= len(lines) <= 4

    def test_no_bedtime_goal_uses_00_00_default(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": ["dormir mejor"]}.get(field))
        ds = {"days": self._days([30, 45, 20, 10, 60, 15, 25])}
        result = m.bedtime_brief(ds)
        assert "00:00" in result

    def test_goal_with_explicit_hour_parsed(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {
            "goals": ["acostarme antes de las 23:00"],
        }.get(field))
        ds = {"days": self._days([30, 45, 20, 10, 60, 15, 25])}
        result = m.bedtime_brief(ds)
        assert "23:00" in result

    def test_no_sleep_data_none_safe(self, monkeypatch):
        """Sin bed_min en ningún día -> no crashea, mensaje amable con la meta."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": []}.get(field))
        ds = {"days": [{"date": "2026-06-20", "recovery": 50}]}
        result = m.bedtime_brief(ds)
        assert isinstance(result, str)
        assert "sin datos de hora de dormir" in result.lower()

    def test_recovery_line_present_when_available(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective", lambda field: {"goals": []}.get(field))
        ds = {"days": self._days([30], recovery=80)}
        result = m.bedtime_brief(ds)
        assert "80%" in result

    def test_profile_unavailable_does_not_crash(self, monkeypatch):
        """Si app.profile lanza al leer goals, bedtime_brief sigue funcionando
        (usa meta default 00:00)."""
        import builtins
        real_import = builtins.__import__

        def _boom(name, *a, **kw):
            if name == "app.profile":
                raise RuntimeError("boom")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _boom)
        ds = {"days": self._days([30, 45, 20])}
        result = m.bedtime_brief(ds)
        assert isinstance(result, str)
        assert "00:00" in result

    def test_bedtime_regex_rejects_24_00(self):
        assert m._parse_bedtime_goal(["acostarme a las 24:00"]) is None

    def test_bedtime_regex_rejects_single_digit_minute(self):
        assert m._parse_bedtime_goal(["acostarme a las 0:5"]) is None

    def test_bedtime_regex_accepts_valid_hour(self):
        assert m._parse_bedtime_goal(["meta: 22:30 en la cama"]) == "22:30"

    def test_bedtime_regex_no_goals_returns_none(self):
        assert m._parse_bedtime_goal([]) is None
        assert m._parse_bedtime_goal(None) is None

    def test_bed_min_to_hhmm_negative_offset(self):
        # -30 min desde medianoche = 23:30
        assert m._bed_min_to_hhmm(-30) == "23:30"

    def test_bed_min_to_hhmm_positive_offset(self):
        # +45 min desde medianoche = 00:45
        assert m._bed_min_to_hhmm(45) == "00:45"


# ── cycle_summary (Fase 7: salud femenina, opt-in) ───────────────────────────

class TestCycleSummary:
    def test_disabled_when_toggle_off(self, monkeypatch):
        """Default (cycle_tracking=False) -> {enabled:false}, cero fuga de
        datos de ciclo hacia el MCP/Alfred (criterio #1 del roadmap)."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {"cycle_tracking": False})
        result = m.cycle_summary({"days": []})
        assert result == {"enabled": False}

    def test_disabled_with_none_dataset(self, monkeypatch):
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {"cycle_tracking": False})
        assert m.cycle_summary(None) == {"enabled": False}

    def test_enabled_returns_state_with_data(self, monkeypatch):
        from app import profile as _pm, cycle as _cyc
        monkeypatch.setattr(_pm, "effective_profile_dict", lambda: {"cycle_tracking": True})
        monkeypatch.setattr(_cyc, "load_cycle_log", lambda: {
            "periods": [{"start": "2026-06-01", "source": "manual"}, {"start": "2026-06-29", "source": "manual"}]
        })
        ds = {"days": [{"date": "2026-07-05", "recovery": 60}]}
        result = m.cycle_summary(ds)
        assert result["enabled"] is True
        assert result["cycle_day"] is not None
        assert result["disclaimer"] == "cycle_disclaimer"

    def test_never_crashes_on_internal_error(self, monkeypatch):
        """Un fallo inesperado en cycle.py -> {enabled:false}, nunca propaga."""
        from app import profile as _pm
        monkeypatch.setattr(_pm, "effective_profile_dict",
                             lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        result = m.cycle_summary({"days": []})
        assert result == {"enabled": False}
