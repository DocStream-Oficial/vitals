"""
test_whoop.py — Fase 5C: WhoopSource a ciegas (sin cuenta real).

Verifica, con fixtures de payloads WHOOP v2 y HTTP mockeado:
  - normalización WHOOP → dict interno (milli→min, kJ→kcal, día, 4.0-only) correcta
  - build_auth_url bien formada (scopes incl. 'offline', redirect_uri, params OAuth)
  - token storage POR-FUENTE: escribe data/token_whoop.json, NUNCA token.json/token_oura.json
  - 🔴 refresh ROTATORIO: el refresh response trae un refresh_token NUEVO → access_token()
    lo persiste de inmediato, y un 2do refresh usa el nuevo (no el viejo)
  - get_source('whoop') ya no lanza (5A stub → 5C implementado)
  - tolerancia a None / colecciones vacías / hardware sin spo2/skin_temp (3.0)
"""
import json
import time
from unittest.mock import patch, MagicMock

import pytest

from app.sources import get_source
from app.sources.whoop import WhoopSource
from app.sources.base import Source, TokenExpired, NoToken


# ── Fixtures de payloads WHOOP v2 (muestra representativa) ──

RECOVERY_FIXTURE = [
    {
        "cycle_id": 1001,
        "sleep_id": 2001,
        "created_at": "2026-06-28T07:10:00.000Z",
        "score_state": "SCORED",
        "score": {
            "recovery_score": 67,
            "resting_heart_rate": 48,
            "hrv_rmssd_milli": 52.0,   # RMSSD ya viene en ms (no se convierte)
            "spo2_percentage": 97.2,       # hardware 4.0
            "skin_temp_celsius": 33.1,     # hardware 4.0
        },
    },
    {
        "cycle_id": 1002,
        "sleep_id": 2002,
        "created_at": "2026-06-29T07:25:00.000Z",
        "score_state": "SCORED",
        "score": {
            "recovery_score": 54,
            "resting_heart_rate": 50,
            "hrv_rmssd_milli": 41.0,
            "spo2_percentage": None,       # hardware 3.0 / no disponible
            "skin_temp_celsius": None,
        },
    },
]

SLEEP_FIXTURE = [
    {
        "id": 2001,
        "start": "2026-06-27T23:41:24.000Z",
        "end": "2026-06-28T06:55:10.000Z",
        "score_state": "SCORED",
        "score": {
            "respiratory_rate": 14.2,
            "sleep_efficiency_percentage": 94.0,
            "stage_summary": {
                "total_in_bed_time_milli": 26026000,     # 433.77 min
                "total_awake_time_milli": 1426000,        # 23.77 min
                "total_slow_wave_sleep_time_milli": 4800000,   # 80 min (deep)
                "total_rem_sleep_time_milli": 5700000,         # 95 min
                "total_light_sleep_time_milli": 14100000,      # 235 min
            },
        },
    },
    {
        "id": 2002,
        "start": "2026-06-28T23:50:00.000Z",
        "end": "2026-06-29T07:10:00.000Z",
        "score_state": "SCORED",
        "score": {
            "respiratory_rate": 13.8,
            "sleep_efficiency_percentage": 91.0,
            "stage_summary": {
                "total_in_bed_time_milli": 26400000,
                "total_awake_time_milli": 1200000,
                "total_slow_wave_sleep_time_milli": None,  # hueco: campo faltante
                "total_rem_sleep_time_milli": 6000000,
                "total_light_sleep_time_milli": 13000000,
            },
        },
    },
]

WORKOUT_FIXTURE = [
    {
        "id": 3001,
        "sport_id": 1,
        "sport_name": "cycling",
        "start": "2026-06-28T17:00:00.000Z",
        "end": "2026-06-28T17:45:00.000Z",
        "score_state": "SCORED",
        "score": {
            "strain": 12.4,
            "kilojoule": 1716.0,        # *0.239 = 410.124 kcal
            "distance_meter": 18200.0,
        },
    },
]


def _resp(status_code, json_body):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_body
    return m


def _mock_get_collection(url, headers=None, params=None, timeout=None):
    """Despacha el GET paginado según la colección en la URL. Una sola página
    (sin next_token) para mantener los fixtures simples."""
    mapping = {
        "recovery": RECOVERY_FIXTURE,
        "activity/sleep": SLEEP_FIXTURE,
        "activity/workout": WORKOUT_FIXTURE,
    }
    for key, data in mapping.items():
        if url.endswith(f"/{key}"):
            return _resp(200, {"records": data, "next_token": None})
    return _resp(200, {"records": [], "next_token": None})


