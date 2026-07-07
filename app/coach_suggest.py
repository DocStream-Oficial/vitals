"""
coach_suggest.py — Preguntas sugeridas (chips) para el tab Coach.

Roadmap P0-launch-gaps, F1: chips contextuales generadas desde el motor de
insights (app.insights.evaluate) para que el usuario tenga un atajo de un
toque hacia el coach, igual que WHOOP/Oura (no botones fijos: preguntas
relevantes al momento).

Módulo PURO (sin I/O): solo mapea insights ya evaluados a claves i18n y
rellena con un pool genérico hasta `limit`. No llama al LLM (sería caro,
lento y no determinista — el motor de insights ya es la fuente de verdad
contextual, ver roadmap).

suggested_questions(dataset, locale="es", limit=4) -> list[dict]
  [{"id": ..., "text": tr(key, locale)}, ...]
"""
from __future__ import annotations

import logging

from app.i18n import tr

logger = logging.getLogger("vitals.coach_suggest")


# Mapa insight_id -> clave i18n de la pregunta asociada. Solo los ids de
# app/insights.py listados en el roadmap llevan pregunta propia; los demás
# ids de ciclo (period_approaching, cycle_delay, perimenopause_signal) y los
# `change_*` dinámicos NO tienen entrada aquí -> caen al pool genérico.
INSIGHT_QUESTION_KEYS: dict = {
    "illness_early_warning": "coach_q_illness_early_warning",
    "spo2_low": "coach_q_spo2_low",
    "sleep_debt": "coach_q_sleep_debt",
    "overtraining": "coach_q_overtraining",
    "recovery_declining": "coach_q_recovery_declining",
    "bedtime_inconsistency": "coach_q_bedtime_inconsistency",
    "strength_gap": "coach_q_strength_gap",
    "positive_hrv": "coach_q_positive_hrv",
    "positive_sleep": "coach_q_positive_sleep",
    "cycle_phase": "coach_q_cycle_phase",
}

# Pool genérico (4 preguntas fijas), se usan para rellenar cuando no hay
# suficientes insights activos con pregunta propia.
GENERIC_QUESTION_KEYS: list = [
    "coach_q_generic_1",
    "coach_q_generic_2",
    "coach_q_generic_3",
    "coach_q_generic_4",
]


def suggested_questions(dataset: dict, locale: str = "es", limit: int = 4) -> list:
    """Devuelve hasta `limit` preguntas sugeridas: primero las derivadas de
    los insights activos (en el mismo orden de severidad que evaluate() ya
    trae), después genéricas hasta completar `limit`, sin duplicados de id.

    None-safe: dataset vacío/None, o evaluate() lanzando -> pool genérico
    completo (nunca deja el chip-bar vacío por un fallo del motor).
    """
    dataset = dataset or {}
    ids_seen: set = set()
    out: list = []

    try:
        from app.insights import evaluate
        insights = evaluate(dataset, locale) or []
    except Exception as exc:
        logger.warning("suggested_questions: evaluate() falló, uso pool genérico: %s", exc)
        insights = []

    for insight in insights:
        if len(out) >= limit:
            break
        insight_id = insight.get("id") if isinstance(insight, dict) else None
        key = INSIGHT_QUESTION_KEYS.get(insight_id)
        if not key or insight_id in ids_seen:
            continue
        ids_seen.add(insight_id)
        out.append({"id": insight_id, "text": tr(key, locale)})

    for i, key in enumerate(GENERIC_QUESTION_KEYS):
        if len(out) >= limit:
            break
        generic_id = f"generic_{i + 1}"
        if generic_id in ids_seen:
            continue
        ids_seen.add(generic_id)
        out.append({"id": generic_id, "text": tr(key, locale)})

    return out[:limit]
