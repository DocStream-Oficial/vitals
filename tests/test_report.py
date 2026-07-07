"""
test_report.py — Tests de app/report.py (Fase 8B, paso B6).

Cubre:
- agregación (build_report_data) con datasets sintéticos: semana partida por
  falta de datos, huecos, deltas vs período anterior.
- firma/caché: verifica que NO regenera si la firma no cambió (monkeypatch
  _call_cli para contar invocaciones).
- fallback determinista sin CLI (mock _call_cli -> None).
- endpoint /api/report (nunca 500, con/sin dataset, con/sin cache).
"""
from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app import report


def date_seq(start: str, n: int) -> list[str]:
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


# ── build_report_data — agregación pura ──────────────────────────────────────

def test_build_report_data_weekly_basic():
    """Semana ISO lun-dom completa anterior a ref_date, con datos completos."""
    dates = date_seq("2026-06-08", 21)  # 2026-06-08 (lun) .. 2026-06-28 (dom)
    days = [{"date": d, "recovery": 60 + (i % 10), "hrv": 50, "strain": 10, "asleep": 420}
            for i, d in enumerate(dates)]
    dataset = {"days": days}
    journal = {"entries": {}, "custom": []}

    rd = report.build_report_data(dataset, journal, "weekly", datetime.date(2026, 6, 29))
    assert rd is not None
    assert rd["period"] == "weekly"
    assert rd["start"] == "2026-06-22"
    assert rd["end"] == "2026-06-28"
    assert rd["n_days"] == 7
    assert rd["means"]["recovery"] is not None
    assert rd["means"]["hrv"] == 50.0


def test_build_report_data_monthly_basic():
    dates = date_seq("2026-05-01", 61)  # cubre mayo y junio completos
    days = [{"date": d, "recovery": 65, "hrv": 55, "strain": 8, "asleep": 440} for d in dates]
    dataset = {"days": days}
    journal = {"entries": {}, "custom": []}

    rd = report.build_report_data(dataset, journal, "monthly", datetime.date(2026, 7, 4))
    assert rd is not None
    assert rd["period"] == "monthly"
    assert rd["start"] == "2026-06-01"
    assert rd["end"] == "2026-06-30"
    assert rd["period_key"] == "2026-06"


def test_build_report_data_returns_none_when_no_days_in_period():
    """Sin ningún día del dataset dentro del período completo más reciente ->
    None (documentado como desviación del roadmap textual — ver informe final:
    'estructura con métricas None' se implementó como None sentinel)."""
    dataset = {"days": []}
    rd = report.build_report_data(dataset, {}, "weekly", datetime.date(2026, 6, 29))
    assert rd is None


def test_build_report_data_handles_gap_in_period():
    """Semana con huecos (solo 3 de 7 días con dato) -> no crashea, medias
    calculadas solo sobre los días presentes."""
    days = [
        {"date": "2026-06-22", "recovery": 70},
        {"date": "2026-06-24", "recovery": 60},
        {"date": "2026-06-26", "recovery": 50},
    ]
    dataset = {"days": days}
    rd = report.build_report_data(dataset, {}, "weekly", datetime.date(2026, 6, 29))
    assert rd is not None
    assert rd["n_days"] == 3
    assert rd["means"]["recovery"] == 60.0


def test_build_report_data_deltas_vs_previous_period():
    """Deltas = media(período actual) - media(período anterior equivalente)."""
    prev_week = date_seq("2026-06-15", 7)   # semana anterior
    curr_week = date_seq("2026-06-22", 7)   # semana objetivo
    days = [{"date": d, "recovery": 50} for d in prev_week] + \
           [{"date": d, "recovery": 70} for d in curr_week]
    dataset = {"days": days}
    rd = report.build_report_data(dataset, {}, "weekly", datetime.date(2026, 6, 29))
    assert rd["deltas"]["recovery"] == 20.0


def test_build_report_data_delta_none_when_no_previous_period_data():
    curr_week = date_seq("2026-06-22", 7)
    days = [{"date": d, "recovery": 70} for d in curr_week]
    dataset = {"days": days}
    rd = report.build_report_data(dataset, {}, "weekly", datetime.date(2026, 6, 29))
    assert rd["deltas"]["recovery"] is None


def test_build_report_data_best_worst_day():
    days = [
        {"date": "2026-06-22", "recovery": 40},
        {"date": "2026-06-23", "recovery": 90},
        {"date": "2026-06-24", "recovery": 60},
    ]
    dataset = {"days": days}
    rd = report.build_report_data(dataset, {}, "weekly", datetime.date(2026, 6, 29))
    assert rd["best_day"] == "2026-06-23"
    assert rd["worst_day"] == "2026-06-22"


