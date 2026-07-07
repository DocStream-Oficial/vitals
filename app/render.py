"""
render.py — inyecta __DATA__/__COACH__/__AUTH__/__INSIGHTS__ en los templates HTML.
- render_dashboard(): template viejo (vitals_premium_template.html) — conservado como rollback.
- render_ios():       template nuevo iOS (vitals_ios.html).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from app.config import settings

TEMPLATE_PATH = settings.TEMPLATES_DIR / "vitals_premium_template.html"
TEMPLATE_IOS_PATH = settings.TEMPLATES_DIR / "vitals_ios.html"


def _json_for_script(obj, **kwargs) -> str:
    """Serializa a JSON seguro para inyectar dentro de un bloque <script>.

    json.dumps escapa comillas pero NO la secuencia '</script>'. Si un valor
    (p.ej. el nombre del perfil) contiene '</script>', el navegador cierra el
    bloque <script> prematuramente y ejecuta lo que siga (XSS / script-breakout).
    Escapamos '<' como '\\u003c' (sigue siendo JSON válido y JS-parseable) para
    neutralizar '</script>', '<!--' y similares. Aplica a TODOS los placeholders.
    """
    return json.dumps(obj, **kwargs).replace("<", "\\u003c")


def _inject_placeholders(
    html: str,
    dataset: dict,
    coach_payload,
    auth_st: dict,
    insights: Optional[List[dict]] = None,
    drivers: Optional[list] = None,
    trends: Optional[dict] = None,
    profile: Optional[dict] = None,
    cycle: Optional[dict] = None,
) -> str:
    """Inyecta __DATA__, __COACH__, __AUTH__, __INSIGHTS__, __DRIVERS__, __TRENDS__, __PROFILE__, __CYCLE__ en un template HTML."""
    # __DATA__ → json compacto del dataset
    data_json = _json_for_script(dataset, separators=(",", ":"), ensure_ascii=False)
    html = html.replace("__DATA__", data_json, 1)

    # __COACH__ → JSON (puede ser string HTML o dict estructurado)
    coach_json = _json_for_script(coach_payload, ensure_ascii=False)
    html = html.replace("__COACH__", coach_json, 1)

    # __AUTH__ → json del estado auth
    # Normaliza 'no_token' a 'expired' para que el JS muestre el banner Reconectar
    auth_render = dict(auth_st)
    if auth_render.get("status") == "no_token":
        auth_render["status"] = "expired"
    auth_json = _json_for_script(auth_render, ensure_ascii=False)
    html = html.replace("__AUTH__", auth_json, 1)

    # __INSIGHTS__ → lista JSON de insights ([] si no se pasan)
    insights_json = _json_for_script(insights or [], ensure_ascii=False)
    html = html.replace("__INSIGHTS__", insights_json, 1)

    # __DRIVERS__ → lista JSON de drivers/palancas ([] si no se pasan)
    drivers_json = _json_for_script(drivers or [], ensure_ascii=False)
    html = html.replace("__DRIVERS__", drivers_json, 1)

    # __TRENDS__ → dict JSON de tendencias por métrica ({} si no se pasan)
    trends_json = _json_for_script(trends or {}, ensure_ascii=False)
    html = html.replace("__TRENDS__", trends_json, 1)

    # __PROFILE__ → dict JSON del perfil efectivo (name, age, locale, units, …)
    # Fallback si no se pasó perfil: intenta cargar en caliente
    if profile is None:
        try:
            from app.profile import effective_profile_dict
            profile = effective_profile_dict()
        except Exception:
            profile = {"name": "", "email": "", "birthdate": "", "sex": "M",
                       "waist_cm": None, "height_cm": None, "weight_kg": None,
                       "locale": "es", "units": "metric", "source": "google_health",
                       "onboarded": False, "age": 0}
    profile_json = _json_for_script(profile, ensure_ascii=False)
    html = html.replace("__PROFILE__", profile_json, 1)

    # __CYCLE__ → estado de ciclo (Fase 7, salud femenina). {enabled:false} si no
    # se pasa (toggle apagado o el caller no lo computó) — mismo shape que
    # devuelve GET /api/cycle, así el front reusa la misma lógica de render.
    cycle_json = _json_for_script(cycle or {"enabled": False}, ensure_ascii=False)
    html = html.replace("__CYCLE__", cycle_json, 1)

    return html


def render_dashboard(dataset: dict, coach_html: str, auth_st: dict) -> str:
    """Template viejo (premium). Conservado como rollback."""
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    return _inject_placeholders(html, dataset, coach_html, auth_st)


def render_ios(
    dataset: dict,
    coach_card: dict,
    auth_st: dict,
    insights: Optional[List[dict]] = None,
    drivers: Optional[list] = None,
    trends: Optional[dict] = None,
    profile: Optional[dict] = None,
    cycle: Optional[dict] = None,
) -> str:
    """Template nuevo iOS. coach_card es el dict {chips, bullets} de coach_card().
    insights: lista de evaluate() para inyectar como __INSIGHTS__ ([] si None).
    drivers: lista de analyze_drivers() para inyectar como __DRIVERS__ ([] si None).
    trends: dict de trend_summary() por métrica para inyectar como __TRENDS__ ({} si None).
    profile: dict del perfil efectivo para inyectar como __PROFILE__ (se carga si None).
    cycle: dict de cycle.compute_cycle_state() para inyectar como __CYCLE__
      ({enabled:false} si None — Fase 7, salud femenina, opt-in)."""
    html = TEMPLATE_IOS_PATH.read_text(encoding="utf-8")
    return _inject_placeholders(html, dataset, coach_card, auth_st, insights, drivers, trends, profile, cycle)
