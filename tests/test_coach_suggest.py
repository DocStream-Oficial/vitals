"""
test_coach_suggest.py — Tests de app/coach_suggest.py (F1: preguntas sugeridas).
"""
from __future__ import annotations

from app.coach_suggest import (
    suggested_questions,
    INSIGHT_QUESTION_KEYS,
    GENERIC_QUESTION_KEYS,
)


def make_day(date, **kwargs):
    return {"date": date, **kwargs}


def date_seq(n):
    return [f"2024-01-{i+1:02d}" for i in range(n)]


def test_sleep_debt_dataset_first_question_is_sleep_debt():
    """Dataset sintético que dispara sleep_debt (5/7 noches cortas, alert) ->
    la primera pregunta sugerida debe ser la de deuda de sueño (mismo orden
    de severidad que evaluate() ya trae: sleep_debt/alert va primero)."""
    dates = date_seq(7)
    days = [make_day(d, asleep=480, recovery=80) for d in dates[:2]]
    for d in dates[2:]:
        days.append(make_day(d, asleep=340, recovery=55))
    dataset = {"days": days, "summary": {}, "exercises": []}

    qs = suggested_questions(dataset, locale="es", limit=4)
    assert len(qs) >= 1
    assert qs[0]["id"] == "sleep_debt"
    assert qs[0]["text"]  # texto no vacío


def test_empty_dataset_returns_generic_pool():
    """Dataset vacío -> 4 preguntas genéricas (evaluate() devuelve [])."""
    qs = suggested_questions({}, locale="es", limit=4)
    assert len(qs) == 4
    ids = [q["id"] for q in qs]
    assert ids == ["generic_1", "generic_2", "generic_3", "generic_4"]


def test_none_dataset_is_safe():
    """dataset=None no debe crashear -> pool genérico."""
    qs = suggested_questions(None, locale="es", limit=4)
    assert len(qs) == 4


def test_limit_respected():
    qs = suggested_questions({}, locale="es", limit=2)
    assert len(qs) == 2


def test_no_duplicate_ids():
    dates = date_seq(30)
    # Dataset con varias señales: sleep_debt + bedtime_inconsistency
    days = []
    for i, d in enumerate(dates):
        bed = 15 if i % 2 == 0 else 200  # alta variabilidad
        asleep = 340 if i >= 25 else 480
        days.append(make_day(d, asleep=asleep, recovery=70, bed_min=bed))
    dataset = {"days": days, "summary": {}, "exercises": []}

    qs = suggested_questions(dataset, locale="es", limit=4)
    ids = [q["id"] for q in qs]
    assert len(ids) == len(set(ids))


def test_locale_variants_produce_text():
    for locale in ("es", "en", "fr", "pt"):
        qs = suggested_questions({}, locale=locale, limit=4)
        assert len(qs) == 4
        for q in qs:
            assert isinstance(q["text"], str) and q["text"]


def test_evaluate_exception_falls_back_to_generic(monkeypatch):
    """Si evaluate() lanza, suggested_questions no debe propagar -> genéricas."""
    import app.coach_suggest as mod

    def _boom(dataset, locale):
        raise RuntimeError("boom")

    monkeypatch.setattr("app.insights.evaluate", _boom)
    qs = mod.suggested_questions({"days": [{"date": "2024-01-01"}]}, locale="es", limit=4)
    assert len(qs) == 4


def test_fitness_age_gap_dataset_chip_present(monkeypatch):
    """Roadmap coach-objetivo-vo2, Paso 3: con el insight fitness_age_gap
    activo, su chip (coach_q_fitness_age_gap) debe aparecer en las preguntas
    sugeridas (mismo mecanismo INSIGHT_QUESTION_KEYS, cero lógica nueva)."""
    from app import profile as _profile
    monkeypatch.setattr(_profile, "effective_sources", lambda: ["google_health"])
    dates = date_seq(7)
    days = [make_day(d) for d in dates]
    summary = {
        "bodyage": {
            "vo2max_source": "measured", "vo2max_percentile": 35,
            "fitness_age": 56, "fitness_age_display": 56, "age": 49,
        },
    }
    dataset = {"days": days, "summary": summary, "exercises": []}

    qs = suggested_questions(dataset, locale="es", limit=4)
    ids = [q["id"] for q in qs]
    assert "fitness_age_gap" in ids
    # VALIDACIÓN (validador): el chip debe traer el TEXTO traducido, no la
    # clave cruda — un fallo de i18n aquí se vería como "coach_q_..." en la UI.
    chip = next(q for q in qs if q["id"] == "fitness_age_gap")
    assert chip["text"] == "¿Cómo bajo mi edad fitness?"


def test_vo2_unmeasured_dataset_chip_present():
    """Roadmap vo2-sin-inventar, Paso 3: con el insight vo2_unmeasured activo
    (bodyage.unavailable_reason == 'no_vo2_measurement'), su chip
    (coach_q_vo2_unmeasured) debe aparecer en las preguntas sugeridas."""
    dates = date_seq(7)
    days = [make_day(d) for d in dates]
    summary = {"bodyage": {"unavailable_reason": "no_vo2_measurement", "vo2max_source": "estimated"}}
    dataset = {"days": days, "summary": summary, "exercises": []}

    qs = suggested_questions(dataset, locale="es", limit=4)
    ids = [q["id"] for q in qs]
    assert "vo2_unmeasured" in ids
    chip = next(q for q in qs if q["id"] == "vo2_unmeasured")
    assert chip["text"] == "¿Cómo mido mi VO₂máx?"


def test_all_insight_question_keys_exist_in_i18n():
    """Todas las claves de INSIGHT_QUESTION_KEYS y GENERIC_QUESTION_KEYS deben
    existir en los 4 locales (red de seguridad adicional al audit de i18n)."""
    from app.i18n import STRINGS

    all_keys = list(INSIGHT_QUESTION_KEYS.values()) + list(GENERIC_QUESTION_KEYS)
    for locale in ("es", "en", "fr", "pt"):
        for key in all_keys:
            assert key in STRINGS[locale], f"falta {key} en {locale}"
