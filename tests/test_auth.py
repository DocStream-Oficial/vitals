"""
test_auth.py — build_auth_url + auth_state (ambos modos: permanente/countdown, sin red).

Roadmap: ROADMAP-vitals-token-nag.md. Con GOOGLE_TOKEN_EXPIRY_DAYS<=0 (default,
app OAuth publicada) el token es permanente y solo el flag real `expired`
(invalid_grant) dispara el banner. Con >0 (app en modo Testing de Google) se
conserva el countdown por edad de siempre.
"""
import json
import time
from unittest.mock import patch


def test_build_auth_url_contains_scopes():
    """build_auth_url debe incluir los 3 scopes y access_type=offline."""
    from app.auth import build_auth_url
    url = build_auth_url("teststate123")
    assert "access_type=offline" in url
    assert "googlehealth.activity_and_fitness" in url
    assert "googlehealth.health_metrics_and_measurements" in url
    assert "googlehealth.sleep" in url
    assert "state=teststate123" in url
    assert "response_type=code" in url
    assert "prompt=consent" in url


def test_build_auth_url_starts_with_google():
    from app.auth import build_auth_url
    url = build_auth_url("abc")
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth")


def test_auth_state_no_token(tmp_path):
    """Sin token.json → status no_token, sin importar el setting."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    with patch.object(auth_mod, "TOKEN_PATH", fake_token):
        result = auth_mod.auth_state()
    assert result["status"] == "no_token"
    assert result["days_left"] == 0


# ──────────────────────────────────────────── modo permanente (setting <= 0, default)

def test_auth_state_permanent_ignores_age(tmp_path):
    """App OAuth publicada (setting=0): token de 30 días de edad -> active,
    sin importar la edad (NUNCA expiring/expired solo por antigüedad)."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "access_token": "fake",
        "obtained_at": int(time.time()) - 30 * 86400,  # 30 días atrás
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 0):
        result = auth_mod.auth_state()
    assert result["status"] == "active"


def test_auth_state_permanent_default_setting_is_zero():
    """Default de config: GOOGLE_TOKEN_EXPIRY_DAYS es 0 si no se define env var."""
    from app.config import settings
    assert settings.GOOGLE_TOKEN_EXPIRY_DAYS == 0


def test_auth_state_marked_expired_wins_over_permanent_mode(tmp_path):
    """Flag expired=True gana SIEMPRE, incluso en modo permanente (setting 0)."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - 86400,
        "expired": True,
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 0):
        result = auth_mod.auth_state()
    assert result["status"] == "expired"


# ──────────────────────────────────────────── modo countdown (setting > 0, Testing)

def test_auth_state_marked_expired_wins_over_countdown_mode(tmp_path):
    """Flag expired=True gana SIEMPRE, también en modo countdown (setting 7)."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - 86400,
        "expired": True,
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result = auth_mod.auth_state()
    assert result["status"] == "expired"


def test_auth_state_countdown_active(tmp_path):
    """Setting=7 (modo Testing), token obtenido hace 2 días → active, days_left=5."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "access_token": "fake",
        "obtained_at": int(time.time()) - 2 * 86400,  # 2 días atrás
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result = auth_mod.auth_state()
    assert result["status"] == "active"
    assert result["days_left"] == 5


def test_auth_state_countdown_expiring(tmp_path):
    """Setting=7, token obtenido hace 5.5 días → status expiring, days_left=1."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - int(5.5 * 86400),
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result = auth_mod.auth_state()
    assert result["status"] == "expiring"
    assert result["days_left"] <= 2


def test_auth_state_countdown_expiring_6_days_old(tmp_path):
    """Setting=7, edad 6 días → expiring (últimos 2 días del countdown)."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - 6 * 86400,
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result = auth_mod.auth_state()
    assert result["status"] == "expiring"
    assert result["days_left"] == 1


def test_auth_state_countdown_expired(tmp_path):
    """Setting=7, token obtenido hace 8 días → status expired (edad agotó el countdown)."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - 8 * 86400,
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result = auth_mod.auth_state()
    assert result["status"] == "expired"
    assert result["days_left"] == 0


def test_auth_state_same_token_active_with_setting_0_expired_with_setting_7(tmp_path):
    """El mismo token (edad 30d) cambia de status según el setting: prueba
    directa de que el modo permanente vs countdown depende SOLO del setting,
    no de otro estado oculto."""
    from app import auth as auth_mod
    fake_token = tmp_path / "token.json"
    tok = {
        "refresh_token": "fake",
        "obtained_at": int(time.time()) - 30 * 86400,
    }
    fake_token.write_text(json.dumps(tok))
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 0):
        result_permanent = auth_mod.auth_state()
    with patch.object(auth_mod, "TOKEN_PATH", fake_token), \
         patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
        result_countdown = auth_mod.auth_state()
    assert result_permanent["status"] == "active"
    assert result_countdown["status"] == "expired"


def test_days_left_calculation_countdown_mode():
    """Validación directa del countdown con setting=7 (modo Testing):
    N - días_transcurridos, clamp [0,7]."""
    from app import auth as auth_mod
    now = int(time.time())
    for days_ago, expected_days_left in [(0, 7), (3, 4), (6, 1), (7, 0), (10, 0)]:
        tok = {"refresh_token": "x", "obtained_at": now - days_ago * 86400}
        with patch.object(auth_mod, "_load_token", return_value=tok), \
             patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 7):
            result = auth_mod.auth_state()
        assert result["days_left"] == expected_days_left, (
            f"days_ago={days_ago}: got {result['days_left']}, expected {expected_days_left}"
        )


def test_days_left_permanent_mode_never_triggers_countdown():
    """Con setting<=0, days_left nunca activa expiring/expired por edad,
    sin importar cuántos días hayan pasado."""
    from app import auth as auth_mod
    now = int(time.time())
    for days_ago in [0, 3, 6, 7, 10, 365]:
        tok = {"refresh_token": "x", "obtained_at": now - days_ago * 86400}
        with patch.object(auth_mod, "_load_token", return_value=tok), \
             patch.object(auth_mod.settings, "GOOGLE_TOKEN_EXPIRY_DAYS", 0):
            result = auth_mod.auth_state()
        assert result["status"] == "active", f"days_ago={days_ago}: got {result['status']}"
