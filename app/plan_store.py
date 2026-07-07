"""
plan_store.py — Estado del plan activo del usuario (Roadmap P1, F4, paso 5).

Persistencia: data/users/<uid>/plan_log.json (patrón EXACTO de journal.py):
    {active: {program_id, started_date, checks: {"YYYY-MM-DD": "auto"|"manual"|null}},
     history: [{program_id, started_date, ended_date, ended_reason}]}

Un solo plan activo a la vez (iniciar con otro activo -> el caller de main.py
responde 409). load/save NUNCA lanzan; escritura atómica vía
fsutil.atomic_write_text (mismo patrón journal/coach_store).

Adherencia CALCULADA AL LEER (`plan_status()` evalúa checks auto contra el
dataset del momento) — solo los checks MANUALES se persisten. Esto evita un
job/cron y el estado nunca se desincroniza del dataset (roadmap "Arquitectura
F4"). Por día transcurrido: check manual explícito SIEMPRE gana; si no hay
manual, se evalúa auto por tipo de tarea (criterio 5 del roadmap):
    sleep    -> asleep >= need - 30min esa noche
    cardio   -> >= params.min minutos de exercises NO-fuerza ese día
    strength -> >= params.min minutos de fuerza (strength_minutes())
    habit    -> el hábito correspondiente del journal marcado "sí" ese día

None-safe en todo: dataset vacío, journal vacío, día sin dato -> el día
cuenta como "no cumplido" (False), NUNCA crashea ni lanza.
"""
from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.fsutil import atomic_write_text
from app import programs as _programs
from app.load import strength_minutes as _strength_minutes
from app.sleep_scores import sleep_need_min as _sleep_need_min

logger = logging.getLogger("vitals.plan_store")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR
_PLAN_LOG_FILE = _DATA_DIR / "plan_log.json"

# Minutos de tolerancia bajo el need para que la noche cuente como "cumplida"
# (criterio 5 del roadmap: `asleep >= need - 30min`).
_SLEEP_ADHERENCE_SLACK_MIN = 30


