"""
test_exercise_window.py — Roadmap ejercicios-truncados Paso 1.

Cubre los criterios de aceptación 1-4 del roadmap
(_dev-harness/ejercicios-truncados/ROADMAP.md):

1. build_dataset(...)["exercises"] sale SIEMPRE ordenado por `date` ascendente,
   con cualquier orden de entrada.
2. No se pierde ninguna fuente: con 45 ejercicios de google_health (recientes) +
   3 de healthkit (dentro de la ventana, al PRINCIPIO de la lista de entrada,
   simulando el orden de concatenación por SOURCE_PRIORITY de merge.py), los 3
   de healthkit sobreviven. Este test FALLA con `exercises[-40:]` (el código
   viejo) -- verificado a mano revirtiendo el fix.
3. Ventana temporal: se conservan los últimos EXERCISE_WINDOW_DAYS (60) días
   relativos a la fecha del ÚLTIMO day del dataset (nunca date.today()).
   Entradas sin fecha válida o más viejas que la ventana se descartan.
4. No regresión de cobertura: con una sola fuente y >=40 ejercicios recientes,
   la salida contiene AL MENOS los mismos ejercicios que el truncado viejo
   (últimos 40 por fecha).

Bonus (riesgo #3 del roadmap): el tope EXERCISE_MAX_ENTRIES se aplica DESPUÉS
de ordenar -- con más de 200 ejercicios dentro de la ventana, sobreviven los
200 más RECIENTES, no los 200 primeros de la lista de entrada.
"""
from __future__ import annotations

import datetime

from app.scoring import build_dataset, EXERCISE_WINDOW_DAYS, EXERCISE_MAX_ENTRIES


def _mk(date, dur_min=30, type_="running", name="Run", **extra):
    d = {"date": date, "type": type_, "name": name, "dur_min": dur_min}
    d.update(extra)
    return d


# ---------------------------------------------------------------- criterio 1: orden

def test_exercises_output_always_sorted_ascending_regardless_of_input_order():
    ref = "2024-06-10"
    exercises_in = [
        _mk("2024-06-10", name="Run"),
        _mk("2024-06-05", type_="strength", name="Lift"),
        _mk("2024-06-08", type_="cycling", name="Bike"),
    ]
    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=exercises_in)
    dates_out = [e["date"] for e in ds["exercises"]]
    assert dates_out == sorted(dates_out)
    assert dates_out == ["2024-06-05", "2024-06-08", "2024-06-10"]


# ---------------------------------------------------------------- criterio 2: no se pierde fuente

def test_no_source_lost_healthkit_survives_45_google_entries():
    """Reproduce el bug de prod: merge.py concatena healthkit ANTES que
    google_health (SOURCE_PRIORITY). Con exercises[-40:] (código viejo), los
    3 de healthkit (al principio de la lista) se pierden porque los 45 de
    google_health (al final) ya llenan el tope de 40. Con la ventana de 60
    días, los 3 sobreviven."""
    ref_date = datetime.date(2024, 8, 29)
    ref = ref_date.isoformat()

    healthkit_entries = [
        _mk((ref_date - datetime.timedelta(days=50 - i)).isoformat(),
            type_="healthkit", name="Strength", distance_km=None, source="healthkit")
        for i in range(3)
    ]
    google_entries = [
        _mk((ref_date - datetime.timedelta(days=44 - i)).isoformat(),
            type_="running", name="Run", azm=10, source="google_health")
        for i in range(45)
    ]
    # Orden de entrada = concatenación por fuente (healthkit primero), NO por fecha.
    exercises_in = healthkit_entries + google_entries
    assert len(exercises_in) == 48

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=exercises_in)
    out_dates = {e["date"] for e in ds["exercises"]}
    hk_dates = {e["date"] for e in healthkit_entries}

    # Con exercises[-40:] (código viejo, 48 entradas -> sobreviven índices 8..47),
    # los 3 primeros (healthkit) se pierden -- este assert falla con ese código.
    assert hk_dates.issubset(out_dates), (
        "los ejercicios de healthkit se perdieron -- ¿volvió el truncado por posición?"
    )
    assert len(ds["exercises"]) == 48


# ---------------------------------------------------------------- criterio 3: ventana temporal

