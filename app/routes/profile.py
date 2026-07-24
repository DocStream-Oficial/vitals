"""
app/routes/profile.py — GET/PUT /api/profile (Fase 9, paso A2). Movidos TAL
CUAL desde main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import profile as _profile
from app.profile import load_profile, save_profile, effective_profile_dict, profile_impact_path
from app.deps import _clean_str_list, _CLINICAL_FIELDS, _KNOWN_SOURCES, _load_dataset
from app.bodyage import compute_body_age
from app.fsutil import atomic_write_text
from app.routes._models import ProfileUpdate

logger = logging.getLogger("vitals.main")

router = APIRouter()

# ── Roadmap edad-corporal-credibilidad Paso 4: atribución de cambios de perfil ──
# Campos que, si cambian, pueden mover body_age de forma no obvia para la
# usuaria (waist/sex alimentan directo la regresión NTNU; birthdate cambia la
# edad cronológica que ancla TODO el cómputo). Ver decisiones cerradas del
# roadmap: profile_impact.json vive junto a profile.json, TTL 14 días.
_IMPACT_FIELDS = ("waist_cm", "sex", "birthdate")
_IMPACT_DELTA_THRESHOLD = 2.0


def _age_from_birthdate(bd_str) -> Optional[float]:
    """Edad en años (entero, misma fórmula que app.profile.current_age) a
    partir de un birthdate ISO arbitrario. None si es inválido/ausente —
    nunca lanza (el caller lo trata como 'no calculable', best-effort)."""
    try:
        by = _dt.date.fromisoformat(bd_str)
        td = _dt.date.today()
        return td.year - by.year - ((td.month, td.day) < (by.month, by.day))
    except Exception:
        return None


def _maybe_record_profile_impact(old_values: dict) -> None:
    """Best-effort TOTAL (patrón sync.py): si el PUT que YA se guardó cambió
    waist_cm/sex/birthdate y eso mueve body_age >=2 años sobre el MISMO
    dataset, escribe profile_impact.json junto a profile.json. Cualquier
    fallo (dataset ausente/corrupto, perfil sin waist/birthdate utilizables,
    etc.) se traga en silencio — un PUT exitoso JAMÁS debe fallar por esto."""
    try:
        new_values = {f: _profile.effective(f) for f in _IMPACT_FIELDS}
        changed = [f for f in _IMPACT_FIELDS if old_values.get(f) != new_values.get(f)]
        if not changed:
            return

        old_waist, old_sex, old_bd = old_values.get("waist_cm"), old_values.get("sex"), old_values.get("birthdate")
        new_waist, new_sex, new_bd = new_values.get("waist_cm"), new_values.get("sex"), new_values.get("birthdate")
        if not (old_waist and old_bd and new_waist and new_bd):
            return  # sin bd/waist utilizables (perfil viejo/incompleto) -> nada que atribuir

        age_old = _age_from_birthdate(old_bd)
        age_new = _age_from_birthdate(new_bd)
        if age_old is None or age_new is None:
            return

        dataset = _load_dataset()
        if not dataset:
            return
        days = dataset.get("days") or []
        exercises = dataset.get("exercises") or []
        if not days:
            return

        ba_old = compute_body_age(days, exercises, age_old, float(old_waist), old_sex or "M")
        ba_new = compute_body_age(days, exercises, age_new, float(new_waist), new_sex or "M")
        delta = ba_new["body_age"] - ba_old["body_age"]
        if abs(delta) < _IMPACT_DELTA_THRESHOLD:
            return

        impact = {
            "date": _dt.date.today().isoformat(),
            "fields": changed,
            "old_body_age": ba_old["body_age"],
            "new_body_age": ba_new["body_age"],
            "delta_years": delta,
        }
        path = profile_impact_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(impact, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning("profile_impact best-effort falló en PUT /api/profile (ignorado): %s", exc)


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
    - sleep_goal_min: entero 300-600 (minutos), opcional (sleep-goal-vs-need;
      NO se valida goal<=target — un objetivo mayor que la necesidad es legítimo)
    - steps_target: entero 1000-50000 (pasos), opcional
    - locale: 'es', 'en', 'fr' o 'pt', opcional
    - units: 'metric' o 'imperial', opcional
    - source: 'google_health', 'oura', 'whoop' o 'healthkit', opcional
    - goals/injuries/conditions/medications: lista de strings, opcional (Ronda 4).
      Cada item se trimea, se descartan vacíos, máx 10 items x 120 chars.
    """
    # Roadmap edad-corporal-credibilidad Paso 4: capturar el perfil ANTES de
    # tocar nada — _maybe_record_profile_impact() lo compara contra el
    # efectivo DESPUÉS del guardado exitoso, para atribuir el movimiento de
    # body_age al cambio de perfil (no a una recalculada nueva regresión).
    _old_profile_values = {f: _profile.effective(f) for f in _IMPACT_FIELDS}

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

    if body.sleep_goal_min is not None and not (300 <= body.sleep_goal_min <= 600):
        errors.append("sleep_goal_min debe estar entre 300 y 600 (minutos)")

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
        # Roadmap edad-corporal-credibilidad Paso 4: best-effort TOTAL, fuera
        # de este try/except no debe estar — cualquier fallo aquí ya se traga
        # dentro de _maybe_record_profile_impact, pero la llamada en sí NUNCA
        # debe impedir la respuesta 200 de un guardado que ya tuvo éxito.
        _maybe_record_profile_impact(_old_profile_values)
        return JSONResponse(content=effective_profile_dict())
    except Exception as e:
        logger.error(f"PUT /api/profile falló: {e}")
        return JSONResponse(
            content={"status": "error", "message": "Error guardando perfil"},
            status_code=500,
        )
