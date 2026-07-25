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


def _default_vo2_fn(i, n):
    # Roadmap vo2-sin-inventar Paso 2: healthspan honesto solo agrega puntos
    # con vo2max_source == "measured" -> los fixtures sintéticos necesitan
    # una lectura "vo2" en CADA day (constante por default) para que las
    # ventanas trailing de compute_healthspan sigan produciendo serie, igual
    # que un usuario que sí corre outdoor con su reloj periódicamente.
    return 45.0


def _make_days(n_days, start="2025-01-01", rhr_fn=None, hrv_fn=None, sleep_min=420, vo2_fn=None):
    """Genera n_days días con rhr/hrv/asleep/vo2 suficientes para que
    compute_body_age tenga datos (confidence != low irrelevante aquí, solo
    necesitamos valores no-None). `vo2_fn(i, n_days)` inyecta una lectura de
    VO2 MEDIDO por día (default: constante, ver _default_vo2_fn) — sin ella,
    vo2max_source sería SIEMPRE "estimated" y el gate honesto (Paso 2) dejaría
    la serie completa vacía."""
    vo2_fn = vo2_fn or _default_vo2_fn
    dates = _date_seq(start, n_days)
    days = []
    for i, d in enumerate(dates):
        rhr = rhr_fn(i, n_days) if rhr_fn else 55.0
        hrv = hrv_fn(i, n_days) if hrv_fn else 50.0
        day = {"date": d, "rhr": rhr, "hrv": hrv, "asleep": sleep_min}
        vo2 = vo2_fn(i, n_days)
        if vo2 is not None:
            day["vo2"] = vo2
        days.append(day)
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
    cronológica en vez de reflejar la mejora real (ver informe D2).

    Roadmap vo2-sin-inventar Paso 2: con el gate honesto, solo cuentan
    ventanas con vo2max_source == "measured" -> el vo2 ahora se inyecta
    DIRECTO como lectura medida (vo2_fn) en vez de dejar que la regresión lo
    infiera del rhr. Los valores de vo2_fn replican el MISMO rango que la
    regresión producía antes con este rhr_fn (41.75->46.4, ver comentario
    original) para que el test siga verificando exactamente la misma señal
    direccional de antes, solo que por la vía medida."""
    n = 330  # ~11 meses, suficientes puntos de ventana mensual
    def rhr_fn(i, n):
        # de 75 (mal) a 45 (bien) progresivamente — fitness_age 24-37, no saturado
        return 75.0 - (i / n) * 30.0
    def hrv_fn(i, n):
        # de 30 (mal) a 65 (bien) progresivamente
        return 30.0 + (i / n) * 35.0
    def vo2_fn(i, n):
        return 41.75 + (i / n) * 4.65
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480, vo2_fn=vo2_fn)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert result["pace"] is not None
    assert result["pace"] < 1.0, f"esperaba pace<1 (mejorando), got {result['pace']}"


def test_worsening_rhr_and_hrv_yields_pace_above_1():
    """Lo inverso: RHR subiendo + HRV bajando -> el gap se agranda con el
    tiempo -> pace > 1. Mismos rangos no saturados que el test de mejora
    (vo2_fn espejo del de arriba, ver su docstring)."""
    n = 330
    def rhr_fn(i, n):
        return 45.0 + (i / n) * 30.0
    def hrv_fn(i, n):
        return 65.0 - (i / n) * 35.0
    def vo2_fn(i, n):
        return 46.4 - (i / n) * 4.65
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480, vo2_fn=vo2_fn)
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
    def vo2_fn(i, n):
        return 41.75 + (i / n) * 4.65
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480, vo2_fn=vo2_fn)
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


# ── Roadmap edad-corporal-credibilidad Paso 3: pace robusto (Theil-Sen) ─────
# Nuevo contrato de `pace`: clampeado a [0.5, 1.5] SIEMPRE (nunca negativo ni
# disparatado), y None si la serie tiene <4 puntos (antes OLS con linreg_slope
# daba un valor incluso con solo 3 — contrato aceptado, ver ROADMAP §Paso 3).

def test_pace_none_with_less_than_4_series_points():
    """Serie de exactamente 3 puntos (n=151 días, window=90/step=30) -> pace
    None bajo el nuevo contrato Theil-Sen (n<4), aunque la serie en sí
    (>=2 puntos) siga siendo válida y se devuelva."""
    days = _make_days(151)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert len(result["series"]) == 3
    assert result["pace"] is None


def test_pace_is_always_clamped_to_0_5_1_5_range():
    """Deterioro EXTREMO y sostenido (RHR subiendo de 90 a 20, HRV bajando de
    20 a 80, sin clamp esto daría un pace disparatado tipo el -5.46/... del
    diagnóstico original) -> pace nunca sale de [0.5, 1.5]. vo2_fn con swing
    grande (30 puntos) para forzar una señal de gap extrema por la vía
    medida."""
    n = 330
    def rhr_fn(i, n):
        return 90.0 - (i / n) * 70.0
    def hrv_fn(i, n):
        return 20.0 + (i / n) * 60.0
    def vo2_fn(i, n):
        return 30.0 + (i / n) * 30.0
    days = _make_days(n, rhr_fn=rhr_fn, hrv_fn=hrv_fn, sleep_min=480, vo2_fn=vo2_fn)
    result = compute_healthspan(days, [], _profile())
    assert result is not None
    assert result["pace"] is not None
    assert 0.5 <= result["pace"] <= 1.5


# ── Roadmap vo2-sin-inventar Paso 2: healthspan honesto ─────────────────────
# compute_healthspan solo agrega puntos con vo2max_source == "measured" — sin
# ninguna lectura de vo2 en absoluto (o todas fuera de la ventana de validez
# de bodyage.py), NINGUNA ventana produce un punto -> serie vacía -> None
# (criterio de aceptación 5).

def test_no_vo2_measurement_anywhere_yields_none():
    """Sin clave "vo2" en NINGÚN day, todas las ventanas trailing calculan
    vo2max_source == "estimated" -> 0 puntos válidos -> compute_healthspan
    devuelve None (nunca inventa una serie con edades no respaldadas)."""
    days = _make_days(200, vo2_fn=lambda i, n: None)
    result = compute_healthspan(days, [], _profile())
    assert result is None


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
