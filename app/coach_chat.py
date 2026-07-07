"""
coach_chat.py — Coach IA conversacional (backend intercambiable, F3 roadmap P0).

ask_coach(question, dataset, history=None) -> str
  Arma el prompt con contexto de salud del usuario y delega la llamada al LLM en
  app.llm.generate() (backend claude_cli u openai_compat según
  settings.COACH_BACKEND) — devuelve la respuesta en texto.

  En caso de error (CLI ausente, timeout, exit != 0, servidor openai_compat
  caído) generate() ya devuelve None; ask_coach() lo traduce a un mensaje de
  fallback amable y loguea; nunca propaga la excepción.
"""
from __future__ import annotations

import logging
import re
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app import llm as _llm
from app.scoring import recent_base
from app.load import strength_minutes

logger = logging.getLogger("vitals.coach_chat")

# ── Cerebro del Coach (editable, app/coach_brain.md) ─────────────────────────
_BRAIN_PATH = Path(__file__).resolve().parent / "coach_brain.md"


def _brain_fallback() -> str:
    """Genera el fallback del cerebro EN CADA LLAMADA (no import-time — Ronda 4).

    Antes vivía como constante calculada al importar el módulo (`_BRAIN_FALLBACK`),
    lo que dejaba el texto stale si el perfil cambiaba después del primer import
    (proceso long-lived de uvicorn). Ahora se recalcula cada vez que coach_brain.md
    no está disponible.

    Usa las metas declaradas del perfil si existen (respeta su orden); si no hay
    metas, cae al default histórico "sueño > fuerza > longevidad".
    """
    try:
        from app.profile import effective as _peff, current_age as _page
        name = _peff("name") or "el usuario"
        age = _page()
        goals = _peff("goals") or []
    except Exception:
        name = "el usuario"
        age = 0
        goals = []
    age_str = f"{age} años" if age else "edad desconocida"
    if goals:
        priorities = ", ".join(goals)
    else:
        priorities = "sueño > fuerza > longevidad"
    return (
        f"Eres el Coach de Vitals, coach de salud personal directo en español. "
        f"No eres médico. Aconseja con datos de wearable, sé específico y conciso, "
        f"prioriza {priorities} para {name} ({age_str})."
    )


def _load_brain() -> str:
    try:
        return _BRAIN_PATH.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("No pude leer coach_brain.md (%s); uso fallback corto.", exc)
        return _brain_fallback()

_FALLBACK = (
    "Por el momento no puedo conectarme con el coach IA. "
    "Revisa tus métricas en el tab Hoy y vuelve a intentarlo en un momento."
)


def _vals(days: list, key: str, n: Optional[int] = None) -> list:
    """Valores no-nulos de `key` en los últimos n días (o todos)."""
    sub = days[-n:] if n else days
    return [d[key] for d in sub if d.get(key) is not None]


def _avg(vals: list, nd: int = 1):
    return round(statistics.mean(vals), nd) if vals else None


# ── PERFIL DECLARADO (Ronda 4: intake clínico) ───────────────────────────────

def _clinical_block(profile_dict: Optional[dict]) -> str:
    """Bloque '=== PERFIL DECLARADO ===' con metas (en el ORDEN del usuario),
    lesiones, condiciones y medicamentos. Campos vacíos → línea omitida.
    Si TODO está vacío → "" (bloque omitido entero => prompt idéntico al de antes
    de esta ronda, backward-compat con el perfil real del usuario que no trae estos
    campos)."""
    if not profile_dict:
        return ""
    goals = profile_dict.get("goals") or []
    injuries = profile_dict.get("injuries") or []
    conditions = profile_dict.get("conditions") or []
    medications = profile_dict.get("medications") or []

    if not (goals or injuries or conditions or medications):
        return ""

    lines = ["=== PERFIL DECLARADO ==="]
    if goals:
        # El orden es significativo (prioridad declarada por el usuario) — se
        # numeran tal cual vienen, nunca se reordenan.
        numbered = ", ".join(f"{i+1}) {g}" for i, g in enumerate(goals))
        lines.append(f"Metas (en orden de prioridad): {numbered}")
    if injuries:
        lines.append(f"Lesiones: {', '.join(injuries)}")
    if conditions:
        lines.append(f"Condiciones: {', '.join(conditions)}")
    if medications:
        lines.append(f"Medicamentos: {', '.join(medications)}")
    return "\n".join(lines)


