"""
test_oura.py — Fase 5B: OuraSource a ciegas (sin cuenta real).

Verifica, con fixtures de payloads Oura v2 y HTTP mockeado:
  - normalización Oura → dict interno (unidades, fechas, mapeo) correcta
  - build_auth_url bien formada (scopes, redirect_uri, params OAuth)
  - token storage POR-FUENTE: escribe data/token_oura.json, NUNCA token.json
  - get_source('oura') ya no lanza (5A stub → 5B implementado)
  - tolerancia a None / colecciones vacías
"""
import json
import time
from unittest.mock import patch, MagicMock

import pytest

from app.sources import get_source
from app.sources.oura import OuraSource
from app.sources.base import Source, TokenExpired, NoToken


# ── Fixtures de payloads Oura v2 (muestra representativa, formato real de la API) ──

SLEEP_FIXTURE = [
    {
        "id": "sleep-1",
        "day": "2026-06-28",
        "bedtime_start": "2026-06-27T23:41:24-07:00",
        "bedtime_end": "2026-06-28T06:55:10-07:00",
        "total_sleep_duration": 24600,   # 410 min
        "deep_sleep_duration": 4800,     # 80 min
        "rem_sleep_duration": 5700,      # 95 min
        "light_sleep_duration": 14100,   # 235 min
        "time_in_bed": 26026,            # 433.77 min
        "efficiency": 94,
        "average_hrv": 52.0,
        "lowest_heart_rate": 48,
        "average_breath": 14.2,
    },
    {
        "id": "sleep-2",
        "day": "2026-06-29",
        "bedtime_start": "2026-06-28T23:50:00-07:00",
        "bedtime_end": "2026-06-29T07:10:00-07:00",
        "total_sleep_duration": 25200,
        "deep_sleep_duration": None,     # hueco: campo faltante
        "rem_sleep_duration": 6000,
        "light_sleep_duration": 13000,
        "time_in_bed": 26400,
        "efficiency": 91,
        "average_hrv": None,             # hueco: sensor sin lectura esa noche
        "lowest_heart_rate": 50,
        "average_breath": 13.8,
    },
]

SPO2_FIXTURE = [
    {"day": "2026-06-28", "spo2_percentage": {"average": 96.8}},
    {"day": "2026-06-29", "spo2_percentage": {}},  # sin 'average' → None-safe
]

READINESS_FIXTURE = [
    {"day": "2026-06-28", "temperature_deviation": -0.12},
    {"day": "2026-06-29", "temperature_deviation": 0.31},
]

ACTIVITY_FIXTURE = [
    {
        "day": "2026-06-28",
        "steps": 8423,
        "equivalent_walking_distance": 6210,  # metros
        "total_calories": 2480,
        "active_calories": 540,
    },
    {
        "day": "2026-06-29",
        "steps": 11290,
        "equivalent_walking_distance": 9100,
        "total_calories": 2710,
        "active_calories": 720,
    },
]

VO2_FIXTURE = [
    {"day": "2026-06-15", "vo2_max": 47.3},
]

WORKOUT_FIXTURE = [
    {
        "day": "2026-06-28",
        "activity": "cycling",
        "start_datetime": "2026-06-28T17:00:00-07:00",
        "end_datetime": "2026-06-28T17:45:00-07:00",
        "calories": 410,
        "distance": 18200,  # metros
    },
]


def _resp(status_code, json_body):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body
    return m


def _mock_get_collection(url, headers=None, params=None, timeout=None):
    """Despacha el GET de usercollection según la colección en la URL."""
    mapping = {
        "sleep": SLEEP_FIXTURE,
        "daily_spo2": SPO2_FIXTURE,
        "daily_readiness": READINESS_FIXTURE,
        "daily_activity": ACTIVITY_FIXTURE,
        "vO2_max": VO2_FIXTURE,
        "workout": WORKOUT_FIXTURE,
    }
    for key, data in mapping.items():
        if url.endswith(f"/{key}"):
            return _resp(200, {"data": data})
    return _resp(200, {"data": []})


# ───────────────────────────────────────────────────────────── build_auth_url

