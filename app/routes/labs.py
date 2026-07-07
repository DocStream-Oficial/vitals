"""
app/routes/labs.py — GET/POST /api/labs, POST /api/labs/import,
DELETE /api/labs/{entry_id} (Fase 9, paso A2). Movidos TAL CUAL desde main.py
— ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dtm
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import labs as _labs
from app import profile as _profile
from app.routes._models import LabEntryCreate

logger = logging.getLogger("vitals.main")

router = APIRouter()


# ---------------------------------------------------------------- labs (Fase 8D, paso D1)

@router.get("/api/labs")
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


@router.post("/api/labs")
async def api_labs_post(body: LabEntryCreate):
    """Alta manual de una toma de laboratorio. Valida fecha ISO, marcador
    contra el catálogo y value numérico. Nunca 500."""
    try:
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


@router.post("/api/labs/import")
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


@router.delete("/api/labs/{entry_id}")
async def api_labs_delete(entry_id: str):
    """Borra una entrada de laboratorio por id. Idempotente. Nunca 500."""
    try:
        _labs.delete_entry(entry_id)
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/labs/{entry_id} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error borrando laboratorio"}, status_code=200)
