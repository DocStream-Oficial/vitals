"""
test_bodyage_stable.py — Tests de app/bodyage.py::compute_body_age_stable
(roadmap edad-corporal-estable, paso 2 — modelo "dos números" estilo WHOOP).

Cubre los 4 criterios de aceptación del roadmap:
- reduce el jitter diario vs. el instantáneo (media rodante 30d de días
  cerrados sobre body_age_raw)
- invarianza intradía: mutar el último día (el "hoy" parcial) NO cambia
  el resultado, porque el estable lo excluye siempre
- fallback a instantáneo con <14 días cerrados (stable_confidence == "low")
- equivalencia con 1 solo día (nada que promediar -> instantáneo)

No depende de datos reales de ningún usuario (no están en el repo) — el
dataset sintético fabrica ruido pseudo-aleatorio con seed fija (determinista).
"""
from __future__ import annotations

import datetime as _dt
import random

from app.bodyage import compute_body_age, compute_body_age_stable, MIN_STABLE_DAYS

BIRTHDATE = "1985-06-15"
WAIST = 82.0
SEX = "M"


def _date_seq(start, n):
    d0 = _dt.date.fromisoformat(start)
    return [(d0 + _dt.timedelta(days=i)).isoformat() for i in range(n)]


def _chrono_age(date_str, birthdate=BIRTHDATE):
    bd = _dt.date.fromisoformat(birthdate)
    d = _dt.date.fromisoformat(date_str)
    return (d - bd).days / 365.25


def _make_days(n, start="2025-01-01", rhr_fn=None, hrv_fn=None, sleep_fn=None):
    dates = _date_seq(start, n)
    days = []
    for i, d in enumerate(dates):
        rhr = rhr_fn(i) if rhr_fn else 55.0
        hrv = hrv_fn(i) if hrv_fn else 45.0
        sleep = sleep_fn(i) if sleep_fn else 420
        days.append({"date": d, "rhr": rhr, "hrv": hrv, "asleep": sleep})
    return days


# ── criterio 2: estabilidad sobre serie sintética con jitter ────────────────

def test_stable_reduces_jitter():
    """Dataset sintético con ruido pseudo-aleatorio (seed fija) día a día en
    rhr/hrv/asleep, SIN tendencia real. Simula, para cada uno de los últimos
    90 "días" del dataset, qué habría mostrado body_age_stable si ese día
    fuera "hoy" (mismo método de simulación del roadmap, corrido sobre los
    un histórico real: cortar el dataset en cada fecha y recomputar).
    Espera <=4 cambios y salto máximo <=1 (baseline instantáneo real: 24
    cambios, salto máx ±2).

    Nota: con estos parámetros (rhr~55, waist=82, age~40, sin ejercicio)
    fitness_age queda clavado en el piso del clamp (20) — el MISMO escenario
    de referencia donde todo el movimiento del
    instantáneo viene de las penalties de HRV/sueño + el redondeo. El
    instantáneo (medido aparte, no asertado aquí) sí cambia varias veces en
    esos 90 días; el estable, al promediar sobre la ventana de 30d cerrados,
    los absorbe."""
    rnd = random.Random(1234)
    n = 150
    rhrs = [max(40.0, min(75.0, rnd.gauss(55, 6))) for _ in range(n)]
    hrvs = [max(20.0, min(70.0, rnd.gauss(45, 8))) for _ in range(n)]
    sleeps = [max(300, min(480, rnd.gauss(420, 35))) for _ in range(n)]
    days = _make_days(
        n,
        rhr_fn=lambda i: rhrs[i],
        hrv_fn=lambda i: hrvs[i],
        sleep_fn=lambda i: sleeps[i],
    )

    stable_series = []
    for i in range(60, n):  # últimos 90 "días" simulados, con >=60d de historial ya acumulada
        slice_days = days[: i + 1]
        result = compute_body_age_stable(slice_days, [], BIRTHDATE, WAIST, SEX)
        stable_series.append(result["body_age_stable"])

    assert len(stable_series) == 90

    changes = sum(1 for a, b in zip(stable_series, stable_series[1:]) if a != b)
    max_jump = max((abs(a - b) for a, b in zip(stable_series, stable_series[1:])), default=0)

    assert changes <= 4, f"esperaba <=4 cambios en 90d, hubo {changes}: {stable_series}"
    assert max_jump <= 1, f"esperaba salto máximo <=1, hubo {max_jump}: {stable_series}"


# ── criterio 3: invarianza intradía ─────────────────────────────────────────

def test_stable_ignores_current_day():
    """Mutar el último día (rhr/hrv/asleep MUY distintos) no debe cambiar
    body_age_stable — el estable excluye siempre el último día del dataset
    (el "hoy" parcial que llena el auto-sync)."""
    n = 40
    days = _make_days(n, rhr_fn=lambda i: 58.0, hrv_fn=lambda i: 42.0, sleep_fn=lambda i: 410)
    result1 = compute_body_age_stable(days, [], BIRTHDATE, WAIST, SEX)

    days_mutated = [dict(d) for d in days]
    days_mutated[-1] = dict(days_mutated[-1], rhr=140.0, hrv=5.0, asleep=90)
    result2 = compute_body_age_stable(days_mutated, [], BIRTHDATE, WAIST, SEX)

    assert result1["body_age_stable"] == result2["body_age_stable"], (
        f"mutar el último día no debería cambiar el estable: {result1} vs {result2}"
    )
    assert result1["n_days_stable"] == result2["n_days_stable"]
    assert result1["stable_confidence"] == result2["stable_confidence"]


# ── criterio 4: fallback con poco historial ─────────────────────────────────

def test_stable_fallback_short_history():
    """Con <14 días cerrados, body_age_stable cae al body_age instantáneo:
    no rompe, no da null, y queda marcado stable_confidence == 'low'."""
    n = 10  # closed = 9 días < MIN_STABLE_DAYS (14)
    days = _make_days(n, rhr_fn=lambda i: 60.0, hrv_fn=lambda i: 40.0, sleep_fn=lambda i: 400)
    result = compute_body_age_stable(days, [], BIRTHDATE, WAIST, SEX)

    assert result["stable_confidence"] == "low"
    assert result["n_days_stable"] == 0
    assert result["body_age_stable"] is not None

    age_now = _chrono_age(days[-1]["date"])
    instant = compute_body_age(days, [], age_now, WAIST, SEX)
    assert result["body_age_stable"] == instant["body_age"]


# ── equivalencia con 1 solo día ──────────────────────────────────────────────

def test_stable_equivalence_single_day():
    """Con 1 solo día no hay nada que promediar -> body_age_stable ==
    body_age instantáneo."""
    days = [{"date": "2025-01-01", "rhr": 58.0, "hrv": 44.0, "asleep": 415}]
    result = compute_body_age_stable(days, [], BIRTHDATE, WAIST, SEX)

    age_now = _chrono_age(days[-1]["date"])
    instant = compute_body_age(days, [], age_now, WAIST, SEX)

    assert result["body_age_stable"] == instant["body_age"]
    assert result["stable_confidence"] == "low"
    assert result["n_days_stable"] == 0
