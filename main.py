"""
main.py — FastAPI app: rutas, startup, monta scheduler.
Escucha SOLO en 127.0.0.1 (el puente al tailnet lo hace tailscale serve).
"""
from __future__ import annotations  # enables X | Y and set[str] on Python 3.9

import csv
import datetime as _dt
import html
import io
import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import Any, Dict, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from typing import List, Optional

from app.config import settings
from app.auth import TokenExpired, NoToken
from app import ecg_store
from app.sources import get_source
from app.coach import build_coach, coach_card as build_coach_card
from app.coach_chat import ask_coach
from app import coach_store as _coach_store
from app.coach_store import load_history, clear as clear_history
from app.insights import evaluate as evaluate_insights
from app.coach_suggest import suggested_questions as _suggested_questions
from app import changes as _changes
from app import coach_headline as _coach_headline
from app.profile import (
    load_profile, save_profile, is_onboarded, effective, effective_profile_dict
)
from app import profile as _profile
from app import cycle as _cycle
from app import journal as _journal
from app import report as _report
from app import labs as _labs
from app import healthspan as _healthspan
from app import userctx as _userctx
from app import api_keys as _api_keys
from app.render import render_dashboard, render_ios
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("vitals.main")

app = FastAPI(title="Vitals Web App")
# GZip (Ronda 1): el GET / manda ~250 KB de HTML sin comprimir; minimum_size=1024
# deja pasar respuestas chicas (JSON de status, etc.) sin overhead.
app.add_middleware(GZipMiddleware, minimum_size=1024)

DATA_PATH: Optional[Path] = None  # override SOLO para tests (patch.object); None = runtime

# Fase 9 (paso A1): _active_source/_data_path/_load_dataset/_load_demo_dataset/
# _demo_blocked_response viven ahora en app/deps.py (pegamento compartido entre
# routers) — se importan aquí con el MISMO nombre para que main_mod._data_path()
# etc. sigan siendo válidos (decenas de tests los llaman/parchean por nombre).
# _data_path() lee main.DATA_PATH vía import diferido dentro de app/deps.py,
# así que el sentinel DE ARRIBA sigue siendo la única fuente de verdad.
from app.deps import (  # noqa: E402
    _active_source,
    _data_path,
    _load_dataset,
    _load_demo_dataset,
    _demo_blocked_response,
)

STATIC_DIR = settings.ROOT_DIR / "static"

# ---------------------------------------------------------------- PWA / static
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Fase 9 (paso A2): manifest/service-worker/ingest-token/qr viven ahora en
# app/routes/pwa.py — se registran vía include_router más abajo (después de
# definir la app y el mount de estáticos, igual que antes).
from app.routes.pwa import router as _pwa_router  # noqa: E402
app.include_router(_pwa_router)

# CSRF state store (en memoria; se reinicia con el server, suficiente para dev).
# Fase 6A: dict state -> source_name (era Set[str]) — permite que /auth/callback sepa
# a qué fuente pertenece cada state cuando dos flujos OAuth están en vuelo en paralelo
# (ej. usuario conecta Google, luego sin recargar conecta Oura).
_oauth_states: Dict[str, str] = {}

# Fuentes conocidas del sistema (usado para validar POST/DELETE /api/sources/{name}).
_KNOWN_SOURCES = ("google_health", "oura", "whoop", "healthkit")


# ---------------------------------------------------------------- lifecycle

def _seed_demo_side_data() -> None:
    """Fase 8A (paso A1) + Roadmap P1 (F4, paso 9) + Roadmap P2 (F8, paso 5):
    precarga journal_log.json / labs_log.json / plan_log.json / reports.json
    de ejemplo (generados por scripts/gen_demo_data.py) en el DATA_DIR
    efímero de la demo — para que /api/journal, /api/journal/impact,
    /api/labs, /api/healthspan, /api/plan (planCard + sección Programas) y
    /api/report (incluido el sleep_archetype adjunto, que depende de que
    `data` no sea None) tengan señal desde el primer request, no solo el
    dataset principal.
    Escribe SOLO bajo settings.DATA_DIR (ya el tempdir de la demo, ver
    config.py) — nunca data/ real. Best-effort: si los fixtures no existen o
    falla la copia, la demo sigue funcionando (esas secciones muestran su
    empty-state normal), nunca tumba el arranque."""
    try:
        fixtures = settings.ROOT_DIR / "tests" / "fixtures"
        pairs = (
            (fixtures / "demo_journal.json", settings.DATA_DIR / "journal_log.json"),
            (fixtures / "demo_labs.json", settings.DATA_DIR / "labs_log.json"),
            (fixtures / "demo_plan.json", settings.DATA_DIR / "plan_log.json"),
            (fixtures / "demo_reports.json", settings.DATA_DIR / "reports.json"),
        )
        for src, dst in pairs:
            if src.exists() and not dst.exists():
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    except Exception as exc:
        logger.warning("No pude precargar datos laterales de la demo: %s", exc)


@app.on_event("startup")
async def on_startup():
    settings.DATA_DIR.mkdir(exist_ok=True)

    if settings.DASHBOARD_TOKEN:
        logger.info("Dashboard auth ENABLED")

    if settings.VITALS_DEMO:
        # Fase 8A (paso A1): modo demo — HERMÉTICO. Nunca migra layout legacy
        # (eso tocaría data/ real vía userctx si DATA_DIR no fuera ya el
        # tempdir), nunca arranca el scheduler real (que dispararía sync/
        # llamadas HTTP reales al primer boot). Solo precarga journal/labs
        # sintéticos en el tempdir ya aislado.
        logger.warning(
            "VITALS_DEMO=1: sirviendo dataset sintético. Sync/auth/ingest "
            "deshabilitados; journal/labs/cycle escriben en un directorio "
            "efímero (%s), nunca en data/ real.", settings.DATA_DIR,
        )
        _seed_demo_side_data()
        return

    # Fase 8D (paso D3): migración automática de layout viejo (single-user) a
    # data/users/default/ — SIEMPRE antes de start_scheduler() (que ya asume
    # household: itera usuarios registrados). Idempotente — no-op si ya migró
    # o si es instalación fresh household desde cero. Nunca lanza, nunca
    # bloquea el arranque (ver userctx.migrate_legacy_layout_if_needed).
    migration_msg = _userctx.migrate_legacy_layout_if_needed()
    if migration_msg:
        logger.info(migration_msg)
    # Fase 8C (paso C6): INGEST_TOKEN ya SIEMPRE tiene un valor (autogenerado y
    # persistido por config.py si faltaba en .env) — el aviso de "hueco de
    # auth" de rondas anteriores ya no aplica. Si el token fue recién
    # autogenerado (primera vez), config.py ya logueó un warning propio
    # avisando que hay que copiarlo desde 'Más' antes de que HealthKit/ECG
    # puedan pushear de nuevo (401 hasta entonces).
    start_scheduler()


@app.on_event("shutdown")
async def on_shutdown():
    stop_scheduler()


# ---------------------------------------------------------------- middleware (Fase 8D, paso D3)

_USER_COOKIE_NAME = "vitals_user"


@app.middleware("http")
async def _userctx_middleware(request: Request, call_next):
    """Fija el contextvar de userctx.current_uid() para TODO el request,
    resuelto por header X-Vitals-User -> cookie vitals_user -> único usuario
    -> default (userctx.resolve_user). Se limpia SIEMPRE al final (finally),
    incluso si la ruta lanza — evita fugas de contexto entre requests que
    reusen el mismo worker/thread.

    Con un único usuario registrado (o ninguno — instalación fresh antes del
    primer /api/users POST), esto resuelve a "default" para TODOS los
    requests: comportamiento 100% equivalente al single-user de antes de esta
    fase, sin que el resto del código note la diferencia."""
    header_user = request.headers.get("X-Vitals-User")
    cookie_user = request.cookies.get(_USER_COOKIE_NAME)
    uid = _userctx.resolve_user(header_user=header_user, cookie_user=cookie_user)
    token = _userctx.set_current_uid(uid)
    try:
        response = await call_next(request)
    finally:
        _userctx.reset_current_uid(token)
    return response


# ---------------------------------------------------------------- middleware (R2 pre-publicación)

_DASHBOARD_COOKIE_NAME = "vitals_dash"

