"""
gen_golden.py — Ronda 5: regenera tests/fixtures/golden_synthetic.json bajo
engine v2 (strain híbrido TRIMP, recovery rodante, sleep_target_min).

DETERMINISTA: reconstruye los INPUTS crudos (sleep/rhr/hrv/steps/azm/exercises)
desde los días ya presentes en el fixture ACTUAL (que son datos sintéticos
fabricados, no personales), corre el pipeline v2 (build_dataset + compute_body_age)
sobre esos mismos inputs, y reescribe las secciones calculadas del fixture
(summary/days/bodyage). meta/_comment/exercises (inputs) se preservan tal cual.

Correrlo dos veces debe dar diff CERO la segunda vez (determinismo) — la única
excepción es summary["updated"] (fecha de hoy), que gen_golden.py NORMALIZA
explícitamente a la fecha ya presente en el fixture para no ensuciar el diff.

Uso:
    .venv/bin/python scripts/gen_golden.py
    .venv/bin/python scripts/gen_golden.py   # correrlo 2x -> diff cero
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
GOLDEN = ROOT / "tests" / "fixtures" / "golden_synthetic.json"

# Campos que build_dataset copia de vuelta a cada day si vienen en el sleep
# record crudo (ver _SLEEP_FIELDS en app/scoring.py + bed_min/bedtime/waketime).
_SLEEP_FIELDS = ("asleep", "inbed", "awake", "deep", "rem", "light", "eff",
                 "bedtime", "waketime", "bed_min")


def _reconstruct_inputs(days: list[dict]):
    """Reconstruye los dicts de entrada {date: value} desde los días ya-calculados
    del fixture actual (que en su día se generaron desde estos mismos inputs)."""
    sleep: dict = {}
    rhr: dict = {}
    hrv: dict = {}
    resp: dict = {}
    steps: dict = {}
    azm: dict = {}
    spo2: dict = {}
    skin: dict = {}

    for d in days:
        date = d["date"]
        sleep_rec = {k: d[k] for k in _SLEEP_FIELDS if k in d}
        if sleep_rec:
            sleep[date] = sleep_rec
        if d.get("rhr") is not None:
            rhr[date] = d["rhr"]
        if d.get("hrv") is not None:
            hrv[date] = d["hrv"]
        if d.get("resp") is not None:
            resp[date] = d["resp"]
        if d.get("steps") is not None:
            steps[date] = d["steps"]
        if d.get("vigorous") is not None:
            azm[date] = d["vigorous"]
        if d.get("spo2") is not None:
            spo2[date] = d["spo2"]
        if d.get("skin_temp") is not None:
            skin[date] = d["skin_temp"]

    return sleep, rhr, hrv, resp, steps, azm, spo2, skin


def main():
    from app.scoring import build_dataset
    from app.bodyage import compute_body_age

    current = json.loads(GOLDEN.read_text(encoding="utf-8"))
    meta = current["meta"]
    comment = current["_comment"]
    exercises_input = current["exercises"]  # inputs crudos (avg_hr/dur_min/date/type)

    sleep, rhr, hrv, resp, steps, azm, spo2, skin = _reconstruct_inputs(current["days"])

    ds = build_dataset(
        sleep, rhr, hrv, resp, {}, steps, azm,
        spo2=spo2, skin=skin, exercises=exercises_input,
        age=meta["age"], sex=meta["sex"],
        sleep_target_min=480,  # default — golden congela el comportamiento default
    )

    bodyage = compute_body_age(
        ds["days"], ds.get("exercises", []),
        age=meta["age"], waist=meta["waist"], sex=meta["sex"],
        sleep_penalty_h=7.0,  # derivado de (480-60)/60 — default equivalente a antes
    )

    # Normalizar "updated" a un valor FIJO para que el fixture sea reproducible
    # byte-a-byte entre corridas (no depende de la fecha de hoy).
    ds["summary"]["updated"] = current["summary"].get("updated", "2026-06-28")

    out = {
        "_comment": comment,
        "meta": meta,
        "summary": ds["summary"],
        "days": ds["days"],
        "exercises": exercises_input,
        "bodyage": bodyage,
    }

    GOLDEN.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Golden regenerado -> {GOLDEN}")
    print(f"  n_days: {ds['summary']['n_days']}  engine: {ds['summary'].get('engine')}")


if __name__ == "__main__":
    main()
