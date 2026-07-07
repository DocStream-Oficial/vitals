"""
app/routes/healthspan.py — GET /api/healthspan (Fase 9, paso A2). Movido TAL
CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import healthspan as _healthspan
from app.deps import _load_dataset
from app.profile import effective_profile_dict

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/healthspan")
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
