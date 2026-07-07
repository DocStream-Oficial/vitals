"""
gen_demo_data.py — Fase 8A (paso A1): genera el dataset DEMO usado por
VITALS_DEMO=1 — un histórico sintético largo (>=120 días) con recovery/strain/
sleep/hrv variados, ejercicios, journal de hábitos y labs de ejemplo, para que
TODAS las features de Fase 8 (journal+impact, informes, labs, healthspan,
tendencias) tengan señal visible al abrir la app en modo demo.

DETERMINISTA: usa random.Random(SEED) (semilla fija) — nunca `random` module-
level sin sembrar. Correrlo N veces produce BYTE-IDÉNTICO output (salvo que se
cambie el SEED o la lógica). Mismo patrón de "reconstruir inputs -> correr
pipeline real" que scripts/gen_golden.py: los datos NO se inventan ya-
calculados, se generan los inputs crudos (sleep/rhr/hrv/steps/exercises) y se
corre el mismo motor real (app.scoring.build_dataset + app.bodyage.compute_body_age)
para que recovery/strain/bodyage salgan CONSISTENTES con el resto del producto.

Salidas (todas bajo tests/fixtures/, consumidas por app.config/main.py cuando
VITALS_DEMO=1 — ver docstring de app/config.py):
    tests/fixtures/demo_dataset.json   — {summary, days, exercises, bodyage}
    tests/fixtures/demo_journal.json   — formato nativo journal_log.json
    tests/fixtures/demo_labs.json      — formato nativo labs_log.json

Uso:
    .venv/bin/python scripts/gen_demo_data.py
    .venv/bin/python scripts/gen_demo_data.py   # correrlo 2x -> diff cero
"""
from __future__ import annotations

import datetime as _dt
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

FIXTURES_DIR = ROOT / "tests" / "fixtures"
DATASET_OUT = FIXTURES_DIR / "demo_dataset.json"
JOURNAL_OUT = FIXTURES_DIR / "demo_journal.json"
LABS_OUT = FIXTURES_DIR / "demo_labs.json"
PLAN_OUT = FIXTURES_DIR / "demo_plan.json"
REPORTS_OUT = FIXTURES_DIR / "demo_reports.json"

SEED = 20260705  # fijo — NUNCA cambiar sin razón documentada (rompe el diff-cero)
N_DAYS = 150      # >=120 exigidos por el roadmap (healthspan/impact/trends necesitan señal)
DEMO_AGE = 38
DEMO_WAIST = 82.0
DEMO_SEX = "M"

# Fecha ancla FIJA (no "hoy") — el dataset demo es un artefacto versionado, no
# debe cambiar solo porque pasó un día. gen_demo_data.py normaliza summary/
# updated a esta misma fecha, igual que gen_golden.py hace con la suya.
_END_DATE = _dt.date(2026, 7, 4)
_START_DATE = _END_DATE - _dt.timedelta(days=N_DAYS - 1)


def _daterange():
    d = _START_DATE
    while d <= _END_DATE:
        yield d
        d += _dt.timedelta(days=1)


