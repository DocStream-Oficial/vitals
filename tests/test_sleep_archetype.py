"""
test_sleep_archetype.py — Tests de app/sleep_archetype.py (Roadmap P2, F8,
paso 4).

Cubre:
(a) gate de >=14 noches: 13 -> None, 14 -> arquetipo (borde exacto, riesgo #5
    del roadmap).
(b) mes sin ningún dato -> None limpio, nunca crashea.
(c) clasificación determinista: escenarios sintéticos "de libro" para cada
    arquetipo (consistente+cumple, consistente+corto, consistente+excede,
    inconsistente, tarde).
(d) percentiles siempre dentro de [0,100] cuando no son None.
(e) i18n: name/description resuelven a texto real (no la clave cruda) en los
    4 locales, para los 6 arquetipos.
(f) nunca lanza ante datos ralos (bed_min/eff faltantes, fechas fuera de orden).
"""
from __future__ import annotations

import datetime

import pytest

from app import sleep_archetype
from app.sleep_archetype import classify_month, _ARCHETYPES


def _make_month_days(start: datetime.date, n_days: int, *, asleep=480, eff=90.0,
                      bed_min=-30.0, waketime="07:00", strain=10, recovery=60,
                      jitter_bed=None, jitter_asleep=None):
    """Genera `n_days` días sintéticos consecutivos desde `start`, con valores
    constantes salvo que se pase una función `jitter_*(i) -> delta` para variar
    bed_min/asleep día a día (usado para simular inconsistencia)."""
    days = []
    for i in range(n_days):
        d = start + datetime.timedelta(days=i)
        bm = bed_min + (jitter_bed(i) if jitter_bed else 0)
        asl = asleep + (jitter_asleep(i) if jitter_asleep else 0)
        days.append({
            "date": d.isoformat(),
            "asleep": asl,
            "eff": eff,
            "bed_min": bm,
            "waketime": waketime,
            "strain": strain,
            "recovery": recovery,
        })
    return days


# ── (a) gate de >=14 noches (borde exacto) ──────────────────────────────────

def test_gate_13_nights_returns_none():
    start = datetime.date(2026, 5, 1)
    days = _make_month_days(start, 13)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is None


def test_gate_14_nights_returns_archetype():
    start = datetime.date(2026, 5, 1)
    days = _make_month_days(start, 14)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] in _ARCHETYPES


# ── (b) sin datos -> None limpio ─────────────────────────────────────────────

def test_no_data_returns_none():
    assert classify_month([], ref_date=datetime.date(2026, 6, 15)) is None
    assert classify_month(None, ref_date=datetime.date(2026, 6, 15)) is None


def test_month_with_zero_nights_in_range_returns_none():
    """Datos existen pero NINGUNO cae en el último mes completo."""
    days = _make_month_days(datetime.date(2026, 1, 1), 20)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is None


# ── (c) clasificación determinista ──────────────────────────────────────────

def test_consistent_meets_need_is_swiss_clock():
    """Consistencia alta + duración cumple + hora normal -> Reloj Suizo."""
    # Historial largo (>=90 días) para que sleep_need_min se estabilice cerca
    # de 480 sin deuda residual de los primeros días.
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 120, asleep=480, eff=92.0, bed_min=-30.0, strain=8, recovery=70)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] == "swiss_clock"
    assert result["metrics"]["consistency_score"] >= 70
    assert 90 <= result["metrics"]["mean_sleep_score"] <= 110


def test_consistent_short_sleep_is_wound_too_tight():
    """Consistencia alta pero duerme sistemáticamente poco -> Corto de Cuerda."""
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 120, asleep=340, eff=88.0, bed_min=-15.0, strain=8, recovery=60)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] == "wound_too_tight"
    assert result["metrics"]["mean_sleep_score"] < 90


def test_consistent_excess_sleep_is_extended_stay():
    """Consistencia alta + duerme claramente de más -> Sueño Extendido.
    mean_sleep_score es display (CAPADO a 100 por diseño, ver docstring del
    módulo) — el bucket de duración usa el ratio SIN CAP internamente, así
    que aquí solo verificamos que el score capado efectivamente tocó el
    techo (evidencia indirecta del ratio real siendo >110)."""
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 120, asleep=620, eff=93.0, bed_min=-30.0, strain=5, recovery=75)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] == "extended_stay"
    assert result["metrics"]["mean_sleep_score"] == 100


