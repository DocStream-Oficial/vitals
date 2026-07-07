"""
app/routes/household.py — GET/POST /api/users, DELETE /api/users/{uid}
(Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Response
from fastapi.responses import JSONResponse

from app import userctx as _userctx
from app.deps import _USER_COOKIE_NAME
from app.routes._models import UserCreate

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/users")
async def api_users_get(response: Response):
    """Lista de usuarios registrados [{id,name,color}] + cuál es el activo
    para ESTE request (según el mismo resolve_user que ya corrió el
    middleware). Instalación sin household (sin data/users/) -> lista vacía,
    active=null — el switcher UI de Más lo interpreta como "modo single-user,
    no mostrar selector". Nunca 500."""
    try:
        users = _userctx.list_users()
        active = _userctx.current_uid() if _userctx.should_use_household_paths() else None
        return JSONResponse(content={"users": users, "active": active})
    except Exception as e:
        logger.error(f"GET /api/users falló: {e}")
        return JSONResponse(content={"users": [], "active": None})


@router.post("/api/users")
async def api_users_post(body: UserCreate, response: Response):
    """Alta de un nuevo usuario (household). El PRIMER usuario creado en una
    instalación fresh dispara la migración implícita: al existir ya
    data/users/, should_use_household_paths() pasa a True para todo request
    futuro. Si la instancia tenía datos legacy sin migrar (caso improbable —
    la migración de startup ya corrió antes), añade el usuario nuevo AL LADO
    del 'default' migrado, nunca lo reemplaza. Devuelve 422 si el nombre es
    inválido. Nunca 500."""
    try:
        user = _userctx.add_user(body.name, color=body.color)
        if user is None:
            return JSONResponse(
                content={"status": "error", "message": "nombre inválido"},
                status_code=422,
            )
        resp = JSONResponse(content={"status": "ok", "user": user})
        # Deja al usuario recién creado como ACTIVO (cookie vitals_user): el
        # reload que hace household.js tras el alta cae directo a su onboarding
        # (ya no es dead-end gracias a F1 en GET /). SIEMPRE fija el usuario
        # nuevo como activo. set_cookie debe ir sobre el Response que se
        # RETORNA — fijarlo sobre el Response inyectado por FastAPI y retornar
        # un JSONResponse nuevo (como antes) es un no-op: ese Set-Cookie nunca
        # llega al cliente.
        resp.set_cookie(_USER_COOKIE_NAME, user["id"], httponly=False, samesite="lax")
        return resp
    except Exception as e:
        logger.error(f"POST /api/users falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error creando usuario"}, status_code=200)


@router.delete("/api/users/{uid}")
async def api_users_delete(uid: str, confirm: bool = False, delete_data: bool = False):
    """Quita un usuario del registro. Requiere `confirm=true` explícito en la
    querystring (roadmap D3: "DELETE con confirmación") — sin él, 400
    controlado (no borra nada). `delete_data=true` además borra su carpeta de
    datos (destructivo, opt-in explícito); sin ese flag, los datos quedan en
    disco (recuperables a mano) y solo se quita del registro/switcher.
    Idempotente. Nunca 500."""
    if not confirm:
        return JSONResponse(
            content={"status": "error", "message": "requiere confirm=true"},
            status_code=400,
        )
    try:
        _userctx.delete_user(uid, delete_data=delete_data)
        return JSONResponse(content={"status": "ok"})
    except Exception as e:
        logger.error(f"DELETE /api/users/{uid} falló: {e}")
        return JSONResponse(content={"status": "error", "message": "Error borrando usuario"}, status_code=200)