def _plan_log_path() -> Path:
    """Ruta a plan_log.json del usuario activo (household-aware, mismo patrón
    que journal._journal_log_path()). Fuera de un request household-aware,
    usa _PLAN_LOG_FILE tal cual (compat total)."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "plan_log.json"
    except Exception:
        pass
    return _PLAN_LOG_FILE


# ── Persistencia atómica (patrón cycle.py/journal.py — nunca lanza) ─────────

def _empty_plan_log() -> dict:
    return {"active": None, "history": []}


def load_plan_log() -> dict:
    """Lee data/users/<uid>/plan_log.json -> {active, history}.
    Si no existe o está corrupto -> estructura vacía (nunca lanza)."""
    empty = _empty_plan_log()
    try:
        path = _plan_log_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("plan_log.json no es dict; usando estructura vacía.")
            return empty
        data.setdefault("active", None)
        data.setdefault("history", [])
        if not isinstance(data.get("history"), list):
            data["history"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("plan_log.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo plan_log.json: %s", exc)
        return empty


def save_plan_log(d: dict) -> None:
    """Guarda plan_log.json con escritura ATÓMICA. Nunca lanza."""
    try:
        path = _plan_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("active", None)
        d.setdefault("history", [])
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando plan_log.json: %s", exc)


# ── Mutaciones ───────────────────────────────────────────────────────────────

def start_plan(program_id: str, started_date: Optional[str] = None) -> Optional[dict]:
    """Inicia un programa. Devuelve el estado `active` creado, o None si:
      - `program_id` no existe en el catálogo, o
      - ya hay un plan activo (el caller de main.py debe checar
        `has_active_plan()` ANTES y responder 409 — esta función es la
        mutación pura, no decide el código HTTP).
    `started_date` default hoy (ISO). Nunca lanza."""
    try:
        if not _programs.program_exists(program_id):
            return None
        log = load_plan_log()
        if log.get("active"):
            return None
        date_str = started_date or datetime.date.today().isoformat()
        try:
            datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None
        active = {"program_id": program_id, "started_date": date_str, "checks": {}}
        log["active"] = active
        save_plan_log(log)
        return active
    except Exception as exc:
        logger.error("start_plan falló: %s", exc)
        return None


def abandon_plan(ended_date: Optional[str] = None) -> bool:
    """Abandona el plan activo (pasa a `history`). True si había uno activo y
    se abandonó, False si no había plan activo. Nunca lanza."""
    try:
        log = load_plan_log()
        active = log.get("active")
        if not active:
            return False
        date_str = ended_date or datetime.date.today().isoformat()
        entry = dict(active)
        entry["ended_date"] = date_str
        entry["ended_reason"] = "abandoned"
        history = log.get("history") or []
        history.append(entry)
        log["history"] = history
        log["active"] = None
        save_plan_log(log)
        return True
    except Exception as exc:
        logger.error("abandon_plan falló: %s", exc)
        return False


def manual_check(date: Optional[str] = None) -> Optional[dict]:
    """Marca el día `date` (default hoy) como cumplido MANUAL — sobreescribe
    cualquier evaluación auto para ese día (criterio 5: "el check manual
    siempre puede sobreescribir"). Devuelve el `active` actualizado, o None
    si no hay plan activo o la fecha es inválida. Nunca lanza."""
    try:
        log = load_plan_log()
        active = log.get("active")
        if not active:
            return None
        date_str = date or datetime.date.today().isoformat()
        try:
            datetime.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            return None
        checks = active.get("checks") or {}
        checks[date_str] = "manual"
        active["checks"] = checks
        log["active"] = active
        save_plan_log(log)
        return active
    except Exception as exc:
        logger.error("manual_check falló: %s", exc)
        return None


def has_active_plan() -> bool:
    try:
        return bool(load_plan_log().get("active"))
    except Exception:
        return False


# ── Adherencia AUTO (criterio 5 del roadmap) ────────────────────────────────

def _day_row_for_date(days: list, date_str: str) -> Optional[dict]:
    for d in days or []:
        if isinstance(d, dict) and d.get("date") == date_str:
            return d
    return None


def _journal_entry_for_date(journal: Optional[dict], date_str: str) -> dict:
    if not journal:
        return {}
    entry = (journal.get("entries") or {}).get(date_str)
    return dict(entry) if isinstance(entry, dict) else {}


def _auto_adherence_for_task(task: dict, date_str: str, days: list,
                              exercises: list, journal: Optional[dict]) -> bool:
    """Evalúa adherencia AUTO de un día concreto contra el dataset, según
    `kind` de la tarea (criterio 5). Nunca lanza — cualquier dato ralo
    degrada a False (no cumplido), NUNCA a excepción."""
    try:
        kind = task.get("kind")
        params = task.get("params") or {}
        day_row = _day_row_for_date(days, date_str)

        if kind == "sleep":
            if not day_row:
                return False
            asleep = day_row.get("asleep")
            if asleep is None:
                return False
            need = _sleep_need_min(days, {}, None)
            if need is None:
                return False
            return float(asleep) >= (need - _SLEEP_ADHERENCE_SLACK_MIN)

        if kind == "cardio":
            min_required = params.get("min")
            if min_required is None:
                return False
            day_exercises = [e for e in (exercises or []) if isinstance(e, dict) and e.get("date") == date_str]
            from app.load import STRENGTH_RE
            non_strength_min = 0
            for e in day_exercises:
                haystack = f"{e.get('type', '')} {e.get('name', '')}"
                if not STRENGTH_RE.search(haystack):
                    non_strength_min += e.get("dur_min", 0) or 0
            return non_strength_min >= float(min_required)

        if kind == "strength":
            min_required = params.get("min")
            if min_required is None:
                return False
            strength_min = _strength_minutes(exercises or [], dates={date_str})
            return strength_min >= float(min_required)

        if kind == "habit":
            habit_key = params.get("habit")
            if not habit_key:
                return False
            entry = _journal_entry_for_date(journal, date_str)
            return bool(entry.get(habit_key))

        return False
    except Exception as exc:
        logger.warning("_auto_adherence_for_task falló para %s (%s): %s", date_str, kind, exc)
        return False


def _dates_between(start: str, end: str) -> list:
    """Lista de fechas ISO entre start y end (inclusive), o [] si son
    inválidas/start>end. Nunca lanza."""
    try:
        d0 = datetime.date.fromisoformat(start)
        d1 = datetime.date.fromisoformat(end)
        if d1 < d0:
            return []
        out = []
        cur = d0
        while cur <= d1:
            out.append(cur.isoformat())
            cur += datetime.timedelta(days=1)
        return out
    except Exception:
        return []


def plan_status(dataset: Optional[dict], locale: str = "es") -> Optional[dict]:
    """Estado completo del plan activo para GET /api/plan: día N/M, tarea de
    hoy (adaptada), adherencia % (total y `adherence_pct_7d` — ventana de los
    últimos 7 días evaluables, usada por coach_chat para el bloque PLAN
    ACTIVO). None si no hay plan activo (el caller de main.py responde con
    `{active: null}` o similar).

    Bordes cubiertos (criterio 4 del roadmap):
      - plan iniciado HOY: día 1, 0 días evaluables -> adherence_pct None.
      - plan que terminó (today_index >= duración): status "completed", NO
        crash — sigue devolviendo el resumen con is_completed=True.
      - fechas sin datos en el dataset: cuentan como no-cumplidas (False).
      - journal vacío: hábitos siempre evalúan a no-cumplido (False).
    Nunca lanza."""
    try:
        log = load_plan_log()
        active = log.get("active")
        if not active:
            return None

        program_id = active.get("program_id")
        started_date = active.get("started_date")
        checks = active.get("checks") or {}
        if not _programs.program_exists(program_id) or not started_date:
            return None

        duration = _programs.program_duration(program_id)
        if duration is None:
            return None

        dataset = dataset or {}
        days = dataset.get("days") or []
        exercises = dataset.get("exercises") or []
        summary = dataset.get("summary") or {}

        # today_str: última fecha del dataset si existe, si no la fecha real
        # de hoy (permite operar incluso sin sync reciente).
        today_str = days[-1].get("date") if days and isinstance(days[-1], dict) and days[-1].get("date") else datetime.date.today().isoformat()

        try:
            start_d = datetime.date.fromisoformat(started_date)
            today_d = datetime.date.fromisoformat(today_str)
        except (ValueError, TypeError):
            return None

        day_index = (today_d - start_d).days  # 0-based
        is_completed = day_index >= duration
        # 1-based, capado a [0, duration-1] ANTES de sumar 1 — day_index puede
        # ser negativo si `today_str` (última fecha del dataset) es anterior a
        # `started_date` (ej. dataset demo con fecha sintética vs plan iniciado
        # con la fecha real de hoy): sin este clamp, day_number salía <=0.
        day_number = max(0, min(day_index, duration - 1)) + 1

        today_row = _day_row_for_date(days, today_str)

        # Tarea de hoy (adaptada) — solo si el plan sigue vigente (no completed).
        today_task = None
        if not is_completed and day_index >= 0:
            today_task = _programs.task_for_day(program_id, day_index, today_row, summary, locale)

        # ── Adherencia: por cada día transcurrido (started_date..min(today,
        # fin del programa) - 1, o hasta today si el plan sigue vigente) ──
        eval_end_index = min(day_index, duration) - 1  # último índice evaluable (ayer si hoy sigue en curso)
        journal = None
        try:
            from app import journal as _journal_mod
            journal = _journal_mod.load_journal()
        except Exception:
            journal = None

        evaluable_dates = []
        if eval_end_index >= 0:
            end_date = (start_d + datetime.timedelta(days=eval_end_index)).isoformat()
            evaluable_dates = _dates_between(started_date, end_date)

        n_evaluable = len(evaluable_dates)
        n_met = 0
        met_by_date: dict = {}
        for i, date_str in enumerate(evaluable_dates):
            manual_or_auto = checks.get(date_str)
            if manual_or_auto == "manual":
                n_met += 1
                met_by_date[date_str] = True
                continue
            task = _programs.task_for_day(program_id, i, _day_row_for_date(days, date_str), summary, locale)
            met = bool(task and _auto_adherence_for_task(task, date_str, days, exercises, journal))
            if met:
                n_met += 1
            met_by_date[date_str] = met

        adherence_pct = round(n_met / n_evaluable * 100) if n_evaluable > 0 else None

        # Adherencia 7d (ADITIVA, para el bloque del coach — criterio 7 del
        # roadmap): mismos datos ya evaluados arriba, solo se recorta a la
        # ventana de los últimos 7 días evaluables (no re-evalúa nada).
        last7_dates = evaluable_dates[-7:]
        n_met_7d = sum(1 for d in last7_dates if met_by_date.get(d))
        adherence_pct_7d = round(n_met_7d / len(last7_dates) * 100) if last7_dates else None

        return {
            "program_id": program_id,
            "started_date": started_date,
            "day_number": day_number,
            "duration_days": duration,
            "is_completed": is_completed,
            "today_task": today_task,
            "adherence_pct": adherence_pct,
            "adherence_pct_7d": adherence_pct_7d,
            "n_evaluable_days": n_evaluable,
            "n_met_days": n_met,
        }
    except Exception as exc:
        logger.warning("plan_status falló (degradando a None): %s", exc)
        return None
