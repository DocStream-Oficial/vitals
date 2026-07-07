"""
test_healthspan.py — Tests de app/healthspan.py (Fase 8D, paso D2).

Cubre (roadmap D2):
- sintético mejorando -> pace < 1
- sintético empeorando -> pace > 1
- datos cortos (<120 días) -> None
- sin perfil utilizable (sin birthdate/waist) -> None
- forma de la serie ([{month, body_age, chrono_age, gap}])
- delta_quarter con signo correcto
- endpoint GET /api/healthspan
"""
from __future__ import annotations

import datetime

import pytest

from app.healthspan import compute_healthspan, MIN_HISTORY_DAYS, WINDOW_DAYS


def _date_seq(start: str, n: int) -> list[str]:
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


def _profile(birthdate="1985-01-01", waist=95, sex="M", sleep_target_min=480):
    return {
        "birthdate": birthdate,
        "waist_cm": waist,
        "sex": sex,
        "sleep_target_min": sleep_target_min,
    }


def _make_days(n_days, start="2025-01-01", rhr_fn=None, hrv_fn=None, sleep_min=420):
    """Genera n_days días con rhr/hrv/asleep suficientes para que
    compute_body_age tenga datos (confidence != low irrelevante aquí, solo
    necesitamos valores no-None)."""
    dates = _date_seq(start, n_days)
    days = []
    for i, d in enumerate(dates):
        rhr = rhr_fn(i, n_days) if rhr_fn else 55.0
        hrv = hrv_fn(i, n_days) if hrv_fn else 50.0
        days.append({"date": d, "rhr": rhr, "hrv": hrv, "asleep": sleep_min})
    return days


# ── gates ────────────────────────────────────────────────────────────────────

def test_short_history_returns_none():
    days = _make_days(60)  # < MIN_HISTORY_DAYS (120)
    result = compute_healthspan(days, [], _profile())
    assert result is None


def test_no_birthdate_returns_none():
    days = _make_days(200)
    result = compute_healthspan(days, [], _profile(birthdate=None))
    assert result is None


def test_no_waist_returns_none():
    days = _make_days(200)
    result = compute_healthspan(days, [], _profile(waist=None))
    assert result is None


def test_empty_days_returns_none():
    result = compute_healthspan([], [], _profile())
    assert result is None


def test_none_profile_returns_none():
    days = _make_days(200)
    result = compute_healthspan(days, [], None)
    assert result is None


# ── forma de la serie ─────────────────────────────────────────────────────────

def test_series_shape_and_history_long_enough():
    days = _make_days(200)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert "series" in result and "pace" in result and "delta_quarter" in result
    assert len(result["series"]) >= 2
    for pt in result["series"]:
        assert set(("month", "date", "body_age", "chrono_age", "gap")) <= set(pt.keys())
        assert isinstance(pt["body_age"], (int, float))
        assert isinstance(pt["chrono_age"], (int, float))
        assert pt["gap"] == round(pt["body_age"] - pt["chrono_age"], 1)


# ── pace of aging: mejorando vs empeorando ───────────────────────────────────

def test_improving_rhr_and_hrv_yields_pace_below_1():
    """RHR bajando + HRV subiendo progresivamente a lo largo de ~10 meses debe
    traducirse en un gap (body_age - chrono_age) que se ACHICA con el tiempo
    -> pace < 1. Rangos elegidos (con waist=95 del _profile default) para que
    fitness_age NUNCA toque el piso/techo de compute_body_age (18/90) — de lo
    contrario el clamp aplana la señal y el gap se mueve 1:1 con la edad
    cronológica en vez de reflejar la mejora real (ver informe D2)."""
    n = 330  # ~11 meses, suficientes puntos de ventana mensual
    def rhr_fn(i, n):
        # de 75 (mal) a 45 (bien) progresivamente — fitness_age 24-37, no saturado
        return 75.0 - (i / n) * 30.0
    def hrv_fn(i, n):
        # de 30 (mal) a 65 (bien) progresivamente
        return 30.0 + (i / n) * 35.0
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert result["pace"] is not None
    assert result["pace"] < 1.0, f"esperaba pace<1 (mejorando), got {result['pace']}"


def test_worsening_rhr_and_hrv_yields_pace_above_1():
    """Lo inverso: RHR subiendo + HRV bajando -> el gap se agranda con el
    tiempo -> pace > 1. Mismos rangos no saturados que el test de mejora."""
    n = 330
    def rhr_fn(i, n):
        return 45.0 + (i / n) * 30.0
    def hrv_fn(i, n):
        return 65.0 - (i / n) * 35.0
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert result["pace"] is not None
    assert result["pace"] > 1.0, f"esperaba pace>1 (empeorando), got {result['pace']}"


def test_delta_quarter_sign_matches_trend_direction():
    n = 330
    def rhr_fn(i, n):
        return 75.0 - (i / n) * 30.0
    def hrv_fn(i, n):
        return 30.0 + (i / n) * 35.0
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    # Mejorando -> el gap final debería ser menor (más negativo o menos
    # positivo) que unos meses atrás -> delta_quarter <= 0.
    assert result["delta_quarter"] <= 0


def test_flat_metrics_yields_pace_below_1_not_above():
    """Con RHR/HRV fisiológicamente CONSTANTES (rango no saturado), el VO2máx
    resultante queda prácticamente fijo (el término -0.296·edad de la fórmula
    de compute_body_age es minúsculo frente al redondeo a 1 decimal en un
    horizonte de pocos meses — ver informe D2, "desviación documentada").
    Con body_age fijo y chrono_age avanzando, el gap se ACHICA -> pace < 1.
    Esto es correcto y esperado: no envejecer fisiológicamente (mientras el
    calendario sí avanza) equivale a "envejecer más lento que 1 año/año".
    La aserción clave del gate D2 es que NUNCA sea >1 cuando no hay
    deterioro real en las métricas."""
    days = _make_days(200, rhr_fn=lambda i, n: 65.0, hrv_fn=lambda i, n: 40.0, sleep_min=480)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert result["pace"] is not None
    assert result["pace"] <= 1.0


# ── endpoint ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def healthspan_client(tmp_path, monkeypatch):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "DATA_PATH", tmp_path / "health_compact.json")
    return TestClient(main.app)


def test_endpoint_no_dataset_returns_unavailable(healthspan_client):
    r = healthspan_client.get("/api/healthspan")
    assert r.status_code == 200
    assert r.json() == {"available": False}


def test_endpoint_computes_on_demand_when_not_cached(healthspan_client, tmp_path, monkeypatch):
    import json
    days = _make_days(200)
    dataset = {"days": days, "exercises": [], "summary": {}}
    (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")

    # Perfil con birthdate/waist válidos vía profile.effective — monkeypatch
    # directo de effective_profile_dict en el módulo main (import directo).
    import main as _main
    monkeypatch.setattr(_main, "effective_profile_dict", lambda: _profile())

    r = healthspan_client.get("/api/healthspan")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert "series" in body and "pace" in body


def test_endpoint_uses_cached_summary_healthspan(healthspan_client, tmp_path):
    import json
    cached = {"series": [{"month": "2026-01", "date": "2026-01-30", "body_age": 40, "chrono_age": 38, "gap": 2}],
              "pace": 0.9, "delta_quarter": -0.5, "current_gap": 2}
    dataset = {"days": [], "exercises": [], "summary": {"healthspan": cached}}
    (tmp_path / "health_compact.json").write_text(json.dumps(dataset), encoding="utf-8")
    r = healthspan_client.get("/api/healthspan")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert body["pace"] == 0.9
