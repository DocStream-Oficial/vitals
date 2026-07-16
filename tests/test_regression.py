"""
test_regression.py — verifica que scoring y bodyage son self-consistent
con el golden fixture SINTÉTICO (tests/fixtures/golden_synthetic.json).

El fixture fue generado corriendo el pipeline real sobre datos FABRICADOS
(no son datos personales del usuario). Consultar tests/fixtures/golden_synthetic.json.

Lo que se valida:
1. compute_body_age: alimentada con los días/ejercicios del golden sintético
   reproduce exactamente el bodyage guardado en el fixture
   (vo2max, fitness_age, body_age, pa_index, penalty, category, rhr, hrv).
2. build_dataset con inputs sintéticos pequeños verifica que los scores
   (recovery, sleep_perf, strain) se calculan con las fórmulas correctas.

Ronda 5 (engine v2 — excepción versionada, ver ROADMAP-vitals-ronda5-engine-v2.md):
3. test_build_dataset_reproduces_golden_days: reconstruye los INPUTS crudos desde
   el fixture (misma lógica que scripts/gen_golden.py) y compara byte-a-byte
   contra los "days"/"summary" YA CONGELADOS en el JSON — a diferencia del test 2
   (que recalcula el "expected" con las constantes EN VIVO del módulo, y por
   tanto no detecta si alguien las muta), este test SÍ congela las fórmulas:
   mutar STRAIN_V2_K/F_VIG/F_STEPS in-process hace que el resultado recalculado
   diverja del valor guardado en disco → test rojo. Es el gate que exige el
   roadmap ("mutar una constante → rojo").
"""
import json
import math
import statistics
from pathlib import Path

import pytest

# Ruta al golden SINTÉTICO (no depende de data/ del usuario)
GOLDEN = Path(__file__).parent / "fixtures" / "golden_synthetic.json"


# ---------------------------------------------------------------- helpers

def load_golden():
    with open(GOLDEN) as f:
        return json.load(f)


# ---------------------------------------------------------------- test 1: compute_body_age reproduce el golden sintético

def test_bodyage_reproduces_golden():
    """compute_body_age con los datos del golden SINTÉTICO debe reproducir el bodyage guardado."""
    from app.bodyage import compute_body_age

    data = load_golden()
    days = data["days"]
    exercises = data.get("exercises", [])
    meta = data["meta"]
    expected = data["bodyage"]

    result = compute_body_age(
        days, exercises,
        age=meta["age"],
        waist=meta["waist"],
        sex=meta["sex"],
    )

    assert result["vo2max"] == expected["vo2max"], (
        f"vo2max: got {result['vo2max']}, expected {expected['vo2max']}"
    )
    assert result["fitness_age"] == expected["fitness_age"], (
        f"fitness_age: got {result['fitness_age']}, expected {expected['fitness_age']}"
    )
    assert result["body_age"] == expected["body_age"], (
        f"body_age: got {result['body_age']}, expected {expected['body_age']}"
    )
    assert result["pa_index"] == expected["pa_index"], (
        f"pa_index: got {result['pa_index']}, expected {expected['pa_index']}"
    )
    assert result["penalty"] == expected["penalty"], (
        f"penalty: got {result['penalty']}, expected {expected['penalty']}"
    )
    assert result["category"] == expected["category"], (
        f"category: got {result['category']}, expected {expected['category']}"
    )
    assert result["rhr"] == expected["rhr"], (
        f"rhr: got {result['rhr']}, expected {expected['rhr']}"
    )
    assert result["hrv"] == expected["hrv"], (
        f"hrv: got {result['hrv']}, expected {expected['hrv']}"
    )


# ------------- test 3 (Ronda 5): build_dataset reproduce el golden CONGELADO ------------

_SLEEP_FIELDS_GOLDEN = ("asleep", "inbed", "awake", "deep", "rem", "light", "eff",
                        "bedtime", "waketime", "bed_min")


