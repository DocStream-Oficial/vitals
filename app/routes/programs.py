"""
app/routes/programs.py — GET /api/programs, GET/POST/DELETE /api/plan,
POST /api/plan/check (Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dt
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import profile as _profile
from app.deps import _load_dataset
from app.routes._models import PlanStart, PlanCheck

logger = logging.getLogger("vitals.main")

router = APIRouter()


# ---------------------------------------------------------------- programas del coach (Roadmap P1, F4)

@router.get("/api/programs")
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


@router.get("/api/plan")
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


@router.post("/api/plan")
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


@router.delete("/api/plan")
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


@router.post("/api/plan/check")
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
