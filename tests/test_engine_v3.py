"""
test_engine_v3.py — Roadmap engine-v3-port: motor v3 de recovery (z-score vs
base personal + logística anclada) portado desde PROD, con el arreglo del
arranque en frío.

Cubre los criterios de aceptación que no tienen cobertura previa en el repo
(los 8 tests v2 preexistentes quedan parametrizados por el flag en sus propios
archivos — test_regression.py / test_ronda5_engine_v2.py / test_tier1_analytics.py):

- Criterio 2: engine.version / recovery_scale reflejan el flag RECOVERY_ANCHORED.
- Criterio 3: arranque en frío arreglado — recovery_n==3 desde el día 1, sin
  salto artificial >=15 puntos entre días consecutivos (usuario nuevo, 12 días,
  HRV+RHR+sueño completos todos los días).
- Criterio 5: cero lecturas de HRV/RHR (solo sueño) NO fabrica componentes —
  recovery_n==1.
- Criterio 7(b): el ancla — un día EN la base (z=0 en todos los componentes)
  da recovery ~74 +/-2, y la logística nunca sale de [0,100].
- Criterio 7(c): guardia de frescura de HRV en app/merge.py — una fuente con
  más historia pero HRV vieja (>3 días de rezago vs la fuente más fresca)
  pierde el ranking histórico; con fechas no parseables degrada con seguridad
  al ranking (sin crashear).

Cada test fuerza explícitamente RECOVERY_ANCHORED (patrón de monkeypatch ya
usado en el repo, ver tests/test_export.py / tests/test_endpoints.py con
_PROFILE_FILE) — nunca depende del default del módulo.
"""
from __future__ import annotations

import datetime
import math

import pytest


# ── Criterio 2: engine.version / recovery_scale por flag ────────────────────

def test_engine_version_and_scale_v3(monkeypatch):
    """RECOVERY_ANCHORED=True -> engine.version==3, recovery_scale==baseline-anchored-v3."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    hrv = {"2024-01-01": 55.0}
    rhr = {"2024-01-01": 52.0}
    sleep_d = {"2024-01-01": {"asleep": 420, "inbed": 440}}
    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})

    assert ds["summary"]["engine"]["version"] == 3
    assert ds["summary"]["engine"]["recovery_scale"] == "baseline-anchored-v3"


def test_engine_version_and_scale_v2(monkeypatch):
    """RECOVERY_ANCHORED=False -> engine.version==2, recovery_scale==rolling-90d
    (revert de 1 línea, ruta v2 conservada)."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)

    hrv = {"2024-01-01": 55.0}
    rhr = {"2024-01-01": 52.0}
    sleep_d = {"2024-01-01": {"asleep": 420, "inbed": 440}}
    ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})

    assert ds["summary"]["engine"]["version"] == 2
    assert ds["summary"]["engine"]["recovery_scale"] == "rolling-90d"


# ── Criterio 3: arranque en frío arreglado ───────────────────────────────────