# Paths exentos del auth de dashboard (prefijos o exactos) — cada uno tiene su
# propio modelo de auth (o ninguno, por diseño: PWA estático / login mismo):
# - /login: la página para autenticarse, obviamente no puede exigir el token.
# - /static/, /manifest.webmanifest, /service-worker.js: assets estáticos de la PWA.
# - /api/ingest, /api/ecg (SOLO POST — ver abajo): ya exigen X-Vitals-Token propio
#   (secrets.compare_digest). Si quedaran detrás del dashboard-auth, la app iOS
#   dejaría de pushear en silencio (deuda 6B).
# - /api/v1/: API pública de solo lectura con su propia auth Bearer (API keys),
#   independiente del dashboard.
# NOTA: GET /api/ecg y GET /api/ecg/{uuid} NO tienen auth propia (confirmado
# leyendo main.py) — por eso NO están exentos aquí, quedan detrás del
# dashboard-auth como cualquier otra ruta de lectura.
_DASHBOARD_AUTH_EXEMPT_PREFIXES = (
    "/login",
    "/static/",
    "/manifest.webmanifest",
    "/service-worker.js",
    "/api/v1/",
)


def _dashboard_auth_exempt(path: str, method: str) -> bool:
    """True si el request no debe pasar por el auth de dashboard."""
    if path in ("/api/ingest",) and method == "POST":
        return True
    if path == "/api/ecg" and method == "POST":
        return True
    for prefix in _DASHBOARD_AUTH_EXEMPT_PREFIXES:
        if prefix.endswith("/"):
            if path == prefix[:-1] or path.startswith(prefix):
                return True
        elif path == prefix:
            return True
    return False


@app.middleware("http")
async def _dashboard_auth_middleware(request: Request, call_next):
    """R2 pre-publicación: auth OPT-IN del dashboard web. Registrado DESPUÉS de
    _userctx_middleware para que quede MÁS EXTERNO (Starlette corre el último
    middleware registrado primero) — el chequeo de auth ocurre ANTES de
    resolver usuario.

    Con settings.DASHBOARD_TOKEN vacío (default): call_next directo, cero
    overhead medible, cero cambio de comportamiento — byte-idéntico a antes de
    este middleware existir.

    Con token seteado: exige cookie `vitals_dash` o header
    `Authorization: Bearer <token>`, ambos comparados en bytes UTF-8 con
    secrets.compare_digest (NUNCA comparar str: TypeError con no-ASCII = DoS,
    lección 5D-B). Rutas exentas (ver _DASHBOARD_AUTH_EXEMPT_PREFIXES /
    _dashboard_auth_exempt): login, estáticos PWA, ingest/ecg POST (su propio
    X-Vitals-Token), /api/v1/ (su propia API key).

    Falla: GET / siempre redirige 303 a /login (es la ruta de dashboard/
    navegador por excelencia); cualquier otra ruta redirige 303 solo si pide
    HTML (Accept: text/html), y devuelve 401 JSON en cualquier otro caso
    (JSON/API)."""
    token = settings.DASHBOARD_TOKEN
    if not token:
        return await call_next(request)

    path = request.url.path
    method = request.method

    if _dashboard_auth_exempt(path, method):
        return await call_next(request)

    cookie_val = request.cookies.get(_DASHBOARD_COOKIE_NAME, "")
    auth_header = request.headers.get("Authorization", "")
    bearer_val = auth_header[7:] if auth_header.startswith("Bearer ") else ""

    authorized = (
        (cookie_val and secrets.compare_digest(cookie_val.encode("utf-8"), token.encode("utf-8")))
        or (bearer_val and secrets.compare_digest(bearer_val.encode("utf-8"), token.encode("utf-8")))
    )

    if authorized:
        return await call_next(request)

    # GET / (el dashboard raíz) SIEMPRE redirige a /login, sin depender del
    # header Accept — es la ruta que golpea un navegador (o un curl de
    # smoke-test sin headers); ver el plan de pruebas del ROADMAP
    # ("curl .../ -> 303"). Cualquier otra ruta decide por Accept: un cliente
    # que pide HTML explícito también recibe el redirect; todo lo demás
    # (JSON/API) recibe 401 JSON.
    accept = request.headers.get("accept", "")
    if path == "/" or "text/html" in accept:
        return RedirectResponse(url="/login", status_code=303)
    return JSONResponse({"detail": "dashboard token required"}, status_code=401)


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


@app.get("/login", include_in_schema=False)
async def login_page():
    """Página HTML mínima de login para el dashboard-auth opt-in — inline
    (no depende del template grande), bilingüe ES/EN estática por locale de
    perfil. Accesible siempre, incluso con DASHBOARD_TOKEN vacío (en ese caso
    simplemente no hace nada útil, pero no rompe)."""
    locale = _profile.effective("locale") or "es"
    return HTMLResponse(content=_render_login_page(locale), status_code=200)


@app.post("/login", include_in_schema=False)
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


# ---------------------------------------------------------------- rutas

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    dataset = _load_dataset()
    if not dataset:
        # Sin datos todavía (primer arranque / antes del primer sync): C2 (Fase
        # 8C) — skeleton shimmer en vez de un mensaje plano, mismo look Liquid
        # Glass que el resto de la app. Progressive enhancement puro HTML/CSS
        # (nada de JS que pueda fallar); los links de acción siguen presentes
        # abajo del shimmer por si el usuario necesita actuar manualmente.
        locale = _profile.effective("locale") or "es"
        from app.i18n import tr as _tr
        return HTMLResponse(
            "<html><body style='background:#07090e;color:#e6edf3;font-family:-apple-system,sans-serif;"
            "padding:24px;margin:0'>"
            "<style>"
            "@keyframes sk{0%{background-position:-200% 0}100%{background-position:200% 0}}"
            ".sk{border-radius:26px;height:110px;margin-bottom:14px;"
            "background:linear-gradient(90deg,rgba(255,255,255,.06) 25%,rgba(255,255,255,.14) 50%,"
            "rgba(255,255,255,.06) 75%);background-size:200% 100%;animation:sk 1.4s ease-in-out infinite;"
            "border:1px solid rgba(255,255,255,.1)}"
            ".sk.tall{height:170px}"
            "</style>"
            "<h1 style='font-size:22px;margin:6px 0 16px'>Vitals</h1>"
            "<div class='sk tall'></div><div class='sk'></div><div class='sk'></div>"
            f"<p style='color:rgba(235,235,245,.62);font-size:14px;margin-top:18px'>{_tr('skeleton_first_load', locale)}</p>"
            "<p style='margin-top:10px'><a href='/auth/login' style='color:#16c784'>Conectar Google Health</a> "
            "&middot; <a href='/api/sync' style='color:#16c784'>POST /api/sync</a></p>"
            "</body></html>",
            status_code=200,
        )
    auth_st = _active_source().auth_state()

    # Roadmap P2 paso 4/5: banner de reconexión inteligente + banner demo
    # honesto. Ambos flags viajan dentro de __AUTH__ (auth_st) para no tocar
    # la firma de render_ios/_inject_placeholders — el JS ya lee window.AUTH.
    # data_age_hours: antigüedad del dataset en disco (mtime de
    # health_compact.json del usuario activo), NO la fecha lógica del último
    # día con datos — es una señal de "¿hace cuánto corrió el último sync
    # exitoso?", que es lo que le importa a la regla del banner (roadmap:
    # "el server ya conoce last_updated del dataset"). None si no se puede
    # determinar (nunca tumba el dashboard); el JS trata None como "desconocida"
    # y no suprime el banner rojo (fail-safe: ante la duda, se muestra el
    # banner de siempre en vez de ocultar un problema real).
    auth_st = dict(auth_st)
    auth_st["is_demo"] = bool(settings.VITALS_DEMO)
    try:
        data_mtime = _data_path().stat().st_mtime
        auth_st["data_age_hours"] = round((time.time() - data_mtime) / 3600, 1)
    except Exception as e:
        logger.warning(f"No pude calcular data_age_hours (mtime de {_data_path()}): {e}")
        auth_st["data_age_hours"] = None

    locale = _profile.effective("locale") or "es"
    card = build_coach_card(dataset, locale=locale)

    # Frescura de Alertas + Coach (Paso 4): titular del Coach. get_headline()
    # SOLO lee data/coach_headline.json (o cae al fallback determinista) — CERO
    # subprocess en este path. detect_changes() es puro/determinista (sin I/O
    # de red ni CLI), igual que evaluate_insights() más abajo; se reusa aquí
    # únicamente para poder construir el fallback si no hay cache aún.
    # Envuelto en try/except: un fallo nunca debe tumbar el dashboard ni dejar
    # el nodo #coachHeadline con contenido roto (card.headline queda "" y el
    # template lo trata como "sin headline" -> no renderiza el nodo).
    try:
        _change_events_for_headline = _changes.detect_changes(dataset, locale)
        card["headline"] = _coach_headline.get_headline(dataset, _change_events_for_headline, locale)
    except Exception as e:
        logger.error(f"coach_headline.get_headline falló en dashboard: {e}")
        card["headline"] = ""

    # Fase 7: estado de ciclo (salud femenina, opt-in). Envuelto en try/except,
    # nunca rompe el dashboard — un fallo del módulo de ciclo degrada a None
    # (equivalente a toggle apagado), NO tumba insights, __CYCLE__ ni el resto
    # de la ruta. cycle_state se reusa tanto para insights (_cycle inyectado en
    # el dataset) como para el placeholder __CYCLE__ del template.
    dataset_with_cycle = dataset
    cycle_state = None
    try:
        cycle_profile = effective_profile_dict()
        if cycle_profile.get("cycle_tracking"):
            cycle_log = _cycle.load_cycle_log()
            cycle_state = _cycle.compute_cycle_state(dataset.get("days", []), cycle_log, cycle_profile)
            dataset_with_cycle = dict(dataset)
            dataset_with_cycle["_cycle"] = cycle_state
    except Exception as e:
        logger.error(f"compute_cycle_state falló en dashboard: {e}")

    insights = evaluate_insights(dataset_with_cycle, locale=locale)

    # Tier 3: drivers (palancas) — envuelto en try/except, nunca rompe el dashboard
    drivers = []
    try:
        from app.drivers import analyze_drivers
        drivers = analyze_drivers(dataset.get("days", []), locale=locale)
    except Exception as e:
        logger.error(f"analyze_drivers falló en dashboard: {e}")

    # Tier 2: trends (tendencias 30d) para recovery/hrv/rhr/sueño
    trends = {}
    try:
        from app.trends import trend_summary
        last30 = dataset.get("days", [])[-30:]
        for metric in ["recovery", "hrv", "rhr", "asleep"]:
            vals = [d.get(metric) for d in last30]
            trends[metric] = trend_summary(vals)
    except Exception as e:
        logger.error(f"trend_summary falló en dashboard: {e}")

    profile = effective_profile_dict()
    html = render_ios(dataset, card, auth_st, insights, drivers, trends, profile, cycle_state)
    return HTMLResponse(content=html, status_code=200)


