"""
auth.py — OAuth con Google: build_auth_url, exchange_code, access_token, auth_state.
Guarda/lee data/token.json. Lanza TokenExpired en invalid_grant.
"""
from __future__ import annotations  # enables X | Y union syntax on Python 3.9+

import json
import time
import urllib.parse
from pathlib import Path
from typing import Optional

import requests

from app.config import settings
from app.fsutil import atomic_write_text

# Sentinel (deuda R2, aislamiento de tests): None en reposo -> el accessor
# resuelve SIEMPRE contra settings.DATA_DIR en runtime, así un
# importlib.reload(auth) nunca re-liga esta constante a una ruta congelada de
# import-time. Override SOLO para tests (patch.object(auth_mod, "TOKEN_PATH",
# ruta) — sigue funcionando idéntico, ver docstring de _token_path).
TOKEN_PATH: Optional[Path] = None  # legacy — usado si userctx no está activo/disponible


def _token_path() -> Path:
    """Ruta a token.json del usuario activo (Fase 8D, paso D3: household).
    Fuera de un request household-aware (is_context_active()=False — tests
    preexistentes que hacen patch.object(auth_mod, "TOKEN_PATH", ...), scripts),
    usa TOKEN_PATH tal cual si fue fijado explícitamente; si no, resuelve en
    RUNTIME contra settings.DATA_DIR (reload-proof — ver comentario del
    sentinel arriba). Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "token.json"
    except Exception:
        pass
    if TOKEN_PATH is not None:   # override explícito de un test
        return TOKEN_PATH
    return settings.DATA_DIR / "token.json"   # resolución RUNTIME


class TokenExpired(Exception):
    """El refresh token expiró (invalid_grant) o fue revocado."""
    pass


class NoToken(Exception):
    """No hay token guardado — hay que hacer auth."""
    pass


def build_auth_url(state: str) -> str:
    """Construye la URL de autorización de Google con los 3 scopes + offline."""
    params = {
        "client_id": settings.CLIENT_ID,
        "redirect_uri": settings.REDIRECT_URI,
        "response_type": "code",
        "access_type": "offline",
        "prompt": "consent",
        "scope": " ".join(settings.SCOPES),
        "state": state,
    }
    return settings.AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    """Intercambia el authorization code por tokens; guarda en data/token.json."""
    data = {
        "code": code,
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "redirect_uri": settings.REDIRECT_URI,
        "grant_type": "authorization_code",
    }
    resp = requests.post(
        settings.TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    body = resp.json()
    if resp.status_code != 200 or "refresh_token" not in body:
        raise RuntimeError(f"exchange_code falló (status {resp.status_code}): {body}")
    tok = {
        "refresh_token": body["refresh_token"],
        "access_token": body.get("access_token"),
        "obtained_at": int(time.time()),
    }
    _save_token(tok)
    return tok


def access_token() -> str:
    """Refresca y devuelve un access_token válido.
    Lanza TokenExpired si invalid_grant, NoToken si no hay token."""
    tok = _load_token()
    if not tok or "refresh_token" not in tok:
        raise NoToken("No hay token. Visita /auth/login para autorizar.")
    data = {
        "client_id": settings.CLIENT_ID,
        "client_secret": settings.CLIENT_SECRET,
        "refresh_token": tok["refresh_token"],
        "grant_type": "refresh_token",
    }
    resp = requests.post(
        settings.TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    body = resp.json()
    if resp.status_code != 200 or "access_token" not in body:
        if "invalid_grant" in json.dumps(body):
            raise TokenExpired("Token expirado (invalid_grant). Visita /auth/login.")
        raise RuntimeError(f"No pude refrescar el token (status {resp.status_code}): {body}")
    return body["access_token"]


def auth_state() -> dict:
    """Devuelve {status, days_left} leyendo data/token.json.
    status: 'active' | 'expiring' | 'expired' | 'no_token'
    days_left: int 0-7 (o el valor del setting en modo permanente — no dispara UI)

    Prioridad (roadmap: ROADMAP-vitals-token-nag.md — "aviso honesto"):
      1. Sin token -> no_token.
      2. tok['expired'] True (invalid_grant real, marcado por mark_expired())
         -> expired SIEMPRE, gana sobre cualquier otra cosa.
      3. settings.GOOGLE_TOKEN_EXPIRY_DAYS <= 0 (default: app OAuth publicada,
         el refresh_token de Google no caduca por edad) -> active, ignora la
         edad del token por completo.
      4. settings.GOOGLE_TOKEN_EXPIRY_DAYS > 0 (app en modo Testing de Google,
         límite real de N días) -> countdown como antes: expiring en los
         últimos 2 días, expired al llegar a 0.
    """
    tok = _load_token()
    if not tok or "obtained_at" not in tok:
        return {"status": "no_token", "days_left": 0}
    # ground truth: un fallo real de invalid_grant siempre gana, sin importar
    # el modo/setting.
    if tok.get("expired"):
        return {"status": "expired", "days_left": 0}

    expiry_days = settings.GOOGLE_TOKEN_EXPIRY_DAYS
    if expiry_days <= 0:
        # App OAuth publicada (o self-hoster que optó por ignorar la edad):
        # el token es permanente mientras no falle de verdad. days_left se
        # devuelve como el propio setting (<=0) por compat de shape con la
        # UI, pero ningún consumidor debe usarlo para decidir mostrar banner
        # con status='active' (ver templates/vitals_ios.html::renderAuth).
        return {"status": "active", "days_left": expiry_days}

    elapsed_days = (time.time() - tok["obtained_at"]) / 86400
    days_left = max(0, min(expiry_days, round(expiry_days - elapsed_days)))
    if days_left <= 0:
        return {"status": "expired", "days_left": 0}
    if days_left <= 2:
        return {"status": "expiring", "days_left": days_left}
    return {"status": "active", "days_left": days_left}


def mark_expired():
    """Marca el token como expirado (llamado cuando sync recibe invalid_grant)."""
    tok = _load_token() or {}
    tok["expired"] = True
    _save_token(tok)


# ---------------------------------------------------------------- helpers

def _load_token() -> dict | None:
    path = _token_path()
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return None
    return None


def _save_token(tok: dict):
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Escritura ATÓMICA (.tmp + os.replace) — un crash a media escritura no puede
    # dejar token.json truncado/corrupto (perdería la sesión de Google).
    atomic_write_text(path, json.dumps(tok, indent=2))
