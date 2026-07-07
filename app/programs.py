"""
programs.py — Biblioteca de programas predefinidos del coach (Roadmap P1, F4,
paso 4).

CATÁLOGO fijo en código (`_CATALOG`), NO archivos sueltos ni generación por
LLM (fuera de alcance del roadmap — v2). 4 programas plantilla:
    sleep_reset   (14 días) — recuperar higiene de sueño
    aerobic_base  (28 días) — base aeróbica progresiva
    strength_3x   (28 días) — fuerza 3x/semana
    stress_reset  (14 días) — manejo de estrés / recuperación mental

Cada programa es una lista de tareas por día-índice (0-based): {task_key,
kind, params, light: {task_key, params}}. `kind` ∈ {sleep, cardio, strength,
habit} — determina cómo se evalúa la adherencia AUTO (ver app/plan_store.py).

Motor de adaptación PURO (`task_for_day`): el LLM NO decide la tarea —
determinismo auditable, mismo principio que app/insights.py. Regla (criterio
2 del roadmap): si el día actual tiene `recovery < 34` O `acwr_zone` en
("precaucion","alto") -> se sustituye por la variante light y se marca
`adapted: true` con la razón. Sin dato de recovery -> tarea normal (ausencia
de dato ≠ malo, patrón consistente del resto del repo).

i18n: los textos (nombre/descripción del programa, texto de cada tarea) NO
viven aquí como strings crudos — son CLAVES i18n (`program_<id>_name`,
`program_<id>_desc`, y el propio `task_key` -que YA lleva el prefijo
"task_..."- como clave) resueltas por app/i18n.py en get_catalog()/
task_for_day() según el locale pedido. Este módulo en sí es puro (sin I/O) y
Python 3.9-compatible.
"""
from __future__ import annotations

from typing import Any, Optional

from app.i18n import tr as _tr

# ── Umbrales de adaptación (criterio 2 del roadmap) ─────────────────────────
_RECOVERY_LOW_THRESHOLD = 34
_ACWR_CAUTION_ZONES = ("precaucion", "alto")


def _task(task_key: str, kind: str, params: Optional[dict] = None,
          light_task_key: Optional[str] = None,
          light_params: Optional[dict] = None) -> dict:
    """Helper interno para construir una entrada de tarea del catálogo (evita
    repetir la forma del dict a mano en las ~14-28 entradas por programa)."""
    entry: dict = {"task_key": task_key, "kind": kind, "params": params or {}}
    if light_task_key:
        entry["light"] = {"task_key": light_task_key, "params": light_params or {}}
    else:
        # Sin variante light explícita: la versión light es la MISMA tarea
        # con params reducidos si se dieron, o la tarea tal cual (algunas
        # tareas -ej. "respira 10 min"- ya son de baja carga y no necesitan
        # una variante distinta).
        entry["light"] = {"task_key": task_key, "params": (light_params or params or {})}
    return entry