def test_build_report_data_adherence_pct():
    dates = date_seq("2026-06-22", 7)
    days = [{"date": d, "recovery": 60} for d in dates]
    dataset = {"days": days}
    journal = {"entries": {dates[0]: {"alcohol": True}, dates[1]: {"alcohol": False}}, "custom": []}
    rd = report.build_report_data(dataset, journal, "weekly", datetime.date(2026, 6, 29))
    assert rd["adherence"]["days_logged"] == 2
    assert rd["adherence"]["days_total"] == 7


def test_build_report_data_never_crashes_on_garbage():
    assert report.build_report_data(None, None, "weekly", datetime.date(2026, 6, 29)) is None
    assert report.build_report_data({}, {}, "bogus_period", datetime.date(2026, 6, 29)) is None
    garbage_days = {"days": [{"date": "not-a-date"}, "garbage", {}, None]}
    result = report.build_report_data(garbage_days, {}, "weekly", datetime.date(2026, 6, 29))
    assert result is None  # ningún día válido cae en el período


# ── firma / caché: NO regenera si la firma no cambió ─────────────────────────

def _dataset_for_cache_tests():
    dates = date_seq("2026-06-01", 35)
    days = [{"date": d, "recovery": 65, "hrv": 50, "strain": 9, "asleep": 430} for d in dates]
    return {"days": days}