def test_cold_start_recovery_n_3_from_day_1_no_jump(monkeypatch):
    """Usuario nuevo, 12 días, HRV+RHR+sueño COMPLETOS todos los días: recovery_n
    debe ser 3 desde el día 1 (antes del arreglo: n=1 los primeros 4 días, sd=None
    en _rolling_baseline_ranges hacía que el componente HRV/RHR se descartara en
    silencio) y no debe haber salto artificial >=15 puntos entre días consecutivos
    (antes del arreglo: salto de 23 puntos del día 4 al 5, ver ROADMAP.md)."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    base = datetime.date(2026, 1, 1)
    hrv, rhr, sleep = {}, {}, {}
    for i in range(12):
        d = (base + datetime.timedelta(days=i)).isoformat()
        hrv[d] = 55.0 + i
        rhr[d] = 52.0
        sleep[d] = {"asleep": 420, "inbed": 440, "bed_min": 0, "deep": 60,
                     "rem": 80, "light": 280, "awake": 20, "eff": 95}

    ds = build_dataset(hrv=hrv, rhr=rhr, sleep=sleep, resp={}, vo2={}, steps={}, azm={})
    days = ds["days"]

    assert len(days) == 12
    for day in days:
        assert day.get("recovery_n") == 3, (
            f"{day['date']}: expected recovery_n=3 desde el dia 1, got {day.get('recovery_n')}"
        )

    recoveries = [d["recovery"] for d in days]
    max_jump = max(abs(b - a) for a, b in zip(recoveries, recoveries[1:]))
    assert max_jump < 15, (
        f"salto artificial entre dias consecutivos >= 15 puntos: {recoveries} (max_jump={max_jump})"
    )


def test_cold_start_dispersed_series_does_not_saturate(monkeypatch):
    """Arranque en frío con una serie REALISTA: dispersa y con la primera lectura
    atípica (patrón habitual — el primer dato de un dispositivo suele ser basura).

    Es el caso que el test de arriba NO cubre: con una serie suave (55, 56, 57...)
    la sd apenas importa y cualquier implementación parece correcta. Con dispersión
    real, usar el PISO de sd (3.0 ms) en vez de la sd verdadera (~34 ms) subestima
    el ancho ~11x -> z≈-11 -> recovery satura en 0/1. Cazado en datos reales:
    daba 74,0,0,19,2 donde debía dar 74,55,59,69,63."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    # Primera lectura atípica (119.6) seguida de una base ~50: dispersión de 76 ms.
    hrv_vals = [119.6, 52.3, 43.5, 59.9, 47.0, 53.7, 47.3, 52.6, 55.1, 49.8]
    base = datetime.date(2026, 1, 1)
    hrv, rhr, sleep = {}, {}, {}
    for i, v in enumerate(hrv_vals):
        d = (base + datetime.timedelta(days=i)).isoformat()
        hrv[d] = v
        rhr[d] = 52.0 + (i % 3)
        sleep[d] = {"asleep": 420, "inbed": 440, "bed_min": 0, "deep": 60,
                    "rem": 80, "light": 280, "awake": 20, "eff": 95}

    ds = build_dataset(hrv=hrv, rhr=rhr, sleep=sleep, resp={}, vo2={}, steps={}, azm={})
    recoveries = [d["recovery"] for d in ds["days"]]

    # Ningún día del arranque en frío debe saturar: la logística solo debe llegar a
    # los extremos con desviaciones fisiológicas reales, nunca por sd subestimada.
    for day in ds["days"]:
        assert 5 <= day["recovery"] <= 95, (
            f"{day['date']}: recovery={day['recovery']} satura — la sd del arranque "
            f"en frío está subestimada (¿se está usando el piso en vez de la sd real?). "
            f"serie: {recoveries}"
        )

    max_jump = max(abs(b - a) for a, b in zip(recoveries, recoveries[1:]))
    assert max_jump < 25, (
        f"salto artificial >=25 puntos en arranque en frío con serie dispersa: "
        f"{recoveries} (max_jump={max_jump})"
    )


def test_cold_start_partial_readings_no_typeerror(monkeypatch):
    """1, 2, 4 y 5 lecturas (tramo sd=None y tramo sd real) no deben producir
    TypeError en max(sd, FLOOR) -- riesgo #5 del roadmap."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    for n in (1, 2, 4, 5):
        base = datetime.date(2026, 1, 1)
        hrv, rhr, sleep = {}, {}, {}
        for i in range(n):
            d = (base + datetime.timedelta(days=i)).isoformat()
            hrv[d] = 55.0 + i
            rhr[d] = 52.0
            sleep[d] = {"asleep": 420, "inbed": 440, "bed_min": 0, "deep": 60,
                         "rem": 80, "light": 280, "awake": 20, "eff": 95}
        ds = build_dataset(hrv=hrv, rhr=rhr, sleep=sleep, resp={}, vo2={}, steps={}, azm={})
        last = ds["days"][-1]
        assert last.get("recovery_n") == 3, f"n={n}: expected recovery_n=3, got {last.get('recovery_n')}"
        assert last.get("recovery") is not None


# ── Criterio 5: cero lecturas no fabrica dato ───────────────────────────────

def test_zero_hrv_rhr_readings_no_fabricated_component(monkeypatch):
    """Un día sin HRV y sin RHR (solo sueño) NO debe fabricar componentes:
    recovery_n == 1 (ausencia de dato != base). Con 0 lecturas, _rolling_baseline_ranges
    devuelve (None, None) y el guard `hb is not None` sigue omitiendo el componente."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    d = "2026-01-01"
    sleep = {d: {"asleep": 420, "inbed": 440, "bed_min": 0, "deep": 60,
                  "rem": 80, "light": 280, "awake": 20, "eff": 95}}
    ds = build_dataset(hrv={}, rhr={}, sleep=sleep, resp={}, vo2={}, steps={}, azm={})

    day = ds["days"][0]
    assert "hrv" not in day
    assert "rhr" not in day
    assert day.get("recovery_n") == 1, (
        f"solo sueno (0 lecturas hrv/rhr): expected recovery_n=1, got {day.get('recovery_n')}"
    )
    assert "recovery" in day