def test_build_auth_url_well_formed():
    src = OuraSource()
    with patch("app.sources.oura.settings") as mock_settings:
        mock_settings.OURA_CLIENT_ID = "test_client_id"
        mock_settings.OURA_AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
        mock_settings.REDIRECT_URI = "http://localhost:8700/auth/callback"
        mock_settings.OURA_SCOPES = ["personal", "daily", "heartrate", "workout", "spo2", "session"]
        url = src.build_auth_url("xyz123")

    assert url.startswith("https://cloud.ouraring.com/oauth/authorize")
    assert "client_id=test_client_id" in url
    assert "response_type=code" in url
    assert "state=xyz123" in url
    assert "redirect_uri=" in url
    # scopes presentes (urlencoded con espacios -> %20 o +)
    assert "personal" in url and "daily" in url and "workout" in url
    assert "spo2" in url and "session" in url and "heartrate" in url


def test_build_auth_url_uses_real_settings():
    """Con settings reales (.env vacío en test) la URL sigue bien formada."""
    src = get_source("oura")
    url = src.build_auth_url("s")
    assert url.startswith("https://cloud.ouraring.com/oauth/authorize")
    assert "state=s" in url


# ───────────────────────────────────────────────────────────── token storage por-fuente

def test_exchange_code_writes_token_oura_not_token_json(tmp_path):
    """exchange_code debe escribir token_oura.json y NUNCA tocar token.json."""
    from app.sources import _tokenstore

    fake_data_dir = tmp_path
    google_token_path = fake_data_dir / "token.json"
    oura_token_path = fake_data_dir / "token_oura.json"

    src = OuraSource()
    fake_response = _resp(200, {
        "access_token": "oura_access_abc",
        "refresh_token": "oura_refresh_xyz",
        "token_type": "bearer",
    })

    with patch.object(_tokenstore.settings, "DATA_DIR", fake_data_dir):
        with patch("app.sources.oura.requests.post", return_value=fake_response):
            tok = src.exchange_code("authcode123")

    assert tok["access_token"] == "oura_access_abc"
    assert tok["refresh_token"] == "oura_refresh_xyz"
    assert oura_token_path.exists()
    assert not google_token_path.exists()

    saved = json.loads(oura_token_path.read_text())
    assert saved["access_token"] == "oura_access_abc"
    assert saved["refresh_token"] == "oura_refresh_xyz"
    assert "obtained_at" in saved


def test_tokenstore_load_save_roundtrip(tmp_path):
    from app.sources import _tokenstore

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "abc", "obtained_at": 123})
        loaded = _tokenstore.load_token("oura")

    assert loaded == {"access_token": "abc", "obtained_at": 123}
    assert (tmp_path / "token_oura.json").exists()
    assert not (tmp_path / "token.json").exists()


def test_tokenstore_load_missing_returns_none(tmp_path):
    from app.sources import _tokenstore
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        assert _tokenstore.load_token("oura") is None


def test_tokenstore_does_not_collide_with_google(tmp_path):
    """Escribir token_oura.json no debe afectar un token.json de Google preexistente."""
    from app.sources import _tokenstore

    google_token = tmp_path / "token.json"
    google_token.write_text(json.dumps({"refresh_token": "google_rt", "obtained_at": 1}))

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "oura_at", "obtained_at": 2})

    # Google token intacto
    google_data = json.loads(google_token.read_text())
    assert google_data["refresh_token"] == "google_rt"
    # Oura token separado
    oura_data = json.loads((tmp_path / "token_oura.json").read_text())
    assert oura_data["access_token"] == "oura_at"


# ───────────────────────────────────────────────────────────── auth_state

def test_auth_state_no_token(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        result = src.auth_state()
    assert result["status"] == "no_token"
    assert result["days_left"] == 0


def test_auth_state_active_long_lived_token(tmp_path):
    """Token Oura de larga vida → 'active' independientemente de obtained_at (no hay
    ciclo de expiración corto como Google)."""
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {
            "access_token": "abc",
            "refresh_token": "def",
            "obtained_at": int(time.time()) - 60 * 86400,  # 60 días — Google ya marcaría expired
        })
        result = src.auth_state()
    assert result["status"] == "active"


def test_auth_state_marked_expired(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "abc", "expired": True})
        result = src.auth_state()
    assert result["status"] == "expired"


# ───────────────────────────────────────────────────────────── access_token / errores

def test_access_token_no_token_raises(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        with pytest.raises(NoToken):
            src.access_token()


def test_access_token_expired_raises(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "abc", "expired": True})
        with pytest.raises(TokenExpired):
            src.access_token()