@app.get("/api/insights")
async def api_insights():
    """Devuelve la lista de insights evaluados sobre el dataset actual.
    Si no hay datos → [] (nunca 500)."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content=[])
    try:
        locale = _profile.effective("locale") or "es"

        # Fase 7: estado de ciclo (opt-in). Mismo patrón try/except que en /.
        dataset_with_cycle = dataset
        try:
            cycle_profile = effective_profile_dict()
            if cycle_profile.get("cycle_tracking"):
                cycle_log = _cycle.load_cycle_log()
                cycle_state = _cycle.compute_cycle_state(dataset.get("days", []), cycle_log, cycle_profile)
                dataset_with_cycle = dict(dataset)
                dataset_with_cycle["_cycle"] = cycle_state
        except Exception as e:
            logger.error(f"compute_cycle_state falló en /api/insights: {e}")

        return JSONResponse(content=evaluate_insights(dataset_with_cycle, locale=locale))
    except Exception as e:
        logger.error(f"evaluate_insights falló: {e}")
        return JSONResponse(content=[])


@app.get("/api/coach/suggestions")
async def api_coach_suggestions(locale: Optional[str] = None):
    """Preguntas sugeridas (chips) del tab Coach — F1 del roadmap P0.

    Devuelve {questions: [{id, text}]}, derivadas de los insights activos del
    dataset actual (mismo dataset/household que /api/insights — resuelto por
    el middleware de userctx vía X-Vitals-User) con fallback al pool genérico.
    Nunca 500: sin datos -> lista de genéricas (coach_suggest ya es None-safe).
    """
    dataset = _load_dataset()
    try:
        resolved_locale = locale or _profile.effective("locale") or "es"
        questions = _suggested_questions(dataset or {}, locale=resolved_locale, limit=4)
        return JSONResponse(content={"questions": questions})
    except Exception as e:
        logger.error(f"suggested_questions falló: {e}")
        return JSONResponse(content={"questions": []})


@app.get("/api/drivers")
async def api_drivers():
    """Devuelve los drivers (palancas) con correlación de Spearman rezagada.
    Findings filtrados: n>=25, significativos, |ρ|>=0.2; ordenados por |ρ| desc.
    Si no hay datos o ningún driver pasa el filtro → [] (nunca 500)."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content=[])
    try:
        from app.drivers import analyze_drivers
        locale = _profile.effective("locale") or "es"
        return JSONResponse(content=analyze_drivers(dataset.get("days", []), locale=locale))
    except Exception as e:
        logger.error(f"analyze_drivers falló: {e}")
        return JSONResponse(content=[])


@app.get("/api/report")
async def api_report(period: str = "weekly"):
    """Informe narrativo del último período COMPLETO (weekly|monthly): narrativa
    cacheada (generada por el claude CLI SOLO en run_sync) o fallback determinista
    de datos. Nunca 500, nunca llama al CLI en este path (Fase 8B, paso B6).

    Roadmap P2 (F8, paso 5): para period=monthly, el campo `data` gana la
    clave ADITIVA `sleep_archetype` (null si el gate de >=14 noches no se
    cumple) — calculada ON-READ desde el dataset actual (sleep_archetype.py
    es puro, <1ms sobre el dataset ya cargado en memoria), NUNCA desde el
    cache de report.py (que solo se regenera en sync) — así el arquetipo
    siempre refleja el último mes completo real, no el momento del último
    sync. El resto del shape de get_report() NO cambia."""
    if period not in ("weekly", "monthly"):
        return JSONResponse(
            content={"status": "error", "message": "period debe ser 'weekly' o 'monthly'"},
            status_code=422,
        )
    try:
        locale = _profile.effective("locale") or "es"
        payload = _report.get_report(period, locale=locale)
        if period == "monthly" and isinstance(payload.get("data"), dict):
            try:
                from app import sleep_archetype as _sleep_archetype
                dataset = _load_dataset()
                days = (dataset or {}).get("days") or []
                payload["data"]["sleep_archetype"] = _sleep_archetype.classify_month(days, locale=locale)
            except Exception as e:
                logger.warning(f"sleep_archetype.classify_month falló en /api/report: {e}")
                payload["data"]["sleep_archetype"] = None
        return JSONResponse(content=payload)
    except Exception as e:
        logger.error(f"GET /api/report falló: {e}")
        return JSONResponse(content={
            "period": period, "start": None, "end": None,
            "narrative": "", "data": None, "has_narrative": False,
        })


# Fase 9 (paso A2): GET /api/data y GET /api/export viven ahora en
# app/routes/export.py. _csv_safe se re-importa aquí con el MISMO nombre
# porque tests/test_export.py hace `from main import _csv_safe` (import
# directo por nombre) — debe seguir resolviendo a la misma función.
from app.routes.export import router as _export_router, _csv_safe  # noqa: E402
app.include_router(_export_router)


