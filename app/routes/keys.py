"""
app/routes/keys.py — POST/GET/DELETE /api/keys*, GET /api/v1/data,
GET /api/v1/insights (Fase 9, paso A2, F10: API pública de lectura). Movidos
TAL CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import api_keys as _api_keys
from app import cycle as _cycle
from app import profile as _profile
from app import userctx as _userctx
from app.deps import _load_dataset
from app.insights import evaluate as evaluate_insights
from app.profile import effective_profile_dict
from app.routes._models import ApiKeyCreate

logger = logging.getLogger("vitals.main")

router = APIRouter()


# ---------------------------------------------------------------- F10: API pública de lectura (Roadmap P2)

@router.post("/api/keys")
async def api_keys_post(body: ApiKeyCreate):
    """Genera una API key de solo lectura para el usuario ACTIVO del request
    (resuelto por el middleware de userctx, IGUAL que el resto de /api/* —
    NO se autentica con la propia API key, es un endpoint de gestión de
    sesión normal). Devuelve la clave CRUDA una sola vez — nunca se puede
    recuperar después. 422 si se alcanzó el tope de 10 claves. Nunca 500."""
    try:
        result = _api_keys.generate_key(body.label)
        if result is None:
            return JSONResponse(
                content={"status": "error", "message": "límite de 10 claves alcanzado"},
                status_code=422,
            )
        return JSONResponse(content={"status": "ok", **result})
    except Exception as e:
        logger.error(f"POST /api/keys falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error creando la clave"}, status_code=200)


@router.get("/api/keys")
async def api_keys_get():
    """Lista SOLO metadatos de las claves del usuario activo — NUNCA el valor
    crudo ni el hash. Nunca 500."""
    try:
        return JSONResponse(content={"keys": _api_keys.list_keys()})
    except Exception as e:
        logger.error(f"GET /api/keys falló: {e}")
        return JSONResponse(content={"keys": []})


@router.delete("/api/keys/{key_id}")
async def api_keys_delete(key_id: str):
    """Revoca una clave del usuario activo. 404 si el id no existe (o no es
    del usuario actual — resolve_key()/revoke_key() ya operan SOLO sobre el
    store del uid resuelto por el middleware, así que un id de otro usuario
    simplemente no se encuentra aquí). Nunca 500."""
    try:
        ok = _api_keys.revoke_key(key_id)
        if not ok:
            return JSONResponse(
                content={"status": "error", "message": "clave no encontrada"},
                status_code=404,
            )
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/keys/{key_id} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error revocando la clave"}, status_code=200)


def _resolve_api_key_uid(request: Request) -> Optional[str]:
    """Resuelve el uid dueño de la API key del header `Authorization: Bearer
    <key>`, o None si falta/es inválida/está revocada. Itera TODOS los
    usuarios registrados (userctx.list_users()) probando la clave contra el
    store de api_keys.json de cada uno vía set_current_uid/reset_current_uid
    (mismo mecanismo que ya usa el middleware household) — así se reusa
    api_keys.resolve_key() (que opera sobre 'el usuario activo del contexto')
    sin duplicar lógica de resolución de store por uid.

    Instalación single-user (sin data/users/ todavía): list_users() es [],
    así que se prueba directo contra el uid 'default' actual del contexto
    (ya fijado por el middleware household) — cubre el caso de una instancia
    fresh que aún no creó ningún usuario explícito pero ya quiere usar F10.

    Nunca lanza — cualquier fallo degrada a None (401 en el caller)."""
    try:
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return None
        raw_key = auth_header[len("Bearer "):].strip()
        if not raw_key:
            return None

        users = _userctx.list_users()
        candidate_uids = [u["id"] for u in users if u.get("id")] or [_userctx.current_uid()]

        for uid in candidate_uids:
            token = _userctx.set_current_uid(uid)
            try:
                if _api_keys.resolve_key(raw_key):
                    return uid
            finally:
                _userctx.reset_current_uid(token)
        return None
    except Exception as e:
        logger.warning(f"_resolve_api_key_uid falló (degradando a None -> 401): {e}")
        return None


def _api_v1_unauthorized() -> JSONResponse:
    """401 JSON uniforme para /api/v1/* — NUNCA 500, nunca cae a household
    header/cookie (criterio F10: límite de confianza distinto)."""
    return JSONResponse(content={"status": "error", "message": "API key inválida, ausente o revocada"}, status_code=401)


@router.get("/api/v1/data")
async def api_v1_data(request: Request):
    """Superficie pública de solo lectura (Roadmap P2, F10): mismo shape que
    GET /api/data, pero acotado al uid resuelto de la API key del header
    Authorization — NUNCA por header/cookie de household. Sin clave o clave
    inválida/revocada -> 401 JSON. Reusa _load_dataset()/_data_path() con el
    contextvar de userctx fijado al uid de la clave (mismo mecanismo que el
    middleware), así que no duplica lógica de carga de datos."""
    uid = _resolve_api_key_uid(request)
    if uid is None:
        return _api_v1_unauthorized()
    token = _userctx.set_current_uid(uid)
    try:
        dataset = _load_dataset()
        if not dataset:
            return JSONResponse(content={"status": "error", "message": "No hay datos."}, status_code=404)
        return JSONResponse(content=dataset)
    except Exception as e:
        logger.error(f"GET /api/v1/data falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error interno"}, status_code=200)
    finally:
        _userctx.reset_current_uid(token)


@router.get("/api/v1/insights")
async def api_v1_insights(request: Request):
    """Ídem /api/v1/data pero para GET /api/insights — mismo shape, acotado al
    uid de la API key. Sin clave válida -> 401 JSON, nunca 500."""
    uid = _resolve_api_key_uid(request)
    if uid is None:
        return _api_v1_unauthorized()
    token = _userctx.set_current_uid(uid)
    try:
        dataset = _load_dataset()
        if not dataset:
            return JSONResponse(content=[])
        locale = _profile.effective("locale") or "es"

        dataset_with_cycle = dataset
        try:
            cycle_profile = effective_profile_dict()
            if cycle_profile.get("cycle_tracking"):
                cycle_log = _cycle.load_cycle_log()
                cycle_state = _cycle.compute_cycle_state(dataset.get("days", []), cycle_log, cycle_profile)
                dataset_with_cycle = dict(dataset)
                dataset_with_cycle["_cycle"] = cycle_state
        except Exception as e:
            logger.error(f"compute_cycle_state falló en /api/v1/insights: {e}")

        return JSONResponse(content=evaluate_insights(dataset_with_cycle, locale=locale))
    except Exception as e:
        logger.error(f"GET /api/v1/insights falló: {e}")
        return JSONResponse(content=[])
    finally:
        _userctx.reset_current_uid(token)
