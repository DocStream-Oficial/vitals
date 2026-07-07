"""
app/routes/sources.py — POST/DELETE /api/sources/{name}, GET /api/sources
(Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.config import settings
from app import profile as _profile
from app.profile import load_profile, save_profile
from app.sources import get_source
from app.deps import _KNOWN_SOURCES, _demo_blocked_response

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.post("/api/sources/{name}")
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


@router.delete("/api/sources/{name}")
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


@router.get("/api/sources")
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
