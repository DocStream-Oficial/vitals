"""
app/routes/journal.py — GET/PUT /api/journal*, POST /api/journal/custom,
GET /api/journal/impact, GET /api/journal/dose-response (+ helper
_journal_week_count) (Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import journal as _journal
from app import profile as _profile
from app.deps import _load_dataset
from app.routes._models import JournalUpdate, JournalCustomCreate

logger = logging.getLogger("vitals.main")

router = APIRouter()


# ---------------------------------------------------------------- journal (Fase 8B)

def _journal_week_count(journal: dict, today: _dt.date) -> int:
    """Días con AL MENOS un hábito registrado en la semana ISO (lun-dom) que
    contiene `today` — alimenta el contador "N registrados esta semana" de la
    card Diario. Nunca lanza."""
    try:
        monday = today - _dt.timedelta(days=today.weekday())
        entries = (journal or {}).get("entries") or {}
        count = 0
        for i in range(7):
            e = entries.get((monday + _dt.timedelta(days=i)).isoformat())
            if isinstance(e, dict) and e:
                count += 1
        return count
    except Exception:
        return 0


@router.get("/api/journal")
async def api_journal_get(date: Optional[str] = None):
    """Catálogo de hábitos (labels localizadas, fijo + custom) + entry de la
    fecha dada (default hoy) + contador de días registrados esta semana ISO.
    Nunca 500 — dataset/journal ilegibles degradan a catálogo vacío / entry
    vacía."""
    try:
        target_date = date or _dt.date.today().isoformat()
        try:
            _dt.date.fromisoformat(target_date)
        except (ValueError, TypeError):
            return JSONResponse(
                content={"status": "error", "message": "date debe ser fecha ISO 8601 (YYYY-MM-DD)"},
                status_code=422,
            )
        locale = _profile.effective("locale") or "es"
        j = _journal.load_journal()
        return JSONResponse(content={
            "date": target_date,
            "catalog": _journal.catalog(j, locale=locale),
            "entry": _journal.get_entry(target_date),
            "week_count": _journal_week_count(j, _dt.date.today()),
        })
    except Exception as e:
        logger.error(f"GET /api/journal falló: {e}")
        return JSONResponse(content={"date": date, "catalog": [], "entry": {}, "week_count": 0})


@router.put("/api/journal/{date}")
async def api_journal_put(date: str, body: JournalUpdate):
    """Actualiza (merge) los hábitos marcados de una fecha. Valida fecha ISO
    no-futura y que las keys enviadas existan en el catálogo (fijo o custom).
    Nunca 500."""
    try:
        parsed = _dt.date.fromisoformat(date)
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

    try:
        valid_keys = _journal.valid_habit_keys()
        unknown = [k for k in body.habits.keys() if k not in valid_keys]
        if unknown:
            return JSONResponse(
                content={"status": "error", "message": f"hábitos desconocidos: {unknown}"},
                status_code=422,
            )
        entry = _journal.set_entry(date, body.habits)
        return JSONResponse(content={"status": "ok", "date": date, "entry": entry})
    except Exception as e:
        logger.error(f"PUT /api/journal/{date} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error guardando hábitos"}, status_code=200)


@router.post("/api/journal/custom")
async def api_journal_custom_post(body: JournalCustomCreate):
    """Alta de hábito custom (típicamente un suplemento no listado). Nunca 500."""
    try:
        habit = _journal.add_custom_habit(body.label)
        if habit is None:
            return JSONResponse(
                content={"status": "error", "message": "label inválido o límite de hábitos custom alcanzado"},
                status_code=422,
            )
        return JSONResponse(content={"status": "ok", "habit": habit})
    except Exception as e:
        logger.error(f"POST /api/journal/custom falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error creando hábito custom"}, status_code=200)


@router.get("/api/journal/impact")
async def api_journal_impact():
    """Findings de impacto hábito→biometría (análisis de correlación honesta,
    BH-corregido). Sin datos o sin hábitos suficientes -> [] (nunca 500).

    NOTA (desviación documentada del criterio 19 del roadmap P2/F9): el
    roadmap pide que este MISMO endpoint gane una "clave aditiva" con los
    hallazgos de dosis-respuesta — pero la respuesta HOY es una lista JSON
    en la raíz (no un objeto), consumida como tal tanto por el frontend
    (static/js/journal.js: `findings.map(...)` directo sobre la respuesta)
    como por los tests preexistentes (`resp.json() == []`, `body[0]["habit"]`
    indexando la raíz como lista). Convertir la raíz a un objeto
    `{findings:[...], dose_response:[...]}` para poder anidar la clave
    aditiva SÍ habría cambiado el shape existente, violando el criterio
    hermano ("el shape existente de la respuesta NO cambia") y rompiendo el
    consumidor actual. Se prioriza no tocar el shape ya en producción: los
    hallazgos de dosis-respuesta se exponen en un endpoint NUEVO y aditivo,
    `GET /api/journal/dose-response` (ver abajo), en vez de en una clave
    dentro de este mismo payload."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content=[])
    try:
        locale = _profile.effective("locale") or "es"
        j = _journal.load_journal()
        findings = _journal.analyze_journal(dataset.get("days", []), j, locale=locale)
        return JSONResponse(content=findings)
    except Exception as e:
        logger.error(f"GET /api/journal/impact falló: {e}")
        return JSONResponse(content=[])


@router.get("/api/journal/dose-response")
async def api_journal_dose_response():
    """Roadmap P2 (F9, paso 8/criterio 19): hallazgos de dosis-respuesta
    (¿la CANTIDAD del hábito importa?, no solo sí/no) para los 3 hábitos
    cuantificables. Endpoint NUEVO y aditivo (ver nota de desviación en
    api_journal_impact arriba) — mismo contrato de nunca-500 que el resto de
    /api/journal/*: sin datos o sin hábitos cuantificables suficientes -> []."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content=[])
    try:
        locale = _profile.effective("locale") or "es"
        j = _journal.load_journal()
        findings = _journal.analyze_journal_dose_response(dataset.get("days", []), j, locale=locale)
        return JSONResponse(content=findings)
    except Exception as e:
        logger.error(f"GET /api/journal/dose-response falló: {e}")
        return JSONResponse(content=[])