def test_maybe_regenerate_reports_calls_cli_when_signature_changes(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch.object(report, "_call_cli", return_value="Narrativa. Acción 1. Acción 2.") as mock_cli:
        dataset = _dataset_for_cache_tests()
        report.maybe_regenerate_reports(dataset, {"entries": {}, "custom": []}, "es")
        # weekly + monthly -> 2 llamadas (ambos períodos tienen datos)
        assert mock_cli.call_count >= 1


def test_maybe_regenerate_reports_skips_cli_when_signature_unchanged(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch.object(report, "_call_cli", return_value="Narrativa. Acción 1. Acción 2.") as mock_cli:
        dataset = _dataset_for_cache_tests()
        journal = {"entries": {}, "custom": []}
        report.maybe_regenerate_reports(dataset, journal, "es")
        first_call_count = mock_cli.call_count
        assert first_call_count > 0

        # Mismo dataset/journal/locale -> misma firma -> CERO llamadas nuevas.
        report.maybe_regenerate_reports(dataset, journal, "es")
        assert mock_cli.call_count == first_call_count


def test_maybe_regenerate_reports_calls_cli_again_when_locale_changes(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch.object(report, "_call_cli", return_value="Narrative. Action 1. Action 2.") as mock_cli:
        dataset = _dataset_for_cache_tests()
        journal = {"entries": {}, "custom": []}
        report.maybe_regenerate_reports(dataset, journal, "es")
        n_es = mock_cli.call_count
        report.maybe_regenerate_reports(dataset, journal, "en")
        assert mock_cli.call_count > n_es


def test_maybe_regenerate_reports_preserves_cache_when_cli_fails(tmp_path):
    """Si el CLI falla en la segunda corrida, el cache viejo se conserva
    intacto (no se pisa con nada)."""
    cache_path = tmp_path / "reports.json"
    with patch.object(report, "_CACHE_PATH", cache_path), \
         patch.object(report, "_call_cli", return_value="Narrativa original.") as mock_cli:
        dataset = _dataset_for_cache_tests()
        report.maybe_regenerate_reports(dataset, {"entries": {}, "custom": []}, "es")
        cache_after_success = json.loads(cache_path.read_text())
        assert cache_after_success["weekly"]["narrative"] == "Narrativa original."

    # Ahora forzamos una firma distinta (nuevo dataset) con el CLI fallando.
    dates2 = date_seq("2026-07-01", 35)
    days2 = [{"date": d, "recovery": 40, "hrv": 30, "strain": 15, "asleep": 300} for d in dates2]
    dataset2 = {"days": days2}
    with patch.object(report, "_CACHE_PATH", cache_path), \
         patch.object(report, "_call_cli", return_value=None):
        report.maybe_regenerate_reports(dataset2, {"entries": {}, "custom": []}, "es")

    cache_after_failure = json.loads(cache_path.read_text())
    assert cache_after_failure["weekly"]["narrative"] == "Narrativa original."


def test_maybe_regenerate_reports_never_raises_on_garbage():
    """Best-effort total: nunca propaga excepción, incluso con dataset/journal
    corruptos."""
    report.maybe_regenerate_reports(None, None, "es")
    report.maybe_regenerate_reports("garbage", "garbage", "es")  # type: ignore[arg-type]


# ── fallback determinista sin CLI ────────────────────────────────────────────

def test_get_report_fallback_when_cli_never_ran(tmp_path):
    """Sin cache -> narrativa = nota i18n, data=None, has_narrative=False."""
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"):
        result = report.get_report("weekly", locale="es")
        assert result["has_narrative"] is False
        assert result["data"] is None
        assert result["narrative"]  # nota i18n no vacía


def test_get_report_fallback_when_cli_returns_none(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch.object(report, "_call_cli", return_value=None):
        dataset = _dataset_for_cache_tests()
        report.maybe_regenerate_reports(dataset, {"entries": {}, "custom": []}, "es")
        result = report.get_report("weekly", locale="es")
        assert result["has_narrative"] is False
        # Los números SÍ deben estar disponibles (calculados en maybe_regenerate,
        # cacheados sin narrativa aunque el CLI haya fallado): fallback
        # determinista del roadmap B6 — "solo los números formateados".
        assert result["narrative"]  # nota i18n no vacía
        assert result["data"] is not None
        assert result["data"]["means"]["recovery"] == 65.0
        assert result["start"] is not None and result["end"] is not None


def test_maybe_regenerate_data_only_cache_never_clobbers_old_narrative(tmp_path):
    """El fallback data-only NO pisa una entrada previa con narrativa: si la
    firma cambió y el CLI falla, la narrativa vieja se conserva intacta."""
    cache_path = tmp_path / "reports.json"
    with patch.object(report, "_CACHE_PATH", cache_path), \
         patch.object(report, "_call_cli", return_value="Narrativa vieja."):
        report.maybe_regenerate_reports(_dataset_for_cache_tests(), {"entries": {}, "custom": []}, "es")
    dates2 = date_seq("2026-07-01", 35)
    days2 = [{"date": d, "recovery": 40} for d in dates2]
    with patch.object(report, "_CACHE_PATH", cache_path), \
         patch.object(report, "_call_cli", return_value=None):
        report.maybe_regenerate_reports({"days": days2}, {"entries": {}, "custom": []}, "es")
    cache = json.loads(cache_path.read_text())
    assert cache["weekly"]["narrative"] == "Narrativa vieja."


def test_get_report_returns_cached_narrative(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch.object(report, "_call_cli", return_value="Tu semana fue sólida. Acción 1. Acción 2."):
        dataset = _dataset_for_cache_tests()
        report.maybe_regenerate_reports(dataset, {"entries": {}, "custom": []}, "es")
        result = report.get_report("weekly", locale="es")
        assert result["has_narrative"] is True
        assert result["narrative"] == "Tu semana fue sólida. Acción 1. Acción 2."


def test_get_report_never_raises_with_corrupt_cache(tmp_path):
    cache_path = tmp_path / "reports.json"
    cache_path.write_text("NOT JSON{{{", encoding="utf-8")
    with patch.object(report, "_CACHE_PATH", cache_path):
        result = report.get_report("weekly", locale="es")
        assert result["has_narrative"] is False


def test_get_report_invalid_period_defaults_to_weekly(tmp_path):
    with patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"):
        result = report.get_report("bogus", locale="es")
        assert result["period"] == "weekly"


# ── endpoint /api/report ──────────────────────────────────────────────────────

def _get_report_client(tmp_path: Path):
    from app import config, profile as _pm
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch.object(report, "_CACHE_PATH", tmp_path / "reports.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def report_client(tmp_path):
    yield from _get_report_client(tmp_path)


def test_api_report_no_cache_no_dataset_never_500(report_client):
    resp = report_client.get("/api/report", params={"period": "weekly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_narrative"] is False


def test_api_report_invalid_period_422(report_client):
    resp = report_client.get("/api/report", params={"period": "yearly"})
    assert resp.status_code == 422


def test_api_report_monthly_no_cache_never_500(report_client):
    resp = report_client.get("/api/report", params={"period": "monthly"})
    assert resp.status_code == 200


def test_api_report_with_cache_returns_narrative(report_client, tmp_path):
    cache = {
        "weekly": {
            "signature": "2026-06-22_2026-06-28",
            "locale": "es",
            "narrative": "Semana sólida. Sigue así. Acción 1. Acción 2.",
            "data": {"start": "2026-06-22", "end": "2026-06-28", "period_key": "2026-06-22_2026-06-28"},
            "generated_at": "2026-06-29T08:00:00",
        }
    }
    (tmp_path / "reports.json").write_text(json.dumps(cache), encoding="utf-8")
    resp = report_client.get("/api/report", params={"period": "weekly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_narrative"] is True
    assert body["narrative"] == "Semana sólida. Sigue así. Acción 1. Acción 2."


# ── sleep_archetype aditivo en GET /api/report?period=monthly (Roadmap P2, F8, paso 5) ──

def _write_dataset_with_month(tmp_path, n_days=120, ref_end=None):
    """Escribe health_compact.json con `n_days` noches consistentes que
    cumplen la necesidad de sueño — suficientes para que classify_month()
    dispare un arquetipo real (no None) en el último mes completo. El
    endpoint /api/report llama a classify_month() SIN ref_date explícito
    (usa datetime.date.today() real) — por eso el ancla por default es
    'hoy' menos unos días, no una fecha fija, para que el último mes
    calendario completo relativo a la fecha REAL de ejecución del test
    tenga suficientes noches."""
    import datetime as _dt
    ref_end = ref_end or (_dt.date.today() - _dt.timedelta(days=3))
    start = ref_end - _dt.timedelta(days=n_days - 1)
    days = []
    d = start
    while d <= ref_end:
        days.append({
            "date": d.isoformat(), "asleep": 480, "eff": 92.0, "bed_min": -30.0,
            "waketime": "07:00", "strain": 8, "recovery": 65, "hrv": 55, "sleep_perf": 85,
        })
        d += _dt.timedelta(days=1)
    (tmp_path / "health_compact.json").write_text(
        json.dumps({"summary": {}, "days": days, "exercises": []}), encoding="utf-8",
    )


def _seed_monthly_cache_with_data(tmp_path, data_extra=None):
    """Siembra reports.json con una entrada 'monthly' que YA tiene `data`
    (como si un sync previo hubiera corrido maybe_regenerate_reports) — el
    escenario real donde sleep_archetype se adjunta ADITIVAMENTE sobre datos
    ya cacheados, sin inventar un shape nuevo cuando no hay cache todavía."""
    cache = {
        "monthly": {
            "signature": "2026-05",
            "locale": "es",
            "narrative": "Mes sólido. Sigue así. Acción 1. Acción 2.",
            "data": dict(data_extra or {}, start="2026-05-01", end="2026-05-31", period_key="2026-05"),
            "generated_at": "2026-06-01T08:00:00",
        }
    }
    (tmp_path / "reports.json").write_text(json.dumps(cache), encoding="utf-8")


def test_api_report_monthly_includes_sleep_archetype_when_enough_data(report_client, tmp_path):
    _seed_monthly_cache_with_data(tmp_path)
    _write_dataset_with_month(tmp_path)
    resp = report_client.get("/api/report", params={"period": "monthly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] is not None
    assert "sleep_archetype" in body["data"]
    archetype = body["data"]["sleep_archetype"]
    assert archetype is not None
    assert archetype["archetype"] in (
        "swiss_clock", "warm_night_owl", "early_riser",
        "wound_too_tight", "erratic_rhythm", "extended_stay",
    )


def test_api_report_monthly_sleep_archetype_null_when_not_enough_data(report_client, tmp_path):
    """Dataset presente pero <14 noches en el mes -> sleep_archetype None,
    el resto del shape sigue intacto (criterio 12: aditivo, nunca rompe el resto)."""
    _seed_monthly_cache_with_data(tmp_path)
    _write_dataset_with_month(tmp_path, n_days=5)
    resp = report_client.get("/api/report", params={"period": "monthly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["sleep_archetype"] is None


def test_api_report_weekly_never_includes_sleep_archetype(report_client, tmp_path):
    """sleep_archetype es SOLO para monthly — weekly nunca lo agrega (criterio
    12: 'el resto del shape de report.py NO cambia')."""
    cache = {
        "weekly": {
            "signature": "2026-05-25_2026-05-31",
            "locale": "es",
            "narrative": "Semana sólida.",
            "data": {"start": "2026-05-25", "end": "2026-05-31", "period_key": "2026-05-25_2026-05-31"},
            "generated_at": "2026-06-01T08:00:00",
        }
    }
    (tmp_path / "reports.json").write_text(json.dumps(cache), encoding="utf-8")
    _write_dataset_with_month(tmp_path)
    resp = report_client.get("/api/report", params={"period": "weekly"})
    assert resp.status_code == 200
    body = resp.json()
    if body.get("data"):
        assert "sleep_archetype" not in body["data"]


def test_api_report_monthly_no_dataset_never_500_with_archetype_field(report_client):
    """Sin dataset ni cache: data es None -> no se intenta adjuntar
    sleep_archetype (no hay dict donde meterlo) — nunca 500."""
    resp = report_client.get("/api/report", params={"period": "monthly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] is None