@app.post("/api/sync")
async def api_sync():
    """Dispara un sync bajo demanda. Sin token válido: responde con estado controlado, no 500.
    Si ya hay un sync en curso (single-flight, Ronda 1): {status: "already_running"}."""
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    from app.sync import run_sync, SyncInProgress
    try:
        dataset = await run_in_threadpool(run_sync)
        return JSONResponse({"status": "ok", "n_days": dataset["summary"]["n_days"]})
    except TokenExpired:
        return JSONResponse(
            {"status": "expired", "message": "Token expirado. Visita /auth/login para reconectar."},
            status_code=200,
        )
    except NoToken:
        return JSONResponse(
            {"status": "no_token", "message": "No hay token. Visita /auth/login para autorizar."},
            status_code=200,
        )
    except SyncInProgress:
        # ANTES del except Exception genérico — si no, caería como "error".
        return JSONResponse(
            {"status": "already_running", "message": "Ya hay un sync en curso."},
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Sync falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


@app.post("/api/ingest")
async def api_ingest(request: Request):
    """Ingestión PUSH de HealthKit (Fase 5D-A). La app nativa iOS (Fase 5D-B) lee
    HealthKit on-device y empuja aquí el payload normalizado.

    🔴 GUARD de fuente (Fase 6A): solo aplica el ingest si 'healthkit' está en las
    fuentes CONECTADAS del perfil (profile.effective_sources()) — ya NO exige que sea
    la única fuente activa. Si no está conectada, responde
    {status:'wrong_source', active:<source>} con HTTP 200 y NO sobrescribe
    health_compact.json — protege a usuarios que no conectaron HealthKit de un push
    accidental.

    Nunca 500: payload roto / cualquier error → {status:'error', message} con 200.
    """
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    # ── Auth: secreto compartido, SIEMPRE obligatorio (Fase 8C, paso C6). ──
    # settings.INGEST_TOKEN nunca está vacío desde C6 (config.py autogenera y
    # persiste uno si falta en .env) — ya NO existe el modo permisivo de fases
    # anteriores. 401 SIEMPRE que el header no coincida byte a byte.
    expected = settings.INGEST_TOKEN
    provided = request.headers.get("X-Vitals-Token", "")
    # Comparar en bytes (UTF-8): secrets.compare_digest sobre str lanza TypeError
    # si algún arg trae caracteres no-ASCII (un header latin-1 malformado podría
    # forzar un 500). En bytes es timing-safe y nunca lanza.
    if not expected or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    # Parseo manual del body (un JSON roto da 'error' controlado, no 422/500).
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "JSON inválido."}, status_code=200)

    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "error", "message": "El payload debe ser un objeto JSON."},
            status_code=200,
        )

    # GUARD (Fase 6A): 'healthkit' debe estar entre las fuentes CONECTADAS.
    sources = _profile.effective_sources()
    if "healthkit" not in sources:
        active = sources[0] if sources else (_profile.effective("source") or "google_health")
        return JSONResponse({"status": "wrong_source", "active": active}, status_code=200)

    try:
        from app.sources.healthkit import HealthKitSource
        from app.sync import run_sync, SyncInProgress

        hk = HealthKitSource()
        hk.ingest(payload)  # guarda el crudo en healthkit_ingest.json; fetch() lo reusará.
        # run_sync() re-consulta TODAS las fuentes conectadas (incluye el healthkit recién
        # ingerido, vía HealthKitSource.fetch() que reusa el último payload) y las funde —
        # mismo motor que usa /api/sync. Evita reimplementar merge/bodyage aquí (DRY).
        # Offload a threadpool: run_sync() hace llamadas HTTP síncronas bloqueantes
        # (requests) — sin esto, congela el event loop entero mientras corre.
        dataset = await run_in_threadpool(run_sync)
        return JSONResponse({"status": "ok", "n_days": dataset["summary"]["n_days"]})
    except SyncInProgress:
        # ANTES del except Exception genérico. El payload YA quedó guardado por
        # hk.ingest() (arriba) — no se pierde: HealthKitSource.fetch() lo reusa.
        return JSONResponse(
            {"status": "already_running",
             "message": "Payload guardado; se integrará en el sync en curso o el siguiente."},
            status_code=200,
        )
    except Exception as e:
        logger.error(f"/api/ingest falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


@app.post("/api/ecg")
async def api_ecg_post(request: Request):
    """Ingestión PUSH de lecturas de ECG (HKElectrocardiogram) desde la app nativa.

    VISOR AISLADO: este endpoint y app/ecg_store.py son la ÚNICA vía de entrada/
    salida de data/ecg/. Los voltajes NO tocan health_compact.json, build_dataset,
    scoring, bodyage, merge ni el contexto del coach — ver ROADMAP-vitals-ecg.md.

    Auth: mismo patrón que /api/ingest — header X-Vitals-Token comparado en
    bytes con secrets.compare_digest contra INGEST_TOKEN. SIEMPRE obligatorio
    desde Fase 8C (paso C6) — settings.INGEST_TOKEN nunca está vacío (se
    autogenera en config.py si falta en .env).

    Payload mínimo: {uuid, date, classification, avg_hr, sampling_frequency,
    sample_count, symptoms_status, voltages:[float µV]}. Solo `uuid` es obligatorio;
    todo lo demás es best-effort / None-safe (ver ecg_store._clean_voltages).

    Idempotente por uuid (mismo uuid sobreescribe, no duplica). Nunca 500 — JSON
    roto o payload inválido responden {status:'error', message} con 200.
    """
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    expected = settings.INGEST_TOKEN
    provided = request.headers.get("X-Vitals-Token", "")
    if not expected or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "JSON inválido."}, status_code=200)

    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "error", "message": "El payload debe ser un objeto JSON."},
            status_code=200,
        )

    try:
        result = ecg_store.save_ecg(payload)
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error(f"/api/ecg (POST) falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


@app.get("/api/ecg")
async def api_ecg_list():
    """Lista LIGERA de lecturas de ECG (sin voltajes), ordenada por fecha desc.
    Sin lecturas -> []. Nunca 500 (una meta corrupta se omite, no tumba el listado)."""
    try:
        return JSONResponse(content=ecg_store.list_ecg())
    except Exception as e:
        logger.error(f"GET /api/ecg falló: {e}")
        return JSONResponse(content=[])


@app.get("/api/ecg/{uuid}")
async def api_ecg_get(uuid: str):
    """Meta + voltajes completos de una lectura de ECG, para el visor de la tira.
    UUID inexistente -> 404 controlado."""
    try:
        result = ecg_store.get_ecg(uuid)
    except Exception as e:
        logger.error(f"GET /api/ecg/{uuid} falló: {e}")
        result = None
    if result is None:
        raise HTTPException(status_code=404, detail="Lectura de ECG no encontrada.")
    return JSONResponse(content=result)


@app.get("/auth/login")
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


# Fase 9 (paso A1): modelos Pydantic de request movidos a
# app/routes/_models.py (pegamento compartido entre routers). Importados aquí
# TAL CUAL, mismos nombres — ningún test los referencia por import directo de
# main, así que no hace falta shim de compat adicional.
from app.routes._models import (  # noqa: E402
    CoachRequest,
    ConversationCreate,
    ProfileUpdate,
    CyclePeriodCreate,
    CycleSymptomCreate,
    JournalUpdate,
    JournalCustomCreate,
    PlanStart,
    PlanCheck,
    LabEntryCreate,
    UserCreate,
    ApiKeyCreate,
)

_CLINICAL_FIELDS = ("goals", "injuries", "conditions", "medications")
_CLINICAL_MAX_ITEMS = 10
_CLINICAL_MAX_LEN = 120


def _clean_str_list(v: Any) -> list[str]:
    """Valida y normaliza una lista de strings del intake clínico (goals/injuries/
    conditions/medications). Acepta SOLO una lista de strings: trimea cada item,
    filtra vacíos, corta a _CLINICAL_MAX_ITEMS items de máx _CLINICAL_MAX_LEN chars.

    Cualquier otra cosa (no-lista, o lista con items no-string) → ValueError con
    mensaje controlado, para que el caller lo capture y devuelva 422 (nunca 500).
    """
    if not isinstance(v, list):
        raise ValueError("debe ser una lista de strings")
    out = []
    for item in v:
        if not isinstance(item, str):
            raise ValueError("cada elemento debe ser texto")
        s = item.strip()
        if not s:
            continue
        out.append(s[:_CLINICAL_MAX_LEN])
        if len(out) >= _CLINICAL_MAX_ITEMS:
            break
    return out


@app.get("/api/profile")
async def api_profile_get():
    """Devuelve el perfil efectivo (cascada: profile.json → .env → defaults).
    Nunca 500: si no hay profile.json devuelve los valores efectivos actuales."""
    try:
        return JSONResponse(content=effective_profile_dict())
    except Exception as e:
        logger.error(f"GET /api/profile falló: {e}")
        return JSONResponse(content={})


@app.put("/api/profile")
async def api_profile_put(body: ProfileUpdate):
    """Actualiza el perfil con validación. Escritura atómica. Nunca 500.

    Validaciones:
    - birthdate: ISO 8601 (YYYY-MM-DD), opcional
    - sex: 'M' o 'F', opcional
    - waist_cm: > 0, opcional
    - sleep_target_min: entero 300-600 (minutos), opcional (Ronda 5)
    - steps_target: entero 1000-50000 (pasos), opcional
    - locale: 'es', 'en', 'fr' o 'pt', opcional
    - units: 'metric' o 'imperial', opcional
    - source: 'google_health', 'oura', 'whoop' o 'healthkit', opcional
    - goals/injuries/conditions/medications: lista de strings, opcional (Ronda 4).
      Cada item se trimea, se descartan vacíos, máx 10 items x 120 chars.
    """
    errors = []

    if body.birthdate is not None:
        try:
            import datetime as _dt
            _dt.date.fromisoformat(body.birthdate)
        except ValueError:
            errors.append("birthdate debe ser ISO 8601 (YYYY-MM-DD)")

    if body.sex is not None and body.sex not in ("M", "F"):
        errors.append("sex debe ser 'M' o 'F'")

    if body.waist_cm is not None and body.waist_cm <= 0:
        errors.append("waist_cm debe ser > 0")

    if body.sleep_target_min is not None and not (300 <= body.sleep_target_min <= 600):
        errors.append("sleep_target_min debe estar entre 300 y 600 (minutos)")

    if body.steps_target is not None and not (1000 <= body.steps_target <= 50000):
        errors.append("steps_target debe estar entre 1000 y 50000 (pasos)")

    if body.locale is not None and body.locale not in ("es", "en", "fr", "pt"):
        errors.append("locale debe ser 'es', 'en', 'fr' o 'pt'")

    if body.units is not None and body.units not in ("metric", "imperial"):
        errors.append("units debe ser 'metric' o 'imperial'")

    if body.source is not None and body.source not in _KNOWN_SOURCES:
        errors.append("source debe ser 'google_health', 'oura', 'whoop' o 'healthkit'")

    if body.sources is not None:
        if not isinstance(body.sources, list) or any(s not in _KNOWN_SOURCES for s in body.sources):
            errors.append("sources debe ser una lista de 'google_health', 'oura', 'whoop' y/o 'healthkit'")

    # Ronda 4: intake clínico — cada campo, si viene, debe ser lista de strings
    # (≤10 items × ≤120 chars). Errores controlados, nunca 500.
    _clinical_clean: dict = {}
    for field in _CLINICAL_FIELDS:
        raw = getattr(body, field)
        if raw is not None:
            try:
                _clinical_clean[field] = _clean_str_list(raw)
            except ValueError as e:
                errors.append(f"{field} {e}")

    # Fase 8C (paso C3): notifications — dict con subcampos conocidos, MERGE
    # parcial sobre el existente (no un replace total: togglear morning_brief
    # no debe borrar un ntfy_url ya configurado). Cualquier otra cosa (no-dict,
    # subcampo de tipo raro) -> 422 controlado, nunca 500.
    _NOTIFY_STR_FIELDS = ("ntfy_url", "telegram_bot_token", "telegram_chat_id")
    _NOTIFY_BOOL_FIELDS = ("morning_brief", "alerts")
    _notify_clean: Optional[dict] = None
    if body.notifications is not None:
        if not isinstance(body.notifications, dict):
            errors.append("notifications debe ser un objeto")
        else:
            _notify_clean = {}
            for k, v in body.notifications.items():
                if k in _NOTIFY_STR_FIELDS:
                    if not isinstance(v, str):
                        errors.append(f"notifications.{k} debe ser texto")
                        continue
                    _notify_clean[k] = v.strip()[:300]
                elif k in _NOTIFY_BOOL_FIELDS:
                    if not isinstance(v, bool):
                        errors.append(f"notifications.{k} debe ser booleano")
                        continue
                    _notify_clean[k] = v
                # claves desconocidas se ignoran silenciosamente (forward-compat)

    if errors:
        return JSONResponse(
            content={"status": "error", "errors": errors},
            status_code=422,
        )

    try:
        # Merge: solo los campos enviados (no None en el body)
        update_fields = body.model_dump(exclude_none=True)
        # Sobrescribir los campos clínicos con su versión YA validada/limpia
        # (model_dump traería la lista cruda sin trim/cap).
        update_fields.update(_clinical_clean)

        existing = load_profile()
        # PUT sin campos efectivos: no crear/sobrescribir un profile.json basura.
        # Si ya existía perfil, lo dejamos intacto; si no, no escribimos nada.
        if not update_fields and _notify_clean is None:
            return JSONResponse(content=effective_profile_dict())

        # notifications: leer el valor YA persistido ANTES de mutar `current`
        # más abajo. current = existing (misma referencia, no copia) cuando
        # existing no es None -> current.update(update_fields) mutaría
        # existing["notifications"] in-place SI notifications viniera crudo
        # dentro de update_fields, corrompiendo la lectura de "lo ya
        # guardado" (bug real cazado con test_put_notifications_partial_
        # update_merges: togglear morning_brief borraba un ntfy_url ya
        # guardado). Por eso: (1) leer existing_notify PRIMERO, (2) sacar
        # 'notifications' de update_fields para que el .update() genérico de
        # abajo no la toque en absoluto — el MERGE parcial es el único que
        # escribe esa clave.
        existing_notify = (existing or {}).get("notifications")
        update_fields.pop("notifications", None)

        current = existing or {}
        current.update(update_fields)

        # notifications: MERGE parcial sobre el dict ya persistido (o los
        # defaults si no había), NO el replace total que haría un .update()
        # genérico (togglear morning_brief no debe borrar un ntfy_url ya
        # guardado).
        if _notify_clean is not None:
            base_notify = dict(existing_notify) if isinstance(existing_notify, dict) else dict(_profile.effective_notifications())
            base_notify.update(_notify_clean)
            current["notifications"] = base_notify

        save_profile(current)
        return JSONResponse(content=effective_profile_dict())
    except Exception as e:
        logger.error(f"PUT /api/profile falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error guardando perfil"},
            status_code=500,
        )