# ── sleep_reset (14 días) ────────────────────────────────────────────────────
# Progresión: rutina de horario fijo -> higiene de pantallas -> consolidación.
_SLEEP_RESET_DAYS = [
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_no_screens_1h", "habit", {"habit": "screen_bed"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_no_caffeine_pm", "habit", {"habit": "caffeine_late"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_wind_down", "habit", {"habit": "reading_bed"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_no_screens_1h", "habit", {"habit": "screen_bed"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_no_caffeine_pm", "habit", {"habit": "caffeine_late"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_wind_down", "habit", {"habit": "reading_bed"}),
    _task("task_sleep_fixed_bedtime", "sleep", {"min": 0}),
    _task("task_sleep_consolidation", "sleep", {"min": 0}),
]

# ── aerobic_base (28 días) ───────────────────────────────────────────────────
# 4 semanas, ciclo de 7 días: 3 sesiones cardio ligero/moderado + descanso activo.
_AEROBIC_WEEK = [
    _task("task_cardio_easy", "cardio", {"min": 30}, light_params={"min": 15}),
    _task("task_rest_active", "habit", {"habit": "stretching"}),
    _task("task_cardio_moderate", "cardio", {"min": 35}, light_task_key="task_cardio_easy", light_params={"min": 15}),
    _task("task_rest_active", "habit", {"habit": "stretching"}),
    _task("task_cardio_easy", "cardio", {"min": 30}, light_params={"min": 15}),
    _task("task_cardio_long", "cardio", {"min": 45}, light_task_key="task_cardio_easy", light_params={"min": 20}),
    _task("task_rest_full", "habit", {"habit": "nap_today"}),
]
_AEROBIC_BASE_DAYS = _AEROBIC_WEEK * 4

# ── strength_3x (28 días) ────────────────────────────────────────────────────
# 4 semanas, ciclo de 7 días: 3 sesiones de fuerza + cardio ligero + descanso.
_STRENGTH_WEEK = [
    _task("task_strength_full", "strength", {"min": 40}, light_params={"min": 20}),
    _task("task_cardio_easy", "cardio", {"min": 20}),
    _task("task_strength_full", "strength", {"min": 40}, light_params={"min": 20}),
    _task("task_rest_active", "habit", {"habit": "stretching"}),
    _task("task_strength_full", "strength", {"min": 40}, light_params={"min": 20}),
    _task("task_cardio_easy", "cardio", {"min": 20}),
    _task("task_rest_full", "habit", {"habit": "nap_today"}),
]
_STRENGTH_3X_DAYS = _STRENGTH_WEEK * 4

# ── stress_reset (14 días) ───────────────────────────────────────────────────
_STRESS_RESET_DAYS = [
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_meditation", "habit", {"habit": "meditation"}),
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_cold_or_sauna", "habit", {"habit": "cold_exposure"}),
    _task("task_meditation", "habit", {"habit": "meditation"}),
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_digital_detox", "habit", {"habit": "screen_bed"}),
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_meditation", "habit", {"habit": "meditation"}),
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_cold_or_sauna", "habit", {"habit": "sauna"}),
    _task("task_meditation", "habit", {"habit": "meditation"}),
    _task("task_breathwork", "habit", {"habit": "breathwork"}),
    _task("task_stress_reset_close", "habit", {"habit": "meditation"}),
]

_CATALOG: dict[str, dict] = {
    "sleep_reset": {"duration_days": 14, "days": _SLEEP_RESET_DAYS},
    "aerobic_base": {"duration_days": 28, "days": _AEROBIC_BASE_DAYS},
    "strength_3x": {"duration_days": 28, "days": _STRENGTH_3X_DAYS},
    "stress_reset": {"duration_days": 14, "days": _STRESS_RESET_DAYS},
}

PROGRAM_IDS = tuple(_CATALOG.keys())


def program_exists(program_id: Any) -> bool:
    return isinstance(program_id, str) and program_id in _CATALOG


def _program_duration(program_id: str) -> int:
    return _CATALOG[program_id]["duration_days"]


def get_catalog(locale: str = "es") -> list[dict]:
    """Catálogo completo localizado: [{id, duration_days, name, description}].
    Nunca lanza — un locale desconocido cae a "es" (mismo criterio que
    app/i18n.py::tr). Usado por GET /api/programs y la sección 'Más'."""
    out = []
    try:
        for pid in PROGRAM_IDS:
            out.append({
                "id": pid,
                "duration_days": _program_duration(pid),
                "name": _tr(f"program_{pid}_name", locale),
                "description": _tr(f"program_{pid}_desc", locale),
            })
    except Exception:
        return out
    return out


def _acwr_is_caution(summary: Optional[dict]) -> bool:
    if not summary:
        return False
    zone = summary.get("acwr_zone")
    return zone in _ACWR_CAUTION_ZONES


def _recovery_is_low(today_row: Optional[dict]) -> bool:
    if not today_row:
        return False
    rec = today_row.get("recovery")
    if rec is None:
        return False
    try:
        return float(rec) < _RECOVERY_LOW_THRESHOLD
    except (TypeError, ValueError):
        return False


def task_for_day(program_id: str, day_index: int, today_row: Optional[dict] = None,
                  summary: Optional[dict] = None, locale: str = "es") -> Optional[dict]:
    """Tarea determinista del día `day_index` (0-based) del programa
    `program_id`, adaptada según recovery/ACWR de hoy (criterio 2 del
    roadmap).

    Devuelve:
        {task_key, kind, params, label, adapted: bool, adapted_reason: str|None}
    o None si `program_id` no existe o `day_index` está fuera de rango
    (día <0 o >= duración -> el caller de plan_store.py decide "completed").

    Regla de adaptación (determinista, auditable — el LLM NO decide esto):
        recovery de HOY < 34  O  acwr_zone en ("precaucion","alto")
        -> se sustituye por la variante `light` de la tarea, adapted=True.
    Sin dato de recovery/acwr -> tarea normal (ausencia de dato ≠ señal mala).
    Nunca lanza."""
    try:
        if not program_exists(program_id):
            return None
        days = _CATALOG[program_id]["days"]
        if day_index is None or day_index < 0 or day_index >= len(days):
            return None

        base_task = days[day_index]
        low_recovery = _recovery_is_low(today_row)
        acwr_caution = _acwr_is_caution(summary)
        adapted = bool(low_recovery or acwr_caution)

        chosen = base_task["light"] if adapted else base_task
        reason = None
        if adapted:
            if low_recovery and acwr_caution:
                reason = "task_adapted_reason_both"
            elif low_recovery:
                reason = "task_adapted_reason_recovery"
            else:
                reason = "task_adapted_reason_acwr"

        return {
            "task_key": chosen["task_key"],
            "kind": base_task["kind"],
            "params": chosen.get("params") or {},
            "label": _tr(chosen["task_key"], locale),
            "adapted": adapted,
            "adapted_reason": _tr(reason, locale) if reason else None,
        }
    except Exception:
        return None


def program_duration(program_id: str) -> Optional[int]:
    """Duración en días del programa, o None si no existe. Nunca lanza."""
    try:
        if not program_exists(program_id):
            return None
        return _program_duration(program_id)
    except Exception:
        return None
