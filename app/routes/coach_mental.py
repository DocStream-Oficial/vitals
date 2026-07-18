"""
app/routes/coach_mental.py — Coach Deportivo (Sesión Master), roadmap
coach-mental Fase 1, Paso 4. Patrón de app/routes/coach.py: APIRouter,
JSONResponse, logger "vitals.main", nunca 500 en flujos degradables.

GET/PUT  /api/coach/mental/profile          — perfil mental (expediente).
POST     /api/coach/mental/session          — abre una Sesión Master.
POST     /api/coach/mental/session/{cid}/close — cierra y extrae el resumen.

Los identificadores internos (kind "mental_master", rutas /mental/*) se
quedan como están — el nombre de cara al usuario es "Coach Deportivo"
(decisión de mitad de corrida, ver IMPL-REPORT.md).
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import coach_mental as _coach_mental
from app import coach_store as _coach_store
from app import mental_store as _mental_store
from app.deps import _load_dataset

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/coach/mental/profile")
async def api_coach_mental_profile_get():
    """Perfil mental (expediente) del usuario activo. Sin perfil -> {}."""
    return JSONResponse(content=_mental_store.get_profile())


@router.put("/api/coach/mental/profile")
async def api_coach_mental_profile_put(request: Request):
    """Siembra/actualiza el perfil mental. Body debe ser un dict JSON (la
    encuesta como UI es v2 — en Fase 1 se siembra vía PUT directo, ver
    Notas de deployment del roadmap). Body no-dict -> 400 controlado."""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido.")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="El body debe ser un objeto JSON.")
    _mental_store.set_profile(payload)
    return JSONResponse(content=_mental_store.get_profile())


@router.post("/api/coach/mental/session")
async def api_coach_mental_session_start():
    """Abre una Sesión Master: crea la conversación (kind=mental_master),
    genera la apertura vía LLM (mockeable, fallback estático si el LLM está
    caído) y la persiste como primer mensaje assistant. Nunca 500."""
    dataset = _load_dataset() or {}
    today = date.today().isoformat()
    conv = _coach_store.create_conversation(
        title=f"Sesión Master — {today}", kind="mental_master",
    )
    cid = conv["id"]
    opening = await run_in_threadpool(_coach_mental.opening_message, dataset)
    _coach_store.append_message(cid, "assistant", opening)
    _coach_store.set_active(cid)
    return JSONResponse(content={"conversation_id": cid, "opening": opening})


@router.post("/api/coach/mental/session/{cid}/close")
async def api_coach_mental_session_close(cid: str):
    """Cierra una Sesión Master: 404 controlado si la conversación no existe
    o no es kind=mental_master; si ok, extrae resumen/focos vía LLM (parseo
    defensivo, nunca pierde la sesión) y los guarda en el expediente."""
    conv = _coach_store.get_conversation(cid)
    if conv is None or _coach_store.get_kind(cid) != "mental_master":
        raise HTTPException(status_code=404, detail="Sesión Master no encontrada.")
    dataset = _load_dataset() or {}
    result = await run_in_threadpool(_coach_mental.close_session, cid, dataset)
    return JSONResponse(content={
        "saved": True,
        "focos": result.get("focos", []),
        "resumen": result.get("resumen", ""),
    })