def _reconstruct_inputs_from_golden(days: list[dict]):
    """Idéntico a scripts/gen_golden.py::_reconstruct_inputs — reconstruye los
    inputs crudos {date: value} desde los días ya-calculados del fixture."""
    sleep, rhr, hrv, resp, steps, azm, spo2, skin = {}, {}, {}, {}, {}, {}, {}, {}
    for d in days:
        date = d["date"]
        sleep_rec = {k: d[k] for k in _SLEEP_FIELDS_GOLDEN if k in d}
        if sleep_rec:
            sleep[date] = sleep_rec
        if d.get("rhr") is not None: rhr[date] = d["rhr"]
        if d.get("hrv") is not None: hrv[date] = d["hrv"]
        if d.get("resp") is not None: resp[date] = d["resp"]
        if d.get("steps") is not None: steps[date] = d["steps"]
        if d.get("vigorous") is not None: azm[date] = d["vigorous"]
        if d.get("spo2") is not None: spo2[date] = d["spo2"]
        if d.get("skin_temp") is not None: skin[date] = d["skin_temp"]
    return sleep, rhr, hrv, resp, steps, azm, spo2, skin


def test_build_dataset_reproduces_golden_days(monkeypatch):
    """GATE anti-deriva: reconstruye los inputs del golden y compara el resultado
    de build_dataset() CONTRA EL JSON CONGELADO (no contra un recálculo con las
    constantes en vivo) — mutar STRAIN_V2_K/F_VIG/F_STEPS debe poner este test
    en rojo. Ver scripts/gen_golden.py (misma lógica de reconstrucción).

    Engine-v3-port: el golden se generó contra la ruta v2 (percentil rodante) —
    se fuerza RECOVERY_ANCHORED=False para correr contra ese motor explícitamente."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    data = load_golden()
    meta = data["meta"]
    frozen_days = data["days"]
    frozen_summary = data["summary"]
    exercises = data["exercises"]

    sleep, rhr, hrv, resp, steps, azm, spo2, skin = _reconstruct_inputs_from_golden(frozen_days)

    ds = build_dataset(
        sleep, rhr, hrv, resp, {}, steps, azm,
        spo2=spo2, skin=skin, exercises=exercises,
        age=meta["age"], sex=meta["sex"],
        sleep_target_min=480,
    )

    # Comparar día a día los campos calculados (excluir "updated", que es la
    # fecha de generación y no una fórmula).
    got_by_date = {d["date"]: d for d in ds["days"]}
    for frozen_day in frozen_days:
        date = frozen_day["date"]
        got_day = got_by_date[date]
        for field in ("recovery", "recovery_n", "sleep_perf", "strain", "trimp", "wellbeing"):
            assert got_day.get(field) == frozen_day.get(field), (
                f"{field} en {date}: got {got_day.get(field)}, "
                f"frozen (golden) {frozen_day.get(field)} — "
                f"¿se mutó una constante de strain v2 o de recovery rodante?"
            )

    for field in ("acwr", "acwr_zone", "sleep_target_min", "engine"):
        assert ds["summary"].get(field) == frozen_summary.get(field), (
            f"summary[{field}]: got {ds['summary'].get(field)}, "
            f"frozen {frozen_summary.get(field)}"
        )


# ---------------------------------------------------------------- test 2: build_dataset con inputs sintéticos

def _pct(a, p):
    """Replica exacta del pct() interno de build_dataset."""
    if not a:
        return 0
    a = sorted(a)
    k = (len(a) - 1) * p / 100
    f = int(k)
    return a[f] if f + 1 >= len(a) else a[f] + (a[f + 1] - a[f]) * (k - f)


def test_build_dataset_recovery_formula(monkeypatch):
    """Verifica la fórmula de recovery: 55% HRV + 25% RHR + 20% sueño.

    Ronda 5 (engine v2 — excepción versionada, ver ROADMAP-vitals-ronda5-engine-v2.md):
    - Recovery: con solo 5 lecturas de hrv/rhr en la serie completa, la ventana
      rodante trailing-90d de CUALQUIER día nunca alcanza el mínimo parcial de 10
      lecturas (_ROLLING_RECOVERY_MIN_PARTIAL) → recovery usa los defaults FIJOS
      (40,70)/(48,60) en vez del percentil 5-95 GLOBAL de esos 5 valores (que es
      lo que la v1 de este test verificaba). Es el comportamiento correcto y
      esperado de la escala rodante con historia corta (roadmap: "<10 → fallback
      a los defaults actuales").
    - Strain: reemplaza el proxy `vigorous*0.10 + steps/2500` por el híbrido TRIMP
      (sin exercises en este test, no hay trimp → cae al fallback de vigorous*F_VIG
      + NEAT de steps, comprimido asintóticamente vía 21*(1-exp(-L/K))).

    Engine-v3-port: fórmula de recovery v2 (percentil) — forzar RECOVERY_ANCHORED=False.
    """
    import app.scoring as scoring
    from app.scoring import build_dataset
    from app.scoring import STRAIN_V2_F_VIG, STRAIN_V2_F_STEPS, STRAIN_V2_K
    import math
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    # Inputs sintéticos con 5 días bien definidos
    hrv  = {"2024-01-01": 60.0, "2024-01-02": 55.0, "2024-01-03": 65.0,
             "2024-01-04": 50.0, "2024-01-05": 70.0}
    rhr  = {"2024-01-01": 50.0, "2024-01-02": 52.0, "2024-01-03": 48.0,
             "2024-01-04": 55.0, "2024-01-05": 46.0}
    sleep_d = {"2024-01-01": {"asleep": 400, "inbed": 450},
                "2024-01-02": {"asleep": 480, "inbed": 520},
                "2024-01-03": {"asleep": 360, "inbed": 400},
                "2024-01-04": {"asleep": 450, "inbed": 500},
                "2024-01-05": {"asleep": 500, "inbed": 540}}
    steps = {"2024-01-01": 8000, "2024-01-02": 10000, "2024-01-03": 6000,
              "2024-01-04": 12000, "2024-01-05": 9000}
    azm   = {"2024-01-01": 20, "2024-01-02": 30}

    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, steps, azm)

    # Ronda 5: con solo 5 lecturas totales (< _ROLLING_RECOVERY_MIN_PARTIAL=10),
    # TODOS los días caen en el fallback de defaults fijos, no en un percentil
    # calculado sobre la serie.
    hlo, hhi = 40, 70
    rlo, rhi = 48, 60
    NEED = 480

    def clamp(x, a=0, b=100): return max(a, min(b, x))

    # Verificar recovery para cada día que tenga hrv + asleep
    days_by_date = {d["date"]: d for d in ds["days"]}
    for date in sorted(hrv.keys()):
        if date not in sleep_d or date not in rhr:
            continue
        h = hrv[date]
        r = rhr[date]
        s = sleep_d[date]["asleep"]
        hrv_score = clamp((h - hlo) / (hhi - hlo) * 100)
        rhr_score = clamp((rhi - r) / (rhi - rlo) * 100)
        slp_score = clamp(s / NEED * 100)
        comps = [(hrv_score, 0.55), (rhr_score, 0.25), (slp_score, 0.20)]
        w = sum(wt for _, wt in comps)
        expected_rec = round(sum(v * wt for v, wt in comps) / w)
        got = days_by_date[date].get("recovery")
        assert got == expected_rec, (
            f"recovery {date}: got {got}, expected {expected_rec}"
        )

        # sleep_perf
        expected_sp = round(clamp(s / NEED * 100))
        got_sp = days_by_date[date].get("sleep_perf")
        assert got_sp == expected_sp, (
            f"sleep_perf {date}: got {got_sp}, expected {expected_sp}"
        )

    # Verificar strain v2 para un día con pasos (sin exercises → sin trimp →
    # fallback vigorous*F_VIG + NEAT de steps, comprimido asintóticamente).
    for date in ["2024-01-01", "2024-01-02"]:
        d = days_by_date[date]
        L = azm.get(date, 0) * STRAIN_V2_F_VIG + steps[date] / STRAIN_V2_F_STEPS
        expected_strain = round(21.0 * (1.0 - math.exp(-L / STRAIN_V2_K)), 1)
        assert d.get("strain") == expected_strain, (
            f"strain {date}: got {d.get('strain')}, expected {expected_strain}"
        )


def test_build_dataset_summary_fields():
    """Verifica que summary contiene los campos correctos con valores plausibles."""
    from app.scoring import build_dataset

    hrv  = {"2024-01-01": 60.0, "2024-01-02": 55.0}
    rhr  = {"2024-01-01": 50.0, "2024-01-02": 52.0}
    sleep_d = {"2024-01-01": {"asleep": 400}}
    steps = {}
    azm   = {}

    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, steps, azm)
    s = ds["summary"]
    assert "hrv_base" in s
    assert "rhr_base" in s
    assert "hrv_range" in s
    assert "rhr_range" in s
    assert "n_days" in s
    assert "updated" in s
    assert s["hrv_base"] == 57.5  # median of 55, 60
    assert s["rhr_base"] == 51.0  # median of 50, 52


def test_build_dataset_fase35_new_fields():
    """Fase 3.5 — distance_km/energy_kcal/active_hours: aditivos, tolerantes a None.
    - Sin pasar los kwargs (shape vieja) → los 3 campos existen como None, no rompe.
    - Pasando dicts parciales → solo se puebla la fecha presente; el resto None.
    - active_hours siempre None (diferido).
    No deben alterar recovery/strain/scores existentes."""
    from app.scoring import build_dataset

    hrv  = {"2024-01-01": 60.0, "2024-01-02": 55.0}
    rhr  = {"2024-01-01": 50.0, "2024-01-02": 52.0}
    sleep_d = {"2024-01-01": {"asleep": 400}, "2024-01-02": {"asleep": 410}}

    # Shape vieja (sin kwargs nuevos): campos presentes como None
    ds_old = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
    for d in ds_old["days"]:
        assert d["distance_km"] is None
        assert d["energy_kcal"] is None
        assert d["active_hours"] is None

    # Shape nueva con datos parciales (solo 01-01)
    ds_new = build_dataset(
        sleep_d, rhr, hrv, {}, {}, {}, {},
        distance_km={"2024-01-01": 5.3},
        energy_kcal={"2024-01-01": 2100},
        active_hours={},  # diferido
    )
    by_date = {d["date"]: d for d in ds_new["days"]}
    assert by_date["2024-01-01"]["distance_km"] == 5.3
    assert by_date["2024-01-01"]["energy_kcal"] == 2100
    assert by_date["2024-01-01"]["active_hours"] is None  # diferido siempre None
    # La fecha sin datos en los dicts → None (no se filtra de otra fecha)
    assert by_date["2024-01-02"]["distance_km"] is None
    assert by_date["2024-01-02"]["energy_kcal"] is None

    # Los scores existentes NO cambian al agregar los campos nuevos
    rec_old = {d["date"]: d.get("recovery") for d in ds_old["days"]}
    rec_new = {d["date"]: d.get("recovery") for d in ds_new["days"]}
    assert rec_old == rec_new


def test_build_dataset_no_false_recovery():
    """Un día con solo RHR (sin HRV ni sueño) no debe tener recovery."""
    from app.scoring import build_dataset

    rhr = {"2024-01-01": 50.0}
    ds = build_dataset({}, rhr, {}, {}, {}, {}, {})
    assert len(ds["days"]) == 1
    # Sin HRV ni asleep, no debe calcular recovery
    assert "recovery" not in ds["days"][0]


# ---------------------------------------------------------------- test 4: higiene PARTE A

def test_solo_hrv_surgical(monkeypatch):
    """PARTE A — regla QUIRÚRGICA en solo-HRV: se suprime SOLO si clampea a extremo (0/100);
    un solo-HRV razonable CONSERVA su recovery (HRV es la señal dominante, peso 0.55).

    Engine-v3-port: la regla quirúrgica es del motor v2 (percentil) — forzar
    RECOVERY_ANCHORED=False. La lógica v3 no tiene compuerta de supresión por clamp."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    # HRV con spread para que un valor medio no clampee. 40=mínimo→0 (suprimir);
    # 55=medio→recovery presente. 45 y 70 son anclas con rhr+sueño (fijan percentiles).
    hrv = {"2024-01-01": 40.0, "2024-01-02": 55.0, "2024-01-03": 45.0, "2024-01-04": 70.0}
    rhr = {"2024-01-03": 50.0, "2024-01-04": 50.0}
    sleep_d = {"2024-01-03": {"asleep": 420, "inbed": 480},
               "2024-01-04": {"asleep": 420, "inbed": 480}}

    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
    by_date = {d["date"]: d for d in ds["days"]}

    # solo-HRV en el mínimo (clampea a 0) → recovery SUPRIMIDO (era el caso espurio)
    d1 = by_date["2024-01-01"]
    assert d1.get("hrv") == 40.0
    assert "recovery" not in d1, (
        f"solo-HRV que clampea a 0 debe suprimirse, got {d1.get('recovery')}"
    )

    # solo-HRV medio → recovery PRESENTE (la regla quirúrgica lo conserva)
    d2 = by_date["2024-01-02"]
    assert "recovery" in d2, "solo-HRV razonable debe CONSERVAR recovery (no perder el dato)"
    assert 0 < d2["recovery"] < 100, f"recovery debe ser razonable, got {d2.get('recovery')}"

    # Día con HRV+RHR+sueño → recovery normal
    d4 = by_date["2024-01-04"]
    assert "recovery" in d4 and d4["recovery"] > 0


def test_nap_fields_excluded(monkeypatch):
    """PARTE A — día de siesta → campos de sueño ausentes; días legítimos intactos.

    Engine-v3-port: el assert final depende de la compuerta "2 comps -> recovery
    presente" del motor v2 — forzar RECOVERY_ANCHORED=False."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    # Siesta: onset 09:00 (bed_min = 540 > 240) con 134 min asleep
    # Noche legítima: onset 23:00 (bed_min = -60) con 420 min asleep
    # Short night (asleep=80 < 120) con onset normal → también excluida
    sleep_d = {
        "2024-01-01": {"asleep": 134, "inbed": 150, "bed_min": 540,  "bedtime": "09:00",
                        "deep": 10, "rem": 20, "light": 104, "eff": 89.3, "waketime": "11:14"},   # siesta
        "2024-01-02": {"asleep": 420, "inbed": 460, "bed_min": -60,  "bedtime": "23:00",
                        "deep": 80, "rem": 100, "light": 240, "eff": 91.3, "waketime": "06:00"},  # noche legít.
        "2024-01-03": {"asleep": 80,  "inbed": 100, "bed_min": -30,  "bedtime": "23:30",
                        "deep": None, "rem": None, "light": 80, "eff": 80.0, "waketime": "01:00"}, # short (<120)
        "2024-01-04": {"asleep": 480, "inbed": 520, "bed_min": -390, "bedtime": "17:30",  # evening onset <-300
                        "deep": 90, "rem": 110, "light": 280, "eff": 92.3, "waketime": "07:30"},  # siesta-tarde
    }
    hrv  = {"2024-01-01": 60.0, "2024-01-02": 60.0, "2024-01-03": 60.0, "2024-01-04": 60.0}
    rhr  = {"2024-01-01": 50.0, "2024-01-02": 50.0, "2024-01-03": 50.0, "2024-01-04": 50.0}
    steps = {}
    azm = {}

    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, steps, azm)
    by_date = {d["date"]: d for d in ds["days"]}

    # 2024-01-01: siesta (bed_min=540 > 240) → sin campos de sueño
    d1 = by_date["2024-01-01"]
    for f in ("asleep", "inbed", "deep", "rem", "light", "eff", "bedtime", "waketime", "bed_min", "sleep_perf"):
        assert f not in d1, f"Campo {f} debe estar ausente en siesta (got {d1.get(f)})"
    # HRV y RHR siguen presentes
    assert d1.get("hrv") == 60.0
    assert d1.get("rhr") == 50.0
    # Solo 1 comp HRV + 1 comp RHR = 2 comps → recovery presente
    assert "recovery" in d1

    # 2024-01-02: noche legítima → campos de sueño presentes
    d2 = by_date["2024-01-02"]
    assert d2.get("asleep") == 420, "asleep debe estar en noche legítima"
    assert d2.get("bed_min") == -60
    assert "recovery" in d2

    # 2024-01-03: asleep=80 < 120 → sin campos de sueño
    d3 = by_date["2024-01-03"]
    assert "asleep" not in d3, f"asleep debe estar ausente (short night), got {d3.get('asleep')}"
    assert "bed_min" not in d3

    # 2024-01-04: onset 17:30 (bed_min=-390 < -300) → sin campos de sueño
    d4 = by_date["2024-01-04"]
    assert "asleep" not in d4, f"asleep debe estar ausente (evening onset), got {d4.get('asleep')}"


def test_normal_day_formula_unchanged(monkeypatch):
    """PARTE A — regresión: día normal (hrv+rhr+asleep, noche legítima).

    Ronda 5 (engine v2 — excepción versionada): con 1 sola lectura de hrv/rhr en
    la serie (< _ROLLING_RECOVERY_MIN_PARTIAL=10), la ventana rodante cae en el
    fallback de defaults FIJOS (40,70)/(48,60) — YA NO en el caso especial
    hlo=valor/hhi=valor+1 que usaba el percentil GLOBAL de v1 con un solo dato.
    Strain usa el híbrido TRIMP v2 (sin exercises → fallback vigorous*F_VIG + NEAT).

    Engine-v3-port: fórmula de recovery v2 (percentil) — forzar RECOVERY_ANCHORED=False.
    """
    import app.scoring as scoring
    from app.scoring import build_dataset
    from app.scoring import STRAIN_V2_F_VIG, STRAIN_V2_F_STEPS, STRAIN_V2_K
    import math
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    # Día normal: onset 23:30, asleep 450 min (7.5h), hrv=60, rhr=50
    sleep_d = {"2024-01-01": {"asleep": 450, "inbed": 490, "bed_min": -30, "bedtime": "23:30",
                               "deep": 90, "rem": 105, "eff": 91.8}}
    hrv = {"2024-01-01": 60.0}
    rhr = {"2024-01-01": 50.0}
    steps = {"2024-01-01": 8000}
    azm = {"2024-01-01": 20}

    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, steps, azm)
    day = ds["days"][0]

    # Ronda 5: 1 lectura < 10 → fallback fijo (no percentil global de un solo valor).
    hlo, hhi = 40, 70
    rlo, rhi = 48, 60
    NEED = 480
    def clamp(x): return max(0, min(100, x))
    hrv_score = clamp((60.0 - hlo) / (hhi - hlo) * 100)   # (60-40)/(70-40)*100 = 66.67
    rhr_score  = clamp((rhi - 50.0) / (rhi - rlo) * 100)  # (60-50)/(60-48)*100 = 83.33
    slp_score  = clamp(450 / NEED * 100)                    # 450/480*100 = 93.75
    comps = [(hrv_score, 0.55), (rhr_score, 0.25), (slp_score, 0.20)]
    w = sum(wt for _, wt in comps)
    expected_rec = round(sum(v*wt for v, wt in comps) / w)
    expected_sp  = round(clamp(450 / NEED * 100))
    L = 20 * STRAIN_V2_F_VIG + 8000 / STRAIN_V2_F_STEPS
    expected_str = round(21.0 * (1.0 - math.exp(-L / STRAIN_V2_K)), 1)

    assert day.get("recovery") == expected_rec, f"recovery: {day.get('recovery')} vs {expected_rec}"
    assert day.get("sleep_perf") == expected_sp, f"sleep_perf: {day.get('sleep_perf')} vs {expected_sp}"
    assert day.get("strain") == expected_str, f"strain: {day.get('strain')} vs {expected_str}"
    # Campos de sueño intactos
    assert day.get("asleep") == 450
    assert day.get("deep") == 90


# ---------------------------------------------------------------- test 5: compute_body_age sintético

def test_bodyage_synthetic_male():
    """Verifica la fórmula VO2máx y body_age con valores calculados a mano (hombre, 40 años)."""
    from app.bodyage import compute_body_age

    # 14 días idénticos de rhr=52, hrv=55, asleep=420 (7h)
    days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52, "hrv": 55.0, "asleep": 420}
             for i in range(14)]
    # ejercicios: 5 días distintos, avg_hr=110, dur_min=45
    exercises = [{"date": f"2024-01-{i+1:02d}", "avg_hr": 110, "dur_min": 45}
                  for i in range(5)]

    result = compute_body_age(days, exercises, age=40, waist=82, sex="M")

    # Calcular a mano:
    rhr = 52.0; hrv = 55.0; sleep_h = 420/60  # 7.0h
    freq = 5 / 4.0  # 5 días en 4 semanas → fs=2
    ahr = 110; adur = 45
    fs = 2   # freq=1.25 → ≥1 → 2
    iss = 4  # ahr=110 → ≥105 → 4
    ds = 4   # adur=45 → ≥30 → 4
    PA = fs + iss + ds  # = 10
    vo2 = 100.27 - 0.296*40 + 0.226*PA - 0.369*82 - 0.155*52
    vo2 = round(vo2, 1)
    intercept = 55.1
    fitness_age = max(20, min(80, (intercept - vo2) / 0.363))
    # penalización HRV: exp_hrv = 50 - 0.5*(40-20) = 40; hrv=55 > 40 → no penaliza
    pen = 0.0
    # penalización sueño: sleep_h=7 → no hay déficit (7 < 7 es False)
    body_age = max(18, min(90, fitness_age + pen))

    assert result["pa_index"] == PA, f"PA: {result['pa_index']} vs {PA}"
    assert result["vo2max"] == vo2, f"vo2max: {result['vo2max']} vs {vo2}"
    assert result["fitness_age"] == round(fitness_age), f"fitness_age: {result['fitness_age']} vs {round(fitness_age)}"
    assert result["body_age"] == round(body_age), f"body_age: {result['body_age']} vs {round(body_age)}"
    assert result["penalty"] == round(pen, 1)


# ── F2 roadmap P0: hipnograma — segments NO alteran scoring (riesgo #1) ──────

def test_segments_travel_to_day_rows_and_scores_are_byte_identical():
    """build_dataset con un sleep rec que trae `segments`:
    1. days[n]["segments"] llega presente e intacto (via o.update del rec).
    2. TODOS los scores (recovery, sleep_perf, strain) y el summary completo
       son BYTE-IDÉNTICOS a los del mismo dataset sin segments — los segments
       son un campo de display, jamás una entrada del motor (criterio de
       aceptación 13 + fuera-de-alcance explícito del roadmap)."""
    from app.scoring import build_dataset

    def _inputs(with_segments):
        sleep = {}
        for i in range(1, 15):
            date = f"2026-06-{i:02d}"
            rec = {
                "asleep": 400 + i, "inbed": 440 + i, "deep": 70, "rem": 90,
                "light": 240, "eff": 91.0, "bedtime": "23:30",
                "waketime": "06:40", "bed_min": -30,
            }
            if with_segments:
                rec["segments"] = [
                    {"s": 0, "e": 70, "st": "deep"},
                    {"s": 70, "e": 160, "st": "rem"},
                    {"s": 160, "e": 400 + i, "st": "light"},
                ]
            sleep[date] = rec
        rhr = {f"2026-06-{i:02d}": 52 + (i % 3) for i in range(1, 15)}
        hrv = {f"2026-06-{i:02d}": 55 + (i % 5) for i in range(1, 15)}
        steps = {f"2026-06-{i:02d}": 8000 + i * 100 for i in range(1, 15)}
        azm = {f"2026-06-{i:02d}": 20 for i in range(1, 15)}
        return sleep, rhr, hrv, steps, azm

    sleep_a, rhr, hrv, steps, azm = _inputs(with_segments=True)
    sleep_b, _, _, _, _ = _inputs(with_segments=False)

    ds_with = build_dataset(sleep_a, rhr, hrv, {}, {}, steps, azm)
    ds_without = build_dataset(sleep_b, rhr, hrv, {}, {}, steps, azm)

    # 1. segments presentes e intactos en el day row correspondiente.
    day_with = ds_with["days"][-1]
    assert day_with["segments"][-1] == {"s": 160, "e": 414, "st": "light"}

    # 2. Al quitar segments de los day rows, los datasets deben ser
    #    BYTE-IDÉNTICOS (JSON serializado): mismos scores, mismos baselines.
    days_stripped = []
    for d in ds_with["days"]:
        d2 = {k: v for k, v in d.items() if k != "segments"}
        days_stripped.append(d2)
    assert json.dumps(days_stripped, sort_keys=True) == \
        json.dumps(ds_without["days"], sort_keys=True)
    assert json.dumps(ds_with["summary"], sort_keys=True) == \
        json.dumps(ds_without["summary"], sort_keys=True)
