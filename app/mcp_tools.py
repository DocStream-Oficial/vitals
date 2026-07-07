"""
mcp_tools.py — Funciones PURAS para el MCP server de Vitals.

NO importa `mcp`. Corre en Python 3.9+.
Cada función recibe el dataset (dict) y devuelve un dict o str JSON-serializable.
Tolera dataset vacío ({}) o None → devuelve respuesta razonable, no crashea.

Uso:
    ds = _load_dataset()
    snap = today_snapshot(ds)
    brief = morning_brief(ds)
"""
from __future__ import annotations

import html
import json
import logging
import re
import statistics
from datetime import date
from pathlib import Path
from statistics import mean
from typing import Any, Optional

logger = logging.getLogger("vitals.mcp_tools")

# ── Constante de ruta ─────────────────────────────────────────────────────────
# Importamos settings (solo config, sin mcp) para obtener DATA_DIR.
# Igual que lo hace main.py con DATA_PATH = settings.DATA_DIR / "health_compact.json".
from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

from app.scoring import recent_base


_DATASET_FILE = _DATA_DIR / "health_compact.json"  # legacy — ver _dataset_path()


def _dataset_path() -> Path:
    """Ruta a health_compact.json a leer (Fase 8D, paso D3: household).

    El MCP server (vitals_mcp.py) es un proceso standalone SIN contexto de
    request FastAPI — nunca pasa por el middleware que fija el usuario activo
    (roadmap D3 no lo exige explícitamente para MCP). Para que no quede leyendo
    en el vacío tras la migración a household (que MUEVE health_compact.json
    de la raíz de data/ a data/users/default/), esta función:
        1. Si hay un contexto de request activo (is_context_active()=True,
           caso improbable para MCP pero soportado por si algún día se cablea)
           -> usa ese usuario.
        2. Si NO hay contexto activo (caso real de MCP hoy) pero _DATASET_FILE
           (ruta legacy) ya no existe Y data/users/default/health_compact.json
           SÍ existe -> usa esa (la instancia ya migró; MCP sigue sirviendo al
           usuario default sin configuración adicional).
        3. Si nada de lo anterior aplica -> _DATASET_FILE tal cual (compat
           total con tests preexistentes que monkeypatchean esta constante y
           con instalaciones que aún no migraron).
    Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "health_compact.json"
        if not _DATASET_FILE.exists():
            default_path = _userctx.user_dir(_userctx.DEFAULT_UID) / "health_compact.json"
            if default_path.exists():
                return default_path
    except Exception:
        pass
    return _DATASET_FILE


# ── Carga del dataset ─────────────────────────────────────────────────────────

def _load_dataset() -> dict:
    """
    Lee health_compact.json (ver _dataset_path() para la resolución de ruta).
    Si no existe, está vacío o falla la lectura → devuelve {} (sin excepción).
    """
    path = _dataset_path()
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            return {}
        return data
    except FileNotFoundError:
        logger.warning("Dataset no encontrado: %s", path)
        return {}
    except json.JSONDecodeError as exc:
        logger.warning("Dataset JSON inválido: %s", exc)
        return {}
    except Exception as exc:
        logger.warning("Error cargando dataset: %s", exc)
        return {}


# ── Helpers internos ──────────────────────────────────────────────────────────

def _safe_mean(vals: list) -> Optional[float]:
    return round(mean(vals), 1) if vals else None


def _recovery_estado(rec: float) -> str:
    if rec >= 67:
        return "alta"
    if rec >= 34:
        return "media"
    return "baja"


def _window_vals(days: list, field: str, n: int) -> list:
    """Últimos n días con `field` no-None."""
    return [float(d[field]) for d in days[-n:] if d.get(field) is not None]


def _normalize_html(text: str) -> str:
    """Convierte HTML-escapes a caracteres reales: &lt; → <, &gt; → >, etc."""
    return html.unescape(text)


def _normalize_insight(ins: dict) -> dict:
    """Normaliza todos los campos de texto de un insight (elimina HTML-escapes)."""
    result = {}
    for k, v in ins.items():
        if isinstance(v, str):
            result[k] = _normalize_html(v)
        elif isinstance(v, list):
            result[k] = [_normalize_html(x) if isinstance(x, str) else x for x in v]
        else:
            result[k] = v
    return result


# ── today_snapshot ─────────────────────────────────────────────────────────────

def today_snapshot(ds: Optional[dict]) -> dict:
    """
    Snapshot del día más reciente del dataset.
    Devuelve: fecha, recovery (+estado alta/media/baja), sueño (h y %),
              hrv (+vs base), rhr (+vs base), strain, fuerza_semana_min.

    Si no hay datos → {"status": "sin datos"}.
    """
    if not ds:
        return {"status": "sin datos"}

    days = ds.get("days", [])
    summary = ds.get("summary", {})

    if not days:
        return {"status": "sin datos"}

    today = days[-1]
    hrv_base = recent_base(summary, "hrv")
    rhr_base = recent_base(summary, "rhr")

    snap: dict[str, Any] = {"fecha": today.get("date", "sin fecha")}

    # Recovery
    rec = today.get("recovery")
    if rec is not None:
        snap["recovery"] = int(rec)
        snap["recovery_estado"] = _recovery_estado(float(rec))

    # Sueño
    asleep_min = today.get("asleep")
    sleep_perf = today.get("sleep_perf")
    if asleep_min is not None:
        snap["sueno_h"] = round(float(asleep_min) / 60, 1)
    if sleep_perf is not None:
        snap["sueno_pct"] = int(sleep_perf)

    # HRV
    hrv = today.get("hrv")
    if hrv is not None:
        snap["hrv_ms"] = round(float(hrv), 1)
        if hrv_base is not None:
            snap["hrv_vs_base"] = round(float(hrv) - float(hrv_base), 1)
            snap["hrv_base"] = round(float(hrv_base), 1)

    # RHR
    rhr = today.get("rhr")
    if rhr is not None:
        snap["rhr_bpm"] = int(rhr)
        if rhr_base is not None:
            snap["rhr_vs_base"] = round(float(rhr) - float(rhr_base), 1)
            snap["rhr_base"] = round(float(rhr_base), 1)

    # Strain
    strain = today.get("strain")
    if strain is not None:
        snap["strain"] = round(float(strain), 1)

    # Fuerza semana (vigorous proxy últimos 7 días)
    vig_7 = _window_vals(days, "vigorous", 7)
    snap["fuerza_semana_min"] = int(sum(vig_7)) if vig_7 else 0

    return snap


# ── trends ────────────────────────────────────────────────────────────────────

def trends(ds: Optional[dict]) -> dict:
    """
    Promedios 7d/30d de recovery/hrv/rhr/sueño_h/strain + noches_<7h (últimas 7).
    Tier 2: añade {field}_dir y {field}_sig (dirección+significancia Mann-Kendall 30d)
            para recovery/hrv/rhr/sueno_h.
    Si no hay datos → {"status": "sin datos"}.
    """
    if not ds:
        return {"status": "sin datos"}

    days = ds.get("days", [])
    if not days:
        return {"status": "sin datos"}

    result: dict[str, Any] = {}

    # Importar trend_summary (Tier 2 Feature B) — tolerante a ImportError
    try:
        from app.trends import trend_summary as _trend_summary
        _trends_available = True
    except ImportError:
        _trends_available = False

    for field, label in [("recovery", "recovery_pct"), ("hrv", "hrv_ms"),
                         ("rhr", "rhr_bpm"), ("strain", "strain")]:
        v7 = _window_vals(days, field, 7)
        v30 = _window_vals(days, field, 30)
        result[f"{label}_7d"] = _safe_mean(v7)
        result[f"{label}_30d"] = _safe_mean(v30)
        # Tier 2: tendencia sobre 30d (dirección+significancia)
        if _trends_available and field != "strain":  # strain no lleva tendencia en la spec
            ts = _trend_summary(v30)
            result[f"{label}_dir"] = ts["direction"]
            result[f"{label}_sig"] = ts["significant"]

    # Sueño en horas
    sl7 = _window_vals(days, "asleep", 7)
    sl30 = _window_vals(days, "asleep", 30)
    result["sueno_h_7d"] = round(mean(sl7) / 60, 1) if sl7 else None
    result["sueno_h_30d"] = round(mean(sl30) / 60, 1) if sl30 else None
    result["noches_menos_7h"] = sum(1 for v in sl7 if v / 60 < 7)
    # Tier 2: tendencia sueño
    if _trends_available:
        sl30_h = [v / 60 for v in sl30] if sl30 else []
        ts_sl = _trend_summary(sl30_h)
        result["sueno_h_dir"] = ts_sl["direction"]
        result["sueno_h_sig"] = ts_sl["significant"]

    return result


# ── insights_list ──────────────────────────────────────────────────────────────

def insights_list(ds: Optional[dict]) -> list:
    """
    Lista de alertas de insights.evaluate(ds).
    Normaliza HTML-escapes (&lt; → <) en todos los campos de texto.
    Si no hay datos o no hay insights → [].
    """
    if not ds:
        return []

    try:
        from app.insights import evaluate
        raw = evaluate(ds)
    except Exception as exc:
        logger.error("Error al evaluar insights: %s", exc)
        return []

    return [_normalize_insight(ins) for ins in raw]


# ── bodyage_summary ────────────────────────────────────────────────────────────

def bodyage_summary(ds: Optional[dict]) -> dict:
    """
    Resumen de bodyage: vo2max, fitness_age, body_age, category, penalty + drivers.
    Si no hay datos → {"status": "sin datos"}.
    """
    if not ds:
        return {"status": "sin datos"}

    summary = ds.get("summary", {})
    bodyage = summary.get("bodyage")

    if not bodyage:
        return {"status": "sin datos"}

    result: dict[str, Any] = {
        "body_age": bodyage.get("body_age"),
        "fitness_age": bodyage.get("fitness_age"),
        "vo2max": bodyage.get("vo2max"),
        "category": bodyage.get("category"),
        "penalty_years": bodyage.get("penalty", 0),
        "edad_real": bodyage.get("age"),
    }

    # Drivers: sub-métricas que componen el score
    drivers: dict[str, Any] = {}
    for key in ("rhr", "hrv", "sleep_h", "pa_index", "waist"):
        if bodyage.get(key) is not None:
            drivers[key] = bodyage[key]
    if drivers:
        result["drivers"] = drivers

    return result


# ── morning_brief ─────────────────────────────────────────────────────────────

def morning_brief(ds: Optional[dict]) -> str:
    """
    Brief mañanero DETERMINISTA (sin LLM).
    Texto plano listo para enviar por WhatsApp/mensaje.
    Incluye: saludo con fecha (de summary.updated), snapshot clave,
             alertas (insights), 1 prioridad.
    """
    if not ds:
        return "Sin datos de salud disponibles. Ejecuta una sincronización primero."

    days = ds.get("days", [])
    summary = ds.get("summary", {})

    if not days:
        return "Sin datos de salud disponibles. Ejecuta una sincronización primero."

    today_data = days[-1]
    updated = summary.get("updated", today_data.get("date", str(date.today())))

    # ── Encabezado ──
    lines = [f"Buenos dias. Vitals al {updated}.", ""]

    # ── Snapshot ──
    snap = today_snapshot(ds)
    snap_lines = []

    rec = snap.get("recovery")
    rec_estado = snap.get("recovery_estado")
    if rec is not None:
        snap_lines.append(f"Recuperacion: {rec}% ({rec_estado})")

    sueno_h = snap.get("sueno_h")
    sueno_pct = snap.get("sueno_pct")
    if sueno_h is not None:
        s = f"Sueno: {sueno_h}h"
        if sueno_pct is not None:
            s += f" ({sueno_pct}%)"
        snap_lines.append(s)

    hrv = snap.get("hrv_ms")
    hrv_vs_base = snap.get("hrv_vs_base")
    if hrv is not None:
        s = f"HRV: {hrv} ms"
        if hrv_vs_base is not None:
            signo = "+" if hrv_vs_base >= 0 else ""
            s += f" ({signo}{hrv_vs_base} vs base)"
        snap_lines.append(s)

    rhr = snap.get("rhr_bpm")
    rhr_vs_base = snap.get("rhr_vs_base")
    if rhr is not None:
        s = f"FC reposo: {rhr} bpm"
        if rhr_vs_base is not None:
            signo = "+" if rhr_vs_base >= 0 else ""
            s += f" ({signo}{rhr_vs_base} vs base)"
        snap_lines.append(s)

    strain = snap.get("strain")
    if strain is not None:
        snap_lines.append(f"Esfuerzo ayer: {strain}/21")

    if snap_lines:
        lines.append("HOY:")
        lines.extend(f"  {l}" for l in snap_lines)
        lines.append("")

    # ── Alertas ──
    insights = insights_list(ds)
    if insights:
        lines.append("ALERTAS:")
        severity_label = {"alert": "[ALERTA]", "watch": "[OJO]",
                          "positive": "[BIEN]", "info": "[INFO]"}
        for ins in insights:
            sev = severity_label.get(ins.get("severity", "info"), "[INFO]")
            title = ins.get("title", "")
            summary_text = ins.get("summary", "")
            lines.append(f"  {sev} {title}")
            if summary_text:
                lines.append(f"    {summary_text}")
        lines.append("")

    # ── Prioridad del día ──
    priority = _choose_priority(snap, insights)
    lines.append(f"PRIORIDAD: {priority}")

    return "\n".join(lines)


def _choose_priority(snap: dict, insights: list) -> str:
    """Elige 1 prioridad determinista basada en el estado actual."""
    # Primero: alertas rojas
    alerts = [ins for ins in insights if ins.get("severity") == "alert"]
    if alerts:
        return f"Atender alerta: {alerts[0].get('title', 'alerta')}"

    # Recovery muy bajo → descanso
    rec = snap.get("recovery")
    if rec is not None and rec < 34:
        return "Recuperacion baja — prioriza descanso y evita entreno intenso hoy."

    # Watch sobre sueno (insights emite la categoría con ñ: "sueño")
    sleep_watch = next(
        (ins for ins in insights
         if ins.get("severity") == "watch"
         and ins.get("category") in ("sueño", "sueno")),
        None,
    )
    if sleep_watch:
        return f"Sueño: {sleep_watch.get('recommendation', 'mejora consistencia de sueño')}"

    # Recovery medio-bajo + HRV bajo
    hrv_vs_base = snap.get("hrv_vs_base")
    if rec is not None and rec < 55 and hrv_vs_base is not None and hrv_vs_base < -5:
        return "HRV bajo y recuperacion media — mantén la intensidad moderada."

    # Fuerza semana = 0
    fuerza = snap.get("fuerza_semana_min", 0)
    if fuerza == 0:
        return "Sin entreno intenso esta semana — considera una sesion de fuerza o HIIT."

    # Todo bien
    if rec is not None and rec >= 67:
        return "Buena recuperacion — dia apto para entreno exigente si lo tienes planeado."

    return "Mantén la rutina. Revisa tus metricas en el tab Hoy."


# ── drivers_list ──────────────────────────────────────────────────────────────

def drivers_list(ds: Optional[dict]) -> list:
    """
    Lista de drivers (palancas) con correlación de Spearman rezagada.
    Findings filtrados: n>=25, significativos, |ρ|>=0.2; ordenados por |ρ| desc, top-5.
    Si no hay datos o ningún driver pasa el filtro → [].
    """
    if not ds:
        return []

    try:
        from app.drivers import analyze_drivers
        return analyze_drivers(ds.get("days", []))
    except Exception as exc:
        logger.error("Error al computar drivers: %s", exc)
        return []


# ── cycle_summary (Fase 7: salud femenina, opt-in) ────────────────────────────

def cycle_summary(ds: Optional[dict]) -> dict:
    """
    Resumen del estado de ciclo para el MCP. {enabled:false} si el
    toggle profile.cycle_tracking está apagado (default) — mismo criterio de
    opt-in estricto que /api/cycle: cero fuga de datos de ciclo sin activar.
    Nunca lanza — cualquier error interno degrada a {enabled:false}.
    """
    try:
        from app.profile import effective_profile_dict as _peffdict
        profile = _peffdict()
        if not profile.get("cycle_tracking"):
            return {"enabled": False}

        from app import cycle as _cycle_mod
        days = (ds or {}).get("days", [])
        cycle_log = _cycle_mod.load_cycle_log()
        state = _cycle_mod.compute_cycle_state(days, cycle_log, profile)
        return state or {"enabled": False}
    except Exception as exc:
        logger.error("Error al computar cycle_summary: %s", exc)
        return {"enabled": False}


# ── ask ───────────────────────────────────────────────────────────────────────

def ask(question: str, ds: Optional[dict] = None) -> str:
    """
    Envuelve coach_chat.ask_coach.
    Persiste el turno en la conversación ACTIVA de coach_store (v2, multi-chat;
    coherencia con /api/coach: mismo aislamiento de contexto por conversación).
    Si ds no se pasa, carga el dataset actual con _load_dataset().
    """
    if ds is None:
        ds = _load_dataset()

    try:
        from app.coach_chat import ask_coach
        from app import coach_store as _cs
        cid = _cs.get_active_id()
        # Contexto SOLO de la conversación activa (aislado, igual que /api/coach)
        history = _cs.get_context(cid, 10)
        answer = ask_coach(question, ds, history)
        # Persistir el turno (nunca falla: coach_store atrapa excepciones)
        used_cid = _cs.append_turn(cid, question, answer)
        _cs.set_active(used_cid)
        return answer
    except Exception as exc:
        logger.error("Error en ask(): %s", exc)
        return (
            "Por el momento no puedo conectarme con el coach IA. "
            "Revisa tus metricas en el tab Hoy y vuelve a intentarlo."
        )


# ── bedtime_brief (Ronda 4) ──────────────────────────────────────────────────

# Regex de hora HH:MM (00:00-23:59). Evita falsos positivos tipo "24:00" (hora
# inválida) o "0:5" (minutos de 1 dígito) — exige 2 dígitos de minuto siempre.
_BEDTIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _parse_bedtime_goal(goals: Optional[list]) -> Optional[str]:
    """Busca una hora HH:MM en las metas declaradas (ej. 'acostarme antes de las
    23:00'). Devuelve el primer match como string 'HH:MM', o None si no hay meta
    de bedtime con hora explícita."""
    for g in (goals or []):
        m = _BEDTIME_RE.search(g)
        if m:
            return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def _bed_min_to_hhmm(avg_bed_min: float) -> str:
    """Convierte bed_min (offset en minutos vs medianoche, puede ser negativo si
    es antes de medianoche) a 'HH:MM' de reloj de 24h."""
    total = int(round(avg_bed_min)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def bedtime_brief(ds: Optional[dict]) -> str:
    """
    Brief corto (2-4 líneas) para el cron nocturno del agente MCP: recovery de hoy,
    hora media de acostarse (7d) vs meta declarada (o 00:00 default), y UNA
    sugerencia. Texto plano, mismo tono que morning_brief (sin markdown pesado).

    None-safe: sin dataset o sin datos de sueño -> mensaje amable, nunca crashea.
    """
    if not ds:
        return "Sin datos de salud disponibles. Ejecuta una sincronizacion primero."

    days = ds.get("days", [])
    if not days:
        return "Sin datos de salud disponibles. Ejecuta una sincronizacion primero."

    today_data = days[-1]
    lines = []

    # ── Recovery de hoy ──
    rec = today_data.get("recovery")
    if rec is not None:
        estado = _recovery_estado(float(rec))
        lines.append(f"Recuperacion de hoy: {int(rec)}% ({estado}).")

    # ── Meta de bedtime declarada (o 00:00 default) ──
    try:
        from app.profile import effective as _peff
        goals = _peff("goals") or []
    except Exception:
        goals = []
    goal_hhmm = _parse_bedtime_goal(goals)
    target = goal_hhmm or "00:00"

    # ── Hora media de acostarse 7d ──
    bed7 = _window_vals(days, "bed_min", 7)
    if bed7:
        avg_bed_hhmm = _bed_min_to_hhmm(statistics.mean(bed7))
        asleep7 = _window_vals(days, "asleep", 7)
        dur_txt = f" · durmiendo ~{round(statistics.mean(asleep7) / 60, 1)}h" if asleep7 else ""
        meta_txt = f" (tu meta: {goal_hhmm})" if goal_hhmm else f" (meta default {target})"
        lines.append(f"Tu hora media de acostarte (7d): {avg_bed_hhmm}{meta_txt}{dur_txt}.")
    else:
        lines.append(f"Sin datos de hora de dormir esta semana (meta: {target}).")

    # ── UNA sugerencia ──
    if bed7:
        avg_bed_min = statistics.mean(bed7)
        if goal_hhmm:
            gh, gm = (int(x) for x in goal_hhmm.split(":"))
            goal_min = gh * 60 + gm
            if goal_min > 12 * 60:  # normalizar a offset tipo bed_min (ej 23:00 -> -60)
                goal_min -= 24 * 60
            diff = round(avg_bed_min - goal_min)
            if diff > 15:
                lines.append(f"Sugerencia: te has acostado ~{diff}min tarde vs tu meta — adelanta la alarma de wind-down hoy.")
            else:
                lines.append("Sugerencia: vas bien con tu meta de bedtime — mantén la rutina.")
        else:
            if rec is not None and rec < 34:
                lines.append("Sugerencia: recuperacion baja — prioriza acostarte temprano esta noche.")
            else:
                lines.append("Sugerencia: mantén una hora de dormir consistente esta noche.")
    else:
        lines.append("Sugerencia: sincroniza tus datos de sueño para un seguimiento mas preciso.")

    return "\n".join(lines)