@app.post("/api/sources/{name}")
async def api_sources_connect(name: str):
    """Fase 6A: conecta una fuente (añade a profile.sources si no está, sin duplicar).
    NO desconecta las demás — un usuario puede tener varias fuentes conectadas a la vez.
    Valida `name` contra las 4 fuentes conocidas (404 si no reconoce), nunca 500."""
    if name not in _KNOWN_SOURCES:
        raise HTTPException(status_code=404, detail=f"Fuente desconocida: '{name}'.")
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    try:
        current_sources = _profile.effective_sources()
        if name not in current_sources:
            current_sources = current_sources + [name]
        current = load_profile() or {}
        current["sources"] = current_sources
        save_profile(current)
        return JSONResponse({"status": "ok", "sources": current_sources})
    except Exception as e:
        logger.error(f"POST /api/sources/{name} falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error guardando fuente"},
            status_code=500,
        )


@app.delete("/api/sources/{name}")
async def api_sources_disconnect(name: str):
    """Fase 6A: desconecta una fuente (quita de profile.sources si está; idempotente,
    no error si ya no estaba). NO borra el token guardado — permite reconectar sin
    re-autorizar si sigue vigente. Valida `name` contra las 4 fuentes conocidas
    (404 si no reconoce), nunca 500."""
    if name not in _KNOWN_SOURCES:
        raise HTTPException(status_code=404, detail=f"Fuente desconocida: '{name}'.")
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    try:
        current_sources = [s for s in _profile.effective_sources() if s != name]
        current = load_profile() or {}
        current["sources"] = current_sources
        save_profile(current)
        return JSONResponse({"status": "ok", "sources": current_sources})
    except Exception as e:
        logger.error(f"DELETE /api/sources/{name} falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error guardando fuente"},
            status_code=500,
        )


@app.get("/api/sources")
async def api_sources_status():
    """Fase 6B: estado de las 4 fuentes conocidas para la UI de gestión de conexiones.
    Nunca 500 — una fuente que falle en auth_state() reporta status:'error', no tumba el resto."""
    connected = set(_profile.effective_sources())
    out = {}
    for name in _KNOWN_SOURCES:
        try:
            st = get_source(name).auth_state()
        except Exception as e:
            logger.error(f"auth_state() de '{name}' falló: {e}")
            st = {"status": "error"}
        out[name] = {"connected": name in connected, **st}
    return JSONResponse(out)


def _cycle_tracking_enabled() -> bool:
    """True si el toggle opt-in de ciclo está prendido. Nunca lanza."""
    try:
        return bool(_profile.effective("cycle_tracking"))
    except Exception:
        return False


