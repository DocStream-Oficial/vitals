"""
app/routes/ecg.py — POST/GET /api/ecg, GET /api/ecg/{uuid} (Fase 9, paso A2).
Movidos TAL CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app import ecg_store
from app.deps import _demo_blocked_response

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.post("/api/ecg")
async def api_ecg_post(request: Request):
    """Ingestión PUSH de lecturas de ECG (HKElectrocardiogram) desde la app nativa.

    VISOR AISLADO: este endpoint y app/ecg_store.py son la ÚNICA vía de entrada/
    salida de data/ecg/. Los voltajes NO tocan health_compact.json, build_dataset,
    scoring, bodyage, merge ni el contexto del coach — ver ROADMAP-vitals-ecg.md.

    Auth: mismo patrón que /api/ingest — header X-Vitals-Token comparado en
    bytes con secrets.compare_digest contra INGEST_TOKEN. SIEMPRE obligatorio
    desde Fase 8C (paso C6) — settings.INGEST_TOKEN nunca está vacío (se
    autogenera en config.py si falta en .env).

    Payload mínimo: {uuid, date, classification, avg_hr, sampling_frequency,
    sample_count, symptoms_status, voltages:[float µV]}. Solo `uuid` es obligatorio;
    todo lo demás es best-effort / None-safe (ver ecg_store._clean_voltages).

    Idempotente por uuid (mismo uuid sobreescribe, no duplica). Nunca 500 — JSON
    roto o payload inválido responden {status:'error', message} con 200.
    """
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    expected = settings.INGEST_TOKEN
    provided = request.headers.get("X-Vitals-Token", "")
    if not expected or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "JSON inválido."}, status_code=200)

    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "error", "message": "El payload debe ser un objeto JSON."},
            status_code=200,
        )

    try:
        result = ecg_store.save_ecg(payload)
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error(f"/api/ecg (POST) falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


@router.get("/api/ecg")
async def api_ecg_list():
    """Lista LIGERA de lecturas de ECG (sin voltajes), ordenada por fecha desc.
    Sin lecturas -> []. Nunca 500 (una meta corrupta se omite, no tumba el listado)."""
    try:
        return JSONResponse(content=ecg_store.list_ecg())
    except Exception as e:
        logger.error(f"GET /api/ecg falló: {e}")
        return JSONResponse(content=[])


@router.get("/api/ecg/{uuid}")
async def api_ecg_get(uuid: str):
    """Meta + voltajes completos de una lectura de ECG, para el visor de la tira.
    UUID inexistente -> 404 controlado."""
    try:
        result = ecg_store.get_ecg(uuid)
    except Exception as e:
        logger.error(f"GET /api/ecg/{uuid} falló: {e}")
        result = None
    if result is None:
        raise HTTPException(status_code=404, detail="Lectura de ECG no encontrada.")
    return JSONResponse(content=result)