# ───────────────────────────────────────────────────────────── build_auth_url

def test_build_auth_url_well_formed():
    src = WhoopSource()
    with patch("app.sources.whoop.settings") as mock_settings:
        mock_settings.WHOOP_CLIENT_ID = "test_client_id"
        mock_settings.WHOOP_AUTH_URL = "https://api.prod.whoop.com/oauth/oauth2/auth"
        mock_settings.REDIRECT_URI = "http://localhost:8700/auth/callback"
        mock_settings.WHOOP_SCOPES = [
            "read:recovery", "read:sleep", "read:workout", "read:cycles",
            "read:profile", "read:body_measurement", "offline",
        ]
        url = src.build_auth_url("xyz123")

    assert url.startswith("https://api.prod.whoop.com/oauth/oauth2/auth")
    assert "client_id=test_client_id" in url
    assert "response_type=code" in url
    assert "state=xyz123" in url
    assert "redirect_uri=" in url
    # scopes presentes, incl. 'offline' (obligatorio para refresh_token)
    assert "read%3Arecovery" in url or "read:recovery" in url
    assert "offline" in url


def test_build_auth_url_uses_real_settings():
    src = get_source("whoop")
    url = src.build_auth_url("s")
    assert url.startswith("https://api.prod.whoop.com/oauth/oauth2/auth")
    assert "state=s" in url


# ───────────────────────────────────────────────────────────── token storage por-fuente

def test_exchange_code_writes_token_whoop_not_others(tmp_path):
    """exchange_code debe escribir token_whoop.json y NUNCA token.json/token_oura.json."""
    from app.sources import _tokenstore

    fake_data_dir = tmp_path
    google_token_path = fake_data_dir / "token.json"
    oura_token_path = fake_data_dir / "token_oura.json"
    whoop_token_path = fake_data_dir / "token_whoop.json"

    src = WhoopSource()
    fake_response = _resp(200, {
        "access_token": "whoop_access_abc",
        "refresh_token": "whoop_refresh_v1",
        "expires_in": 3600,
        "token_type": "bearer",
    })

    with patch.object(_tokenstore.settings, "DATA_DIR", fake_data_dir):
        with patch("app.sources.whoop.requests.post", return_value=fake_response):
            tok = src.exchange_code("authcode123")

    assert tok["access_token"] == "whoop_access_abc"
    assert tok["refresh_token"] == "whoop_refresh_v1"
    assert whoop_token_path.exists()
    assert not google_token_path.exists()
    assert not oura_token_path.exists()

    saved = json.loads(whoop_token_path.read_text())
    assert saved["access_token"] == "whoop_access_abc"
    assert saved["refresh_token"] == "whoop_refresh_v1"
    assert saved["expires_in"] == 3600
    assert "obtained_at" in saved


def test_tokenstore_does_not_collide_with_google_or_oura(tmp_path):
    from app.sources import _tokenstore

    google_token = tmp_path / "token.json"
    google_token.write_text(json.dumps({"refresh_token": "google_rt", "obtained_at": 1}))
    oura_token = tmp_path / "token_oura.json"
    oura_token.write_text(json.dumps({"access_token": "oura_at", "obtained_at": 1}))

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", {"access_token": "whoop_at", "obtained_at": 2})

    google_data = json.loads(google_token.read_text())
    assert google_data["refresh_token"] == "google_rt"
    oura_data = json.loads(oura_token.read_text())
    assert oura_data["access_token"] == "oura_at"
    whoop_data = json.loads((tmp_path / "token_whoop.json").read_text())
    assert whoop_data["access_token"] == "whoop_at"


# ───────────────────────────────────────────────────────────── auth_state

def test_auth_state_no_token(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        result = src.auth_state()
    assert result["status"] == "no_token"
    assert result["days_left"] == 0


def test_auth_state_active(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", {
            "access_token": "abc",
            "refresh_token": "def",
            "expires_in": 3600,
            "obtained_at": int(time.time()),
        })
        result = src.auth_state()
    assert result["status"] == "active"


