"""
app/routes/insights.py — GET /api/insights, GET /api/drivers (Fase 9, paso
A2). Movidos TAL CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import cycle as _cycle
from app import profile as _profile
from app.deps import _load_dataset
from app.insights import evaluate as evaluate_insights
from app.profile import effective_profile_dict

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/insights")
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


@router.get("/api/drivers")
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