# ── SEGUIMIENTO DE METAS (Ronda 4: adherencia v1) ────────────────────────────

_GOAL_KEYWORD_MAP = [
    (re.compile(r"dormir|sue[ñn]o|acostar", re.I), "sleep"),
    (re.compile(r"fuerza|gym|pesas", re.I), "strength"),
    (re.compile(r"pasos|caminar|steps", re.I), "steps"),
]


def _goals_tracking(goals: Optional[list], dataset: dict) -> str:
    """Bloque 'SEGUIMIENTO DE METAS (7d)': para cada meta declarada, mapea por
    keywords a una métrica real de los últimos 7 días (meta declarada ↔ dato real).
    Metas sin match de keyword se listan igual, sin métrica asociada.

    None-safe: sin `goals` o sin datos suficientes → "" o líneas "sin dato", nunca
    crashea. No exige `dataset` con 'days' — un dataset vacío da "sin dato" en
    todas las métricas mapeadas.
    """
    if not goals:
        return ""

    days = (dataset or {}).get("days", [])
    exercises = (dataset or {}).get("exercises", [])

    lines = ["SEGUIMIENTO DE METAS (7d):"]
    for goal in goals:
        metric_kind = None
        for pattern, kind in _GOAL_KEYWORD_MAP:
            if pattern.search(goal):
                metric_kind = kind
                break

        if metric_kind == "sleep":
            asleep7 = _vals(days, "asleep", 7)
            bed7 = _vals(days, "bed_min", 7)
            if asleep7:
                dur = _avg([v / 60 for v in asleep7])
                if bed7:
                    # bed_min es offset en minutos vs medianoche (00:00 = 0).
                    avg_bed = statistics.mean(bed7)
                    hh = int(avg_bed // 60) % 24
                    mm = int(round(avg_bed % 60))
                    lines.append(f"• \"{goal}\" → hora media de acostarse ~{hh:02d}:{mm:02d} · duración media {dur}h (7d)")
                else:
                    lines.append(f"• \"{goal}\" → duración media {dur}h (7d), sin dato de hora de acostarse")
            else:
                lines.append(f"• \"{goal}\" → sin dato de sueño (7d)")
        elif metric_kind == "strength":
            dates_7d = {d.get("date", "") for d in days[-7:]}
            strength_min = strength_minutes(exercises, dates=dates_7d) if exercises else 0
            if days:
                lines.append(f"• \"{goal}\" → fuerza estructurada: {strength_min} min (7d)")
            else:
                lines.append(f"• \"{goal}\" → sin dato de fuerza (7d)")
        elif metric_kind == "steps":
            steps7 = _vals(days, "steps", 7)
            if steps7:
                lines.append(f"• \"{goal}\" → {int(_avg(steps7, 0))} pasos/día promedio (7d)")
            else:
                lines.append(f"• \"{goal}\" → sin dato de pasos (7d)")
        else:
            lines.append(f"• \"{goal}\" → (sin métrica automática para esta meta)")

    return "\n".join(lines)


def _build_context(dataset: dict) -> str:
    """Contexto de salud enriquecido: snapshot de hoy + tendencias + sueño + carga + banderas."""
    days = dataset.get("days", [])
    summary = dataset.get("summary", {})
    bodyage = summary.get("bodyage", {})
    exercises = dataset.get("exercises", [])
    if not days:
        return "CONTEXTO: (aún no hay datos sincronizados)."

    today = days[-1]
    date_str = today.get("date", "sin fecha")
    hrv_base = recent_base(summary, "hrv")
    rhr_base = recent_base(summary, "rhr")

    def _arrow(v, base, lower_better=False):
        if v is None or base is None:
            return ""
        d = v - base
        if abs(d) < (base * 0.03):
            return " (≈ tu base)"
        up = d > 0
        good = (not up) if lower_better else up
        return f" ({'+' if up else ''}{round(d,1)} vs base {base} · {'bien' if good else 'ojo'})"

    # ── SNAPSHOT HOY ──
    snap = [f"SNAPSHOT DE HOY ({date_str}):"]
    rec = today.get("recovery")
    if rec is not None:
        st = "alta" if rec >= 67 else ("media" if rec >= 34 else "baja")
        snap.append(f"• Recuperación: {rec}% ({st})")
    smin = today.get("asleep")
    if smin is not None:
        sh = round(smin / 60, 1)
        defi = round(8 - sh, 1)
        bt = today.get("bedtime", "")
        snap.append(f"• Sueño: {sh}h" + (f" (déficit {defi}h vs 8h)" if defi > 0 else " (meta ok)")
                    + (f" · se acostó {bt}" if bt else ""))
    if today.get("hrv") is not None:
        snap.append(f"• HRV: {today['hrv']} ms{_arrow(today['hrv'], hrv_base)}")
    if today.get("rhr") is not None:
        snap.append(f"• FC reposo: {today['rhr']} lpm{_arrow(today['rhr'], rhr_base, lower_better=True)}")
    if today.get("strain") is not None:
        snap.append(f"• Esfuerzo: {today['strain']:.1f}/21")

    # ── TENDENCIAS 7d / 30d ──
    # Tier 2: importar trend_summary para añadir dirección+significancia
    try:
        from app.trends import trend_summary as _trend_summary
        _trends_available = True
    except ImportError:
        _trends_available = False

    def _trend_label(vals_30: list) -> str:
        """Devuelve ' · subiendo' / ' · bajando' / '' según significancia."""
        if not _trends_available or not vals_30:
            return ""
        ts = _trend_summary(vals_30)
        if ts["significant"] and ts["direction"] in ("subiendo", "bajando"):
            return f" · {ts['direction']}"
        return ""

    def line(lbl, key, unit="", nd=1):
        a7, a30 = _avg(_vals(days, key, 7), nd), _avg(_vals(days, key, 30), nd)
        if a7 is None and a30 is None:
            return None
        trend_sfx = _trend_label(_vals(days, key, 30))
        return f"• {lbl}: 7d {a7}{unit} · 30d {a30}{unit}{trend_sfx}"
    trends = ["TENDENCIAS (promedios):"]
    for lbl, key, unit in [("Recuperación", "recovery", "%"), ("HRV", "hrv", " ms"),
                           ("FC reposo", "rhr", " lpm"), ("Esfuerzo", "strain", "")]:
        ln = line(lbl, key, unit)
        if ln:
            trends.append(ln)
    sh7 = _avg([v / 60 for v in _vals(days, "asleep", 7)], 1)
    sh30 = _avg([v / 60 for v in _vals(days, "asleep", 30)], 1)
    if sh7 or sh30:
        short = sum(1 for v in _vals(days, "asleep", 7) if v / 60 < 7)
        sl30_h = [v / 60 for v in _vals(days, "asleep", 30)]
        sleep_sfx = _trend_label(sl30_h)
        trends.append(f"• Sueño: 7d {sh7}h · 30d {sh30}h · {short}/7 noches <7h{sleep_sfx}")

    # ── SUEÑO (arquitectura + consistencia, 14d) ──
    sl = ["SUEÑO (últimos 14d):"]
    asleep14 = _vals(days, "asleep", 14)
    deep14, rem14, eff14 = _vals(days, "deep", 14), _vals(days, "rem", 14), _vals(days, "eff", 14)
    if asleep14 and deep14:
        sl.append(f"• Profundo ~{round(statistics.mean(deep14)/statistics.mean(asleep14)*100)}% · "
                  f"REM ~{round(statistics.mean(rem14)/statistics.mean(asleep14)*100)}%"
                  + (f" · eficiencia {_avg(eff14)}%" if eff14 else ""))
    bedmins = _vals(days, "bed_min", 21)
    if len(bedmins) >= 3:
        var = round(statistics.pstdev(bedmins))
        consist = "consistente" if var <= 45 else ("variable" if var <= 75 else "ALTA variabilidad")
        sl.append(f"• Consistencia de hora de dormir: ±{var} min ({consist})")

    # ── CARGA DE ENTRENAMIENTO (7d) ──
    try:
        cutoff = (datetime.fromisoformat(date_str) - timedelta(days=7)).date().isoformat()
    except Exception:
        cutoff = ""
    rec_ex = [e for e in exercises if e.get("date", "") >= cutoff]
    # Ronda 3: strength_minutes() (app/load.py) — superset del regex que vivía aquí
    # duplicado (ahora incluye también "musculac", p.ej. HealthKit "Musculación").
    dates_in_window = {e.get("date", "") for e in rec_ex}
    strength_min = strength_minutes(exercises, dates=dates_in_window)
    total_min = sum(e.get("dur_min", 0) or 0 for e in rec_ex)
    vig7 = sum(_vals(days, "vigorous", 7))
    names = {}
    for e in rec_ex:
        names[e.get("name", "?")] = names.get(e.get("name", "?"), 0) + 1
    top = ", ".join(f"{k} ×{v}" for k, v in sorted(names.items(), key=lambda x: -x[1])[:3])
    # Ronda 4: el "← CERO, su meta #2" estaba hardcodeado asumiendo que fuerza SIEMPRE
    # es la meta #2 del usuario. Ahora es condicional a las metas REALMENTE declaradas:
    # solo se marca si el usuario tiene una meta que mapea a "strength" por keyword
    # (mismo mapeo de _goals_tracking); sin metas declaradas, texto neutro sin
    # suponer prioridad.
    strength_zero_flag = ""
    if strength_min == 0:
        try:
            from app.profile import effective as _peff
            declared_goals = _peff("goals") or []
        except Exception:
            declared_goals = []
        if any(_GOAL_KEYWORD_MAP[1][0].search(g) for g in declared_goals):
            strength_zero_flag = " ← CERO, meta declarada"
        elif not declared_goals:
            strength_zero_flag = " (sin sesiones esta semana)"
    load = ["CARGA (7d):",
            f"• {len(rec_ex)} sesiones · {total_min} min total · {round(vig7)} min vigorosos (Z4-5)",
            f"• Fuerza estructurada: {strength_min} min{strength_zero_flag}",
            f"• Tipos: {top}" if top else "• Tipos: (sin sesiones registradas)"]

    # ── EDAD CORPORAL ──
    ba = []
    if bodyage.get("body_age") is not None:
        s = (f"EDAD CORPORAL: {bodyage['body_age']} años (fitness {bodyage.get('fitness_age')}) · "
             f"VO₂máx {bodyage.get('vo2max')} ({bodyage.get('category','')})")
        if bodyage.get("penalty", 0) and bodyage["penalty"] > 0:
            s += f" · +{bodyage['penalty']} año(s) por dormir <7h"
        ba.append(s)

    # ── BANDERAS ──
    flags = []
    spo2_14 = _vals(days, "spo2", 14)
    if spo2_14:
        mn = min(spo2_14)
        low = sum(1 for v in spo2_14 if v < 90)
        if low:
            flags.append(f"• SpO₂: {low} noche(s) <90% (mín {mn}%) — vigilar")
    red = sum(1 for v in _vals(days, "recovery", 7) if v < 34)
    if red >= 2:
        flags.append(f"• {red}/7 días con recuperación roja — viene cavando un hoyo")
    skin14 = _vals(days, "skin_temp", 14)
    if len(skin14) >= 5:
        base_t = statistics.mean(skin14[:-1]) if len(skin14) > 1 else skin14[0]
        if skin14[-1] - base_t > 0.6:
            flags.append(f"• Temp de piel +{round(skin14[-1]-base_t,1)}° sobre su media — posible enfermedad/alcohol")
    flags_block = ("BANDERAS:\n" + "\n".join(flags)) if flags else ""

    # ── PALANCAS (Tier 3: drivers por correlación Spearman, aditivo) ──
    palancas_block = ""
    try:
        from app.drivers import analyze_drivers
        driver_findings = analyze_drivers(days)
        if driver_findings:
            top = driver_findings[:4]  # máx 4 headlines en el contexto del coach
            pal_lines = ["PALANCAS (asociaciones en TUS datos, no causa):"]
            for f in top:
                pal_lines.append(f"• {f['headline']}")
            palancas_block = "\n".join(pal_lines)
    except Exception:
        pass  # drivers no disponibles → omitir bloque silenciosamente

    # ── SEGUIMIENTO DE METAS (Ronda 4: adherencia v1) ──
    goals_block = ""
    try:
        from app.profile import effective as _peff
        declared_goals = _peff("goals") or []
        if declared_goals:
            goals_block = _goals_tracking(declared_goals, dataset)
    except Exception:
        pass  # perfil no disponible → omitir bloque silenciosamente (None-safe)

    # ── CICLO (Fase 7: salud femenina, opt-in) ──
    # Gateado por profile.cycle_tracking — con el toggle apagado (default), este
    # bloque queda "" y el prompt es IDÉNTICO al de antes de esta fase (cero
    # fuga de datos de ciclo hacia el coach, mismo criterio #1 del roadmap).
    # Envuelto en try/except: un fallo del módulo de ciclo nunca debe tumbar
    # la construcción del contexto del coach.
    cycle_block = ""
    try:
        from app.profile import effective_profile_dict as _peffdict_cycle
        cycle_profile = _peffdict_cycle()
        if cycle_profile.get("cycle_tracking"):
            from app import cycle as _cycle_mod
            cycle_log = _cycle_mod.load_cycle_log()
            cyc = _cycle_mod.compute_cycle_state(days, cycle_log, cycle_profile)
            if cyc and cyc.get("enabled") and cyc.get("cycle_day") is not None:
                lines = ["CICLO (seguimiento activado por el usuario):"]
                lines.append(f"• Día {cyc['cycle_day']} del ciclo — fase {cyc.get('phase', '?')}")
                period = cyc.get("period") or {}
                if period.get("days_until") is not None:
                    lines.append(f"• Próximo periodo estimado: {period.get('predicted_next')} (en {period['days_until']} días, confianza {period.get('confidence')})")
                delay = cyc.get("delay") or {}
                if delay.get("is_delayed"):
                    lines.append(f"• Retraso de {delay.get('days')} días vs la predicción")
                meno = cyc.get("menopause") or {}
                if meno.get("stage") not in (None, "insufficient_history", "premenopausal"):
                    lines.append(f"• Señal de {meno.get('stage')} (ver guardrails: nunca diagnosticar)")
                cycle_block = "\n".join(lines)
    except Exception:
        pass  # módulo de ciclo no disponible/falló → omitir bloque silenciosamente

    # ── LABORATORIOS (Fase 8D, paso D1): últimos biomarcadores + flag
    # fuera-de-rango, para que el coach los tenga presentes. Sin laboratorios
    # registrados -> "" (prompt idéntico al de antes de esta fase). Envuelto
    # en try/except: un fallo del módulo de labs nunca debe tumbar el contexto.
    labs_block = ""
    try:
        from app import labs as _labs_mod
        locale_labs = "es"
        try:
            from app.profile import effective as _peff_labs
            locale_labs = _peff_labs("locale") or "es"
        except Exception:
            pass
        lab_lines = _labs_mod.coach_context_lines(locale=locale_labs)
        if lab_lines:
            labs_block = "ÚLTIMOS LABORATORIOS:\n" + "\n".join(lab_lines)
    except Exception:
        pass  # labs no disponibles/fallaron -> omitir bloque silenciosamente

    # ── PLAN ACTIVO (Roadmap P1, F4, paso 7): programa del coach en curso —
    # día N/M, tarea de hoy (ya adaptada por recovery/ACWR en plan_store.py),
    # adherencia 7d. Sin plan activo -> "" (prompt IDÉNTICO al de antes de
    # este paso — mismo patrón EXACTO que cycle/labs: try/except, degradación
    # silenciosa). El LLM solo NARRA el plan, nunca decide la tarea (esa
    # decisión ya la tomó task_for_day(), determinista y auditable).
    plan_block = ""
    try:
        from app import plan_store as _plan_store_mod
        locale_plan = "es"
        try:
            from app.profile import effective as _peff_plan
            locale_plan = _peff_plan("locale") or "es"
        except Exception:
            pass
        status = _plan_store_mod.plan_status(dataset, locale=locale_plan)
        if status:
            lines = ["PLAN ACTIVO:"]
            lines.append(f"• Programa: {status['program_id']} — día {status['day_number']}/{status['duration_days']}")
            task = status.get("today_task")
            if task:
                adapted_sfx = f" ({task['adapted_reason']})" if task.get("adapted") and task.get("adapted_reason") else ""
                lines.append(f"• Tarea de hoy: {task['label']}{adapted_sfx}")
            elif status.get("is_completed"):
                lines.append("• Programa completado")
            if status.get("adherence_pct_7d") is not None:
                lines.append(f"• Adherencia 7d: {status['adherence_pct_7d']}%")
            plan_block = "\n".join(lines)
    except Exception:
        pass  # plan no disponible/falló -> omitir bloque silenciosamente

    blocks = ["\n".join(snap), "\n".join(trends), "\n".join(sl), "\n".join(load)]
    if ba:
        blocks.append("\n".join(ba))
    if flags_block:
        blocks.append(flags_block)
    if palancas_block:
        blocks.append(palancas_block)
    if goals_block:
        blocks.append(goals_block)
    if cycle_block:
        blocks.append(cycle_block)
    if labs_block:
        blocks.append(labs_block)
    if plan_block:
        blocks.append(plan_block)
    return "\n\n".join(blocks)


def _build_prompt(question: str, dataset: dict, history: Optional[list] = None) -> str:
    """Prompt completo: cerebro (sistema) + contexto enriquecido + historial + pregunta."""
    brain = _load_brain()
    context = _build_context(dataset)

    # Obtener nombre del perfil para personalizar el prompt
    try:
        from app.profile import effective as _peff, current_age as _page, effective_profile_dict as _peffdict
        user_name = _peff("name") or "el usuario"
        user_age = _page()
        user_locale = _peff("locale") or "es"
        profile_dict = _peffdict()
    except Exception:
        user_name = "el usuario"
        user_age = 0
        user_locale = "es"
        profile_dict = None

    # Mapear locale → idioma para la directiva de salida
    _LOCALE_LANG = {
        "es": "español",
        "en": "English",
        "fr": "français",
        "pt": "português",
    }
    output_lang = _LOCALE_LANG.get(user_locale, "español")

    history_block = ""
    if history:
        lines = []
        for turn in history[-6:]:
            prefix = "USUARIO" if turn.get("role", "user") == "user" else "COACH"
            lines.append(f"{prefix}: {turn.get('content', '')}")
        if lines:
            history_block = "\nCONVERSACIÓN PREVIA:\n" + "\n".join(lines) + "\n"

    # Ronda 4: bloque de perfil declarado. Si el perfil no tiene NINGÚN campo
    # clínico (caso del perfil real del usuario hoy), _clinical_block devuelve "" y el
    # prompt queda IDÉNTICO al de antes de esta ronda (backward-compat garantizada).
    clinical = _clinical_block(profile_dict)
    clinical_block = f"\n{clinical}\n" if clinical else ""

    age_str = f"{user_age} años" if user_age else "edad desconocida"
    return (
        f"{brain}\n\n"
        f"=== DATOS ACTUALES DE {user_name.upper()} ({age_str}) ===\n{context}\n"
        f"{clinical_block}"
        f"{history_block}\n"
        f"=== PREGUNTA ===\n{question}\n\n"
        f"Responde como su Coach según las reglas de arriba: en {output_lang}, directo, anclado a sus "
        f"datos, 1-2 prioridades, sin saludos ni despedidas. Diríjete a {user_name} por su nombre."
    )


def ask_coach(question: str, dataset: dict, history: Optional[list] = None) -> str:
    """
    Arma el prompt y delega la llamada al LLM en app.llm.generate() (backend
    intercambiable vía settings.COACH_BACKEND). Retorna la respuesta (str).
    En error (generate() devuelve None): fallback amable, nunca 500.

    SEGURIDAD (backend claude_cli): el prompt va por STDIN, NUNCA interpolado
    en la línea de comando del shell y NUNCA con shell=True — ver
    app.llm._generate_claude_cli().
    """
    prompt = _build_prompt(question, dataset, history)
    answer = _llm.generate(prompt, timeout=90, purpose="coach")
    return answer if answer else _FALLBACK
