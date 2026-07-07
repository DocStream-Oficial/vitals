"""
app/routes/auth.py — GET/POST /login, GET /auth/login, GET /auth/callback
(Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.

_oauth_states (CSRF state store en memoria) vive aquí porque las 2 únicas
rutas que lo usan (/auth/login que escribe, /auth/callback que lee) se
mudaron juntas — antes vivía suelto en main.py.
"""
from __future__ import annotations

import html
import logging
import secrets
from typing import Dict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app import profile as _profile
from app.sources import get_source
from app.deps import _DASHBOARD_COOKIE_NAME

logger = logging.getLogger("vitals.main")

router = APIRouter()

# CSRF state store (en memoria; se reinicia con el server, suficiente para dev).
# Fase 6A: dict state -> source_name (era Set[str]) — permite que /auth/callback sepa
# a qué fuente pertenece cada state cuando dos flujos OAuth están en vuelo en paralelo
# (ej. usuario conecta Google, luego sin recargar conecta Oura).
_oauth_states: Dict[str, str] = {}


_LOGIN_PAGE_TEMPLATE = """<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vitals — Login</title>
<style>
  body {{
    background: #07090e; color: #e6edf3; font-family: -apple-system, sans-serif;
    display: flex; align-items: center; justify-content: center;
    min-height: 100vh; margin: 0; padding: 24px; box-sizing: border-box;
  }}
  form {{
    background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.1);
    border-radius: 20px; padding: 32px; width: 100%; max-width: 340px;
  }}
  h1 {{ font-size: 20px; margin: 0 0 18px; }}
  input {{
    width: 100%; box-sizing: border-box; padding: 12px 14px; border-radius: 12px;
    border: 1px solid rgba(255,255,255,.16); background: rgba(255,255,255,.04);
    color: #e6edf3; font-size: 15px; margin-bottom: 14px;
  }}
  button {{
    width: 100%; padding: 12px 14px; border-radius: 12px; border: none;
    background: #16c784; color: #07090e; font-size: 15px; font-weight: 600;
    cursor: pointer;
  }}
  .err {{ color: #ff6b6b; font-size: 13px; margin: -6px 0 14px; }}
</style>
</head>
<body>
<form method="post" action="/login">
  <h1>Vitals</h1>
  {error_html}
  <input type="password" name="token" placeholder="{placeholder}" autofocus>
  <button type="submit">{button}</button>
</form>
</body>
</html>"""


def _render_login_page(locale: str, error: bool = False) -> str:
    is_es = (locale or "es").startswith("es")
    error_html = (
        f"<p class='err'>{'Token incorrecto.' if is_es else 'Incorrect token.'}</p>" if error else ""
    )
    return _LOGIN_PAGE_TEMPLATE.format(
        lang="es" if is_es else "en",
        error_html=error_html,
        placeholder="Token" if is_es else "Token",
        button="Entrar" if is_es else "Sign in",
    )


@router.get("/login", include_in_schema=False)
async def login_page():
    """Página HTML mínima de login para el dashboard-auth opt-in — inline
    (no depende del template grande), bilingüe ES/EN estática por locale de
    perfil. Accesible siempre, incluso con DASHBOARD_TOKEN vacío (en ese caso
    simplemente no hace nada útil, pero no rompe)."""
    locale = _profile.effective("locale") or "es"
    return HTMLResponse(content=_render_login_page(locale), status_code=200)


@router.post("/login", include_in_schema=False)
async def login_submit(request: Request):
    """Valida el token posteado contra DASHBOARD_TOKEN (compare_digest en
    bytes UTF-8) y, si coincide, setea la cookie HttpOnly SameSite=Lax y
    redirige a '/'. Sin flag Secure: detrás de tailscale serve / reverse
    proxy la app ve http en 127.0.0.1 — Secure rompería el dev local."""
    locale = _profile.effective("locale") or "es"
    try:
        # Parseo manual (urllib.parse, stdlib) en vez de request.form(): esto
        # evita depender de python-multipart, que no está en requirements.txt.
        from urllib.parse import parse_qsl
        raw = await request.body()
        parsed = dict(parse_qsl(raw.decode("utf-8", errors="replace")))
        provided = parsed.get("token", "")
    except Exception:
        provided = ""

    expected = settings.DASHBOARD_TOKEN
    if expected and secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(
            _DASHBOARD_COOKIE_NAME,
            expected,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=31536000,
        )
        return response

    return HTMLResponse(content=_render_login_page(locale, error=True), status_code=401)


