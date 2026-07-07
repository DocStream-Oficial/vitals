"""
app/routes/cycle.py — GET /api/cycle, POST/DELETE /api/cycle/period*,
POST /api/cycle/symptom (+ helper _cycle_tracking_enabled) (Fase 9, paso A2).
Movidos TAL CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dtm
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import cycle as _cycle
from app import profile as _profile
from app.deps import _load_dataset, _clean_str_list
from app.profile import effective_profile_dict
from app.routes._models import CyclePeriodCreate, CycleSymptomCreate

logger = logging.getLogger("vitals.main")

router = APIRouter()


def _cycle_tracking_enabled() -> bool:
    """True si el toggle opt-in de ciclo está prendido. Nunca lanza."""
    try:
        return bool(_profile.effective("cycle_tracking"))
    except Exception:
        return False


@router.get("/api/cycle")
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


@router.post("/api/cycle/period")
async def api_cycle_period_post(body: CyclePeriodCreate):
    """Añade/actualiza un inicio de periodo (de-dupe por 'start'). Validación de
    fechas ISO controlada. Gateado por cycle_tracking. Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"status": "disabled"}, status_code=403)

    try:
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


@router.delete("/api/cycle/period/{start}")
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


@router.post("/api/cycle/symptom")
async def api_cycle_symptom_post(body: CycleSymptomCreate):
    """Registra síntomas para una fecha (tags de lista libre, reusa _clean_str_list:
    cap 10x120 chars). Validación de fecha ISO controlada. Gateado. Nunca 500."""
    if not _cycle_tracking_enabled():
        return JSONResponse(content={"status": "disabled"}, status_code=403)

    try:
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