def test_exercise_window_drops_stale_and_invalid_dates():
    ref_date = datetime.date(2024, 9, 1)
    ref = ref_date.isoformat()
    cutoff_date = ref_date - datetime.timedelta(days=EXERCISE_WINDOW_DAYS)

    inside_date = (cutoff_date + datetime.timedelta(days=1)).isoformat()  # dentro (justo)
    boundary_date = cutoff_date.isoformat()                              # exactamente el corte -> fuera
    old_date = (cutoff_date - datetime.timedelta(days=5)).isoformat()     # más viejo -> fuera

    exercises_in = [
        _mk(inside_date, name="KeepMe"),
        _mk(boundary_date, name="Boundary"),
        _mk(old_date, name="TooOld"),
        _mk(None, name="NoDate"),
        _mk("not-a-date", name="BadDate"),
        {"type": "running", "name": "MissingDateKey", "dur_min": 20},  # sin clave "date"
    ]

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=exercises_in)
    out_dates = {e["date"] for e in ds["exercises"]}
    assert out_dates == {inside_date}


def test_exercise_window_reference_is_last_day_not_today():
    """La ventana se calcula contra sorted_dates[-1] (el último day del
    dataset), nunca datetime.date.today() -- motor puro/determinista."""
    ref_date = datetime.date(2020, 1, 31)  # muy en el pasado vs. hoy real
    ref = ref_date.isoformat()
    inside_date = (ref_date - datetime.timedelta(days=10)).isoformat()

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=[_mk(inside_date)])
    out_dates = {e["date"] for e in ds["exercises"]}
    # Si la ventana usara date.today(), un ejercicio de 2020 quedaría fuera
    # de cualquier ventana de 60 días medida desde hoy.
    assert out_dates == {inside_date}


# ---------------------------------------------------------------- criterio 4: no regresión de cobertura

def test_no_coverage_regression_vs_old_last_40_truncation():
    """Con una sola fuente y 45 ejercicios recientes (todos dentro de la
    ventana de 60 días), la salida nueva debe contener AL MENOS los mismos
    ejercicios que el truncado viejo `exercises[-40:]`."""
    ref_date = datetime.date(2024, 7, 15)
    ref = ref_date.isoformat()
    exercises_in = [
        _mk((ref_date - datetime.timedelta(days=44 - i)).isoformat(), name=f"Run{i}")
        for i in range(45)
    ]
    old_selection_dates = {e["date"] for e in exercises_in[-40:]}

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=exercises_in)
    new_dates = {e["date"] for e in ds["exercises"]}

    assert old_selection_dates.issubset(new_dates)
    assert len(new_dates) == 45  # nada se pierde: las 45 caben en la ventana de 60d


# ---------------------------------------------------------------- bonus: tope de 200 se aplica DESPUÉS de ordenar

def test_max_entries_cap_keeps_most_recent_not_first_in_input_order():
    """Riesgo #3 del roadmap: EXERCISE_MAX_ENTRIES se aplica DESPUÉS de
    ordenar por fecha. Escenario real citado en el comentario de scoring.py:
    un usuario con ~4 workouts/día durante los últimos 60 días (240 entradas,
    todas dentro de la ventana) -- deben sobrevivir los 200 de los días MÁS
    RECIENTES, no los primeros 200 de la lista de entrada (que aquí viene en
    orden deliberadamente NO cronológico)."""
    ref_date = datetime.date(2024, 10, 1)
    ref = ref_date.isoformat()
    exercises_in = []
    for day_offset in range(60):
        date = (ref_date - datetime.timedelta(days=day_offset)).isoformat()
        for k in range(4):
            exercises_in.append(_mk(date, name=f"Run-{day_offset}-{k}"))
    assert len(exercises_in) == 240

    shuffled = exercises_in[::-1]  # orden de entrada invertido (NO cronológico)

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=shuffled)
    assert len(ds["exercises"]) == EXERCISE_MAX_ENTRIES

    # Los sobrevivientes deben ser los de los días con MENOR day_offset (más
    # recientes) -- 200 / 4 por día = 50 días más recientes (offset 0..49).
    surviving_offsets = {int(e["name"].split("-")[1]) for e in ds["exercises"]}
    assert max(surviving_offsets) <= 49
    assert min(surviving_offsets) == 0

    # Y la salida sigue ordenada ascendente por fecha.
    out_dates = [e["date"] for e in ds["exercises"]]
    assert out_dates == sorted(out_dates)