@router.get("/auth/login")
async def auth_login(source: str = "google_health"):
    """Redirige al OAuth de la fuente pedida (?source=oura|whoop|google_health; default
    google_health por compat con links viejos sin el query param).

    Fase 6A: guarda `_oauth_states[state] = source` para que /auth/callback sepa a qué
    fuente pertenece el state — permite 2 flujos OAuth en vuelo en paralelo sin pisarse
    (ej. conectar Google, luego sin recargar conectar Oura)."""
    if settings.VITALS_DEMO:
        return HTMLResponse(
            "<html><body style='background:#07090e;color:#e6edf3;font-family:-apple-system,sans-serif;"
            "padding:40px'><h2>Demo mode</h2>"
            "<p>Esta instancia corre con datos sintéticos (VITALS_DEMO=1) — conectar una fuente "
            "real está deshabilitado aquí. Clona el repo y corre tu propia instancia para conectar "
            "Google Health, Oura, WHOOP o HealthKit.</p>"
            "<p><a href='/' style='color:#4d9fff'>Volver al tablero</a></p></body></html>",
            status_code=200,
        )
    state = secrets.token_urlsafe(16)
    _oauth_states[state] = source
    try:
        url = get_source(source).build_auth_url(state)
    except NotImplementedError as e:
        # HealthKit (Fase 5D-A) se autoriza on-device, no por OAuth web. Si alguien
        # fuerza /auth/login con source=healthkit, devolvemos error controlado (no 500).
        _oauth_states.pop(state, None)
        return HTMLResponse(
            f"<html><body style='background:#07090e;color:#ff6163;font-family:sans-serif;padding:40px'>"
            f"<h2>Esta fuente no usa OAuth web</h2><p>{html.escape(str(e))}</p>"
            f"<p><a href='/' style='color:#4d9fff'>Volver al tablero</a></p></body></html>",
            status_code=400,
        )
    except ValueError as e:
        # Nombre de fuente desconocido — error controlado, no 500.
        _oauth_states.pop(state, None)
        return HTMLResponse(
            f"<html><body style='background:#07090e;color:#ff6163;font-family:sans-serif;padding:40px'>"
            f"<h2>Fuente desconocida</h2><p>{html.escape(str(e))}</p>"
            f"<p><a href='/' style='color:#4d9fff'>Volver al tablero</a></p></body></html>",
            status_code=400,
        )
    return RedirectResponse(url=url, status_code=302)


@router.get("/auth/callback")
async def auth_callback(code: str = None, state: str = None, error: str = None):
    """Recibe el callback de Google, intercambia el code por tokens y los guarda."""
    if settings.VITALS_DEMO:
        # Defensa en profundidad: /auth/login (bloqueado en demo, ver arriba)
        # nunca registra un state válido, así que este callback ya sería
        # inalcanzable en la práctica — este guard evita además cualquier
        # exchange_code() real si alguien lo golpea directo con un state viejo.
        raise HTTPException(status_code=404, detail="Demo mode: OAuth deshabilitado.")
    if error:
        return HTMLResponse(
            f"<html><body style='background:#07090e;color:#ff6163;font-family:sans-serif;padding:40px'>"
            f"<h2>Error OAuth: {html.escape(error)}</h2>"
            f"<p><a href='/auth/login' style='color:#16c784'>Reintentar</a></p></body></html>",
            status_code=400,
        )
    if not code:
        raise HTTPException(status_code=400, detail="Falta el parámetro code.")
    # Validar CSRF state y recuperar a qué fuente pertenece (Fase 6A: dict state->source,
    # ya NO se asume _active_source() — así 2 flujos OAuth en paralelo no se pisan).
    source_name = _oauth_states.get(state)
    if source_name is None:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido o expirado.")
    _oauth_states.pop(state, None)
    try:
        get_source(source_name).exchange_code(code)
    except Exception as e:
        logger.error(f"exchange_code falló: {e}")
        return HTMLResponse(
            f"<html><body style='background:#07090e;color:#ff6163;font-family:sans-serif;padding:40px'>"
            f"<h2>Error al obtener token</h2><p>{html.escape(str(e))}</p>"
            f"<p><a href='/auth/login' style='color:#16c784'>Reintentar</a></p></body></html>",
            status_code=400,
        )
    return HTMLResponse(
        "<html><body style='background:#07090e;color:#16c784;font-family:sans-serif;padding:40px'>"
        "<h2>Conectado correctamente</h2>"
        "<p>Token guardado. <a href='/' style='color:#4d9fff'>Ver tablero</a></p>"
        "<script>setTimeout(()=>location.href='/',2000)</script>"
        "</body></html>",
        status_code=200,
    )
