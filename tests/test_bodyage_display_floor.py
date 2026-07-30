"""
test_bodyage_display_floor.py — Roadmap edad-corporal-credibilidad, Paso 2:
piso relativo de DISPLAY. Nunca mostrar una edad más de 15 años menor que la
cronológica — claves *_display, ADITIVAS (los valores crudos fitness_age/
body_age/body_age_stable siguen intactos, ver test_bodyage_stable.py y
test_regression.py, que no se tocan).

Cubre el criterio de aceptación 3 del roadmap:
- Caso Mariana (F/49, cintura delgada): fitness cruda clampeada a 20 ->
  display 34, age_floored True.
- Edad 30 con body_age ~45 (por encima del piso) -> display == crudo, sin
  piso (el piso solo SUBE, nunca baja).
- body_age_stable_display presente en el camino normal Y en el fallback.
"""
from __future__ import annotations

import datetime as _dt

from app.bodyage import compute_body_age, compute_body_age_stable


def _date_seq(start, n):
    d0 = _dt.date.fromisoformat(start)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n)]


def _make_days(n, start="2025-01-01", vo2=None):
    """Roadmap stable-solo-medido: `vo2` siembra la medición del reloj en TODOS
    los días. Los tests del piso de display sobre compute_body_age NO la
    necesitan (el piso no depende de la fuente del VO2), pero el del camino
    normal de compute_body_age_stable SÍ: esa función ya solo promedia días
    con medición real, así que sin `vo2` caería al fallback."""
    dates = _date_seq(start, n)
    days = [{"date": d, "rhr": 55.0} for d in dates]
    if vo2 is not None:
        for d in days:
            d["vo2"] = vo2
    return days


# ── compute_body_age: piso SUBE cuando la cruda queda muy por debajo ───────

def test_floor_applies_when_fitness_age_clamped_low():
    """Caso Mariana (F/49, cintura delgada): fitness_age cruda clampea a 20
    (piso duro de compute_body_age) -> display se topa en 49-15=34, con
    age_floored=True. body_age crudo SIGUE siendo 20 (no se toca)."""
    days = _make_days(14)
    result = compute_body_age(days, [], 49, 55.0, "F")
    assert result["fitness_age"] == 20
    assert result["body_age"] == 20
    assert result["fitness_age_display"] == 34
    assert result["body_age_display"] == 34
    assert result["age_floored"] is True


def test_no_floor_when_body_age_above_relative_floor():
    """Edad 30, body_age ~45 (por encima del piso relativo, que para <33 años
    coincide con el piso duro de 18) -> display == crudo, age_floored False."""
    days = _make_days(14)
    result = compute_body_age(days, [], 30, 119.5, "M")
    assert result["fitness_age"] == 45
    assert result["body_age"] == 45
    assert result["fitness_age_display"] == 45  # sin piso: el piso solo SUBE
    assert result["body_age_display"] == 45
    assert result["age_floored"] is False


def test_floor_never_lowers_a_value():
    """Sanidad explícita: display nunca es MENOR que el crudo (el piso solo
    puede subir el número, jamás bajarlo)."""
    days = _make_days(14)
    for age, waist, sex in [(49, 55.0, "F"), (30, 119.5, "M"), (65, 90.0, "M")]:
        result = compute_body_age(days, [], age, waist, sex)
        assert result["fitness_age_display"] >= result["fitness_age"]
        assert result["body_age_display"] >= result["body_age"]


# ── compute_body_age_stable: body_age_stable_display en ambos caminos ──────

def test_stable_display_present_in_fallback_path():
    """<2 días -> fallback al instantáneo; body_age_stable_display debe ser
    el mismo body_age_display del instantáneo (con su propio piso)."""
    days = [{"date": "2025-01-01", "rhr": 55.0}]
    result = compute_body_age_stable(days, [], "1976-01-01", 55.0, "F")  # ~49 años
    assert result["stable_confidence"] == "low"
    assert "body_age_stable_display" in result
    instant = compute_body_age(days, [], 49, 55.0, "F")
    assert result["body_age_stable_display"] == instant["body_age_display"]


def test_stable_display_present_in_normal_path():
    """Camino normal (>=MIN_STABLE_DAYS días cerrados) -> body_age_stable_display
    presente, con piso relativo evaluado a la edad cronológica del último día
    cerrado (age_now), consistente con el resto de compute_body_age_stable."""
    # vo2 sembrado: el camino normal del estable exige días MEDIDOS (roadmap
    # stable-solo-medido); sin medición esta función cae al fallback y este
    # test no probaría la ventana rodante.
    # vo2=45.0: mantiene conf=="ok" Y deja el piso ENGANCHADO (fitness cruda
    # clampea a 20 -> display 34). Con 30.0 salía stable=52/display=52 y el
    # assert display>=stable se cumplía por igualdad: el piso quedaba inerte.
    days = _make_days(50, vo2=45.0)
    result = compute_body_age_stable(days, [], "1976-01-01", 55.0, "F")
    assert result["stable_confidence"] == "ok"
    assert "body_age_stable_display" in result
    assert result["body_age_stable_display"] >= result["body_age_stable"]


def test_stable_display_no_floor_when_birthdate_unresolvable():
    """birthdate inválido -> age_now no se puede calcular -> (mismo motivo,
    todo age_d de la ventana tampoco se puede calcular) cae al fallback del
    instantáneo, nunca lanza, y body_age_stable_display sigue presente."""
    days = _make_days(50)
    result = compute_body_age_stable(days, [], "not-a-date", 55.0, "F")
    assert result["stable_confidence"] == "low"
    assert "body_age_stable_display" in result
