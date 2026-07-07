"""
tests/test_google_health.py — Fix: skin_temp de Google Health nunca fue convertido a desviación.

Ver: _dev/ROADMAP-vitals-fix-google-skin-temp.md

Google entrega 'daily-sleep-temperature-derivations' en Celsius ABSOLUTO (~32-35°C), pese al
nombre "derivations". Todo el resto del sistema (scoring.py::compute_wellbeing, whoop.py,
oura.py) asume que skin_temp llega como una DESVIACIÓN centrada en 0. Este archivo prueba que
GoogleHealthSource.fetch() ahora aplica la MISMA conversión que whoop.py (líneas ~379-385):
resta la media de la ventana, None-safe (0/1 valores no lanza).

No existía este archivo antes del fix (se crea aquí, conscientemente, con valores POST-conversión
— no hay asserts previos sobre el valor crudo de `skin` que actualizar).
"""
import time
from unittest.mock import patch

import pytest

from app.sources.google_health import GoogleHealthSource
from app.sources.base import TokenExpired, NoToken


# ───────────────────────────────────────────────────────── conversión aislada (mismo patrón whoop.py)

def _convert(skin: dict) -> dict:
    """Replica exacta de la conversión aplicada en google_health.py::fetch() (y whoop.py)."""
    if skin:
        _mean = sum(skin.values()) / len(skin)
        return {d: round(v - _mean, 2) for d, v in skin.items()}
    return skin


def test_skin_conversion_centers_series_on_zero():
    """5-10 días de skin ABSOLUTO (~32-35°C, variación realista) -> media de la serie
    convertida ~0."""
    raw = {
        "2026-06-20": 32.8,
        "2026-06-21": 33.1,
        "2026-06-22": 34.5,
        "2026-06-23": 32.9,
        "2026-06-24": 33.6,
        "2026-06-25": 35.0,
        "2026-06-26": 33.2,
        "2026-06-27": 32.5,
    }
    converted = _convert(raw)

    assert set(converted.keys()) == set(raw.keys())
    mean_converted = sum(converted.values()) / len(converted)
    assert mean_converted == pytest.approx(0.0, abs=1e-6)
    # Ya no quedan valores en rango absoluto (~30+)
    assert all(abs(v) < 5 for v in converted.values())


def test_skin_conversion_single_value_is_zero():
    """Con 1 solo valor, la desviación es 0.0 (no división por cero, mismo patrón WHOOP)."""
    converted = _convert({"2026-06-20": 33.4})
    assert converted == {"2026-06-20": 0.0}


def test_skin_conversion_empty_dict_no_raise():
    """Con 0 valores (dict vacío), no lanza, devuelve {}."""
    converted = _convert({})
    assert converted == {}


# ───────────────────────────────────────────────────────── fetch() end-to-end

def _save_live_token(tmp_path):
    """Guarda un token.json 'vivo' (no vencido) para que _auth.access_token() no lance."""
    import json
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({
        "access_token": "tok",
        "refresh_token": "rt",
        "expires_in": 3600,
        "obtained_at": int(time.time()),
    }))


def _skin_datapoint(platform, year, month, day, celsius):
    """dataPoint crudo de Google para 'daily-sleep-temperature-derivations':
    metric_obj() toma el primer sub-dict que no sea name/dataSource -> 'metrics';
    parse_daily(value_hint='elsius') busca una clave que contenga 'elsius' ahí dentro."""
    return {
        "dataSource": {"platform": platform},
        "metrics": {
            "date": {"year": year, "month": month, "day": day},
            "averageCelsius": celsius,
        },
    }


def _mock_list_all(datatype, token, save_name=None, max_pages=12):
    """Despacha list_all según el datatype pedido por fetch()."""
    if datatype == "daily-sleep-temperature-derivations":
        # dataPoints crudos de Google: Celsius ABSOLUTO (~32-35°C)
        return [
            _skin_datapoint("FITBIT", 2026, 6, 20, 33.0),
            _skin_datapoint("FITBIT", 2026, 6, 21, 34.0),
            _skin_datapoint("FITBIT", 2026, 6, 22, 32.0),
        ]
    return []


def test_fetch_converts_skin_to_deviation(tmp_path, monkeypatch):
    """fetch() real (con health_api/auth mockeados) debe devolver `skin` como
    desviación centrada en 0, no como Celsius absoluto (~32-35)."""
    from app.sources import _tokenstore

    src = GoogleHealthSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.google_health._auth.access_token", return_value="tok"), \
             patch("app.sources.google_health.health_api.list_all", side_effect=_mock_list_all), \
             patch("app.sources.google_health.health_api.daily_rollup", return_value={}), \
             patch("app.sources.google_health.parse_exercises", return_value=[]):
            data = src.fetch(45)

    skin = data["skin"]
    assert set(skin.keys()) == {"2026-06-20", "2026-06-21", "2026-06-22"}
    # Raw: 33, 34, 32 -> mean 33 -> deviations: 0.0, 1.0, -1.0
    assert skin["2026-06-20"] == 0.0
    assert skin["2026-06-21"] == 1.0
    assert skin["2026-06-22"] == -1.0
    # Ningún valor queda en rango absoluto
    assert all(abs(v) < 5 for v in skin.values())


def test_fetch_skin_empty_no_raise(tmp_path):
    """Sin dato de temperatura de piel (lista vacía) -> skin={} , fetch() no lanza."""
    from app.sources import _tokenstore

    src = GoogleHealthSource()

    def _empty_list_all(datatype, token, save_name=None, max_pages=12):
        return []

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.google_health._auth.access_token", return_value="tok"), \
             patch("app.sources.google_health.health_api.list_all", side_effect=_empty_list_all), \
             patch("app.sources.google_health.health_api.daily_rollup", return_value={}), \
             patch("app.sources.google_health.parse_exercises", return_value=[]):
            data = src.fetch(45)

    assert data["skin"] == {}


