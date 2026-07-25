"""
test_coach.py — Tests de app/coach.py::coach_card (Roadmap coach-objetivo-vo2,
Paso 4).

No existía un archivo de tests dedicado a coach.py antes de esta corrida
(build_coach/coach_card no tenían cobertura directa) — este archivo se
concentra en el bullet de EDAD CORPORAL (BULLET 1), que es el que el
roadmap enriquece con fuente del VO2 (medido/estimado), percentil y
objetivo explícito cuando el gap fitness>real supera 2 años.
"""
from __future__ import annotations

from app.coach import coach_card


def _dataset(bodyage):
    days = [{"date": "2026-06-20", "recovery": 60, "asleep": 420, "hrv": 55}]
    return {"days": days, "summary": {"bodyage": bodyage}, "exercises": []}


def _body_age_bullet(dataset, locale="es"):
    """BULLET 1 (edad corporal) es el PRIMERO que coach_card() agrega cuando
    body_age/fitness_age están presentes (ver app/coach.py) -> bullets[0]."""
    card = coach_card(dataset, locale=locale)
    bullets = card["bullets"]
    if not bullets:
        return None
    return bullets[0]


class TestBodyAgeBulletVo2Enrichment:
    def test_measured_source_with_percentile_shown(self):
        bodyage = {
            "body_age": 56, "fitness_age": 56, "vo2max": 29.1, "category": "Bajo",
            "vo2max_source": "measured", "vo2max_percentile": 35, "age": 49,
        }
        bullet = _body_age_bullet(_dataset(bodyage))
        assert bullet is not None
        assert "VO₂ medido (percentil 35)" in bullet["body"]

    def test_measured_source_without_percentile_falls_back_generic(self):
        bodyage = {
            "body_age": 56, "fitness_age": 56, "vo2max": 29.1, "category": "Bajo",
            "vo2max_source": "measured", "age": 49,
        }
        bullet = _body_age_bullet(_dataset(bodyage))
        assert "VO₂ medido." in bullet["body"]
        assert "percentil" not in bullet["body"]

    def test_estimated_source_shown(self):
        bodyage = {
            "body_age": 45, "fitness_age": 45, "vo2max": 44.0, "category": "Promedio",
            "vo2max_source": "estimated", "age": 40,
        }
        bullet = _body_age_bullet(_dataset(bodyage))
        assert "VO₂ estimado." in bullet["body"]

    def test_goal_present_when_gap_over_2(self):
        bodyage = {
            "body_age": 56, "fitness_age": 56, "fitness_age_display": 56,
            "vo2max": 29.1, "category": "Bajo",
            "vo2max_source": "measured", "vo2max_percentile": 35, "age": 49,
        }
        bullet = _body_age_bullet(_dataset(bodyage))
        assert "Objetivo: baja tu edad fitness de 56 a 49 años." in bullet["body"]

    def test_no_goal_when_gap_at_or_below_2(self):
        bodyage = {
            "body_age": 51, "fitness_age": 51, "fitness_age_display": 51,
            "vo2max": 40.0, "category": "Promedio",
            "vo2max_source": "measured", "vo2max_percentile": 45, "age": 49,
        }
        bullet = _body_age_bullet(_dataset(bodyage))
        assert "Objetivo" not in bullet["body"]

    def test_old_dataset_without_vo2max_source_identical_bullet(self):
        """None-safe: sin vo2max_source (dataset pre-edad-corporal-credibilidad)
        -> el bullet no gana ni fuente ni objetivo (cero regresión visual)."""
        bodyage = {"body_age": 45, "fitness_age": 45, "vo2max": 44.0, "category": "Promedio", "age": 40}
        bullet = _body_age_bullet(_dataset(bodyage))
        assert bullet["body"] == "VO₂máx ~44.0 (Promedio) · tu edad real 40 · FC reposo  · HRV ."
        assert "VO₂ medido" not in bullet["body"]
        assert "VO₂ estimado" not in bullet["body"]
        assert "Objetivo" not in bullet["body"]

    def test_locale_variants_produce_translated_text(self):
        bodyage = {
            "body_age": 56, "fitness_age": 56, "fitness_age_display": 56,
            "vo2max": 29.1, "category": "Bajo",
            "vo2max_source": "measured", "vo2max_percentile": 35, "age": 49,
        }
        for locale, expected in (
            ("en", "VO2 measured (percentile 35)."),
            ("fr", "VO2 mesuré (percentile 35)."),
            ("pt", "VO2 medido (percentil 35)."),
        ):
            bullet = _body_age_bullet(_dataset(bodyage), locale=locale)
            assert expected in bullet["body"], f"[{locale}] falta '{expected}' en '{bullet['body']}'"

    def test_no_bodyage_does_not_crash(self):
        """Sin summary.bodyage en absoluto (perfil incompleto) -> coach_card()
        no debe lanzar; ningún bullet debe mencionar VO2/Objetivo."""
        dataset = {"days": [{"date": "2026-06-20", "recovery": 60}], "summary": {}, "exercises": []}
        card = coach_card(dataset)  # no debe lanzar
        for b in card["bullets"]:
            # VALIDACIÓN (validador): el texto ES usa "VO₂" (subíndice), no
            # "VO2" — buscar "VO2" a secas hacía la aserción casi vacía.
            assert "VO₂" not in b["body"] and "Objetivo" not in b["body"]
