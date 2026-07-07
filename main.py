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

# Fase 9 (paso A2): _oauth_states (CSRF state store) se mudó junto con
# /auth/login y /auth/callback a app/routes/auth.py — ver ahí.

# Fase 9 (paso A2): _KNOWN_SOURCES centralizado en app/deps.py — usado por la
# validación de /api/profile (aquí) y por /api/sources* (app/routes/sources.py).
from app.deps import _KNOWN_SOURCES  # noqa: E402


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

# Fase 9 (paso A2): _DASHBOARD_COOKIE_NAME centralizado en app/deps.py —
# usado por este middleware (aquí) y por /login (app/routes/auth.py).
from app.deps import _DASHBOARD_COOKIE_NAME  # noqa: E402

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


# Fase 9 (paso A2): _LOGIN_PAGE_TEMPLATE/_render_login_page y GET+POST /login
# viven ahora en app/routes/auth.py (se registran más abajo junto con
# /auth/login y /auth/callback).


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


# Fase 9 (paso A2): /api/insights y /api/drivers viven ahora en
# app/routes/insights.py.
from app.routes.insights import router as _insights_router  # noqa: E402
app.include_router(_insights_router)

# Fase 9 (paso A2): /api/coach/suggestions vive ahora en app/routes/coach.py
# (registrado más abajo junto con el resto de rutas del coach, después de que
# ask_coach/load_history/clear_history ya estén definidos arriba en este
# módulo — el router los lee dinámicamente vía `import main`).

# Fase 9 (paso A2): GET /api/report vive ahora en app/routes/report.py.
from app.routes.report import router as _report_router  # noqa: E402
app.include_router(_report_router)


# Fase 9 (paso A2): GET /api/data y GET /api/export viven ahora en
# app/routes/export.py. _csv_safe se re-importa aquí con el MISMO nombre
# porque tests/test_export.py hace `from main import _csv_safe` (import
# directo por nombre) — debe seguir resolviendo a la misma función.
from app.routes.export import router as _export_router, _csv_safe  # noqa: E402
app.include_router(_export_router)


# Fase 9 (paso A2): POST /api/sync y POST /api/ingest viven ahora en
# app/routes/sync.py. api_ingest se re-importa aquí con el MISMO nombre
# porque tests/test_healthkit.py llama a `main_mod.api_ingest(...)` DIRECTO
# (no vía TestClient/HTTP) para probar el caso de header no-ASCII que un
# cliente HTTP real no puede enviar — debe seguir resolviendo a la misma
# función.
from app.routes.sync import router as _sync_router, api_ingest  # noqa: E402
app.include_router(_sync_router)


# Fase 9 (paso A2): /api/ecg* vive ahora en app/routes/ecg.py.
from app.routes.ecg import router as _ecg_router  # noqa: E402
app.include_router(_ecg_router)


# Fase 9 (paso A2): GET/POST /login, GET /auth/login, GET /auth/callback
# viven ahora en app/routes/auth.py.
from app.routes.auth import router as _auth_router  # noqa: E402
app.include_router(_auth_router)


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

# Fase 9 (paso A2): GET/PUT /api/profile viven ahora en
# app/routes/profile.py. _clean_str_list/_CLINICAL_FIELDS ya no se usan
# directamente aquí (solo dentro del router) pero se re-exportan por si algún
# otro módulo los referencia vía main.
from app.deps import _clean_str_list, _CLINICAL_FIELDS  # noqa: E402
from app.routes.profile import router as _profile_router  # noqa: E402
app.include_router(_profile_router)


# Fase 9 (paso A2): /api/sources* vive ahora en app/routes/sources.py.
from app.routes.sources import router as _sources_router  # noqa: E402
app.include_router(_sources_router)


# Fase 9 (paso A2): /api/cycle* vive ahora en app/routes/cycle.py.
from app.routes.cycle import router as _cycle_router  # noqa: E402
app.include_router(_cycle_router)


# Fase 9 (paso A2): /api/journal* vive ahora en app/routes/journal.py.
from app.routes.journal import router as _journal_router  # noqa: E402
app.include_router(_journal_router)


# Fase 9 (paso A2): /api/sleep-coach vive ahora en app/routes/coach.py.


# Fase 9 (paso A2): /api/programs y /api/plan* viven ahora en
# app/routes/programs.py.
from app.routes.programs import router as _programs_router  # noqa: E402
app.include_router(_programs_router)


# Fase 9 (paso A2): /api/labs* vive ahora en app/routes/labs.py.
from app.routes.labs import router as _labs_router  # noqa: E402
app.include_router(_labs_router)


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


# Fase 9 (paso A2): /api/coach/suggestions, /api/sleep-coach,
# /api/coach/conversations*, /api/coach, /api/coach/history viven ahora en
# app/routes/coach.py. ask_coach/load_history/clear_history siguen
# importados arriba en este módulo (from app.coach_chat import ask_coach /
# from app.coach_store import load_history, clear as clear_history) — el
# router los lee vía `import main` diferido porque decenas de tests parchean
# main.ask_coach / main.load_history / main.clear_history por nombre.
from app.routes.coach import router as _coach_router  # noqa: E402
app.include_router(_coach_router)


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


# Fase 9 (paso A2): GET /auth/callback vive ahora en app/routes/auth.py
# (registrado arriba junto con /login y /auth/login).