def test_auth_state_marked_expired(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", {"access_token": "abc", "expired": True})
        result = src.auth_state()
    assert result["status"] == "expired"


# ───────────────────────────────────────────────────────────── access_token / errores básicos

def test_access_token_no_token_raises(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        with pytest.raises(NoToken):
            src.access_token()


def test_access_token_expired_raises(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", {"access_token": "abc", "expired": True})
        with pytest.raises(TokenExpired):
            src.access_token()


def test_access_token_returns_stored_value_when_not_expired(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", {
            "access_token": "tok_live_123",
            "refresh_token": "rt1",
            "expires_in": 3600,
            "obtained_at": int(time.time()),
        })
        assert src.access_token() == "tok_live_123"


# ───────────────────────────────────────────────────── 🔴 refresh ROTATORIO

def test_refresh_persists_new_refresh_token_immediately(tmp_path):
    """El response de refresh trae un refresh_token NUEVO → access_token() debe
    persistirlo (junto con el nuevo access_token) en token_whoop.json antes de
    devolver el access_token al caller."""
    from app.sources import _tokenstore
    src = WhoopSource()

    expired_tok = {
        "access_token": "old_access",
        "refresh_token": "refresh_v1",
        "expires_in": 3600,
        "obtained_at": int(time.time()) - 7200,  # vencido hace rato
    }
    refresh_response = _resp(200, {
        "access_token": "new_access_v2",
        "refresh_token": "refresh_v2",   # 🔴 NUEVO — el v1 queda inválido
        "expires_in": 3600,
        "token_type": "bearer",
    })

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", expired_tok)
        with patch("app.sources.whoop.requests.post", return_value=refresh_response) as mock_post:
            result = src.access_token()

        assert result == "new_access_v2"
        # Persistido en disco INMEDIATAMENTE — no solo en memoria
        saved = _tokenstore.load_token("whoop")
        assert saved["access_token"] == "new_access_v2"
        assert saved["refresh_token"] == "refresh_v2"
        # El POST de refresh usó el refresh_token vigente (v1), no uno viejo
        sent_data = mock_post.call_args.kwargs.get("data") or mock_post.call_args[1]["data"]
        assert sent_data["refresh_token"] == "refresh_v1"
        assert sent_data["grant_type"] == "refresh_token"


def test_second_refresh_uses_new_token_not_old(tmp_path):
    """Tras un primer refresh que rota refresh_v1→refresh_v2, un segundo refresh
    debe usar refresh_v2 (el vigente) — NUNCA reintentar con refresh_v1 (ya inválido
    del lado de WHOOP)."""
    from app.sources import _tokenstore
    src = WhoopSource()

    initial_tok = {
        "access_token": "access_v1",
        "refresh_token": "refresh_v1",
        "expires_in": 3600,
        "obtained_at": int(time.time()) - 7200,
    }
    first_refresh_response = _resp(200, {
        "access_token": "access_v2",
        "refresh_token": "refresh_v2",
        "expires_in": 3600,
    })
    second_refresh_response = _resp(200, {
        "access_token": "access_v3",
        "refresh_token": "refresh_v3",
        "expires_in": 3600,
    })

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", initial_tok)

        # 1er refresh
        with patch("app.sources.whoop.requests.post", return_value=first_refresh_response):
            r1 = src.access_token()
        assert r1 == "access_v2"
        assert _tokenstore.load_token("whoop")["refresh_token"] == "refresh_v2"

        # Forzar expiración de nuevo para disparar un 2do refresh
        tok = _tokenstore.load_token("whoop")
        tok["obtained_at"] = int(time.time()) - 7200
        _tokenstore.save_token("whoop", tok)

        with patch("app.sources.whoop.requests.post", return_value=second_refresh_response) as mock_post2:
            r2 = src.access_token()
        assert r2 == "access_v3"
        sent_data = mock_post2.call_args.kwargs.get("data") or mock_post2.call_args[1]["data"]
        # 🔴 usa refresh_v2 (el nuevo), NUNCA refresh_v1 (el ya consumido/inválido)
        assert sent_data["refresh_token"] == "refresh_v2"
        assert _tokenstore.load_token("whoop")["refresh_token"] == "refresh_v3"


def test_refresh_single_flight_lock_releases_after_use(tmp_path):
    """El lock de single-flight no debe quedar tomado tras un refresh exitoso —
    una llamada subsecuente con token aún válido no debe bloquearse."""
    from app.sources import _tokenstore
    src = WhoopSource()

    expired_tok = {
        "access_token": "old",
        "refresh_token": "rt1",
        "expires_in": 3600,
        "obtained_at": int(time.time()) - 7200,
    }
    refresh_response = _resp(200, {
        "access_token": "new",
        "refresh_token": "rt2",
        "expires_in": 3600,
    })

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", expired_tok)
        with patch("app.sources.whoop.requests.post", return_value=refresh_response):
            src.access_token()

        # El lock se liberó (no quedó tomado) — una llamada inmediata no cuelga
        assert not src._refresh_lock.locked()
        # Y como el token recién refrescado no está vencido, no dispara otro refresh
        assert src.access_token() == "new"


def test_refresh_invalid_grant_marks_expired(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()

    expired_tok = {
        "access_token": "old",
        "refresh_token": "rt_dead",
        "expires_in": 3600,
        "obtained_at": int(time.time()) - 7200,
    }
    fail_response = _resp(400, {"error": "invalid_grant", "error_description": "refresh_token invalid"})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", expired_tok)
        with patch("app.sources.whoop.requests.post", return_value=fail_response):
            with pytest.raises(TokenExpired):
                src.access_token()
        tok = _tokenstore.load_token("whoop")
        assert tok.get("expired") is True


def test_refresh_missing_new_refresh_token_raises_instead_of_reusing_old(tmp_path):
    """Si el response de refresh no trae refresh_token nuevo, NO debemos reusar
    el viejo (WHOOP ya lo invalidó) — debe fallar explícito."""
    from app.sources import _tokenstore
    src = WhoopSource()

    expired_tok = {
        "access_token": "old",
        "refresh_token": "rt1",
        "expires_in": 3600,
        "obtained_at": int(time.time()) - 7200,
    }
    bad_response = _resp(200, {"access_token": "new_access", "expires_in": 3600})  # sin refresh_token

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _tokenstore.save_token("whoop", expired_tok)
        with patch("app.sources.whoop.requests.post", return_value=bad_response):
            with pytest.raises(RuntimeError):
                src.access_token()
        # El token viejo en disco no fue pisado con uno corrupto
        tok = _tokenstore.load_token("whoop")
        assert tok["refresh_token"] == "rt1"


# ───────────────────────────────────────────────────────────── fetch() / normalización

def _save_live_token(tmp_path):
    from app.sources import _tokenstore
    _tokenstore.save_token("whoop", {
        "access_token": "tok",
        "refresh_token": "rt",
        "expires_in": 3600,
        "obtained_at": int(time.time()),
    })


def test_fetch_returns_all_13_keys(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    expected_keys = {
        "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
        "exercises", "distance_km", "energy_kcal", "active_hours",
    }
    assert set(data.keys()) == expected_keys
    # WHOOP no da estos directamente — el motor los deriva/None
    assert data["vo2"] == {}
    assert data["steps"] == {}
    assert data["azm"] == {}
    assert data["active_hours"] == {}


def test_fetch_recovery_normalization_and_4_0_only(tmp_path):
    """rhr directo; hrv en ms tal cual reporta WHOOP (RMSSD ya en ms, sin convertir);
    spo2 SOLO si hardware 4.0; skin_temp_celsius absoluto → DESVIACIÓN centrada en la media
    de la ventana (1 sola lectura → desviación 0.0)."""
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert data["rhr"]["2026-06-28"] == 48
    assert data["hrv"]["2026-06-28"] == 52.0      # ms tal cual (no se multiplica)
    assert data["spo2"]["2026-06-28"] == 97.2
    assert data["skin"]["2026-06-28"] == 0.0      # 1 lectura → desviación 0 (absoluto→centrado)

    # 2026-06-29: spo2/skin_temp = None en el fixture (hardware sin 4.0) → ausentes
    assert data["rhr"]["2026-06-29"] == 50
    assert "2026-06-29" not in data["spo2"]
    assert "2026-06-29" not in data["skin"]


def test_fetch_sleep_normalization_milli_to_min_and_day_from_end(tmp_path):
    """milli→min (/60000); día = fecha local de `end` (el despertar); asleep =
    in_bed - awake; bedtime/waketime en HH:MM desde start/end."""
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    sleep = data["sleep"]
    assert "2026-06-28" in sleep   # día del 'end' (06:55 del 28), no del 'start' (23:41 del 27)
    night = sleep["2026-06-28"]
    assert night["deep"] == pytest.approx(4800000 / 60000, abs=0.01)    # 80.0 min
    assert night["rem"] == pytest.approx(5700000 / 60000, abs=0.01)     # 95.0 min
    assert night["light"] == pytest.approx(14100000 / 60000, abs=0.01)  # 235.0 min
    assert night["inbed"] == pytest.approx(26026000 / 60000, abs=0.05)  # redondeado a 1 decimal en fetch()
    assert night["asleep"] == pytest.approx((26026000 - 1426000) / 60000, abs=0.01)  # in_bed - awake
    assert night["eff"] == 94.0
    assert night["bedtime"] == "23:41"
    assert night["waketime"] == "06:55"

    assert data["resp"]["2026-06-28"] == 14.2


def test_fetch_sleep_none_safety_missing_stage_field(tmp_path):
    """Noche 2026-06-29 tiene total_slow_wave_sleep_time_milli=None → deep=None,
    no rompe, el resto de campos sigue presente."""
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    night2 = data["sleep"]["2026-06-29"]
    assert night2["deep"] is None
    assert night2["rem"] is not None
    assert data["resp"]["2026-06-29"] == 13.8


def test_fetch_workout_kj_to_kcal_and_distance(tmp_path):
    """kilojoule * 0.239 → kcal; distance_meter/1000 → km; exercises[] con
    date/name=sport_name/dur_min/kcal/distance_km; distance_km/energy_kcal
    diarios sumados desde los workouts."""
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_mock_get_collection):
            data = src.fetch(45)

    assert len(data["exercises"]) == 1
    ex = data["exercises"][0]
    assert ex["date"] == "2026-06-28"
    assert ex["name"] == "cycling"
    assert ex["dur_min"] == pytest.approx(45.0, abs=0.01)
    assert ex["kcal"] == pytest.approx(1716.0 * 0.239, abs=0.1)
    assert ex["distance_km"] == pytest.approx(18.2, abs=0.01)

    assert data["distance_km"]["2026-06-28"] == pytest.approx(18.2, abs=0.01)
    assert data["energy_kcal"]["2026-06-28"] == pytest.approx(1716.0 * 0.239, abs=0.1)


def test_fetch_empty_collections_are_none_safe(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()

    def _all_empty(url, headers=None, params=None, timeout=None):
        return _resp(200, {"records": [], "next_token": None})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_all_empty):
            data = src.fetch(45)

    assert data["sleep"] == {}
    assert data["rhr"] == {}
    assert data["spo2"] == {}
    assert data["skin"] == {}
    assert data["exercises"] == []
    assert data["distance_km"] == {}
    assert data["energy_kcal"] == {}


def test_fetch_collection_http_error_degrades_silently(tmp_path):
    """Un endpoint devolviendo 500 no debe romper fetch — se degrada a [] para esa
    colección y el resto sigue funcionando."""
    from app.sources import _tokenstore
    src = WhoopSource()

    def _recovery_fails(url, headers=None, params=None, timeout=None):
        if url.endswith("/recovery"):
            return _resp(500, {})
        return _mock_get_collection(url, headers, params, timeout)

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_recovery_fails):
            data = src.fetch(45)

    assert data["rhr"] == {}
    assert data["spo2"] == {}
    # el resto de colecciones siguen funcionando
    assert "2026-06-28" in data["sleep"]
    assert len(data["exercises"]) == 1


def test_fetch_pagination_follows_next_token(tmp_path):
    """_get_collection debe seguir next_token hasta que venga None, acumulando
    records de todas las páginas."""
    from app.sources import _tokenstore
    src = WhoopSource()

    page1 = RECOVERY_FIXTURE[:1]
    page2 = RECOVERY_FIXTURE[1:]

    calls = {"n": 0}

    def _paginated(url, headers=None, params=None, timeout=None):
        if not url.endswith("/recovery"):
            return _resp(200, {"records": [], "next_token": None})
        calls["n"] += 1
        if params.get("nextToken") is None and calls["n"] == 1:
            return _resp(200, {"records": page1, "next_token": "page2tok"})
        return _resp(200, {"records": page2, "next_token": None})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_paginated):
            data = src.fetch(45)

    assert "2026-06-28" in data["rhr"]
    assert "2026-06-29" in data["rhr"]
    assert calls["n"] == 2


def test_fetch_no_token_raises(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()
    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        with pytest.raises(NoToken):
            src.fetch(45)


def test_fetch_401_marks_expired_and_raises(tmp_path):
    from app.sources import _tokenstore
    src = WhoopSource()

    def _unauthorized(url, headers=None, params=None, timeout=None):
        return _resp(401, {"detail": "invalid token"})

    with patch.object(_tokenstore.settings, "DATA_DIR", tmp_path):
        _save_live_token(tmp_path)
        with patch("app.sources.whoop.requests.get", side_effect=_unauthorized):
            with pytest.raises(TokenExpired):
                src.fetch(45)
        tok = _tokenstore.load_token("whoop")
        assert tok.get("expired") is True


# ───────────────────────────────────────────────────────────── get_source('whoop')

def test_get_source_whoop_no_longer_stub():
    src = get_source("whoop")
    assert isinstance(src, Source)
    assert isinstance(src, WhoopSource)
    assert src.name == "whoop"
