"""
app/routes/report.py — GET /api/report (Fase 9, paso A2). Movido TAL CUAL
desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import profile as _profile
from app import report as _report
from app.deps import _load_dataset

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/report")
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
