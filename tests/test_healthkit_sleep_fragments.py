"""
test_healthkit_sleep_fragments.py — Auditoría 23-jul, H4.

El iOS puede mandar DOS entradas de sueño con el mismo día de despertar (noche
interrumpida >3h partida en fragmentos, o noche + siesta que termina el mismo
día). Antes `_parse_sleep` hacía `out[day] = rec` a secas: la ÚLTIMA entrada
sobrescribía sin criterio (caso real: noche de 8.2h mostrada como 1.2h — se
perdieron 7 horas). Regla nueva: gana el registro con MAYOR `asleep` — mismo
criterio "gana el más completo" que _merge_sleep (merge.py) y parse_sleep
(parsers.py). NO se suman fragmentos (sumar inflaría cuando el duplicado es
una siesta).
"""
from __future__ import annotations

from app.sources.healthkit import HealthKitSource


def _n(date, asleep, **kw):
    e = {"date": date, "asleep": asleep, "deep": 60, "rem": 80, "light": 200,
         "eff": 90, "bedtime": "23:30", "waketime": "07:00",
         "inbed": (asleep + 20) if asleep is not None else None}
    e.update(kw)
    return e


def test_duplicate_date_keeps_largest_not_last():
    """Caso REAL (2025-08-12): noche 420min primero, siesta 75min después.
    Antes: sobrevivía 75 (la última). Ahora: sobrevive 420."""
    src = HealthKitSource()
    out = src._parse_sleep([_n("2025-08-12", 420), _n("2025-08-12", 75)])
    assert out["2025-08-12"]["asleep"] == 420


def test_duplicate_date_keeps_largest_when_larger_comes_last():
    """Orden inverso (fragmento chico primero): también gana el mayor."""
    src = HealthKitSource()
    out = src._parse_sleep([_n("2026-01-10", 90), _n("2026-01-10", 380)])
    assert out["2026-01-10"]["asleep"] == 380


def test_duplicate_with_none_asleep_never_beats_real():
    """Un duplicado con asleep=None no debe destronar una noche real (None-safe)."""
    src = HealthKitSource()
    out = src._parse_sleep([_n("2026-01-11", 400), _n("2026-01-11", None)])
    assert out["2026-01-11"]["asleep"] == 400
    # y al revés: el real gana sobre el None aunque llegue después
    out2 = src._parse_sleep([_n("2026-01-12", None), _n("2026-01-12", 400)])
    assert out2["2026-01-12"]["asleep"] == 400


def test_distinct_dates_unaffected():
    """Noches en días distintos: comportamiento idéntico al de siempre."""
    src = HealthKitSource()
    out = src._parse_sleep([_n("2026-01-13", 400), _n("2026-01-14", 410)])
    assert out["2026-01-13"]["asleep"] == 400
    assert out["2026-01-14"]["asleep"] == 410


def test_winner_keeps_its_own_fields():
    """El registro ganador conserva SUS campos (bedtime/segments), no un híbrido."""
    src = HealthKitSource()
    big = _n("2026-01-15", 420, bedtime="00:10")
    small = _n("2026-01-15", 60, bedtime="15:00")   # siesta vespertina
    out = src._parse_sleep([big, small])
    assert out["2026-01-15"]["bedtime"] == "00:10"
