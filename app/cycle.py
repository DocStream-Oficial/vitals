"""
cycle.py — Motor de salud femenina / seguimiento de ciclo (Fase 7).

Módulo OPT-IN: nada de esto se evalúa si profile.cycle_tracking es False.
compute_cycle_state() es el punto de entrada único; devuelve None si el
toggle está apagado (criterio #1 del roadmap — ninguna fuga de datos).

Persistencia: data/cycle_log.json — verdad-terreno del usuario (periodos +
síntomas + tests de ovulación). Escritura ATÓMICA (.tmp + os.replace), mismo
patrón que profile.py / coach_store.py. Nunca lanza (loguea en error).

Funciones PURAS (sin IO salvo los helpers de persistencia explícitos), sin
dependencias circulares. Ninguna función de cómputo debe lanzar — cualquier
dato ralo/nulo/desordenado degrada a confianza "low", nunca crashea (criterio
#9: nunca crashea).

NO es un dispositivo médico: no diagnostica embarazo, patología ni fertilidad
real. Ver disclaimer (i18n key 'cycle_disclaimer'), obligatorio en toda salida
que muestre ventana fértil, retraso o señal de peri/menopausia.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import statistics
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vitals.cycle")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_CYCLE_LOG_FILE = _DATA_DIR / "cycle_log.json"

# ── Constantes auditables (roadmap §Motor) ──────────────────────────────────
DEFAULT_CYCLE_LEN = 28
DEFAULT_PERIOD_LEN = 5
LUTEAL_LEN = 14
TEMP_SHIFT_C = 0.2
DELAY_THRESHOLD_DAYS = 2
PERI_RANGE_DAYS = 9
SKIPPED_CYCLE_DAYS = 60
MENOPAUSE_MONTHS = 12
FERTILE_PRE = 5
FERTILE_POST = 1

# Historial mínimo (días) para emitir CUALQUIER señal de peri/menopausia —
# por debajo de esto: insufficient_history, cero falsos positivos (criterio #6).
_PERI_MIN_HISTORY_DAYS = 180

# Sostenimiento del shift lútero de temperatura (días consecutivos por encima
# del umbral para confirmar el shift, no solo un pico aislado).
_TEMP_SHIFT_SUSTAIN_DAYS = 3


def _cycle_log_path() -> Path:
    """Ruta a cycle_log.json del usuario activo (Fase 8D, paso D3: household).
    Fuera de un request household-aware (is_context_active()=False — tests
    preexistentes que monkeypatchean _CYCLE_LOG_FILE directamente, scripts),
    usa _CYCLE_LOG_FILE tal cual: comportamiento idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "cycle_log.json"
    except Exception:
        pass
    return _CYCLE_LOG_FILE


# ── Persistencia atómica (patrón profile.py / coach_store.py) ───────────────