def test_access_token_returns_stored_value(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok_live_123"})
        assert src.access_token() == "tok_live_123"


# ───────────────────────────────────────────────────────────── fetch() / normalización

def test_fetch_returns_all_13_keys(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    expected_keys = {
        "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
        "exercises", "distance_km", "energy_kcal", "active_hours",
    }
    assert set(data.keys()) == expected_keys
    assert data["azm"] == {}
    assert data["active_hours"] == {}


def test_fetch_sleep_normalization_units_and_mapping(tmp_path):
    """Verifica conversión segundos→min, % efficiency intacto, bedtime/waketime HH:MM,
    día = el 'day' que Oura reporta (que ya corresponde a bedtime_end)."""
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    sleep = data["sleep"]
    assert "2026-06-28" in sleep
    night = sleep["2026-06-28"]
    assert night["asleep"] == pytest.approx(24600 / 60, abs=0.01)   # 410.0 min
    assert night["deep"] == pytest.approx(4800 / 60, abs=0.01)      # 80.0 min
    assert night["rem"] == pytest.approx(5700 / 60, abs=0.01)       # 95.0 min
    assert night["light"] == pytest.approx(14100 / 60, abs=0.01)    # 235.0 min
    assert night["inbed"] == pytest.approx(26026 / 60, abs=0.05)  # redondeado a 1 decimal en fetch()
    assert night["eff"] == 94
    assert night["bedtime"] == "23:41"
    assert night["waketime"] == "06:55"

    # hrv/rhr/resp keyed por la misma fecha
    assert data["hrv"]["2026-06-28"] == 52.0
    assert data["rhr"]["2026-06-28"] == 48
    assert data["resp"]["2026-06-28"] == 14.2


def test_fetch_sleep_none_safety_missing_fields(tmp_path):
    """Noche 2026-06-29 tiene deep_sleep_duration=None y average_hrv=None →
    no debe romper, deep=None y la fecha está ausente en hrv (no había lectura)."""
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    night2 = data["sleep"]["2026-06-29"]
    assert night2["deep"] is None
    assert "2026-06-29" not in data["hrv"]  # average_hrv era None → no se agrega la clave
    assert data["rhr"]["2026-06-29"] == 50  # el resto de campos sigue presente


# ── F2 roadmap P0: hipnograma — segments desde sleep_phase_5_min ─────────────

def test_segments_from_phase_string_known_mapping():
    """1=deep, 2=light, 3=rem, 4=awake; runs consecutivos iguales se colapsan
    en un solo segmento; cada char = 5 min desde bedtime_start."""
    src = OuraSource()
    # 15 min deep, 10 min light, 5 min rem, 5 min awake
    phase_str = "111" + "22" + "3" + "4"
    segs = src._segments_from_phase_string(phase_str)
    assert segs == [
        {"s": 0, "e": 15, "st": "deep"},
        {"s": 15, "e": 25, "st": "light"},
        {"s": 25, "e": 30, "st": "rem"},
        {"s": 30, "e": 35, "st": "awake"},
    ]


def test_segments_from_phase_string_missing_field_returns_none():
    src = OuraSource()
    assert src._segments_from_phase_string(None) is None
    assert src._segments_from_phase_string("") is None


def test_segments_from_phase_string_unknown_char_skips_gracefully():
    """Un carácter fuera del mapa conocido no debe romper el parseo — se
    descarta ese intervalo de 5 min y se sigue con el resto."""
    src = OuraSource()
    segs = src._segments_from_phase_string("11" + "9" + "22")
    assert segs is not None
    stages = [s["st"] for s in segs]
    assert "deep" in stages and "light" in stages


def test_parse_sleep_attaches_segments_when_phase_field_present():
    """Fixture con sleep_phase_5_min -> rec['segments'] presente con el
    mapeo esperado."""
    src = OuraSource()
    rec = dict(SLEEP_FIXTURE[0])
    rec["sleep_phase_5_min"] = "11" + "22" + "3"  # 10 deep, 10 light, 5 rem
    sleep, _, _, _ = src._parse_sleep([rec])
    night = sleep["2026-06-28"]
    assert "segments" in night
    assert night["segments"] == [
        {"s": 0, "e": 10, "st": "deep"},
        {"s": 10, "e": 20, "st": "light"},
        {"s": 20, "e": 25, "st": "rem"},
    ]


def test_parse_sleep_without_phase_field_has_no_segments_key():
    """Record SIN sleep_phase_5_min (noches viejas / naps, riesgo #9 del
    roadmap) -> rec sin 'segments' — byte-igual al comportamiento anterior a
    F2, el parser no crashea ni inventa datos."""
    src = OuraSource()
    sleep, _, _, _ = src._parse_sleep([SLEEP_FIXTURE[0]])
    night = sleep["2026-06-28"]
    assert "segments" not in night


def test_fetch_sleep_segments_end_to_end(tmp_path):
    """End-to-end vía fetch(): un record con sleep_phase_5_min produce
    segments en el dataset final; el otro (sin el campo) no."""
    from app.sources import _tokenstore
    fixture_with_phase = [dict(r) for r in SLEEP_FIXTURE]
    fixture_with_phase[0]["sleep_phase_5_min"] = "11" + "22"

    def _mock_get_with_phase(url, headers=None, params=None, timeout=None):
        if url.endswith("/sleep"):
            return _resp(200, {"data": fixture_with_phase})
        return _mock_get_collection(url, headers=headers, params=params, timeout=timeout)

    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_with_phase):
            data = src.fetch(45)

    assert "segments" in data["sleep"]["2026-06-28"]
    assert "segments" not in data["sleep"]["2026-06-29"]


def test_fetch_spo2_average_and_missing(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert data["spo2"]["2026-06-28"] == 96.8
    assert "2026-06-29" not in data["spo2"]  # spo2_percentage={} → sin 'average' → None-safe


def test_fetch_skin_temperature_deviation(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert data["skin"]["2026-06-28"] == -0.12
    assert data["skin"]["2026-06-29"] == 0.31


def test_fetch_activity_steps_distance_km_energy(tmp_path):
    """steps directo; distance metros→km (/1000); energy_kcal = total_calories."""
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert data["steps"]["2026-06-28"] == 8423
    assert data["distance_km"]["2026-06-28"] == pytest.approx(6.21, abs=0.001)
    assert data["energy_kcal"]["2026-06-28"] == 2480

    assert data["steps"]["2026-06-29"] == 11290
    assert data["distance_km"]["2026-06-29"] == pytest.approx(9.1, abs=0.001)


def test_fetch_vo2_max(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert data["vo2"]["2026-06-15"] == 47.3


def test_fetch_workouts_to_exercises(tmp_path):
    """workout → exercises[] con date/name/dur_min/kcal/distance_km."""
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert len(data["exercises"]) == 1
    ex = data["exercises"][0]
    assert ex["date"] == "2026-06-28"
    assert ex["name"] == "cycling"
    assert ex["dur_min"] == pytest.approx(45.0, abs=0.01)
    assert ex["kcal"] == 410
    assert ex["distance_km"] == pytest.approx(18.2, abs=0.01)


def test_fetch_empty_collections_are_none_safe(tmp_path):
    """Colecciones vacías ([]) → dicts vacíos / lista vacía, fetch no rompe."""
    from app.sources import _tokenstore
    src = OuraSource()

    def _all_empty(url, headers=None, params=None, timeout=None):
        return _resp(200, {"data": []})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_all_empty):
            data = src.fetch(45)

    assert data["sleep"] == {}
    assert data["hrv"] == {}
    assert data["spo2"] == {}
    assert data["exercises"] == []
    assert data["steps"] == {}


def test_fetch_collection_http_error_degrades_silently(tmp_path):
    """Un endpoint devolviendo 500 no debe romper fetch — se degrada a [] para esa
    colección (igual que Google Health con _try_rollup_candidates)."""
    from app.sources import _tokenstore
    src = OuraSource()

    def _spo2_fails(url, headers=None, params=None, timeout=None):
        if url.endswith("/daily_spo2"):
            return _resp(500, {})
        return _mock_get_collection(url, headers, params, timeout)

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_spo2_fails):
            data = src.fetch(45)

    assert data["spo2"] == {}
    # el resto de colecciones siguen funcionando
    assert "2026-06-28" in data["sleep"]


def test_fetch_no_token_raises(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        with pytest.raises(NoToken):
            src.fetch(45)


def test_fetch_401_marks_expired_and_raises(tmp_path):
    from app.sources import _tokenstore
    src = OuraSource()

    def _unauthorized(url, headers=None, params=None, timeout=None):
        return _resp(401, {"detail": "invalid token"})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("oura", {"access_token": "tok"})
        with patch("app.sources.oura.requests.get", side_effect=_unauthorized):
            with pytest.raises(TokenExpired):
                src.fetch(45)
        # Se marcó como expirado para el próximo intento
        tok = _tokenstore.load_token("oura")
        assert tok.get("expired") is True


# ───────────────────────────────────────────────────────────── get_source('oura')

def test_get_source_oura_no_longer_stub():
    src = get_source("oura")
    assert isinstance(src, Source)
    assert isinstance(src, OuraSource)
    assert src.name == "oura"