def _gen_night_segments(rng_seg: random.Random, deep: int, rem: int, light: int):
    """F2 roadmap P0 (hipnograma): segments sintéticos plausibles para una
    noche demo — ciclos de ~90 min con deep concentrado al inicio (pesos
    decrecientes por ciclo) y REM creciendo hacia el final (pesos crecientes),
    1-3 despertares breves (2-10 min) entre ciclos. Los totales por etapa de
    los segments cuadran EXACTO con los deep/rem/light ya generados (el
    residuo del reparto entero se ajusta en un ciclo, mejor que ±10 min que
    permite el roadmap). Formato canónico {s, e, st} en minutos desde bedtime.

    Determinista: recibe su PROPIO rng (SEED+2) para no correr la secuencia
    del rng principal — así los días demo existentes (hrv/steps/scores) no
    cambian ni un byte, solo se AÑADE el campo segments."""
    total_asleep = deep + rem + light
    if total_asleep <= 0:
        return None
    n_cycles = max(3, min(6, int(round(total_asleep / 90.0))))

    # Reparto por ciclo: deep con pesos decrecientes [n..1] (primer tercio de
    # la noche concentra el sueño profundo), rem crecientes [1..n] (el REM se
    # alarga hacia la madrugada), light repartido parejo.
    w_deep = list(range(n_cycles, 0, -1))
    w_rem = list(range(1, n_cycles + 1))
    deep_parts = [deep * w // sum(w_deep) for w in w_deep]
    rem_parts = [rem * w // sum(w_rem) for w in w_rem]
    light_parts = [light // n_cycles] * n_cycles
    # Residuos del reparto entero: a donde cada etapa pesa más (cuadre exacto).
    deep_parts[0] += deep - sum(deep_parts)
    rem_parts[-1] += rem - sum(rem_parts)
    light_parts[0] += light - sum(light_parts)

    # 1-3 despertares breves entre ciclos (nunca antes de conciliar el sueño).
    n_awake = rng_seg.randint(1, 3)
    awake_slots = rng_seg.sample(range(1, n_cycles), min(n_awake, n_cycles - 1))
    awake_after = {slot: rng_seg.randint(2, 10) for slot in awake_slots}

    segs: list = []
    pos = 0

    def _push(stage: str, dur: int):
        nonlocal pos
        if dur <= 0:
            return
        # Colapsar adyacentes de la misma etapa (p.ej. light+light cuando un
        # ciclo quedó sin deep por el reparto entero).
        if segs and segs[-1]["st"] == stage and segs[-1]["e"] == pos:
            segs[-1]["e"] = pos + dur
        else:
            segs.append({"s": pos, "e": pos + dur, "st": stage})
        pos += dur

    for i in range(n_cycles):
        half_light = light_parts[i] // 2
        _push("light", half_light)
        _push("deep", deep_parts[i])
        _push("light", light_parts[i] - half_light)
        _push("rem", rem_parts[i])
        if (i + 1) in awake_after:
            _push("awake", awake_after[i + 1])

    # Sanity con el validador REAL del producto (mismo contrato que las
    # fuentes): si algo no cuadra, mejor una noche demo sin hipnograma que un
    # fixture corrupto.
    from app.sleep_segments import validate_segments
    return validate_segments(segs)


def _gen_inputs(rng: random.Random, journal_entries: dict):
    """Genera inputs crudos deterministas con patrones realistas:
    - Ciclo semanal de entrenamiento (más strain lun/mié/vie, descanso dom).
    - Racha ocasional de mal sueño (simula una semana estresante) para que
      insights/coach tengan señal real que reportar.
    - HRV/RHR con tendencia y ruido gaussiano acotado (rng.gauss con seed).
    - Penalización determinista de HRV/recovery el día SIGUIENTE a un día con
      alcohol>0 en el journal — inyecta una correlación real y fuerte
      (no azar) para que el Behavior Impact engine (app.journal.analyze_journal,
      gate BH >=5 sí/>=5 no/n>=15) tenga al menos un finding que sobreviva en
      demo, en vez de [] por pura falta de señal.
    - Roadmap P2 (F9, paso 9): la penalización ESCALA con la CANTIDAD de
      copas del día anterior (no solo 0/1) — así el motor de dosis-respuesta
      (analyze_journal_dose_response, gate n>=15 y >=3 valores distintos)
      también tiene señal real que disparar en demo, no solo el gate sí/no.
    """
    sleep: dict = {}
    rhr: dict = {}
    hrv: dict = {}
    steps: dict = {}
    azm: dict = {}
    exercises: list = []

    # F2 (hipnograma): rng INDEPENDIENTE para los segments — ver docstring de
    # _gen_night_segments (no perturbar la secuencia del rng principal).
    rng_segments = random.Random(SEED + 2)

    hrv_base = 58.0
    rhr_base = 54.0

    for i, day in enumerate(_daterange()):
        date_s = day.isoformat()
        weekday = day.weekday()  # 0=lunes .. 6=domingo

        # Semana "mala" simulada cada ~9 semanas (viajes/estrés) para dar señal
        # al journal impact engine y a las alertas de frescura.
        stress_week = (i // 7) % 9 == 4
        stress_factor = 1.35 if stress_week else 1.0

        # Alcohol del día ANTERIOR (lag=1, igual que OUTCOMES en journal.py)
        # penaliza sueño/HRV/recovery de HOY — correlación fuerte y determinista.
        # F9: alcohol ahora es CANTIDAD (copas, 0-20) — alcohol_penalty binario
        # (0/1, para el gate sí/no existente) Y alcohol_drinks (cantidad cruda,
        # para escalar la penalización con dosis real) se derivan del mismo dato.
        prev_date = (day - _dt.timedelta(days=1)).isoformat()
        prev_entry = journal_entries.get(prev_date) or {}
        alcohol_drinks = float(prev_entry.get("alcohol") or 0)
        alcohol_penalty = 1.0 if alcohol_drinks > 0 else 0.0
        # Factor de dosis normalizado (0 copas -> 0.0, ~4 copas -> ~1.0, cap en
        # 1.5 para no desbordar con la cola alta de copas ocasional) — así 1
        # copa penaliza poco y 4+ penaliza fuerte, dando variación real que
        # correlacionar (no un escalón único 0/1).
        alcohol_dose_factor = min(1.5, alcohol_drinks / 4.0)

        # ── Sueño ──
        base_sleep_min = 445 if weekday < 5 else 480  # más sueño en fin de semana
        noise = rng.gauss(0, 22)
        asleep = max(300, int(
            base_sleep_min - noise * stress_factor - (25 if stress_week else 0)
            - 35 * alcohol_penalty
        ))
        inbed = asleep + rng.randint(20, 55)
        deep = int(asleep * rng.uniform(0.14, 0.20))
        rem = int(asleep * rng.uniform(0.18, 0.24))
        light = max(0, asleep - deep - rem)
        eff = round(100 * asleep / inbed, 1)
        bedtime_hour = 22 + rng.uniform(0, 2.5)
        bedtime = f"{int(bedtime_hour) % 24:02d}:{int((bedtime_hour % 1) * 60):02d}"
        wake_min = (bedtime_hour * 60 + asleep) % (24 * 60)
        waketime = f"{int(wake_min // 60):02d}:{int(wake_min % 60):02d}"

        sleep[date_s] = {
            "asleep": asleep, "inbed": inbed, "deep": deep, "rem": rem, "light": light,
            "eff": eff, "bedtime": bedtime, "waketime": waketime, "bed_min": -30,
        }
        # F2 (hipnograma): segments sintéticos coherentes con los totales de
        # arriba (cuadre exacto por construcción) — el hipnograma se ve en la
        # demo del README sin inventar datos que contradigan las cards.
        night_segments = _gen_night_segments(rng_segments, deep, rem, light)
        if night_segments:
            sleep[date_s]["segments"] = night_segments

        # ── HRV / RHR: tendencia lenta + ruido, penalizado en semana de estrés
        # y (más fuerte, deliberado) el día siguiente a alcohol>0 ──
        # F9: usa alcohol_dose_factor (ESCALA con la cantidad de copas) en vez
        # del alcohol_penalty binario — así recovery (derivado de hrv/rhr) trae
        # variación real correlacionable con la CANTIDAD, no solo con el sí/no,
        # para que analyze_journal_dose_response tenga señal real en demo.
        drift = 3.0 * (i / N_DAYS)  # leve mejora de fitness a lo largo del histórico
        hrv_today = (
            hrv_base + drift + rng.gauss(0, 5.5)
            - (9 if stress_week else 0) - 10 * alcohol_dose_factor
        )
        rhr_today = (
            rhr_base - drift * 0.4 + rng.gauss(0, 2.0)
            + (3 if stress_week else 0) + 4 * alcohol_dose_factor
        )
        hrv[date_s] = round(max(25.0, hrv_today), 1)
        rhr[date_s] = round(max(42.0, rhr_today), 1)

        # ── Pasos / AZM (más alto entre semana, patrón de oficina + entreno) ──
        steps_base = 9200 if weekday < 5 else 6500
        steps[date_s] = max(1500, int(steps_base + rng.gauss(0, 1800)))
        azm[date_s] = max(0, int(rng.gauss(22 if weekday in (0, 2, 4) else 8, 6)))

        # ── Ejercicios: fuerza lun/jue, cardio mar/vie, descanso/movilidad resto ──
        if weekday == 0:
            exercises.append({"date": date_s, "type": "Strength", "avg_hr": rng.randint(108, 125), "dur_min": rng.randint(45, 65)})
        elif weekday == 1 and not stress_week:
            exercises.append({"date": date_s, "type": "Run", "avg_hr": rng.randint(135, 155), "dur_min": rng.randint(30, 50)})
        elif weekday == 3:
            exercises.append({"date": date_s, "type": "Strength", "avg_hr": rng.randint(105, 122), "dur_min": rng.randint(40, 60)})
        elif weekday == 4 and not stress_week:
            exercises.append({"date": date_s, "type": "Cycling", "avg_hr": rng.randint(120, 145), "dur_min": rng.randint(35, 70)})
        elif weekday == 5 and rng.random() > 0.4:
            exercises.append({"date": date_s, "type": "Hike", "avg_hr": rng.randint(100, 118), "dur_min": rng.randint(60, 100)})

    return sleep, rhr, hrv, steps, azm, exercises


def _gen_journal(rng: random.Random) -> dict:
    """Journal de hábitos de ejemplo — formato NATIVO de journal_log.json
    (ver app/journal.py): {entries: {"YYYY-MM-DD": {key: bool|float}}, custom:
    [], updated}. Cubre suplementos, alcohol ocasional, meditación, pantallas
    en cama — con suficiente n para que analyze_journal() (gate >=5 sí / >=5
    no / >=15 total) tenga señal real que reportar en demo, no un "sin datos
    suficientes".

    Roadmap P2 (F9, paso 9/criterio 21): alcohol/meditation/breathwork ahora
    generan CANTIDADES reales (copas/minutos), no solo booleanos — con
    variación suficiente (>=3 valores distintos, gate de
    analyze_journal_dose_response) para que el motor de dosis-respuesta
    dispare al menos un finding en la demo, igual que ya hace el gate sí/no
    de analyze_journal con estos mismos datos (truthiness: >0 sigue siendo
    "sí" para ese motor, sin ningún cambio)."""
    entries: dict = {}
    for i, day in enumerate(_daterange()):
        date_s = day.isoformat()
        weekday = day.weekday()
        # Cantidad de copas: la mayoría de los findes toma, con variación real
        # (1-5 copas) — nunca constante, para que el gate de >=3 valores
        # distintos de analyze_journal_dose_response se cumpla en demo.
        drinks_today = 0
        if weekday in (4, 5) and rng.random() < 0.55:
            drinks_today = rng.randint(1, 5)
        # Minutos de meditación/breathwork: 0 la mayoría de los días, con
        # sesiones de duración variable cuando sí se practica.
        meditation_min = rng.randint(10, 30) if rng.random() < 0.35 else 0
        breathwork_min = rng.randint(5, 20) if rng.random() < 0.25 else 0

        habits = {
            "creatine": rng.random() < 0.85,
            "magnesium": rng.random() < 0.6,
            "omega3": rng.random() < 0.5,
            "alcohol": drinks_today,
            "caffeine_late": rng.random() < 0.15,
            "meditation": meditation_min,
            "breathwork": breathwork_min,
            "screen_bed": rng.random() < 0.45,
            "late_workout": weekday in (0, 3) and rng.random() < 0.3,
            "stretching": rng.random() < 0.4,
        }
        entries[date_s] = {k: v for k, v in habits.items() if v is not None}
    return {
        "entries": entries,
        "custom": [{"key": "cold_plunge", "label": "Cold plunge"}],
        "updated": _END_DATE.isoformat(),
    }


def _gen_labs() -> dict:
    """2-3 tomas de labs de ejemplo (formato nativo labs_log.json, ver
    app/labs.py) — esparsas a propósito (los labs reales son 2-6/año)."""
    d1 = (_START_DATE + _dt.timedelta(days=10)).isoformat()
    d2 = (_START_DATE + _dt.timedelta(days=80)).isoformat()
    d3 = _END_DATE.isoformat()
    entries = [
        {"id": "demo-lab-1", "date": d1, "marker": "hba1c", "value": 5.2, "unit": "%",
         "note": "Demo — chequeo trimestral"},
        {"id": "demo-lab-2", "date": d1, "marker": "vitamin_d", "value": 28.0, "unit": "ng/mL",
         "note": "Demo — chequeo trimestral"},
        {"id": "demo-lab-3", "date": d1, "marker": "ldl", "value": 108.0, "unit": "mg/dL", "note": None},
        {"id": "demo-lab-4", "date": d2, "marker": "hba1c", "value": 5.1, "unit": "%", "note": None},
        {"id": "demo-lab-5", "date": d2, "marker": "vitamin_d", "value": 34.0, "unit": "ng/mL",
         "note": "Demo — subió tras suplementar"},
        {"id": "demo-lab-6", "date": d3, "marker": "hba1c", "value": 5.0, "unit": "%", "note": None},
        {"id": "demo-lab-7", "date": d3, "marker": "ldl", "value": 96.0, "unit": "mg/dL",
         "note": "Demo — mejora con entrenamiento de fuerza"},
    ]
    return {"entries": entries, "updated": _END_DATE.isoformat()}


def _gen_reports(dataset: dict, journal: dict) -> dict:
    """Cache de reports.json de ejemplo (weekly + monthly) — formato NATIVO
    de report.py: {weekly: {...}, monthly: {...}}. Roadmap P2 (F8, paso 5):
    GET /api/report?period=monthly SOLO LEE el cache (get_report() es
    contrato estricto de cero I/O extra) — sin este archivo precargado, la
    demo nunca tendría `data` sobre el cual adjuntar `sleep_archetype`
    (quedaría siempre None), aunque el dataset sintético (150 días) sí
    cumpla de sobra el gate de >=14 noches. Se generan los NÚMEROS reales
    (build_report_data, mismo motor que usa el sync real) con una narrativa
    de ejemplo ESTÁTICA (nunca se llama al CLI de claude en la generación de
    fixtures) — mismo patrón 'sin narrativa real' que ya usa el fallback
    determinista de get_report() en producción cuando el CLI no ha corrido."""
    from app import report as _report

    cache = {}
    for period in ("weekly", "monthly"):
        data = _report.build_report_data(dataset, journal, period, ref_date=_END_DATE)
        if data is None:
            continue
        cache[period] = {
            "signature": data["period_key"],
            "locale": "es",
            "narrative": (
                "Demo — narrativa de ejemplo. En una instancia real, el coach "
                "IA genera este texto a partir de tus números reales del período."
            ),
            "data": data,
            "generated_at": _END_DATE.isoformat() + "T09:00:00",
        }
    return cache


def _gen_plan() -> dict:
    """Plan activo de ejemplo (Roadmap P1, F4, paso 9) — formato NATIVO de
    plan_log.json (ver app/plan_store.py): {active: {program_id,
    started_date, checks}, history: []}.

    started_date = _END_DATE - 4 días -> day_index=4 -> day_number=5 ("día ~5"
    del roadmap), relativo a la fecha ANCLA fija del dataset demo (NO "hoy" —
    mismo criterio que el resto de este script, para reproducibilidad byte-a-
    byte). Adherencia MIXTA: día 0 con check manual explícito (para que se
    vea el mecanismo de override), el resto queda sin check -> se evalúa AUTO
    contra el dataset real (algunos días cumplen el hábito sleep_reset,
    otros no — mezcla realista, no 100%/0% artificial)."""
    started = (_END_DATE - _dt.timedelta(days=4)).isoformat()
    checks = {started: "manual"}
    return {
        "active": {"program_id": "sleep_reset", "started_date": started, "checks": checks},
        "history": [],
    }


def main():
    from app.scoring import build_dataset
    from app.bodyage import compute_body_age

    # journal generado PRIMERO (rng independiente, SEED+1) — _gen_inputs lo
    # consulta para inyectar la correlación alcohol->peor sueño/HRV/recovery
    # del día siguiente (ver docstring de _gen_inputs).
    rng_journal = random.Random(SEED + 1)
    journal = _gen_journal(rng_journal)

    rng = random.Random(SEED)
    sleep, rhr, hrv, steps, azm, exercises = _gen_inputs(rng, journal["entries"])

    ds = build_dataset(
        sleep, rhr, hrv, {}, {}, steps, azm,
        exercises=exercises,
        age=DEMO_AGE, sex=DEMO_SEX,
        sleep_target_min=480,
    )

    bodyage = compute_body_age(
        ds["days"], ds.get("exercises", []),
        age=DEMO_AGE, waist=DEMO_WAIST, sex=DEMO_SEX,
        sleep_penalty_h=7.0,
    )

    # Fecha fija (no "hoy") para que el fixture sea reproducible byte-a-byte.
    ds["summary"]["updated"] = _END_DATE.isoformat()

    # El pipeline real (app/sync.py) escribe bodyage DENTRO de summary — todos
    # los consumidores (mcp_tools, coach, changes, /api/data) leen
    # summary["bodyage"], nunca la clave top-level. Sin esto, el modo demo (y
    # CI) muestran "sin datos" en body-age.
    ds["summary"]["bodyage"] = bodyage

    out = {
        "_comment": (
            "Dataset DEMO (Fase 8A) — 100% sintético, generado por "
            "scripts/gen_demo_data.py (SEED fija). Usado SOLO cuando VITALS_DEMO=1. "
            "No contiene datos reales de ningún usuario."
        ),
        "meta": {"age": DEMO_AGE, "waist": DEMO_WAIST, "sex": DEMO_SEX, "generated": _END_DATE.isoformat()},
        "summary": ds["summary"],
        "days": ds["days"],
        "exercises": ds.get("exercises", exercises),
        "bodyage": bodyage,
    }

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    JOURNAL_OUT.write_text(json.dumps(journal, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    labs = _gen_labs()
    LABS_OUT.write_text(json.dumps(labs, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    plan = _gen_plan()
    PLAN_OUT.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Roadmap P2 (F8, paso 5): reports.json de ejemplo — necesario para que
    # GET /api/report?period=monthly tenga `data` sobre el cual adjuntar
    # sleep_archetype en la demo (ver docstring de _gen_reports).
    reports = _gen_reports(out, journal)
    REPORTS_OUT.write_text(json.dumps(reports, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"Demo dataset generado -> {DATASET_OUT}")
    print(f"  n_days: {ds['summary']['n_days']}  engine: {ds['summary'].get('engine')}")
    print(f"Demo journal -> {JOURNAL_OUT}  ({len(journal['entries'])} entradas)")
    print(f"Demo labs -> {LABS_OUT}  ({len(labs['entries'])} tomas)")
    print(f"Demo plan -> {PLAN_OUT}  (program={plan['active']['program_id']}, started={plan['active']['started_date']})")
    print(f"Demo reports -> {REPORTS_OUT}  (periods: {list(reports.keys())})")


if __name__ == "__main__":
    main()