# ── Criterio 7(b): el ancla — z=0 en todos los componentes ──────────────────

def test_anchor_day_at_base_gives_recovery_around_74(monkeypatch):
    """Un día EN la base (hrv==media rodante, rhr==media rodante, asleep==NEED)
    da z=0 en los 3 componentes -> W=0 -> recovery = 100/(1+exp(-RECOVERY_V3_A))
    ~= 74 (+/-2), la filosofía declarada del v3 ("en tu base = verde")."""
    import app.scoring as scoring
    from app.scoring import build_dataset, RECOVERY_V3_A
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    base = datetime.date(2026, 1, 1)
    hrv, rhr, sleep = {}, {}, {}
    for i in range(10):
        d = (base + datetime.timedelta(days=i)).isoformat()
        hrv[d] = 55.0     # constante -> media==55, en el ultimo dia hb==55 -> z_hrv=0
        rhr[d] = 52.0     # constante -> rb==52 -> z_rhr=0
        sleep[d] = {"asleep": 480, "inbed": 500, "bed_min": 0, "deep": 60,  # NEED default=480
                     "rem": 80, "light": 320, "awake": 20, "eff": 95}

    ds = build_dataset(hrv=hrv, rhr=rhr, sleep=sleep, resp={}, vo2={}, steps={}, azm={})
    last = ds["days"][-1]  # dia 10: ventana ya tiene >=5 lecturas, hb/rb estables

    expected = round(100.0 / (1.0 + math.exp(-RECOVERY_V3_A)))
    assert last["recovery"] == expected, (
        f"dia en la base: got {last['recovery']}, expected {expected} (W=0)"
    )
    assert 72 <= last["recovery"] <= 76, f"recovery en la base fuera de ~74+/-2: {last['recovery']}"
    assert last["recovery_n"] == 3


def test_logistic_never_leaves_0_100_range(monkeypatch):
    """La curva logística satura suave los extremos -- ningún recovery calculado
    puede salir de [0,100], ni con z muy alto (HRV muy por encima de base) ni muy
    bajo (HRV muy por debajo, RHR muy por encima, sueño muy corto)."""
    import app.scoring as scoring
    from app.scoring import build_dataset
    monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", True)

    base = datetime.date(2026, 1, 1)
    # Construir 10 dias de base estable, y un dia 11 con valores extremos.
    hrv, rhr, sleep = {}, {}, {}
    for i in range(10):
        d = (base + datetime.timedelta(days=i)).isoformat()
        hrv[d] = 55.0
        rhr[d] = 52.0
        sleep[d] = {"asleep": 480, "inbed": 500, "bed_min": 0, "deep": 60,
                     "rem": 80, "light": 320, "awake": 20, "eff": 95}

    # Dia 11: HRV muy alto (recovery deberia saturar cerca de 100, nunca pasarlo)
    d_high = (base + datetime.timedelta(days=10)).isoformat()
    hrv_high = dict(hrv); hrv_high[d_high] = 500.0
    rhr_high = dict(rhr); rhr_high[d_high] = 52.0
    sleep_high = dict(sleep); sleep_high[d_high] = sleep[list(sleep)[0]]
    ds_high = build_dataset(hrv=hrv_high, rhr=rhr_high, sleep=sleep_high, resp={}, vo2={}, steps={}, azm={})
    rec_high = ds_high["days"][-1]["recovery"]
    assert 0 <= rec_high <= 100, f"recovery fuera de rango con z alto: {rec_high}"

    # Dia 11: HRV muy bajo + RHR muy alto + sueño muy corto (recovery deberia
    # saturar cerca de 0, nunca ser negativo)
    hrv_low = dict(hrv); hrv_low[d_high] = 1.0
    rhr_low = dict(rhr); rhr_low[d_high] = 200.0
    sleep_low = dict(sleep)
    sleep_low[d_high] = {"asleep": 30, "inbed": 40, "bed_min": 0, "deep": 5,
                          "rem": 5, "light": 20, "awake": 5, "eff": 60}
    ds_low = build_dataset(hrv=hrv_low, rhr=rhr_low, sleep=sleep_low, resp={}, vo2={}, steps={}, azm={})
    rec_low = ds_low["days"][-1]["recovery"]
    assert 0 <= rec_low <= 100, f"recovery fuera de rango con z bajo: {rec_low}"
    assert rec_low < rec_high


