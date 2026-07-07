"""
app/routes/profile.py — GET/PUT /api/profile (Fase 9, paso A2). Movidos TAL
CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dt
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import profile as _profile
from app.profile import load_profile, save_profile, effective_profile_dict
from app.deps import _clean_str_list, _CLINICAL_FIELDS, _KNOWN_SOURCES
from app.routes._models import ProfileUpdate

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/profile")
async def api_profile_get():
    """Devuelve el perfil efectivo (cascada: profile.json → .env → defaults).
    Nunca 500: si no hay profile.json devuelve los valores efectivos actuales."""
    try:
        return JSONResponse(content=effective_profile_dict())
    except Exception as e:
        logger.error(f"GET /api/profile falló: {e}")
        return JSONResponse(content={})


@router.put("/api/profile")
async def api_profile_put(body: ProfileUpdate):
    """Actualiza el perfil con validación. Escritura atómica. Nunca 500.

    Validaciones:
    - birthdate: ISO 8601 (YYYY-MM-DD), opcional
    - sex: 'M' o 'F', opcional
    - waist_cm: > 0, opcional
    - sleep_target_min: entero 300-600 (minutos), opcional (Ronda 5)
    - steps_target: entero 1000-50000 (pasos), opcional
    - locale: 'es', 'en', 'fr' o 'pt', opcional
    - units: 'metric' o 'imperial', opcional
    - source: 'google_health', 'oura', 'whoop' o 'healthkit', opcional
    - goals/injuries/conditions/medications: lista de strings, opcional (Ronda 4).
      Cada item se trimea, se descartan vacíos, máx 10 items x 120 chars.
    """
    errors = []

    if body.birthdate is not None:
        try:
            import datetime as _dt
            _dt.date.fromisoformat(body.birthdate)
        except ValueError:
            errors.append("birthdate debe ser ISO 8601 (YYYY-MM-DD)")

    if body.sex is not None and body.sex not in ("M", "F"):
        errors.append("sex debe ser 'M' o 'F'")

    if body.waist_cm is not None and body.waist_cm <= 0:
        errors.append("waist_cm debe ser > 0")

    if body.sleep_target_min is not None and not (300 <= body.sleep_target_min <= 600):
        errors.append("sleep_target_min debe estar entre 300 y 600 (minutos)")

    if body.steps_target is not None and not (1000 <= body.steps_target <= 50000):
        errors.append("steps_target debe estar entre 1000 y 50000 (pasos)")

    if body.locale is not None and body.locale not in ("es", "en", "fr", "pt"):
        errors.append("locale debe ser 'es', 'en', 'fr' o 'pt'")

    if body.units is not None and body.units not in ("metric", "imperial"):
        errors.append("units debe ser 'metric' o 'imperial'")

    if body.source is not None and body.source not in _KNOWN_SOURCES:
        errors.append("source debe ser 'google_health', 'oura', 'whoop' o 'healthkit'")

    if body.sources is not None:
        if not isinstance(body.sources, list) or any(s not in _KNOWN_SOURCES for s in body.sources):
            errors.append("sources debe ser una lista de 'google_health', 'oura', 'whoop' y/o 'healthkit'")

    # Ronda 4: intake clínico — cada campo, si viene, debe ser lista de strings
    # (≤10 items × ≤120 chars). Errores controlados, nunca 500.
    _clinical_clean: dict = {}
    for field in _CLINICAL_FIELDS:
        raw = getattr(body, field)
        if raw is not None:
            try:
                _clinical_clean[field] = _clean_str_list(raw)
            except ValueError as e:
                errors.append(f"{field} {e}")

    # Fase 8C (paso C3): notifications — dict con subcampos conocidos, MERGE
    # parcial sobre el existente (no un replace total: togglear morning_brief
    # no debe borrar un ntfy_url ya configurado). Cualquier otra cosa (no-dict,
    # subcampo de tipo raro) -> 422 controlado, nunca 500.
    _NOTIFY_STR_FIELDS = ("ntfy_url", "telegram_bot_token", "telegram_chat_id")
    _NOTIFY_BOOL_FIELDS = ("morning_brief", "alerts")
    _notify_clean: Optional[dict] = None
    if body.notifications is not None:
        if not isinstance(body.notifications, dict):
            errors.append("notifications debe ser un objeto")
        else:
            _notify_clean = {}
            for k, v in body.notifications.items():
                if k in _NOTIFY_STR_FIELDS:
                    if not isinstance(v, str):
                        errors.append(f"notifications.{k} debe ser texto")
                        continue
                    _notify_clean[k] = v.strip()[:300]
                elif k in _NOTIFY_BOOL_FIELDS:
                    if not isinstance(v, bool):
                        errors.append(f"notifications.{k} debe ser booleano")
                        continue
                    _notify_clean[k] = v
                # claves desconocidas se ignoran silenciosamente (forward-compat)

    if errors:
        return JSONResponse(
            content={"status": "error", "errors": errors},
            status_code=422,
        )

    try:
        # Merge: solo los campos enviados (no None en el body)
        update_fields = body.model_dump(exclude_none=True)
        # Sobrescribir los campos clínicos con su versión YA validada/limpia
        # (model_dump traería la lista cruda sin trim/cap).
        update_fields.update(_clinical_clean)

        existing = load_profile()
        # PUT sin campos efectivos: no crear/sobrescribir un profile.json basura.
        # Si ya existía perfil, lo dejamos intacto; si no, no escribimos nada.
        if not update_fields and _notify_clean is None:
            return JSONResponse(content=effective_profile_dict())

        # notifications: leer el valor YA persistido ANTES de mutar `current`
        # más abajo. current = existing (misma referencia, no copia) cuando
        # existing no es None -> current.update(update_fields) mutaría
        # existing["notifications"] in-place SI notifications viniera crudo
        # dentro de update_fields, corrompiendo la lectura de "lo ya
        # guardado" (bug real cazado con test_put_notifications_partial_
        # update_merges: togglear morning_brief borraba un ntfy_url ya
        # guardado). Por eso: (1) leer existing_notify PRIMERO, (2) sacar
        # 'notifications' de update_fields para que el .update() genérico de
        # abajo no la toque en absoluto — el MERGE parcial es el único que
        # escribe esa clave.
        existing_notify = (existing or {}).get("notifications")
        update_fields.pop("notifications", None)

        current = existing or {}
        current.update(update_fields)

        # notifications: MERGE parcial sobre el dict ya persistido (o los
        # defaults si no había), NO el replace total que haría un .update()
        # genérico (togglear morning_brief no debe borrar un ntfy_url ya
        # guardado).
        if _notify_clean is not None:
            base_notify = dict(existing_notify) if isinstance(existing_notify, dict) else dict(_profile.effective_notifications())
            base_notify.update(_notify_clean)
            current["notifications"] = base_notify

        save_profile(current)
        return JSONResponse(content=effective_profile_dict())
    except Exception as e:
        logger.error(f"PUT /api/profile falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error guardando perfil"},
            status_code=500,
        )