@app.get("/api/cycle")
async def api_cycle_get():
    """Estado de ciclo (fase, predicción, ventana fértil, retraso, peri/meno).
    Con cycle_tracking=False (default) -> {enabled: false}, SIN fuga de datos
    de ciclo (criterio #1 del roadmap de salud femenina). Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"enabled": False})
    try:
        dataset = _load_dataset() or {}
        cycle_log = _cycle.load_cycle_log()
        profile = effective_profile_dict()
        state = _cycle.compute_cycle_state(dataset.get("days", []), cycle_log, profile)
        return JSONResponse(content=state or {"enabled": False})
    except Exception as e:
        logger.error(f"GET /api/cycle falló: {e}")
        return JSONResponse(content={"enabled": False})


@app.post("/api/cycle/period")
async def api_cycle_period_post(body: CyclePeriodCreate):
    """Añade/actualiza un inicio de periodo (de-dupe por 'start'). Validación de
    fechas ISO controlada. Gateado por cycle_tracking. Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"status": "disabled"}, status_code=403)

    try:
        import datetime as _dtm
        _dtm.date.fromisoformat(body.start)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "start debe ser fecha ISO 8601 (YYYY-MM-DD)"},
            status_code=422,
        )
    if body.end is not None:
        try:
            _dtm.date.fromisoformat(body.end)
        except (ValueError, TypeError):
            return JSONResponse(
                content={"status": "error", "message": "end debe ser fecha ISO 8601 (YYYY-MM-DD)"},
                status_code=422,
            )

    try:
        log = _cycle.load_cycle_log()
        periods = [p for p in log.get("periods", []) if isinstance(p, dict) and p.get("start") != body.start]
        periods.append({
            "start": body.start,
            "end": body.end,
            "flow": body.flow,
            "source": "manual",
        })
        log["periods"] = periods
        _cycle.save_cycle_log(log)
        return JSONResponse(content={"status": "ok", "periods": periods})
    except Exception as e:
        logger.error(f"POST /api/cycle/period falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error guardando periodo"}, status_code=200)


@app.delete("/api/cycle/period/{start}")
async def api_cycle_period_delete(start: str):
    """Borra un evento de periodo por su fecha de inicio. Idempotente (no error
    si no existía). Gateado por cycle_tracking. Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"status": "disabled"}, status_code=403)
    try:
        log = _cycle.load_cycle_log()
        periods = [p for p in log.get("periods", []) if isinstance(p, dict) and p.get("start") != start]
        log["periods"] = periods
        _cycle.save_cycle_log(log)
        return JSONResponse(content={"status": "ok", "periods": periods})
    except Exception as e:
        logger.error(f"DELETE /api/cycle/period/{start} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error borrando periodo"}, status_code=200)


@app.post("/api/cycle/symptom")
async def api_cycle_symptom_post(body: CycleSymptomCreate):
    """Registra síntomas para una fecha (tags de lista libre, reusa _clean_str_list:
    cap 10x120 chars). Validación de fecha ISO controlada. Gateado. Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"status": "disabled"}, status_code=403)

    try:
        import datetime as _dtm
        _dtm.date.fromisoformat(body.date)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "date debe ser fecha ISO 8601 (YYYY-MM-DD)"},
            status_code=422,
        )

    tags = body.tags if body.tags is not None else []
    try:
        clean_tags = _clean_str_list(tags)
    except ValueError as e:
        return JSONResponse(content={"status": "error", "errors": [f"tags {e}"]}, status_code=422)

    try:
        log = _cycle.load_cycle_log()
        symptoms = [s for s in log.get("symptoms", []) if isinstance(s, dict)]
        symptoms.append({
            "date": body.date,
            "tags": clean_tags,
            "note": (body.note or "")[:500],
            "source": "manual",
        })
        log["symptoms"] = symptoms
        _cycle.save_cycle_log(log)
        return JSONResponse(content={"status": "ok", "symptoms": symptoms})
    except Exception as e:
        logger.error(f"POST /api/cycle/symptom falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error guardando síntoma"}, status_code=200)


# Fase 9 (paso A2): /api/journal* vive ahora en app/routes/journal.py.
from app.routes.journal import router as _journal_router  # noqa: E402
app.include_router(_journal_router)


@app.get("/api/sleep-coach")
async def api_sleep_coach_get():
    """Recomendación de hora de dormir para esta noche (Fase 8C, paso C4).
    Sin datos suficientes (poco historial de wake time) -> {available: false}
    (nunca 500).

    Roadmap P1 F5 (paso 2): campos ADITIVOS `need_min/sleep_score/consistency`
    — el shape previo (bedtime/wake_assumed/extra_min/need_min/drivers) se
    conserva IDÉNTICO; sleep_score/consistency son None-safe y se calculan
    on-read desde app/sleep_scores.py (nunca tocan build_dataset)."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content={"available": False})
    try:
        from app import sleep_coach as _sleep_coach
        from app import sleep_scores as _sleep_scores
        days = dataset.get("days", [])
        summary = dataset.get("summary", {})
        profile = effective_profile_dict()
        rec = _sleep_coach.recommend_bedtime(days, summary, profile)
        if rec is None:
            return JSONResponse(content={"available": False})
        rec["available"] = True

        # Aditivo (F5): need_min ya viene de recommend_bedtime (misma
        # fórmula) — sleep_score/consistency se derivan aparte, None-safe.
        today = days[-1] if days and isinstance(days[-1], dict) else {}
        rec["sleep_score"] = _sleep_scores.sleep_score(today.get("asleep"), rec.get("need_min"))
        rec["consistency"] = _sleep_scores.consistency_score(days)
        return JSONResponse(content=rec)
    except Exception as e:
        logger.error(f"GET /api/sleep-coach falló: {e}")
        return JSONResponse(content={"available": False})


# ---------------------------------------------------------------- programas del coach (Roadmap P1, F4)

@app.get("/api/programs")
async def api_programs_get():
    """Catálogo localizado de los 4 programas plantilla (Roadmap P1, F4, paso
    6). Nunca 500 — degrada a lista vacía si algo falla."""
    try:
        from app import programs as _programs
        locale = _profile.effective("locale") or "es"
        return JSONResponse(content=_programs.get_catalog(locale))
    except Exception as e:
        logger.error(f"GET /api/programs falló: {e}")
        return JSONResponse(content=[])


@app.get("/api/plan")
async def api_plan_get():
    """Estado del plan activo del usuario: día N/M, tarea de hoy (adaptada),
    adherencia % (Roadmap P1, F4, paso 6). Sin plan activo -> {active: null}.
    Respeta X-Vitals-User (household) vía plan_store/userctx. Nunca 500."""
    try:
        from app import plan_store as _plan_store
        locale = _profile.effective("locale") or "es"
        dataset = _load_dataset() or {}
        status = _plan_store.plan_status(dataset, locale=locale)
        if status is None:
            return JSONResponse(content={"active": False})
        status["active"] = True
        return JSONResponse(content=status)
    except Exception as e:
        logger.error(f"GET /api/plan falló: {e}")
        return JSONResponse(content={"active": False})


@app.post("/api/plan")
async def api_plan_post(body: PlanStart):
    """Inicia un programa del catálogo. 409 si ya hay uno activo (un solo
    plan activo a la vez — criterio 3 del roadmap). 422 si program_id no
    existe en el catálogo. Demo-safe: en VITALS_DEMO=1 el write cae al
    layout efímero de la demo (mismo mecanismo que journal — settings.DATA_DIR
    ya es un tempdir en demo, ver app/config.py). Nunca 500."""
    try:
        from app import programs as _programs
        from app import plan_store as _plan_store

        if not _programs.program_exists(body.program_id):
            return JSONResponse(
                content={"status": "error", "message": f"program_id desconocido: '{body.program_id}'"},
                status_code=422,
            )
        if _plan_store.has_active_plan():
            return JSONResponse(
                content={"status": "error", "message": "Ya hay un plan activo. Abandónalo antes de iniciar otro."},
                status_code=409,
            )
        active = _plan_store.start_plan(body.program_id)
        if active is None:
            return JSONResponse(
                content={"status": "error", "message": "No se pudo iniciar el plan."},
                status_code=409,
            )
        return JSONResponse(content={"status": "ok", "active": active})
    except Exception as e:
        logger.error(f"POST /api/plan falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error iniciando el plan"}, status_code=200)


@app.delete("/api/plan")
async def api_plan_delete():
    """Abandona el plan activo (pasa a history). Idempotente: sin plan activo,
    devuelve ok igual (nada que abandonar). Nunca 500."""
    try:
        from app import plan_store as _plan_store
        _plan_store.abandon_plan()
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/plan falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error abandonando el plan"}, status_code=200)


@app.post("/api/plan/check")
async def api_plan_check_post(body: PlanCheck):
    """Marca el día dado (default hoy) como cumplido MANUAL — sobreescribe
    cualquier evaluación auto para ese día. 422 con fecha inválida o futura,
    404 si no hay plan activo. Nunca 500."""
    try:
        from app import plan_store as _plan_store

        date_str = body.date or _dt.date.today().isoformat()
        try:
            parsed = _dt.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return JSONResponse(
                content={"status": "error", "message": "date debe ser fecha ISO 8601 (YYYY-MM-DD)"},
                status_code=422,
            )
        if parsed > _dt.date.today():
            return JSONResponse(
                content={"status": "error", "message": "date no puede ser futura"},
                status_code=422,
            )
        if not _plan_store.has_active_plan():
            return JSONResponse(
                content={"status": "error", "message": "No hay plan activo."},
                status_code=404,
            )
        active = _plan_store.manual_check(date_str)
        if active is None:
            return JSONResponse(
                content={"status": "error", "message": "No se pudo marcar el día."},
                status_code=200,
            )
        return JSONResponse(content={"status": "ok", "active": active})
    except Exception as e:
        logger.error(f"POST /api/plan/check falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error marcando el día"}, status_code=200)


# ---------------------------------------------------------------- labs (Fase 8D, paso D1)

@app.get("/api/labs")
async def api_labs_get():
    """Catálogo localizado + series por marcador (con flag out_of_range) +
    últimas tomas. Nunca 500 — datos ilegibles degradan a catálogo con series
    vacías."""
    try:
        locale = _profile.effective("locale") or "es"
        sex = _profile.effective("sex")
        labs = _labs.load_labs()
        series = _labs.series_by_marker(labs)
        return JSONResponse(content={
            "catalog": _labs.catalog(locale=locale, sex=sex),
            "series": series,
        })
    except Exception as e:
        logger.error(f"GET /api/labs falló: {e}")
        return JSONResponse(content={"catalog": [], "series": {}})


@app.post("/api/labs")
async def api_labs_post(body: LabEntryCreate):
    """Alta manual de una toma de laboratorio. Valida fecha ISO, marcador
    contra el catálogo y value numérico. Nunca 500."""
    try:
        import datetime as _dtm
        _dtm.date.fromisoformat(body.date)
    except (ValueError, TypeError):
        return JSONResponse(
            content={"status": "error", "message": "date debe ser fecha ISO 8601 (YYYY-MM-DD)"},
            status_code=422,
        )
    if body.marker not in _labs.MARKER_KEYS:
        return JSONResponse(
            content={"status": "error", "message": f"marcador desconocido: {body.marker!r}"},
            status_code=422,
        )
    try:
        sex = _profile.effective("sex")
        entry = _labs.add_entry(body.date, body.marker, body.value, unit=body.unit, note=body.note, sex=sex)
        if entry is None:
            return JSONResponse(
                content={"status": "error", "message": "no se pudo guardar la entrada"},
                status_code=422,
            )
        return JSONResponse(content={"status": "ok", "entry": entry})
    except Exception as e:
        logger.error(f"POST /api/labs falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error guardando laboratorio"}, status_code=200)


@app.post("/api/labs/import")
async def api_labs_import(request: Request):
    """Import CSV tolerante (date,marker,value[,unit][,note]). Filas
    rechazadas se reportan con motivo, no abortan el import. Body: texto
    plano CSV (text/csv o form con campo 'file'). Nunca 500."""
    try:
        content_type = request.headers.get("content-type", "")
        text: str
        if "multipart/form-data" in content_type:
            form = await request.form()
            upload = form.get("file")
            if upload is None:
                return JSONResponse(
                    content={"status": "error", "message": "falta el campo 'file'"},
                    status_code=422,
                )
            raw = await upload.read()
            text = raw.decode("utf-8", errors="replace")
        else:
            raw = await request.body()
            text = raw.decode("utf-8", errors="replace")

        sex = _profile.effective("sex")
        result = _labs.import_csv(text, sex=sex)
        return JSONResponse(content={"status": "ok", **result})
    except Exception as e:
        logger.error(f"POST /api/labs/import falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error procesando el CSV", "imported": [], "rejected": []},
            status_code=200,
        )


@app.delete("/api/labs/{entry_id}")
async def api_labs_delete(entry_id: str):
    """Borra una entrada de laboratorio por id. Idempotente. Nunca 500."""
    try:
        _labs.delete_entry(entry_id)
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/labs/{entry_id} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error borrando laboratorio"}, status_code=200)


# ---------------------------------------------------------------- healthspan (Fase 8D, paso D2)

@app.get("/api/healthspan")
async def api_healthspan_get():
    """Body age histórico por ventanas trailing de 90d + pace of aging +
    delta trimestral. Prioriza el valor YA cacheado en summary.healthspan
    (calculado en el último run_sync); si no está presente (instalación
    vieja / sync aún no corrido tras el deploy de D2), lo computa on-demand
    para no obligar a esperar al próximo sync. <120 días de historial o sin
    perfil -> {available: false} (nunca 500)."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content={"available": False})
    try:
        cached = (dataset.get("summary") or {}).get("healthspan")
        if cached:
            return JSONResponse(content={"available": True, **cached})
        profile = effective_profile_dict()
        hs = _healthspan.compute_healthspan(dataset.get("days", []), dataset.get("exercises", []), profile)
        if hs is None:
            return JSONResponse(content={"available": False})
        return JSONResponse(content={"available": True, **hs})
    except Exception as e:
        logger.error(f"GET /api/healthspan falló: {e}")
        return JSONResponse(content={"available": False})