def load_cycle_log() -> dict:
    """Lee data/cycle_log.json → dict {periods, symptoms, ovulation_tests, updated}.
    Si no existe o está corrupto → estructura vacía (nunca lanza)."""
    empty = {"periods": [], "symptoms": [], "ovulation_tests": [], "updated": None}
    try:
        path = _cycle_log_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("cycle_log.json no es dict; usando estructura vacía.")
            return empty
        # Normaliza claves faltantes (perfiles/logs viejos parciales → cero migración)
        data.setdefault("periods", [])
        data.setdefault("symptoms", [])
        data.setdefault("ovulation_tests", [])
        data.setdefault("updated", None)
        if not isinstance(data.get("periods"), list):
            data["periods"] = []
        if not isinstance(data.get("symptoms"), list):
            data["symptoms"] = []
        if not isinstance(data.get("ovulation_tests"), list):
            data["ovulation_tests"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("cycle_log.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo cycle_log.json: %s", exc)
        return empty


def save_cycle_log(d: dict) -> None:
    """Guarda cycle_log.json con escritura ATÓMICA (.tmp + os.replace).
    Nunca lanza excepción (loguea en error)."""
    try:
        path = _cycle_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.error("Error guardando cycle_log.json: %s", exc)


# ── Helpers de fecha (None-safe) ─────────────────────────────────────────────

def _parse_date(s: Any) -> Optional[datetime.date]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def _sorted_periods(periods: list) -> list:
    """Periodos válidos (con 'start' parseable), ordenados por fecha ascendente.
    Tolerante a desorden/duplicados/entradas basura (criterio: robustez del motor)."""
    valid = []
    for p in periods or []:
        if not isinstance(p, dict):
            continue
        d = _parse_date(p.get("start"))
        if d is None:
            continue
        valid.append((d, p))
    valid.sort(key=lambda x: x[0])
    return [p for _, p in valid]


# ── Cálculo de longitudes de ciclo ───────────────────────────────────────────

def _cycle_lengths(periods: list) -> list[int]:
    """Longitudes (días) entre inicios consecutivos de periodo. Requiere ≥2
    periodos válidos y ordenados; ignora saltos negativos/cero (fechas basura)."""
    sorted_p = _sorted_periods(periods)
    lengths: list[int] = []
    for i in range(1, len(sorted_p)):
        d0 = _parse_date(sorted_p[i - 1].get("start"))
        d1 = _parse_date(sorted_p[i].get("start"))
        if d0 is None or d1 is None:
            continue
        delta = (d1 - d0).days
        if delta > 0:
            lengths.append(delta)
    return lengths


def _median_cycle_length(lengths: list[int]) -> int:
    """Mediana de longitudes de ciclo; default DEFAULT_CYCLE_LEN si <1 longitud."""
    if not lengths:
        return DEFAULT_CYCLE_LEN
    try:
        return int(round(statistics.median(lengths)))
    except Exception:
        return DEFAULT_CYCLE_LEN


# ── Inferencia de ovulación por temperatura ──────────────────────────────────

def _infer_ovulation_from_temp(days: list[dict], last_period_start: Optional[datetime.date]) -> Optional[dict]:
    """Busca nadir folicular + shift lúteo sostenido (≥TEMP_SHIFT_C sobre baseline
    folicular, sostenido ≥_TEMP_SHIFT_SUSTAIN_DAYS días) en la serie skin_temp desde
    el último inicio de periodo.

    Devuelve {day_of_cycle, date, confirmed: bool} o None si no hay suficiente
    señal. Nunca lanza — datos ralos/faltantes devuelven None."""
    if not days or last_period_start is None:
        return None

    try:
        # Serie skin_temp indexada por fecha, solo desde el último inicio de periodo.
        series: list[tuple[datetime.date, float]] = []
        for d in days:
            dt = _parse_date(d.get("date"))
            if dt is None or dt < last_period_start:
                continue
            temp = d.get("skin_temp")
            if temp is None:
                continue
            try:
                series.append((dt, float(temp)))
            except (TypeError, ValueError):
                continue

        series.sort(key=lambda x: x[0])
        if len(series) < (_TEMP_SHIFT_SUSTAIN_DAYS + 2):
            return None

        # Baseline folicular: media de la primera mitad de la ventana disponible
        # (aproximación transparente/auditable, sin ML).
        half = max(2, len(series) // 2)
        follicular_vals = [v for _, v in series[:half]]
        baseline = statistics.mean(follicular_vals) if follicular_vals else None
        if baseline is None:
            return None

        # Buscar el primer día con shift sostenido ≥TEMP_SHIFT_C por encima del baseline
        # durante ≥_TEMP_SHIFT_SUSTAIN_DAYS días consecutivos.
        for i in range(len(series)):
            dt, val = series[i]
            if val - baseline < TEMP_SHIFT_C:
                continue
            # Verificar sostenimiento
            window = series[i:i + _TEMP_SHIFT_SUSTAIN_DAYS]
            if len(window) < _TEMP_SHIFT_SUSTAIN_DAYS:
                # No hay suficientes días restantes para confirmar sostenimiento
                break
            if all((v - baseline) >= TEMP_SHIFT_C for _, v in window):
                # Ovulación retrospectiva ≈ 1 día antes del primer día de shift
                ov_date = dt - datetime.timedelta(days=1)
                day_of_cycle = (ov_date - last_period_start).days + 1
                return {
                    "day_of_cycle": day_of_cycle,
                    "date": ov_date.isoformat(),
                    "confirmed": True,
                }
        return None
    except Exception as exc:
        logger.warning("_infer_ovulation_from_temp falló (degradando a None): %s", exc)
        return None


# ── Fase del ciclo ────────────────────────────────────────────────────────────

def _period_len(periods: list) -> int:
    """Duración típica del periodo (días) desde el log, o default."""
    sorted_p = _sorted_periods(periods)
    durations = []
    for p in sorted_p:
        start = _parse_date(p.get("start"))
        end = _parse_date(p.get("end"))
        if start and end and end >= start:
            durations.append((end - start).days + 1)
    if durations:
        try:
            return int(round(statistics.median(durations)))
        except Exception:
            pass
    return DEFAULT_PERIOD_LEN


def _phase_for_day(cycle_day: int, period_len: int, median_len: int,
                    ovulation_day: Optional[int]) -> str:
    """Determina phase ∈ {menstrual, follicular, ovulatory, luteal}."""
    if cycle_day <= period_len:
        return "menstrual"

    ov_day = ovulation_day if ovulation_day is not None else max(period_len + 1, median_len - LUTEAL_LEN)

    if abs(cycle_day - ov_day) <= 1:
        return "ovulatory"
    if cycle_day < ov_day:
        return "follicular"
    return "luteal"


# ── Peri/menopausia ───────────────────────────────────────────────────────────

def _history_span_days(periods: list) -> int:
    """Días entre el primer y el último periodo registrado (0 si <2)."""
    sorted_p = _sorted_periods(periods)
    if len(sorted_p) < 2:
        return 0
    first = _parse_date(sorted_p[0].get("start"))
    last = _parse_date(sorted_p[-1].get("start"))
    if first is None or last is None:
        return 0
    return max(0, (last - first).days)


def _assess_menopause(periods: list, today: datetime.date) -> dict:
    """Evalúa señales de peri/menopausia. Historial <_PERI_MIN_HISTORY_DAYS →
    insufficient_history, CERO falsos positivos (criterio #6)."""
    sorted_p = _sorted_periods(periods)
    history_days = _history_span_days(periods)

    if history_days < _PERI_MIN_HISTORY_DAYS or len(sorted_p) < 3:
        return {"stage": "insufficient_history", "signals": [], "confidence": "low"}

    lengths = _cycle_lengths(periods)
    signals: list[str] = []

    # (a) variabilidad de longitud creciente (rango > PERI_RANGE_DAYS)
    if lengths:
        length_range = max(lengths) - min(lengths)
        if length_range > PERI_RANGE_DAYS:
            signals.append("length_variability")

    # (b) ciclo saltado (gap ≥ SKIPPED_CYCLE_DAYS entre inicios consecutivos)
    if any(l >= SKIPPED_CYCLE_DAYS for l in lengths):
        signals.append("skipped_cycle")

    # (c) amenorrea ≥ MENOPAUSE_MONTHS meses desde el último periodo registrado
    last_start = _parse_date(sorted_p[-1].get("start"))
    amenorrhea = False
    if last_start is not None:
        gap_days = (today - last_start).days
        if gap_days >= MENOPAUSE_MONTHS * 30:
            signals.append("amenorrhea_12mo")
            amenorrhea = True

    if amenorrhea:
        stage = "menopause_possible"
        confidence = "medium"
    elif signals:
        stage = "perimenopause_possible"
        confidence = "medium" if len(signals) >= 2 else "low"
    else:
        stage = "premenopausal"
        confidence = "low"

    return {"stage": stage, "signals": signals, "confidence": confidence}


# ── Orquestador ───────────────────────────────────────────────────────────────

def compute_cycle_state(days: list[dict], cycle_log: Optional[dict], profile: Optional[dict]) -> Optional[dict]:
    """Orquestador principal. Devuelve None si profile.cycle_tracking es falso
    (criterio #1: opt-in estricto, ninguna fuga de datos con el toggle apagado).

    Nunca lanza — cualquier error interno degrada a un estado de baja confianza
    en vez de propagar la excepción (criterio #9: nunca crashea)."""
    try:
        if not profile or not profile.get("cycle_tracking"):
            return None

        cycle_log = cycle_log or {}
        periods = cycle_log.get("periods") or []
        days = days or []

        today = datetime.date.today()
        # Si hay días en el dataset, usar la fecha del día más reciente como "hoy"
        # lógico (consistente con el resto de la app, que ancla a dataset days[-1]).
        if days:
            last_day_date = _parse_date(days[-1].get("date"))
            if last_day_date is not None:
                today = last_day_date

        sorted_p = _sorted_periods(periods)
        lengths = _cycle_lengths(periods)
        median_len = _median_cycle_length(lengths)
        period_len = _period_len(periods)

        n_cycles = len(lengths)
        history_days = _history_span_days(periods)

        # Suficiencia de datos
        if n_cycles >= 3 and history_days >= 90:
            sufficiency_level = "high"
        elif n_cycles >= 1:
            sufficiency_level = "medium"
        else:
            sufficiency_level = "low"

        data_sufficiency = {
            "cycles_logged": n_cycles,
            "history_days": history_days,
            "level": sufficiency_level,
        }

        sources_used: list[str] = []
        if any(p.get("source") == "healthkit" for p in sorted_p):
            sources_used.append("healthkit")
        if any(p.get("source") == "manual" or p.get("source") is None for p in sorted_p):
            sources_used.append("manual")

        if not sorted_p:
            # Sin ningún periodo registrado: no podemos anclar cycle_day/phase con
            # sentido; devolver estado mínimo pero SIEMPRE enabled=true y con
            # disclaimer (invita a registrar), nunca None (None = toggle off).
            return {
                "enabled": True,
                "cycle_day": None,
                "phase": None,
                "period": {"last_start": None, "predicted_next": None,
                           "days_until": None, "confidence": "low"},
                "fertile_window": None,
                "delay": {"is_delayed": False, "days": 0},
                "menopause": {"stage": "insufficient_history", "signals": [], "confidence": "low"},
                "data_sufficiency": data_sufficiency,
                "disclaimer": "cycle_disclaimer",
                "sources_used": sources_used,
            }

        last_start = _parse_date(sorted_p[-1].get("start"))
        if last_start is None:
            last_start = today

        cycle_day = (today - last_start).days + 1
        if cycle_day < 1:
            cycle_day = 1

        # Inferencia de ovulación por temperatura (retrospectiva sobre el ciclo actual)
        temp_ov = _infer_ovulation_from_temp(days, last_start)
        ovulation_day = temp_ov["day_of_cycle"] if temp_ov else max(period_len + 1, median_len - LUTEAL_LEN)
        ovulation_date = (
            temp_ov["date"] if temp_ov
            else (last_start + datetime.timedelta(days=ovulation_day - 1)).isoformat()
        )

        phase = _phase_for_day(cycle_day, period_len, median_len, ovulation_day if temp_ov else None)

        # Predicción del próximo periodo
        predicted_next_date = last_start + datetime.timedelta(days=median_len)
        days_until = (predicted_next_date - today).days

        if n_cycles >= 3:
            pred_confidence = "high" if lengths and statistics.pstdev(lengths) <= 3 else "medium"
        elif n_cycles >= 1:
            pred_confidence = "medium"
        else:
            pred_confidence = "low"

        period_block = {
            "last_start": last_start.isoformat(),
            "predicted_next": predicted_next_date.isoformat(),
            "days_until": days_until,
            "confidence": pred_confidence,
        }

        # Retraso: hoy > predicho + DELAY_THRESHOLD_DAYS sin nuevo periodo registrado.
        delay_days = (today - predicted_next_date).days
        is_delayed = delay_days > DELAY_THRESHOLD_DAYS
        delay_block = {"is_delayed": is_delayed, "days": max(0, delay_days) if is_delayed else 0}

        # Ventana fértil (config cerrada del usuario: SIEMPRE se incluye, con disclaimer)
        fertile_start = (last_start + datetime.timedelta(days=ovulation_day - 1 - FERTILE_PRE))
        fertile_end = (last_start + datetime.timedelta(days=ovulation_day - 1 + FERTILE_POST))
        fertile_window = {
            "start": fertile_start.isoformat(),
            "end": fertile_end.isoformat(),
            "ovulation_est": ovulation_date,
            "source": "temp+calendar" if temp_ov else "calendar",
        }

        menopause = _assess_menopause(periods, today)

        if temp_ov:
            sources_used.append("inference")

        return {
            "enabled": True,
            "cycle_day": cycle_day,
            "phase": phase,
            "period": period_block,
            "fertile_window": fertile_window,
            "delay": delay_block,
            "menopause": menopause,
            "data_sufficiency": data_sufficiency,
            "disclaimer": "cycle_disclaimer",
            "sources_used": sources_used,
        }
    except Exception as exc:
        logger.warning("compute_cycle_state falló (degradando a None): %s", exc)
        return None
