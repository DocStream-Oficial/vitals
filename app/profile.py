"""
profile.py — Sistema de perfil por-usuario de Vitals.

Fuente de verdad: data/profile.json (se crea en onboarding).
Cascada de fallback: profile.json → settings (.env) → default hardcoded.
Escritura ATÓMICA: write a .tmp + os.replace (patrón de coach_store.py).

Campos de profile.json:
  name, email, birthdate, sex, waist_cm, height_cm, weight_kg,
  locale, units, onboarded, goals, injuries, conditions, medications

locale/units existen como DEFAULTS fijos en 1A (no se usan todavía en la UI,
pero deben existir para que 1B los consuma).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vitals.profile")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_PROFILE_FILE = _DATA_DIR / "profile.json"  # legacy — usado solo si userctx no está disponible

# ── Defaults del sistema ────────────────────────────────────────────────────────
_DEFAULTS: dict[str, Any] = {
    "name": "",
    "email": "",
    "birthdate": "1990-01-01",
    "sex": "M",
    "waist_cm": 80.0,
    "height_cm": None,
    "weight_kg": None,
    # Ronda 5: umbral único de sueño (minutos), reemplaza el trío 480/420/7h
    # hardcodeado. Default 480 = comportamiento idéntico a antes. Validado
    # 300-600 en PUT /api/profile (main.py).
    "sleep_target_min": 480,
    # Sleep-goal-vs-need: OBJETIVO personal de sueño, distinto de la NECESIDAD
    # fisiológica de arriba (sleep_target_min). Este campo NO entra a ningún
    # motor (scoring/bodyage/sleep_scores) — solo alimenta rachas, titular y
    # UI (app/changes.py, app/coach_headline.py, app-dashboard.js). Default
    # 480 = mismo valor que sleep_target_min, pero la cascada real vive en
    # effective_sleep_goal() (cae al target antes que a este default).
    # Validado 300-600 en PUT /api/profile (routes/profile.py).
    "sleep_goal_min": 480,
    # Tarjeta de Pasos en Hoy: meta diaria (pasos). Default 8000 = mismo umbral
    # que ya usaba el estado "on_target" hardcodeado en el drill-down de Fitness.
    # Validado 1000-50000 en PUT /api/profile (main.py), mismo estilo que sleep_target_min.
    "steps_target": 8000,
    "locale": "es",
    "units": "metric",
    "onboarded": False,
    "source": "google_health",  # DEPRECATED: usar sources. Se mantiene por compat de código viejo.
    "sources": ["google_health"],  # Fase 6A: fuentes CONECTADAS (reemplaza el modelo "una activa").
    # Ronda 4: intake clínico. Listas de strings declaradas por el usuario (metas
    # ORDENADAS por prioridad, lesiones, condiciones, medicamentos). Perfiles viejos
    # (sin estos campos) caen aquí -> [] -> cero migración necesaria.
    "goals": [],
    "injuries": [],
    "conditions": [],
    "medications": [],
    # Fase 7: módulo de salud femenina, OPT-IN estricto. Default False -> rollout
    # invisible (nada de ciclo se filtra hasta que la usuaria lo active). Funciona
    # con cualquier 'sex' (toggle inclusivo, nunca forzado). Perfiles viejos sin
    # este campo caen aquí -> False -> cero migración necesaria.
    "cycle_tracking": False,
    # Fase 8C (paso C3): configuración de notificaciones push (ntfy/Telegram).
    # Default: sin canales configurados -> notify_after_sync() es no-op
    # silencioso (cero requests HTTP). morning_brief/alerts en True por
    # default para que, en cuanto el usuario configure UN canal, ambos
    # empiecen a llegar sin un segundo toggle que se le pueda olvidar.
    "notifications": {
        "ntfy_url": "",
        "telegram_bot_token": "",
        "telegram_chat_id": "",
        "morning_brief": True,
        "alerts": True,
    },
}


def _profile_path() -> Path:
    """Devuelve la ruta al profile.json del usuario ACTIVO del contexto actual
    (Fase 8D, paso D3: household). Consulta userctx.current_data_dir() en cada
    llamada (no una constante congelada al importar) — así, dentro de un
    request real (contextvar fijado por el middleware), cada usuario lee/
    escribe SU profile.json aislado.

    Fuera de un request household-aware (is_context_active()=False — código
    legacy, scripts, y TODOS los tests preexistentes de Fases B/C que
    monkeypatchean _PROFILE_FILE directamente), usa _PROFILE_FILE tal cual:
    comportamiento IDÉNTICO a antes de esta fase, cero tests rotos. Nunca
    lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "profile.json"
    except Exception:
        pass
    return _PROFILE_FILE


def load_profile() -> Optional[dict]:
    """Lee data/profile.json → dict.
    Si no existe o está corrupto → None (nunca lanza excepción)."""
    try:
        path = _profile_path()
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        logger.warning("profile.json no es dict; ignorando.")
        return None
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        logger.warning("profile.json JSON inválido (%s); ignorando.", exc)
        return None
    except Exception as exc:
        logger.warning("Error leyendo profile.json: %s", exc)
        return None


def save_profile(d: dict) -> None:
    """Guarda profile.json con escritura ATÓMICA (.tmp + os.replace).
    Nunca lanza excepción (loguea en error)."""
    try:
        path = _profile_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.error("Error guardando profile.json: %s", exc)


