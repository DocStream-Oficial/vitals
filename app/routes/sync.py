"""
app/routes/sync.py — POST /api/sync, POST /api/ingest (Fase 9, paso A2).
Movidos TAL CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import settings
from app.auth import TokenExpired, NoToken
from app import profile as _profile
from app.deps import _demo_blocked_response

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.post("/api/sync")
async def api_sync():
    """Dispara un sync bajo demanda. Sin token válido: responde con estado controlado, no 500.
    Si ya hay un sync en curso (single-flight, Ronda 1): {status: "already_running"}."""
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    from app.sync import run_sync, SyncInProgress
    try:
        dataset = await run_in_threadpool(run_sync)
        return JSONResponse({"status": "ok", "n_days": dataset["summary"]["n_days"]})
    except TokenExpired:
        return JSONResponse(
            {"status": "expired", "message": "Token expirado. Visita /auth/login para reconectar."},
            status_code=200,
        )
    except NoToken:
        return JSONResponse(
            {"status": "no_token", "message": "No hay token. Visita /auth/login para autorizar."},
            status_code=200,
        )
    except SyncInProgress:
        # ANTES del except Exception genérico — si no, caería como "error".
        return JSONResponse(
            {"status": "already_running", "message": "Ya hay un sync en curso."},
            status_code=200,
        )
    except Exception as e:
        logger.error(f"Sync falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)


@router.post("/api/ingest")
async def api_ingest(request: Request):
    """Ingestión PUSH de HealthKit (Fase 5D-A). La app nativa iOS (Fase 5D-B) lee
    HealthKit on-device y empuja aquí el payload normalizado.

    🔴 GUARD de fuente (Fase 6A): solo aplica el ingest si 'healthkit' está en las
    fuentes CONECTADAS del perfil (profile.effective_sources()) — ya NO exige que sea
    la única fuente activa. Si no está conectada, responde
    {status:'wrong_source', active:<source>} con HTTP 200 y NO sobrescribe
    health_compact.json — protege a usuarios que no conectaron HealthKit de un push
    accidental.

    Nunca 500: payload roto / cualquier error → {status:'error', message} con 200.
    """
    if settings.VITALS_DEMO:
        return _demo_blocked_response()
    # ── Auth: secreto compartido, SIEMPRE obligatorio (Fase 8C, paso C6). ──
    # settings.INGEST_TOKEN nunca está vacío desde C6 (config.py autogenera y
    # persiste uno si falta en .env) — ya NO existe el modo permisivo de fases
    # anteriores. 401 SIEMPRE que el header no coincida byte a byte.
    expected = settings.INGEST_TOKEN
    provided = request.headers.get("X-Vitals-Token", "")
    # Comparar en bytes (UTF-8): secrets.compare_digest sobre str lanza TypeError
    # si algún arg trae caracteres no-ASCII (un header latin-1 malformado podría
    # forzar un 500). En bytes es timing-safe y nunca lanza.
    if not expected or not secrets.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return JSONResponse({"status": "unauthorized"}, status_code=401)

    # Parseo manual del body (un JSON roto da 'error' controlado, no 422/500).
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "JSON inválido."}, status_code=200)

    if not isinstance(payload, dict):
        return JSONResponse(
            {"status": "error", "message": "El payload debe ser un objeto JSON."},
            status_code=200,
        )

    # GUARD (Fase 6A): 'healthkit' debe estar entre las fuentes CONECTADAS.
    sources = _profile.effective_sources()
    if "healthkit" not in sources:
        active = sources[0] if sources else (_profile.effective("source") or "google_health")
        return JSONResponse({"status": "wrong_source", "active": active}, status_code=200)

    try:
        from app.sources.healthkit import HealthKitSource
        from app.sync import run_sync, SyncInProgress

        hk = HealthKitSource()
        hk.ingest(payload)  # guarda el crudo en healthkit_ingest.json; fetch() lo reusará.
        # run_sync() re-consulta TODAS las fuentes conectadas (incluye el healthkit recién
        # ingerido, vía HealthKitSource.fetch() que reusa el último payload) y las funde —
        # mismo motor que usa /api/sync. Evita reimplementar merge/bodyage aquí (DRY).
        # Offload a threadpool: run_sync() hace llamadas HTTP síncronas bloqueantes
        # (requests) — sin esto, congela el event loop entero mientras corre.
        dataset = await run_in_threadpool(run_sync)
        return JSONResponse({"status": "ok", "n_days": dataset["summary"]["n_days"]})
    except SyncInProgress:
        # ANTES del except Exception genérico. El payload YA quedó guardado por
        # hk.ingest() (arriba) — no se pierde: HealthKitSource.fetch() lo reusa.
        return JSONResponse(
            {"status": "already_running",
             "message": "Payload guardado; se integrará en el sync en curso o el siguiente."},
            status_code=200,
        )
    except Exception as e:
        logger.error(f"/api/ingest falló: {e}")
        return JSONResponse({"status": "error", "message": str(e)}, status_code=200)