def test_erratic_bedtime_is_erratic_rhythm():
    """bed_min/waketime alternando fuerte día a día -> baja consistencia."""
    start = datetime.date(2026, 2, 1)

    def jitter(i):
        return 200 if i % 2 == 0 else -200

    days = _make_month_days(start, 120, asleep=480, eff=90.0, bed_min=0.0,
                             strain=8, recovery=60, jitter_bed=jitter)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] == "erratic_rhythm"
    assert result["metrics"]["consistency_score"] < 40


def test_late_bedtime_with_good_duration_is_warm_night_owl():
    """Consistencia alta + duración cumple + se acuesta consistentemente
    tarde (>120min tras medianoche) -> Nocturno Templado."""
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 120, asleep=480, eff=90.0, bed_min=150.0,
                             waketime="09:30", strain=8, recovery=65)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["archetype"] == "warm_night_owl"


# ── (d) percentiles siempre en [0,100] ───────────────────────────────────────

def test_percentiles_always_within_0_100():
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 120, asleep=480, eff=90.0, bed_min=-30.0)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    for key, val in result["percentiles"].items():
        if val is not None:
            assert 0 <= val <= 100, f"{key} fuera de rango: {val}"


def test_percentile_of_month_matching_entire_history_is_100():
    """Si el mes evaluado ES toda la historia (mismos valores), el percentil
    de su propia media debe ser 100 (empata con el máximo == todos los valores)."""
    start = datetime.date(2026, 5, 1)
    days = _make_month_days(start, 31, asleep=480, eff=90.0, bed_min=-30.0)
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is not None
    assert result["percentiles"]["mean_asleep_min"] == 100


# ── (e) i18n: name/description resuelven a texto real en los 4 locales ──────

@pytest.mark.parametrize("locale", ["es", "en", "fr", "pt"])
def test_all_archetypes_have_localized_name_and_description(locale):
    for slug in _ARCHETYPES:
        from app.i18n import tr
        name = tr(f"archetype_{slug}_name", locale)
        desc = tr(f"archetype_{slug}_desc", locale)
        assert name and name != f"archetype_{slug}_name", f"Falta name de {slug} en {locale}"
        assert desc and desc != f"archetype_{slug}_desc", f"Falta desc de {slug} en {locale}"


@pytest.mark.parametrize("locale", ["es", "en", "fr", "pt"])
def test_classify_month_returns_localized_strings(locale):
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 40, asleep=480, eff=90.0, bed_min=-30.0)
    result = classify_month(days, ref_date=datetime.date(2026, 3, 15), locale=locale)
    assert result is not None
    assert not result["name"].startswith("archetype_")
    assert not result["description"].startswith("archetype_")


# ── (f) robustez ante datos ralos — nunca lanza ──────────────────────────────

def test_missing_bed_min_and_eff_never_crashes():
    start = datetime.date(2026, 2, 1)
    days = []
    for i in range(20):
        d = start + datetime.timedelta(days=i)
        days.append({"date": d.isoformat(), "asleep": 450})  # sin eff/bed_min/waketime
    result = classify_month(days, ref_date=datetime.date(2026, 3, 1))
    assert result is not None  # gate de asleep se cumple igual
    assert result["metrics"]["mean_efficiency_pct"] is None
    assert result["metrics"]["consistency_score"] is None


def test_malformed_days_entries_never_crash():
    days = [None, "not a dict", {"date": "bad-date", "asleep": 400}, {"asleep": 400}]
    result = classify_month(days, ref_date=datetime.date(2026, 6, 15))
    assert result is None  # no hay suficientes noches válidas


def test_unsorted_dates_still_work():
    start = datetime.date(2026, 2, 1)
    days = _make_month_days(start, 30, asleep=480, eff=90.0, bed_min=-30.0)
    shuffled = list(reversed(days))
    result = classify_month(shuffled, ref_date=datetime.date(2026, 3, 5))
    assert result is not None


def test_classify_month_default_ref_date_is_today():
    """Sin ref_date explícito, usa datetime.date.today() — no debe lanzar."""
    start = datetime.date.today() - datetime.timedelta(days=60)
    days = _make_month_days(start, 40, asleep=480, eff=90.0, bed_min=-30.0)
    result = classify_month(days)  # ref_date=None
    assert result is None or isinstance(result, dict)