@app.get("/api/coach/conversations")
async def api_coach_conversations_list():
    """Lista LIGERA de conversaciones [{id, title, updated, message_count}],
    orden por `updated` desc. Sin conversaciones -> []. Nunca 500."""
    return JSONResponse(content=_coach_store.list_conversations())


@app.post("/api/coach/conversations")
async def api_coach_conversations_create(body: ConversationCreate = None):
    """Crea una conversación vacía. Devuelve {id}."""
    title = body.title if body else None
    conv = _coach_store.create_conversation(title=title)
    return JSONResponse({"id": conv["id"]})


@app.get("/api/coach/conversations/{cid}")
async def api_coach_conversation_get(cid: str):
    """Conversación completa (con messages). id inexistente -> 404 controlado."""
    conv = _coach_store.get_conversation(cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return JSONResponse(content=conv)


@app.delete("/api/coach/conversations/{cid}")
async def api_coach_conversation_delete(cid: str):
    """Borra SOLO esa conversación. Nunca 500."""
    _coach_store.delete_conversation(cid)
    return JSONResponse({"status": "ok"})


@app.post("/api/coach")
async def api_coach(body: CoachRequest):
    """Coach IA conversacional: recibe pregunta + conversation_id opcional,
    responde vía claude CLI. El contexto que se le pasa a ask_coach es SOLO
    el de ESA conversación (aislamiento — nunca se mezcla con otras).
    Sin conversation_id -> usa la activa, o crea una nueva. Si no hay datos de
    salud, responde igualmente con contexto mínimo. Si el CLI falla, devuelve
    fallback amable — nunca 500.

    NOTA (deprecación): `body.history` ya NO se usa como contexto — el contexto
    sale exclusivamente de coach_store.get_context(cid). Se ignora si viene.
    """
    dataset = _load_dataset() or {}
    cid = body.conversation_id or _coach_store.get_active_id()
    # Contexto AISLADO: solo los últimos N mensajes de ESTA conversación.
    context_history = _coach_store.get_context(cid, 10)
    # Ronda 1: offload a threadpool — ask_coach lanza `claude` CLI vía subprocess.run
    # síncrono (hasta ~90 s); en el event loop congelaba TODA la app mientras tanto.
    answer = await run_in_threadpool(ask_coach, body.question, dataset, context_history)
    # Persistir el turno (crea la conversación si cid era None/inexistente).
    used_cid = _coach_store.append_turn(cid, body.question, answer)
    _coach_store.set_active(used_cid)
    return JSONResponse({"answer": answer, "conversation_id": used_cid})


@app.get("/api/coach/history")
async def api_coach_history():
    """DEPRECADO: usa GET /api/coach/conversations/{id}. Devuelve los mensajes
    de la conversación ACTIVA (últimos 100). Sin activa -> [] (nunca 500)."""
    history = load_history()
    return JSONResponse(content=history[-100:])


@app.delete("/api/coach/history")
async def api_coach_history_clear():
    """DEPRECADO: usa DELETE /api/coach/conversations/{id}. Borra TODAS las
    conversaciones (clear_all). Escritura atómica."""
    clear_history()
    return JSONResponse({"status": "ok", "message": "Historial borrado."})


# ---------------------------------------------------------------- household (Fase 8D, paso D3)

@app.get("/api/users")
async def api_users_get(response: Response):
    """Lista de usuarios registrados [{id,name,color}] + cuál es el activo
    para ESTE request (según el mismo resolve_user que ya corrió el
    middleware). Instalación sin household (sin data/users/) -> lista vacía,
    active=null — el switcher UI de Más lo interpreta como "modo single-user,
    no mostrar selector". Nunca 500."""
    try:
        users = _userctx.list_users()
        active = _userctx.current_uid() if _userctx.should_use_household_paths() else None
        return JSONResponse(content={"users": users, "active": active})
    except Exception as e:
        logger.error(f"GET /api/users falló: {e}")
        return JSONResponse(content={"users": [], "active": None})


@app.post("/api/users")
async def api_users_post(body: UserCreate, response: Response):
    """Alta de un nuevo usuario (household). El PRIMER usuario creado en una
    instalación fresh dispara la migración implícita: al existir ya
    data/users/, should_use_household_paths() pasa a True para todo request
    futuro. Si la instancia tenía datos legacy sin migrar (caso improbable —
    la migración de startup ya corrió antes), añade el usuario nuevo AL LADO
    del 'default' migrado, nunca lo reemplaza. Devuelve 422 si el nombre es
    inválido. Nunca 500."""
    try:
        user = _userctx.add_user(body.name, color=body.color)
        if user is None:
            return JSONResponse(
                content={"status": "error", "message": "nombre inválido"},
                status_code=422,
            )
        # Conveniencia: si el caller no tenía cookie de usuario fijada, deja
        # esta cookie apuntando al usuario recién creado (siguiente request ya
        # navega directo a su vista, sin que el picker tenga que elegir de nuevo).
        response.set_cookie(_USER_COOKIE_NAME, user["id"], httponly=False, samesite="lax")
        return JSONResponse(content={"status": "ok", "user": user})
    except Exception as e:
        logger.error(f"POST /api/users falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error creando usuario"}, status_code=200)