# ── Criterio 7(c): guardia de frescura de HRV en merge.py ───────────────────

def _empty_source() -> dict:
    return {
        "sleep": {}, "rhr": {}, "hrv": {}, "resp": {}, "vo2": {}, "steps": {},
        "azm": {}, "spo2": {}, "skin": {}, "exercises": [], "distance_km": {},
        "energy_kcal": {}, "active_hours": {},
    }


def test_freshness_guard_discards_stale_source_with_more_history():
    """Una fuente con MÁS historia pero HRV vieja (>3 dias de rezago vs la fuente
    mas fresca) debe PERDER el ranking historico aunque tenga mas dias -- la
    guardia de frescura la descarta ANTES de comparar n_days. Sin la guardia,
    la fuente vieja (30 dias) le ganaria a la fresca (5 dias) por completitud."""
    from app.merge import merge_sources, last_merge_info

    base = datetime.date(2026, 1, 1)
    # Fuente A ("oura"): 30 dias de HRV, pero el ultimo dato es de hace 10 dias.
    stale_dates = [(base + datetime.timedelta(days=i)).isoformat() for i in range(30)]
    source_stale = _empty_source()
    source_stale["hrv"] = {d: 50.0 for d in stale_dates}

    # Fuente B ("whoop"): solo 5 dias, pero terminan 10 dias despues (fresca).
    fresh_start = base + datetime.timedelta(days=39)  # 10 dias despues del ultimo de A
    fresh_dates = [(fresh_start + datetime.timedelta(days=i)).isoformat() for i in range(5)]
    source_fresh = _empty_source()
    source_fresh["hrv"] = {d: 60.0 for d in fresh_dates}

    fetched = {"oura": source_stale, "whoop": source_fresh}
    result = merge_sources(fetched)
    info = last_merge_info()

    assert info["hrv_source"] == "whoop", (
        f"la fuente fresca (menos historia) debe ganar sobre la vieja (mas historia): "
        f"got hrv_source={info['hrv_source']}"
    )
    assert result["hrv"] == source_fresh["hrv"]


def test_freshness_guard_degrades_safely_on_unparseable_dates():
    """Si alguna fecha de HRV no es parseable (ISO invalido), la guardia de
    frescura se DESACTIVA (degrada con seguridad al ranking historico normal)
    en vez de crashear -- riesgo explicito del roadmap (arquitectura: 'degrada
    con seguridad al ranking si hay fechas no parseables')."""
    from app.merge import merge_sources, last_merge_info

    base = datetime.date(2026, 1, 1)
    dates = [(base + datetime.timedelta(days=i)).isoformat() for i in range(5)]
    source_a = _empty_source()
    source_a["hrv"] = {d: 55.0 for d in dates}
    source_a["hrv"]["not-a-real-date"] = 999.0  # fecha no parseable

    source_b = _empty_source()
    source_b["hrv"] = {dates[0]: 60.0}  # 1 solo dia, mucho menos historia

    fetched = {"oura": source_a, "whoop": source_b}

    # No debe lanzar excepción.
    result = merge_sources(fetched)
    info = last_merge_info()

    # Degradado a ranking normal (sin guardia de frescura): gana quien tenga
    # mas dias con dato -- source_a (6 entradas, incluida la no-parseable que
    # SI cuenta para n_days aunque no aporte a latest_for_source/latest_overall).
    assert info["hrv_source"] == "oura"
    assert result["hrv"] == source_a["hrv"]