def test_max_entries_cap_after_sort_source_first_order_over_cap():
    """VALIDACIÓN (riesgo #3 del roadmap) — test añadido por el validador.

    El test de arriba NO mata la mutación "cap antes de ordenar": con la entrada
    exactamente invertida, `[-200:]` antes de ordenar descarta justo los 40 más
    viejos, o sea el MISMO resultado que capar después. Verificado por mutation
    testing: invertir el orden de `sorted(...)` y `[-EXERCISE_MAX_ENTRIES:]` en
    `_finalize_exercises` dejaba la suite entera VERDE.

    Este test reproduce el escenario que sí distingue los dos órdenes y que es
    el bug de fondo del roadmap: la lista llega concatenada por FUENTE
    (merge.py / SOURCE_PRIORITY), con el bloque de HealthKit —el de fechas MÁS
    RECIENTES— al PRINCIPIO, y más de EXERCISE_MAX_ENTRIES entradas de Google
    (más viejas) después. Capando antes de ordenar, el bloque de HealthKit
    entero desaparece (es el prefijo que `[-200:]` recorta); capando después de
    ordenar, sobrevive completo y se descartan los 10 días más viejos de Google.
    """
    ref_date = datetime.date(2026, 7, 29)
    ref = ref_date.isoformat()

    # HealthKit: 10 sesiones, las MÁS RECIENTES (días 0..9), al PRINCIPIO.
    healthkit_entries = [
        _mk((ref_date - datetime.timedelta(days=i)).isoformat(),
            dur_min=None, type_="Strength", name="Strength",
            distance_km=None, src="healthkit")
        for i in range(10)
    ]
    # Google: 200 sesiones más viejas (días 10..54), DESPUÉS. Total 210 > 200.
    google_entries = [
        _mk((ref_date - datetime.timedelta(days=10 + (i % 45))).isoformat(),
            type_="running", name="Run", azm=10, src="google_health")
        for i in range(200)
    ]
    exercises_in = healthkit_entries + google_entries
    assert len(exercises_in) == 210 > EXERCISE_MAX_ENTRIES

    ds = build_dataset({}, {}, {}, {}, {}, {ref: 1000}, {}, exercises=exercises_in)
    out = ds["exercises"]

    assert len(out) == EXERCISE_MAX_ENTRIES
    n_healthkit = sum(1 for e in out if e.get("src") == "healthkit")
    assert n_healthkit == 10, (
        "el bloque de healthkit se perdió en el tope de EXERCISE_MAX_ENTRIES "
        "-- ¿se está capando ANTES de ordenar por fecha?"
    )
    assert [e["date"] for e in out] == sorted(e["date"] for e in out)


# ── Invariante de cobertura: la ventana debe alcanzar al consumidor más profundo ──

def test_window_covers_deepest_consumer():
    """BLINDAJE del hallazgo N1 del validador: la ventana de ejercicios tiene que
    cubrir al consumidor que mira MÁS atrás, que es compute_body_age_stable:
    promedia `window` (30) días cerrados y, por cada uno, compute_body_age filtra
    ejercicios de los 28 días previos A ESE día -> necesita hasta ref-(30+28)=ref-58.

    Con EXERCISE_WINDOW_DAYS=60 el margen real es de 2 días. Este test NO valida
    comportamiento: valida que nadie suba `window` del estable, o el cutoff de 28d
    de compute_body_age, SIN subir también la ventana de ejercicios — hoy eso
    rompería el cálculo EN SILENCIO (los ejercicios viejos simplemente no
    estarían, y el body_age de los días profundos saldría con menos actividad de
    la real, sin ningún error).
    """
    from app.scoring import EXERCISE_WINDOW_DAYS
    from app.bodyage import MIN_STABLE_DAYS  # noqa: F401  (contexto del cálculo)

    STABLE_WINDOW_DAYS = 30   # default de compute_body_age_stable(window=30)
    BODYAGE_EXERCISE_CUTOFF = 28  # timedelta(days=28) dentro de compute_body_age

    needed = STABLE_WINDOW_DAYS + BODYAGE_EXERCISE_CUTOFF
    assert EXERCISE_WINDOW_DAYS >= needed, (
        f"EXERCISE_WINDOW_DAYS={EXERCISE_WINDOW_DAYS} no alcanza: "
        f"compute_body_age_stable(window={STABLE_WINDOW_DAYS}) + cutoff de "
        f"{BODYAGE_EXERCISE_CUTOFF}d de compute_body_age necesitan {needed} días. "
        f"Si subiste alguno de los dos, sube también EXERCISE_WINDOW_DAYS."
    )