@app.delete("/api/users/{uid}")
async def api_users_delete(uid: str, confirm: bool = False, delete_data: bool = False):
    """Quita un usuario del registro. Requiere `confirm=true` explícito en la
    querystring (roadmap D3: "DELETE con confirmación") — sin él, 400
    controlado (no borra nada). `delete_data=true` además borra su carpeta de
    datos (destructivo, opt-in explícito); sin ese flag, los datos quedan en
    disco (recuperables a mano) y solo se quita del registro/switcher.
    Idempotente. Nunca 500."""
    if not confirm:
        return JSONResponse(
            content={"status": "error", "message": "requiere confirm=true"},
            status_code=400,
        )
    try:
        _userctx.delete_user(uid, delete_data=delete_data)
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/users/{uid} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error borrando usuario"}, status_code=200)


# ---------------------------------------------------------------- F10: API pública de lectura (Roadmap P2)

@app.post("/api/keys")
async def api_keys_post(body: ApiKeyCreate):
    """Genera una API key de solo lectura para el usuario ACTIVO del request
    (resuelto por el middleware de userctx, IGUAL que el resto de /api/* —
    NO se autentica con la propia API key, es un endpoint de gestión de
    sesión normal). Devuelve la clave CRUDA una sola vez — nunca se puede
    recuperar después. 422 si se alcanzó el tope de 10 claves. Nunca 500."""
    try:
        result = _api_keys.generate_key(body.label)
        if result is None:
            return JSONResponse(
                content={"status": "error", "message": "límite de 10 claves alcanzado"},
                status_code=422,
            )
        return JSONResponse(content={"status": "ok", **result})
    except Exception as e:
        logger.error(f"POST /api/keys falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error creando la clave"}, status_code=200)


@app.get("/api/keys")
async def api_keys_get():
    """Lista SOLO metadatos de las claves del usuario activo — NUNCA el valor
    crudo ni el hash. Nunca 500."""
    try:
        return JSONResponse(content={"keys": _api_keys.list_keys()})
    except Exception as e:
        logger.error(f"GET /api/keys falló: {e}")
        return JSONResponse(content={"keys": []})


@app.delete("/api/keys/{key_id}")
async def api_keys_delete(key_id: str):
    """Revoca una clave del usuario activo. 404 si el id no existe (o no es
    del usuario actual — resolve_key()/revoke_key() ya operan SOLO sobre el
    store del uid resuelto por el middleware, así que un id de otro usuario
    simplemente no se encuentra aquí). Nunca 500."""
    try:
        ok = _api_keys.revoke_key(key_id)
        if not ok:
            return JSONResponse(
                content={"status": "error", "message": "clave no encontrada"},
                status_code=404,
            )
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/keys/{key_id} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error revocando la clave"}, status_code=200)


def _resolve_api_key_uid(request: Request) -> Optional[str]:
    """Resuelve el uid dueño de la API key del header `Authorization: Bearer
    <key>`, o None si falta/es inválida/está revocada. Itera TODOS los
    usuarios registrados (userctx.list_users()) probando la clave contra el
    store de api_keys.json de cada uno vía set_current_uid/reset_current_uid
    (mismo mecanismo que ya usa el middleware household) — así se reusa
    api_keys.resolve_key() (que opera sobre 'el usuario activo del contexto')
    sin duplicar lógica de resolución de store por uid.

    Instalación single-user (sin data/users/ todavía): list_users() es [],
    así que se prueba directo contra el uid 'default' actual del contexto
    (ya fijado por el middleware household) — cubre el caso de una instancia
    fresh que aún no creó ningún usuario explícito pero ya quiere usar F10.

    Nunca lanza — cualquier fallo degrada a None (401 en el caller)."""
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        raw_key = auth_header[len("Bearer "):].strip()
        if not raw_key:
            return None

        users = _userctx.list_users()
        candidate_uids = [u["id"] for u in users if u.get("id")] or [_userctx.current_uid()]

        for uid in candidate_uids:
            token = _userctx.set_current_uid(uid)
            try:
                if _api_keys.resolve_key(raw_key):
                    return uid
            finally:
                _userctx.reset_current_uid(token)
        return None
    except Exception as e:
        logger.warning(f"_resolve_api_key_uid falló (degradando a None -> 401): {e}")
        return None


def _api_v1_unauthorized() -> JSONResponse:
    """401 JSON uniforme para /api/v1/* — NUNCA 500, nunca cae a household
    header/cookie (criterio F10: límite de confianza distinto)."""
    return JSONResponse(content={"status": "error", "message": "API key inválida, ausente o revocada"}, status_code=401)


@app.get("/api/v1/data")
async def api_v1_data(request: Request):
    """Superficie pública de solo lectura (Roadmap P2, F10): mismo shape que
    GET /api/data, pero acotado al uid resuelto de la API key del header
    Authorization — NUNCA por header/cookie de household. Sin clave o clave
    inválida/revocada -> 401 JSON. Reusa _load_dataset()/_data_path() con el
    contextvar de userctx fijado al uid de la clave (mismo mecanismo que el
    middleware), así que no duplica lógica de carga de datos."""
    uid = _resolve_api_key_uid(request)
    if uid is None:
        return _api_v1_unauthorized()
    token = _userctx.set_current_uid(uid)
    try:
        dataset = _load_dataset()
        if not dataset:
            return JSONResponse(content={"status": "error", "message": "No hay datos."}, status_code=404)
        return JSONResponse(content=dataset)
    except Exception as e:
        logger.error(f"GET /api/v1/data falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error interno"}, status_code=200)
    finally:
        _userctx.reset_current_uid(token)


@app.get("/api/v1/insights")
async def api_v1_insights(request: Request):
    """Ídem /api/v1/data pero para GET /api/insights — mismo shape, acotado al
    uid de la API key. Sin clave válida -> 401 JSON, nunca 500."""
    uid = _resolve_api_key_uid(request)
    if uid is None:
        return _api_v1_unauthorized()
    token = _userctx.set_current_uid(uid)
    try:
        dataset = _load_dataset()
        if not dataset:
            return JSONResponse(content=[])
        locale = _profile.effective("locale") or "es"

        dataset_with_cycle = dataset
        try:
            cycle_profile = effective_profile_dict()
            if cycle_profile.get("cycle_tracking"):
                cycle_log = _cycle.load_cycle_log()
                cycle_state = _cycle.compute_cycle_state(dataset.get("days", []), cycle_log, cycle_profile)
                dataset_with_cycle = dict(dataset)
                dataset_with_cycle["_cycle"] = cycle_state
        except Exception as e:
            logger.error(f"compute_cycle_state falló en /api/v1/insights: {e}")

        return JSONResponse(content=evaluate_insights(dataset_with_cycle, locale=locale))
    except Exception as e:
        logger.error(f"GET /api/v1/insights falló: {e}")
        return JSONResponse(content=[])
    finally:
        _userctx.reset_current_uid(token)


@app.get("/auth/callback")
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