def test_fetch_skin_single_day_is_zero(tmp_path):
    """Un solo día con dato -> desviación 0.0 (no división por cero) también vía fetch() real."""
    from app.sources import _tokenstore

    src = GoogleHealthSource()

    def _one_day(datatype, token, save_name=None, max_pages=12):
        if datatype == "daily-sleep-temperature-derivations":
            return [_skin_datapoint("FITBIT", 2026, 6, 20, 33.4)]
        return []

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.google_health._auth.access_token", return_value="tok"), \
             patch("app.sources.google_health.health_api.list_all", side_effect=_one_day), \
             patch("app.sources.google_health.health_api.daily_rollup", return_value={}), \
             patch("app.sources.google_health.parse_exercises", return_value=[]):
            data = src.fetch(45)

    assert data["skin"] == {"2026-06-20": 0.0}


# ── F2 roadmap P0: hipnograma — segments desde sleep.stages[] (type=STAGES) ──
#
# Evidencia del payload real (data/users/default/vitals_raw/sleep.json,
# guardado por save_name="sleep"): los records Fitbit type="STAGES" traen un
# timeline granular en sleep.stages[] con startTime/endTime/type
# (AWAKE/LIGHT/DEEP/REM) — por eso el paso 12 del roadmap SÍ se implementa.

def _stage(start, end, typ):
    return {
        "startTime": start, "startUtcOffset": "-21600s",
        "endTime": end, "endUtcOffset": "-21600s",
        "type": typ,
    }


def _sleep_dp(stages):
    return {
        "dataSource": {"platform": "FITBIT"},
        "sleep": {
            "interval": {
                "startTime": "2026-07-05T08:32:00Z", "startUtcOffset": "-21600s",
                "endTime": "2026-07-05T10:28:00Z", "endUtcOffset": "-21600s",
            },
            "type": "STAGES",
            "stages": stages,
            "summary": {
                "minutesAsleep": 110, "minutesInSleepPeriod": 116, "minutesAwake": 6,
                "stagesSummary": [
                    {"type": "DEEP", "minutes": 14}, {"type": "REM", "minutes": 16},
                    {"type": "LIGHT", "minutes": 80},
                ],
            },
        },
    }


def test_parse_sleep_derives_segments_from_stages_timeline():
    """Timeline granular real (shape del payload verificado) -> segments en
    minutos desde bedtime, con las 4 etapas mapeadas y sin traslapes."""
    from app.parsers import parse_sleep

    stages = [
        _stage("2026-07-05T08:32:00Z", "2026-07-05T08:36:00Z", "AWAKE"),
        _stage("2026-07-05T08:36:00Z", "2026-07-05T08:44:00Z", "LIGHT"),
        _stage("2026-07-05T08:44:00Z", "2026-07-05T08:57:00Z", "DEEP"),
        _stage("2026-07-05T08:57:00Z", "2026-07-05T09:17:00Z", "REM"),
        _stage("2026-07-05T09:17:00Z", "2026-07-05T10:28:00Z", "LIGHT"),
    ]
    out = parse_sleep([_sleep_dp(stages)])
    rec = list(out.values())[0]
    assert rec["segments"] == [
        {"s": 0, "e": 4, "st": "awake"},
        {"s": 4, "e": 12, "st": "light"},
        {"s": 12, "e": 25, "st": "deep"},
        {"s": 25, "e": 45, "st": "rem"},
        {"s": 45, "e": 116, "st": "light"},
    ]


def test_parse_sleep_collapses_contiguous_same_stage():
    """Fitbit a veces parte una etapa en 2 stages contiguos -> se colapsan."""
    from app.parsers import parse_sleep

    stages = [
        _stage("2026-07-05T08:32:00Z", "2026-07-05T09:00:00Z", "LIGHT"),
        _stage("2026-07-05T09:00:00Z", "2026-07-05T09:30:00Z", "LIGHT"),
        _stage("2026-07-05T09:30:00Z", "2026-07-05T10:28:00Z", "DEEP"),
    ]
    out = parse_sleep([_sleep_dp(stages)])
    rec = list(out.values())[0]
    assert rec["segments"] == [
        {"s": 0, "e": 58, "st": "light"},
        {"s": 58, "e": 116, "st": "deep"},
    ]


def test_parse_sleep_unknown_stage_type_no_segments():
    """Un tipo fuera del mapa (RESTLESS del sueño CLASSIC de Fitbit) -> el
    record queda SIN segments (no se inventa nada — decisión del paso 12),
    pero el resto del rec (asleep, stagesSummary, etc.) se parsea igual."""
    from app.parsers import parse_sleep

    stages = [
        _stage("2026-07-05T08:32:00Z", "2026-07-05T09:32:00Z", "ASLEEP"),
        _stage("2026-07-05T09:32:00Z", "2026-07-05T09:40:00Z", "RESTLESS"),
    ]
    out = parse_sleep([_sleep_dp(stages)])
    rec = list(out.values())[0]
    assert "segments" not in rec
    assert rec["asleep"] == 110  # la noche entra igual


def test_parse_sleep_no_stages_timeline_no_segments():
    """Record solo con stagesSummary (sin stages[]) -> sin segments, rec
    byte-igual al comportamiento anterior a F2."""
    from app.parsers import parse_sleep

    dp = _sleep_dp([])
    del dp["sleep"]["stages"]
    out = parse_sleep([dp])
    rec = list(out.values())[0]
    assert "segments" not in rec