def is_onboarded() -> bool:
    """True si profile.json existe y tiene onboarded=True."""
    p = load_profile()
    if p is None:
        return False
    return bool(p.get("onboarded", False))


def effective(field: str) -> Any:
    """Accessor con cascada:
       1. profile.json[field]  (si existe el archivo y tiene el campo)
       2. settings.<ENV>       (BIRTHDATE, WAIST_CM, SEX)
       3. default hardcoded

    Mantiene viva la instancia del usuario sin profile.json:
    si .env tiene BIRTHDATE/WAIST_CM/SEX los usa como fallback.
    """
    profile = load_profile()

    # 1. Perfil explícito
    if profile is not None and field in profile and profile[field] is not None:
        return profile[field]

    # 2. Settings/.env (solo campos que el .env histórico puede proveer)
    try:
        from app.config import settings as _s
        if field == "birthdate" and _s.BIRTHDATE:
            return _s.BIRTHDATE
        if field == "waist_cm":
            return _s.WAIST_CM
        if field == "sex" and _s.SEX:
            return _s.SEX
    except Exception:
        pass

    # 3. Default hardcoded
    return _DEFAULTS.get(field)


def effective_sources() -> list[str]:
    """Fase 6A: devuelve la lista de fuentes CONECTADAS.

    Cascada:
      1. profile.json['sources'] — si existe y es una lista NO VACÍA, se usa tal cual
         (fuente de verdad nueva; ignora 'source' si ambas coexisten).
      2. Backward-compat: si profile.json no tiene 'sources' (perfil viejo, solo
         'source' string), cae a [effective("source")].
      3. Si nada existe, ["google_health"] (default).
    """
    profile = load_profile()
    if profile is not None:
        sources = profile.get("sources")
        if isinstance(sources, list) and len(sources) > 0:
            return sources
    # Compat: perfil viejo sin 'sources', o sin profile.json en absoluto.
    fallback = effective("source")
    return [fallback] if fallback else ["google_health"]


def effective_sleep_goal() -> Any:
    """Sleep-goal-vs-need: cascada del OBJETIVO personal de sueño.

    Modelada sobre effective_sources() — campo nuevo que debe caer a un campo
    viejo cuando no existe, para que perfiles ya persistidos no sufran una
    regresión silenciosa el día del deploy:

      1. profile.json['sleep_goal_min'] — si el usuario lo fijó explícito, se usa.
      2. Si no existe, cae a effective("sleep_target_min") — el objetivo sigue
         a la necesidad hasta que se fije un valor propio (nunca un objetivo
         mayor que la necesidad por accidente en perfiles viejos).
      3. Si tampoco hay necesidad, 480 (default duro).

    NO se usa .get("sleep_goal_min", 480) directo: eso saltaría el paso 2 y
    rompería a cualquier usuario con sleep_target_min != 480.
    """
    profile = load_profile()
    if profile is not None and profile.get("sleep_goal_min") is not None:
        return profile["sleep_goal_min"]
    target = effective("sleep_target_min")
    if target is not None:
        return target
    return _DEFAULTS["sleep_goal_min"]


def current_age() -> int:
    """Calcula la edad en años a partir del campo birthdate del perfil.
    Fórmula idéntica a sync.py:74-76."""
    bd_str = effective("birthdate")
    try:
        by = datetime.date.fromisoformat(bd_str)
        td = datetime.date.today()
        return td.year - by.year - ((td.month, td.day) < (by.month, by.day))
    except Exception as exc:
        logger.warning("No se pudo calcular current_age desde '%s': %s", bd_str, exc)
        # Fallback: retornar 0 en lugar de propagar
        return 0


def effective_profile_dict() -> dict:
    """Devuelve un dict completo con todos los campos, usando la cascada effective().
    Útil para inyectar como __PROFILE__ en el template."""
    return {
        "name": effective("name") or "",
        "email": effective("email") or "",
        "birthdate": effective("birthdate") or "",
        "sex": effective("sex") or "M",
        "waist_cm": effective("waist_cm"),
        "height_cm": effective("height_cm"),
        "weight_kg": effective("weight_kg"),
        "sleep_target_min": effective("sleep_target_min"),
        "sleep_goal_min": effective_sleep_goal(),
        "steps_target": effective("steps_target"),
        "locale": effective("locale") or "es",
        "units": effective("units") or "metric",
        "source": effective("source") or "google_health",
        "sources": effective_sources(),
        "onboarded": is_onboarded(),
        "age": current_age(),
        "goals": effective("goals") or [],
        "injuries": effective("injuries") or [],
        "conditions": effective("conditions") or [],
        "medications": effective("medications") or [],
        "cycle_tracking": bool(effective("cycle_tracking")),
        "notifications": effective_notifications(),
    }


def effective_notifications() -> dict:
    """dict `notifications` mergeado con los defaults (perfiles viejos sin la
    clave, o con solo algunos subcampos seteados tras un PUT parcial, caen a
    los defaults para los campos faltantes -> cero migración necesaria)."""
    raw = effective("notifications")
    merged = dict(_DEFAULTS["notifications"])
    if isinstance(raw, dict):
        merged.update({k: v for k, v in raw.items() if k in merged})
    return merged
