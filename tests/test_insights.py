"""
test_insights.py — Tests del motor de insights con datasets sintéticos.

Cada regla tiene:
  - Un dataset que la DISPARA (verifica severidad y factor esperado).
  - El dataset "sano" al final verifica que NO se dispara ninguna alerta/watch
    (anti-alarmismo).
  - Días con campos None no rompen ni disparan.
"""
from __future__ import annotations

import pytest
from app.insights import evaluate, _window, _mean, _pstdev


# ── helpers de construcción ────────────────────────────────────────────────────

def make_day(date, **kwargs):
    """Crea un día con la fecha dada + campos opcionales."""
    return {"date": date, **kwargs}


def make_summary(hrv_base=57.0, rhr_base=50.0):
    return {"hrv_base": hrv_base, "rhr_base": rhr_base}


def make_dataset(days, summary=None, exercises=None):
    return {"days": days, "summary": summary or make_summary(), "exercises": exercises or []}


def date_seq(n):
    """Genera fechas 2024-01-01..2024-01-N."""
    return [f"2024-01-{i+1:02d}" for i in range(n)]


# ── helpers ────────────────────────────────────────────────────────────────────

def test_window_helper_filters_none():
    days = [{"date": "d1", "hrv": 55.0}, {"date": "d2"}, {"date": "d3", "hrv": 60.0}]
    vals = _window(days, 5, "hrv")
    assert vals == [55.0, 60.0]


def test_mean_empty():
    assert _mean([]) is None


def test_pstdev_single():
    assert _pstdev([42.0]) is None


# ── Regla 1: illness_early_warning (alert) ─────────────────────────────────────

def test_illness_alert_temp_plus_signals():
    """temp elevada + rhr alto + hrv baja → alert."""
    dates = date_seq(15)
    # 14 días con temp normal
    days = [make_day(d, skin_temp=35.0, rhr=50.0, hrv=57.0, asleep=440) for d in dates[:14]]
    # día 15: temp alta, rhr alta, hrv baja
    days.append(make_day(dates[14], skin_temp=36.0, rhr=58.0, hrv=45.0, asleep=380))

    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" in ids
    insight = next(r for r in results if r["id"] == "illness_early_warning")
    assert insight["severity"] == "alert"
    # Debe incluir factores de temp y rhr/hrv
    factors_text = " ".join(insight["factors"])
    assert "Temp" in factors_text or "FC" in factors_text or "HRV" in factors_text


def test_illness_watch_two_signals_no_temp():
    """rhr alta + hrv baja sin temp → watch (≥2 señales sin temp)."""
    dates = date_seq(5)
    days = [make_day(d, rhr=50.0, hrv=57.0, asleep=440) for d in dates[:4]]
    days.append(make_day(dates[4], rhr=58.0, hrv=44.0, asleep=380))

    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" in ids
    insight = next(r for r in results if r["id"] == "illness_early_warning")
    assert insight["severity"] == "watch"


def test_illness_alert_temp_delta_masked_by_high_sd_real_case():
    """Escenario de referencia (F1): temp variable
    (sd alta) enmascara el z-score, pero el delta absoluto (mismo criterio que
    coach_chat._build_context) sí lo caza. skin_temp_hoy=0.24, media previa
    ≈ -0.93, sd≈1.25 → z≈0.94 (NO dispara por z solo, <1.5) pero delta≈+1.17>0.6
    → SÍ debe marcar la señal de temp elevada. Con ≥1 señal más (rhr alto)
    → insight illness_early_warning con severity alert.

    Nota: se usan 13 días previos (no 14) para que _window(days,14,...) NO
    descarte ninguno (con 14 días previos + hoy = 15 total, _window solo toma
    los últimos 14 y el primero se cae del cálculo de media/sd — con 13+hoy=14
    entran los 13 completos, que es lo que este test quiere fijar a mano)."""
    dates = date_seq(14)
    # 13 días previos de skin_temp con varianza alta (sd≈1.25, media=-0.93),
    # construidos para reproducir el escenario (sd≈1.18) sin depender de datos
    # externos al repo.
    temps_prev = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                  0.38, 0.42, -2.01, -2.58, 0.28]
    assert abs((sum(temps_prev) / len(temps_prev)) - (-0.93)) < 0.01  # media ≈ -0.93 (sanity)
    days = [make_day(d, skin_temp=t, rhr=50.0, hrv=57.0, asleep=440)
            for d, t in zip(dates[:13], temps_prev)]
    # día 14: temp=0.24 (z≈0.94, NO dispara solo) + rhr alto (2ª señal)
    days.append(make_day(dates[13], skin_temp=0.24, rhr=58.0, hrv=57.0, asleep=440))

    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" in ids, (
        "El delta (+1.17, umbral 0.6) debía disparar la señal de temp aunque "
        "el z-score (≈0.94) quede bajo 1.5 por la sd alta"
    )
    insight = next(r for r in results if r["id"] == "illness_early_warning")
    assert insight["severity"] == "alert"


def test_illness_temp_delta_below_threshold_does_not_fire_alone():
    """Delta pequeño (≤0.6) y z bajo → NO eleva la señal de temp (anti-alarmismo:
    el OR no debe volverse un umbral más permisivo de lo calibrado)."""
    dates = date_seq(14)
    # Varianza alta similar al escenario (13 días previos, ver test anterior
    # para por qué 13 y no 14), pero delta del día final se queda en 0.5
    # (bajo el umbral 0.6) y z también bajo 1.5.
    temps_prev = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                  0.38, 0.42, -2.01, -2.58, 0.28]
    base_mean = sum(temps_prev) / len(temps_prev)
    days = [make_day(d, skin_temp=t) for d, t in zip(dates[:13], temps_prev)]
    days.append(make_day(dates[13], skin_temp=base_mean + 0.5))  # delta=0.5 < 0.6

    summary = make_summary()
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" not in ids


def test_illness_temp_none_safe_short_window_no_trigger():
    """Ventana corta (<3 valores de skin_temp) → ausencia de dato NUNCA dispara.
    None-safe explícito del roadmap: ausencia ≠ enfermedad."""
    dates = date_seq(3)
    days = [make_day(dates[0], skin_temp=None, rhr=50.0, hrv=57.0),
            make_day(dates[1], skin_temp=0.1, rhr=50.0, hrv=57.0),
            make_day(dates[2], skin_temp=5.0, rhr=58.0, hrv=44.0)]  # temp muy alta pero ventana<3
    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    # rhr+hrv sí son 2 señales sin temp -> watch, pero NUNCA "alert" por temp
    # (ventana insuficiente = ausencia de dato confiable, no dispara la señal de temp)
    if "illness_early_warning" in ids:
        insight = next(r for r in results if r["id"] == "illness_early_warning")
        assert insight["severity"] != "alert"


def test_illness_no_trigger_one_signal():
    """Solo rhr alta (1 señal sin temp) → NO dispara."""
    dates = date_seq(5)
    days = [make_day(d, rhr=50.0, hrv=57.0) for d in dates[:4]]
    days.append(make_day(dates[4], rhr=58.0, hrv=57.0))  # solo rhr alta

    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" not in ids


# ── Regla 1b: hrv_anomala (roadmap "vitals-illness-hrv-bidireccional") ─────────

def test_illness_alert_hrv_anomalous_high_with_elevated_temp():
    """Escenario de referencia: temp elevada + HRV anormalmente ALTA (el "pico
    parasimpático" de una infección incipiente). skin_temp=0.24 (delta≈+1.17 vs
    media previa≈-0.93, mismos 13 días previos que
    test_illness_alert_temp_delta_masked_by_high_sd_real_case), hrv=79.77 con
    base_recent=58.8 y sd=7.71 (z≈+2.72 → HRV ANORMALMENTE ALTA, no baja),
    rhr=53/base 51.6 (z≈+0.43, no dispara), resp=15.23 (no dispara),
    spo2=95.68 (>92, no dispara).

    Sin la señal bidireccional: 0 co-señales (la regla solo miraba HRV baja), así
    que la temp elevada quedaba sola → la regla se quedaba en `watch`,
    insuficiente para este patrón. Con hrv_anomala (|z_hrv|>1.5, bidireccional)
    la HRV alta SÍ cuenta como co-señal → escala a `alert`.
    """
    dates = date_seq(14)
    temps_prev = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                  0.38, 0.42, -2.01, -2.58, 0.28]
    days = [make_day(d, skin_temp=t, rhr=51.6, hrv=57.0, resp=15.0)
            for d, t in zip(dates[:13], temps_prev)]
    days.append(make_day(dates[13], skin_temp=0.24, rhr=53.0, hrv=79.77,
                          resp=15.23, spo2=95.68))

    summary = {"hrv_base_recent": 58.8, "hrv_sd": 7.71, "rhr_base": 51.6}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" in ids, (
        "Temp elevada + HRV anómala ALTA (z≈+2.72) debe disparar el insight — "
        "sin la señal bidireccional se quedaba en watch."
    )
    insight = next(r for r in results if r["id"] == "illness_early_warning")
    assert insight["severity"] == "alert"
    factors_text = " ".join(insight["factors"])
    assert "HRV" in factors_text or "VFC" in factors_text


def test_illness_alert_silences_contradictory_positive_hrv():
    """Coherencia de mensaje: el MISMO dato (HRV alta) alimenta la señal
    hrv_anomala de la alerta de enfermedad Y el positivo "HRV en racha
    ascendente". Aparecer juntos el mismo día es la señal mixta que le quita
    credibilidad al coach proactivo, así que con la alerta activa el positivo
    se silencia.

    Se usa el escenario de temp elevada + HRV anómala + una racha ascendente de
    HRV en los días previos, para que positive_hrv dispare de verdad y el
    silenciado tenga algo que suprimir (si no, el test pasaría vacuo).
    """
    dates = date_seq(14)
    temps_prev = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                  0.38, 0.42, -2.01, -2.58, 0.28]
    # HRV en racha ascendente los días previos -> dispara positive_hrv
    hrv_rising = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0,
                  58.0, 59.0, 60.0, 61.0, 62.0]
    days = [make_day(d, skin_temp=t, rhr=51.6, hrv=h, resp=15.0)
            for d, t, h in zip(dates[:13], temps_prev, hrv_rising)]
    days.append(make_day(dates[13], skin_temp=0.24, rhr=53.0, hrv=79.77,
                          resp=15.23, spo2=95.68))

    summary = {"hrv_base_recent": 58.8, "hrv_sd": 7.71, "rhr_base": 51.6}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]

    assert "illness_early_warning" in ids, "la alerta debe seguir presente"
    assert "positive_hrv" not in ids, (
        "positive_hrv debe silenciarse con una alerta de enfermedad activa: "
        "es el mismo dato (HRV alta) contradiciendo a la alerta el mismo día."
    )
    # El evento de cambio equivalente tampoco debe colarse por la otra puerta
    # (al quitar positive_hrv se libera su anti-duplicado en _CHANGE_ANTI_DUP).
    assert not any(i.startswith("change_hrv") for i in ids), (
        "el cambio 'HRV mejoró' tampoco debe reintroducir el mensaje contradictorio"
    )


def test_positive_hrv_survives_without_illness_alert():
    """Contraparte del silenciado: SIN alerta de enfermedad, positive_hrv sigue
    apareciendo igual que siempre (el silenciado no debe comerse el positivo en
    el caso normal)."""
    dates = date_seq(14)
    hrv_rising = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0,
                  58.0, 59.0, 60.0, 61.0, 62.0, 63.0]
    # temp plana y normal -> sin señal de enfermedad
    days = [make_day(d, skin_temp=0.0, rhr=51.6, hrv=h, resp=15.0)
            for d, h in zip(dates, hrv_rising)]

    summary = {"hrv_base_recent": 58.8, "hrv_sd": 7.71, "rhr_base": 51.6}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]

    assert "illness_early_warning" not in ids, "sin señales, no debe haber alerta"
    assert "positive_hrv" in ids, (
        "sin alerta de enfermedad, positive_hrv debe aparecer normalmente"
    )


def test_illness_hrv_high_with_normal_temp_does_not_fire():
    """RIESGO #1 del roadmap: HRV alta = buena recuperación. Un día con HRV
    anormalmente alta (|z|>2.0) pero temp NORMAL (no elevada) y sin otras
    señales NO debe disparar nada — ni alert ni watch (1 sola señal no-temp
    no alcanza el umbral de watch, que exige >=2 sin temp)."""
    dates = date_seq(14)
    # skin_temp estable (sd baja, sin variación relevante) para que la señal
    # de temp NUNCA se eleve.
    days = [make_day(d, skin_temp=35.0, rhr=50.0, hrv=57.0, resp=15.0)
            for d in dates[:13]]
    # hrv muy alta (z = (80-57)/5 = 4.6 >> 2.0) con temp normal
    days.append(make_day(dates[13], skin_temp=35.1, rhr=50.0, hrv=80.0, resp=15.0))

    summary = {"hrv_base_recent": 57.0, "hrv_sd": 5.0, "rhr_base": 50.0}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" not in ids, (
        "HRV alta con temperatura normal es BUENA recuperación, no debe "
        "disparar el insight de enfermedad (falso positivo obvio del roadmap)."
    )


def test_illness_hrv_anomaly_none_safe_degenerate_sd():
    """None-safe: hrv_sd degenerada (<epsilon) → no se evalúa z-score → la
    señal hrv_anomala NUNCA dispara (solo el fallback absoluto de HRV baja
    sigue vigente, sin cambios). Ausencia/degeneración de sd ≠ enfermedad."""
    dates = date_seq(14)
    days = [make_day(d, skin_temp=35.0, rhr=50.0, hrv=57.0, resp=15.0)
            for d in dates[:13]]
    days.append(make_day(dates[13], skin_temp=35.1, rhr=50.0, hrv=90.0, resp=15.0))

    summary = {"hrv_base_recent": 57.0, "hrv_sd": 0.001, "rhr_base": 50.0}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    # hrv=90 vs base=57 con fallback absoluto (0.85*57=48.45): 90 no es < 48.45,
    # así que ni siquiera el fallback de HRV baja dispara. Sin temp elevada ni
    # otras señales, no debe aparecer el insight.
    assert "illness_early_warning" not in ids


def test_illness_hrv_low_signal_preserved_z_below_minus_1_5():
    """No-regresión (criterio 1 del roadmap): la señal existente de HRV BAJA
    (z<-1.5) se conserva intacta, sin verse reemplazada por hrv_anomala.
    skin_temp se mantiene igual al histórico (temp NO elevada) y se suma rhr
    alta como 2ª señal para que dispare watch (sin temp, requiere >=2)."""
    dates = date_seq(14)
    days = [make_day(d, skin_temp=35.0, rhr=50.0, hrv=57.0, resp=15.0)
            for d in dates[:13]]
    # hrv baja: z = (45-57)/5 = -2.4 (< -1.5, dispara hrv baja; también
    # |z|>2.0 pero debe reportarse como "HRV baja", un solo factor).
    # rhr alta (fallback: 60 > 50+5) como 2ª señal no-temp.
    days.append(make_day(dates[13], skin_temp=35.0, rhr=60.0, hrv=45.0, resp=15.0))

    summary = {"hrv_base_recent": 57.0, "hrv_sd": 5.0, "rhr_base": 50.0}
    ds = make_dataset(days, summary)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "illness_early_warning" in ids
    insight = next(r for r in results if r["id"] == "illness_early_warning")
    assert insight["severity"] == "watch"
    factors_text = " ".join(insight["factors"])
    assert "baja" in factors_text.lower()
    # Un solo factor de hrv, no dos (no se duplica low+anomaly)
    hrv_factor_count = sum(1 for f in insight["factors"] if "hrv" in f.lower() or "vfc" in f.lower())
    assert hrv_factor_count == 1


# ── Regla 2: spo2_low ──────────────────────────────────────────────────────────

def test_spo2_low_alert():
    """SpO₂ < 90 en una noche reciente → alert."""
    dates = date_seq(7)
    days = [make_day(d, spo2=95.0) for d in dates[:6]]
    days.append(make_day(dates[6], spo2=87.0))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "spo2_low" in ids
    insight = next(r for r in results if r["id"] == "spo2_low")
    assert insight["severity"] == "alert"


def test_spo2_no_trigger_above_90():
    """SpO₂ ≥ 90 en todos los días → NO dispara."""
    dates = date_seq(7)
    days = [make_day(d, spo2=93.0 + i * 0.3) for i, d in enumerate(dates)]
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "spo2_low" not in ids


def test_spo2_none_fields_no_trigger():
    """Días sin spo2 (None) → NO dispara spo2_low."""
    dates = date_seq(7)
    days = [make_day(d) for d in dates]  # sin spo2
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "spo2_low" not in ids


# ── Regla 3: sleep_debt ────────────────────────────────────────────────────────

def test_sleep_debt_watch_3_nights():
    """3 noches <7h → watch."""
    dates = date_seq(7)
    days = [make_day(d, asleep=480, recovery=80) for d in dates[:4]]
    # 3 noches cortas (350 min = ~5.8h)
    for d in dates[4:7]:
        days.append(make_day(d, asleep=350, recovery=65))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "sleep_debt" in ids
    insight = next(r for r in results if r["id"] == "sleep_debt")
    assert insight["severity"] == "watch"
    factors_text = " ".join(insight["factors"])
    assert "3" in factors_text


def test_sleep_debt_alert_5_nights():
    """5 noches <7h → alert."""
    dates = date_seq(7)
    days = [make_day(d, asleep=480, recovery=80) for d in dates[:2]]
    for d in dates[2:]:
        days.append(make_day(d, asleep=340, recovery=55))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "sleep_debt" in ids
    insight = next(r for r in results if r["id"] == "sleep_debt")
    assert insight["severity"] == "alert"


def test_sleep_debt_no_trigger_2_nights():
    """Solo 2 noches <7h → NO dispara."""
    dates = date_seq(7)
    days = [make_day(d, asleep=480) for d in dates[:5]]
    days += [make_day(d, asleep=350) for d in dates[5:]]

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "sleep_debt" not in ids


# ── Regla 4: overtraining ──────────────────────────────────────────────────────

def test_overtraining_watch():
    """Strain >14 promedio + ≥2 días recovery<34 → watch."""
    dates = date_seq(30)
    # 30 días base
    days = [make_day(d, strain=10.0, recovery=75) for d in dates[:23]]
    # últimos 7 días: strain alto + recovery malo
    for d in dates[23:]:
        days.append(make_day(d, strain=16.0, recovery=28))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "overtraining" in ids
    insight = next(r for r in results if r["id"] == "overtraining")
    assert insight["severity"] == "watch"
    factors_text = " ".join(insight["factors"])
    assert "16" in factors_text or "Esfuerzo" in factors_text


def test_overtraining_no_trigger_low_strain():
    """Strain bajo → NO dispara overtraining."""
    dates = date_seq(30)
    days = [make_day(d, strain=8.0, recovery=70) for d in dates]
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "overtraining" not in ids


# ── Regla 5: recovery_declining ────────────────────────────────────────────────

def test_recovery_declining_watch():
    """Recovery 7d < 30d - 8 → watch (sin overtraining que lo duplique)."""
    dates = date_seq(30)
    # 30d base alta recovery
    days = [make_day(d, recovery=78, strain=9.0) for d in dates[:23]]
    # últimos 7 días recovery bajo, strain normal
    for d in dates[23:]:
        days.append(make_day(d, recovery=55, strain=9.0))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "recovery_declining" in ids
    insight = next(r for r in results if r["id"] == "recovery_declining")
    assert insight["severity"] == "watch"


def test_recovery_declining_no_trigger_small_margin():
    """Caída de solo 5 pts → NO dispara."""
    dates = date_seq(30)
    days = [make_day(d, recovery=75, strain=8.0) for d in dates[:23]]
    for d in dates[23:]:
        days.append(make_day(d, recovery=70, strain=8.0))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "recovery_declining" not in ids


# ── Regla 6: bedtime_inconsistency ─────────────────────────────────────────────

def test_bedtime_inconsistency_watch():
    """pstdev(bed_min, 21d) > 75 → watch.
    Alternamos 0 min (medianoche) y 180 min (03:00) → pstdev ~90 min."""
    dates = date_seq(21)
    days = []
    for i, d in enumerate(dates):
        bm = 0 if i % 2 == 0 else 180  # pstdev ≈ 90 > 75
        days.append(make_day(d, bed_min=bm, asleep=420))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "bedtime_inconsistency" in ids
    insight = next(r for r in results if r["id"] == "bedtime_inconsistency")
    assert insight["severity"] == "watch"
    # Verificar que el factor menciona la desviación
    factors_text = " ".join(insight["factors"])
    assert "min" in factors_text or "desviación" in factors_text.lower() or "Desviación" in factors_text


def test_bedtime_inconsistency_no_trigger_consistent():
    """bed_min muy consistente → NO dispara."""
    dates = date_seq(21)
    days = [make_day(d, bed_min=30 + (i % 3)) for i, d in enumerate(dates)]
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "bedtime_inconsistency" not in ids


# ── Regla 7: strength_gap ──────────────────────────────────────────────────────
# RONDA 3: rule_strength_gap migró de 'vigorous' (proxy cardio/AZM, falso negativo)
# a strength_minutes() real sobre `exercises`. Estos 3 tests se actualizan a la
# nueva señal (cardio puro en exercises SÍ dispara; una sesión de pesas NO dispara).

def test_strength_gap_info():
    """7 días con solo cardio registrado (ninguna sesión de fuerza) → info."""
    dates = date_seq(7)
    days = [make_day(d, asleep=440, recovery=75) for d in dates]
    exercises = [{"date": dates[-1], "type": "running", "name": "Run", "dur_min": 40}]
    results = evaluate(make_dataset(days, exercises=exercises))
    ids = [r["id"] for r in results]
    assert "strength_gap" in ids
    insight = next(r for r in results if r["id"] == "strength_gap")
    assert insight["severity"] == "info"


def test_strength_gap_no_trigger_with_strength_session():
    """Al menos una sesión de fuerza real en la ventana → NO dispara."""
    dates = date_seq(7)
    days = [make_day(d) for d in dates]
    exercises = [
        {"date": dates[0], "type": "running", "name": "Run", "dur_min": 40},
        {"date": dates[6], "type": "strength_training", "name": "Gym", "dur_min": 30},
    ]
    results = evaluate(make_dataset(days, exercises=exercises))
    ids = [r["id"] for r in results]
    assert "strength_gap" not in ids


def test_strength_gap_no_trigger_no_data():
    """Sin ningún ejercicio registrado en la ventana → NO dispara (ausencia de dato ≠ malo)."""
    dates = date_seq(7)
    days = [make_day(d, asleep=440) for d in dates]
    results = evaluate(make_dataset(days, exercises=[]))
    ids = [r["id"] for r in results]
    assert "strength_gap" not in ids


def test_strength_gap_pure_vigorous_cardio_now_triggers():
    """
    RONDA 3 — caso central del roadmap: una semana de puro cardio vigoroso SIN
    fuerza debe disparar (antes era un falso negativo porque 'vigorous' > 0 hacía
    que la regla vieja NO disparara, aunque cero fuera fuerza real)."""
    dates = date_seq(7)
    days = [make_day(d, vigorous=45) for d in dates]  # cardio vigoroso todos los días
    exercises = [{"date": d, "type": "running", "name": "Run", "dur_min": 45} for d in dates]
    results = evaluate(make_dataset(days, exercises=exercises))
    ids = [r["id"] for r in results]
    assert "strength_gap" in ids


# ── Regla 8: positive_hrv ──────────────────────────────────────────────────────

def test_positive_hrv():
    """HRV 7d > 30d y tendencia al alza → positive."""
    dates = date_seq(30)
    # 23 días con HRV 50
    days = [make_day(d, hrv=50.0) for d in dates[:23]]
    # Últimos 7 días: HRV creciendo 58→64
    for i, d in enumerate(dates[23:]):
        days.append(make_day(d, hrv=58.0 + i * 1.0))

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "positive_hrv" in ids
    insight = next(r for r in results if r["id"] == "positive_hrv")
    assert insight["severity"] == "positive"


def test_positive_hrv_no_trigger_flat():
    """HRV plana → NO dispara positive_hrv."""
    dates = date_seq(30)
    days = [make_day(d, hrv=55.0) for d in dates]
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "positive_hrv" not in ids


# ── Regla 9: positive_sleep ────────────────────────────────────────────────────
#
# Ronda P1 (UI plain-language): threshold = sleep_target_min - 60 (antes 420
# literal), misma derivación que rule_sleep_debt (Ronda 5). Con sleep_target_min
# default 480, threshold = 420 — IDÉNTICO a antes (ver
# test_positive_sleep_default_480_matches_420_literal). Con sleep_target_min
# custom, el umbral se mueve junto con el perfil (ver
# test_positive_sleep_respects_custom_sleep_target).

def test_positive_sleep():
    """0 noches < 7h en 7d (perfil default, sin sleep_target_min explícito
    en summary → fallback 480 → threshold 420) → positive."""
    dates = date_seq(7)
    days = [make_day(d, asleep=470 + i * 5) for i, d in enumerate(dates)]
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "positive_sleep" in ids
    insight = next(r for r in results if r["id"] == "positive_sleep")
    assert insight["severity"] == "positive"


def test_positive_sleep_no_trigger_short_nights():
    """Al menos 1 noche <7h → NO dispara positive_sleep."""
    dates = date_seq(7)
    days = [make_day(d, asleep=470) for d in dates[:6]]
    days.append(make_day(dates[6], asleep=380))
    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "positive_sleep" not in ids


def test_positive_sleep_default_480_matches_420_literal():
    """summary sin sleep_target_min (fallback 480) debe marcar como 'corta'
    exactamente las mismas noches que el umbral 420 viejo — equivalencia
    byte-a-byte con el comportamiento pre-P1 (mismo patrón que rule_sleep_debt,
    Ronda 5)."""
    from app.insights import rule_positive_sleep

    dates = date_seq(7)
    # 415min (<420 bajo ambos umbrales) rompe la racha en los dos casos.
    days = [make_day(d, asleep=415 if i == 0 else 450) for i, d in enumerate(dates)]

    summary_no_target = {}  # sin sleep_target_min -> fallback 480 -> threshold 420
    summary_explicit_480 = {"sleep_target_min": 480}

    r1 = rule_positive_sleep(days, summary_no_target)
    r2 = rule_positive_sleep(days, summary_explicit_480)
    assert r1 == r2 is None  # la noche de 415min corta la racha en ambos casos


def test_positive_sleep_respects_custom_sleep_target():
    """Con sleep_target_min=540 (threshold 480), una noche de 450min ya NO
    cuenta como 'buena' — antes (umbral fijo 420) sí hubiera disparado
    positive_sleep. Demuestra que la regla ahora respeta la meta del perfil,
    no un literal fijo."""
    from app.insights import rule_positive_sleep

    dates = date_seq(7)
    days = [make_day(d, asleep=450) for d in dates]  # 450min: >420 pero <480

    r_480 = rule_positive_sleep(days, {"sleep_target_min": 480})  # threshold 420 -> sí es "buena"
    r_540 = rule_positive_sleep(days, {"sleep_target_min": 540})  # threshold 480 -> ya no es "buena"

    assert r_480 is not None
    assert r_480["severity"] == "positive"
    assert r_540 is None


# ── DATASET SANO: anti-alarmismo ───────────────────────────────────────────────

def _build_healthy_dataset():
    """
    Dataset perfectamente sano: 30 días con métricas óptimas.
    NO debe disparar ninguna alerta ni watch.
    """
    dates = date_seq(30)
    days = []
    for i, d in enumerate(dates):
        days.append({
            "date": d,
            "hrv": 58.0 + (i % 4) * 0.5,      # HRV estable ~58-60 (base 57)
            "rhr": 50.0 + (i % 3) * 0.3,       # RHR estable (base 50)
            "asleep": 460 + (i % 5) * 5,       # >7h todas las noches
            "inbed": 490,
            "recovery": 75 + (i % 5) * 2,     # Recovery estable ~75-83
            "strain": 10.0 + (i % 4) * 0.5,   # Strain moderado
            "skin_temp": 35.1 + (i % 5) * 0.05,
            "bed_min": 60 + (i % 5) * 4,      # Consistente ±16 min
            "vigorous": 20 + (i % 3) * 5,     # Con entrenamiento
            "sleep_perf": 80,
            "spo2": 96.0 + (i % 3) * 0.5,     # SpO₂ bien arriba de 92
            "resp": 14.5 + (i % 3) * 0.2,
        })
    summary = {
        "hrv_base": 57.0,
        "rhr_base": 50.0,
        "hrv_range": [44.0, 72.0],
        "rhr_range": [46.0, 60.0],
    }
    return {"days": days, "summary": summary}


def test_healthy_no_alert_or_watch():
    """Dataset sano → cero alertas y cero watch (anti-alarmismo)."""
    ds = _build_healthy_dataset()
    results = evaluate(ds)
    bad = [r for r in results if r["severity"] in ("alert", "watch")]
    assert bad == [], (
        f"El dataset sano disparó alertas/watch inesperadas: "
        + ", ".join(f"{r['id']}({r['severity']})" for r in bad)
    )


# ── Campos None no rompen ni disparan ──────────────────────────────────────────

def test_none_fields_do_not_crash():
    """Días con campos None no generan excepciones."""
    dates = date_seq(10)
    days = [{"date": d} for d in dates]  # todo None
    ds = {"days": days, "summary": {}}
    results = evaluate(ds)  # no debe lanzar
    # No esperamos alertas de datos vacíos
    for r in results:
        assert r["severity"] in ("alert", "watch", "positive", "info")


def test_none_spo2_does_not_trigger_spo2_low():
    """Días sin spo2 (campo ausente) NO disparan spo2_low."""
    dates = date_seq(7)
    days = [{"date": d, "asleep": 460} for d in dates]
    results = evaluate({"days": days, "summary": {}})
    assert all(r["id"] != "spo2_low" for r in results)


def test_none_temp_does_not_trigger_illness():
    """Días sin skin_temp y sin otras señales → NO disparan illness_early_warning."""
    dates = date_seq(15)
    days = [{"date": d, "rhr": 50.0, "hrv": 57.0, "asleep": 460} for d in dates]
    summary = {"hrv_base": 57.0, "rhr_base": 50.0}
    results = evaluate({"days": days, "summary": summary})
    assert all(r["id"] != "illness_early_warning" for r in results)


# ── Ordenamiento y límite ───────────────────────────────────────────────────────

def test_evaluate_ordering():
    """evaluate() devuelve alert antes que watch antes que positive."""
    dates = date_seq(30)
    # Combinar condiciones para disparar varias reglas:
    days = []
    # 30 días base
    for i, d in enumerate(dates[:23]):
        days.append(make_day(d,
            hrv=55.0, rhr=50.0, asleep=440, recovery=75, strain=9.0,
            bed_min=60, vigorous=20, skin_temp=35.0, spo2=95.0))
    # Últimos 7 días: illness (alert) + sleep_debt (watch)
    for i, d in enumerate(dates[23:]):
        days.append(make_day(d,
            rhr=58.0, hrv=44.0,         # 2 señales → watch illness
            asleep=350,                  # deuda de sueño
            recovery=60, strain=9.0,
            bed_min=60, vigorous=20,
            skin_temp=35.0, spo2=95.0))

    summary = make_summary(hrv_base=57.0, rhr_base=50.0)
    results = evaluate({"days": days, "summary": summary})
    severities = [r["severity"] for r in results]
    order = {"alert": 0, "watch": 1, "positive": 2, "info": 3}
    for i in range(len(severities) - 1):
        assert order[severities[i]] <= order[severities[i + 1]], (
            f"Ordenamiento roto: {severities[i]} antes de {severities[i+1]}"
        )


def test_evaluate_max_5():
    """evaluate() no devuelve más de 5 insights."""
    dates = date_seq(30)
    # Dataset patológico que activa muchas reglas
    days = []
    for i, d in enumerate(dates[:7]):
        days.append(make_day(d, hrv=50.0, rhr=50.0, asleep=420, recovery=75,
                             strain=9.0, bed_min=60 + i * 20, vigorous=0,
                             spo2=87.0))  # spo2 baja → alert
    for d in dates[7:]:
        days.append(make_day(d, hrv=50.0, rhr=50.0, asleep=420, recovery=75,
                             strain=9.0, bed_min=60, vigorous=0))
    summary = make_summary()
    results = evaluate({"days": days, "summary": summary})
    assert len(results) <= 5


def test_evaluate_empty_dataset():
    """Dataset vacío o sin days → []."""
    assert evaluate({}) == []
    assert evaluate({"days": [], "summary": {}}) == []
    assert evaluate(None) == []


# ── Ronda 3: evaluate() loguea reglas que lanzan (no las silencia) ─────────────

def test_evaluate_logs_and_continues_when_rule_raises(monkeypatch, caplog):
    """
    Si una regla lanza, evaluate() debe:
      1. Seguir evaluando el resto de las reglas (no perder insights válidos).
      2. Loguear el fallo (antes: `except Exception: pass` silencioso).
    """
    import logging
    import app.insights as insights_mod

    def _boom(days, summary, locale="es"):
        raise ValueError("regla rota a propósito")

    # Reemplazar la primera regla por una que siempre falla; dejar el resto intactas.
    monkeypatch.setattr(insights_mod, "_RULES", [_boom] + insights_mod._RULES[1:])

    dates = date_seq(7)
    days = [make_day(d, asleep=440, recovery=75) for d in dates]
    exercises = [{"date": dates[-1], "type": "running", "name": "Run", "dur_min": 40}]

    with caplog.at_level(logging.WARNING, logger="vitals.insights"):
        results = insights_mod.evaluate(make_dataset(days, exercises=exercises))

    # El resto de las reglas siguió corriendo (strength_gap dispara con este dataset).
    ids = [r["id"] for r in results]
    assert "strength_gap" in ids

    # El fallo quedó logueado (no silencioso).
    assert any("regla rota a propósito" in rec.message or "_boom" in rec.message
               for rec in caplog.records), (
        f"No se logueó el fallo de la regla. Records: {[r.message for r in caplog.records]}"
    )


# ── Fase 7: reglas de ciclo (salud femenina) — gateadas por _cycle ────────────

from app.insights import (
    rule_cycle_phase, rule_period_approaching, rule_cycle_delay, rule_perimenopause_signal,
)


def test_cycle_rules_none_when_no_cycle_key():
    """Sin dataset['_cycle'] en absoluto (toggle off / módulo no corrió) -> las 4
    reglas de ciclo NO disparan. Criterio #1: cero fuga con opt-out."""
    days = [make_day("2026-06-01", recovery=70)]
    ds = make_dataset(days)
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert "cycle_phase" not in ids
    assert "period_approaching" not in ids
    assert "cycle_delay" not in ids
    assert "perimenopause_signal" not in ids


def test_cycle_rules_none_when_cycle_disabled():
    """dataset['_cycle'] = {'enabled': False} -> ninguna regla de ciclo dispara."""
    days = [make_day("2026-06-01", recovery=70)]
    ds = make_dataset(days)
    ds["_cycle"] = {"enabled": False}
    results = evaluate(ds)
    ids = [r["id"] for r in results]
    assert not any(i in ids for i in
                    ("cycle_phase", "period_approaching", "cycle_delay", "perimenopause_signal"))


def test_rule_cycle_phase_fires_with_enabled_state():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "cycle_day": 14, "phase": "ovulatory"}
    result = rule_cycle_phase([], summary)
    assert result is not None
    assert result["id"] == "cycle_phase"
    assert result["severity"] == "info"


def test_rule_cycle_phase_none_without_cycle_day():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "cycle_day": None, "phase": None}
    assert rule_cycle_phase([], summary) is None


def test_rule_period_approaching_fires_within_3_days():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "period": {"days_until": 2, "predicted_next": "2026-07-05"}}
    result = rule_period_approaching([], summary)
    assert result is not None
    assert result["id"] == "period_approaching"


def test_rule_period_approaching_none_when_far():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "period": {"days_until": 10, "predicted_next": "2026-07-15"}}
    assert rule_period_approaching([], summary) is None


def test_rule_cycle_delay_fires_and_includes_disclaimer():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "delay": {"is_delayed": True, "days": 5}}
    result = rule_cycle_delay([], summary)
    assert result is not None
    assert result["id"] == "cycle_delay"
    assert result["severity"] == "watch"
    # Criterio #7: disclaimer presente en salida sensible (retraso)
    from app.i18n import tr
    assert tr("cycle_disclaimer", "es") in result["recommendation"]


def test_rule_cycle_delay_none_when_not_delayed():
    summary = make_summary()
    summary["_cycle"] = {"enabled": True, "delay": {"is_delayed": False, "days": 0}}
    assert rule_cycle_delay([], summary) is None


def test_rule_perimenopause_signal_fires_and_includes_disclaimer():
    summary = make_summary()
    summary["_cycle"] = {
        "enabled": True,
        "menopause": {"stage": "perimenopause_possible", "signals": ["length_variability"], "confidence": "medium"},
    }
    result = rule_perimenopause_signal([], summary)
    assert result is not None
    assert result["id"] == "perimenopause_signal"
    from app.i18n import tr
    assert tr("cycle_disclaimer", "es") in result["recommendation"]


def test_rule_perimenopause_signal_none_with_insufficient_history():
    """Criterio #6: insufficient_history -> NUNCA dispara (cero falsos positivos)."""
    summary = make_summary()
    summary["_cycle"] = {
        "enabled": True,
        "menopause": {"stage": "insufficient_history", "signals": [], "confidence": "low"},
    }
    assert rule_perimenopause_signal([], summary) is None


def test_rule_perimenopause_signal_none_when_premenopausal():
    summary = make_summary()
    summary["_cycle"] = {
        "enabled": True,
        "menopause": {"stage": "premenopausal", "signals": [], "confidence": "low"},
    }
    assert rule_perimenopause_signal([], summary) is None


def test_cycle_rules_never_crash_with_malformed_cycle_state():
    """_cycle con forma inesperada (dict incompleto) -> ninguna regla lanza."""
    days = [make_day("2026-06-01", recovery=70)]
    ds = make_dataset(days)
    ds["_cycle"] = {"enabled": True}  # sin cycle_day/period/delay/menopause
    results = evaluate(ds)  # no debe lanzar
    assert isinstance(results, list)


# ── Paso 2: integración de Frescura de Alertas (changes.py) en evaluate() ────
#
# Las fixtures de ESTOS tests SÍ incluyen "ayer" (2+ días) a propósito, para
# ejercitar detect_changes(). Todos los tests de arriba (fixtures de 1 día o
# sin delta real) siguen produciendo detect_changes() == [] y por lo tanto
# evaluate() con comportamiento IDÉNTICO al de antes de este paso — la
# no-regresión ya quedó demostrada por el resto de este archivo sin tocar
# ninguna fixture existente.

def test_evaluate_zero_changes_identical_to_before():
    """Con fixtures ayer==hoy (sin cambios detectables), evaluate() no incluye
    ningún insight con fresh=True — comportamiento actual preservado 1:1."""
    dates = date_seq(30)
    days = [make_day(d, hrv=55.0, rhr=50.0, asleep=440, recovery=75, strain=9.0,
                      bed_min=60, vigorous=20) for d in dates]
    summary = make_summary(hrv_base=55.0, rhr_base=50.0)
    results = evaluate(make_dataset(days, summary))
    assert not any(r.get("fresh") for r in results)


def test_fresh_change_appears_when_recovery_jumps():
    """Un salto de recovery hoy vs ayer produce un insight fresh en evaluate()."""
    dates = date_seq(10)
    days = [make_day(d, recovery=50, hrv=55.0, rhr=50.0) for d in dates[:-1]]
    days.append(make_day(dates[-1], recovery=75, hrv=55.0, rhr=50.0))  # +25 pts
    summary = make_summary(hrv_base=55.0, rhr_base=50.0)
    results = evaluate(make_dataset(days, summary))
    fresh = [r for r in results if r.get("fresh")]
    assert fresh, "se esperaba al menos un insight fresh"
    assert any("change_recovery" in r["id"] for r in fresh)


def test_fresh_change_ranked_between_alert_and_watch():
    """Orden: alert médica arriba, luego fresh, luego watch persistente."""
    dates = date_seq(30)
    # 23 días base con deuda de sueño acumulándose para watch persistente
    days = [make_day(d, asleep=350, recovery=75, hrv=55.0, rhr=50.0, spo2=95.0)
            for d in dates[:23]]
    # últimos 7: sigue la deuda de sueño (watch persistente: sleep_debt)
    for d in dates[23:29]:
        days.append(make_day(d, asleep=350, recovery=75, hrv=55.0, rhr=50.0, spo2=95.0))
    # último día: spo2 baja (alert médica) + recovery salta (fresh)
    days.append(make_day(dates[29], asleep=350, recovery=95, hrv=55.0, rhr=50.0, spo2=87.0))

    summary = make_summary(hrv_base=55.0, rhr_base=50.0)
    results = evaluate(make_dataset(days, summary))
    ids = [r["id"] for r in results]
    severities = [r["severity"] for r in results]
    freshes = [bool(r.get("fresh")) for r in results]

    assert "spo2_low" in ids
    alert_idx = ids.index("spo2_low")
    fresh_idxs = [i for i, f in enumerate(freshes) if f]
    watch_idxs = [i for i, (s, f) in enumerate(zip(severities, freshes)) if s == "watch" and not f]

    # La alerta médica siempre antes que cualquier fresh o watch persistente.
    for i in fresh_idxs + watch_idxs:
        assert alert_idx < i, "la alerta médica debe ir SIEMPRE arriba"
    # Un fresh nunca queda enterrado bajo un watch persistente.
    if fresh_idxs and watch_idxs:
        assert max(fresh_idxs) < min(watch_idxs), (
            "un cambio fresco no debe quedar enterrado bajo un watch persistente"
        )


def test_anti_duplicate_recovery_declining_suppresses_fresh_recovery_decline():
    """Si recovery_declining (regla persistente) ya disparó, el evento fresco de
    caída de recovery del mismo factor/dirección NO se lista aparte (anti-dup)."""
    dates = date_seq(30)
    # 30d con recovery alto en la base, cayendo fuerte en los últimos 7 días
    # (dispara rule_recovery_declining) Y con una caída día-a-día en el último
    # día (dispararía change_recovery_decline si no fuera por el anti-dup).
    days = [make_day(d, recovery=80, strain=8.0) for d in dates[:23]]
    for d in dates[23:29]:
        days.append(make_day(d, recovery=60, strain=8.0))
    days.append(make_day(dates[29], recovery=45, strain=8.0))  # caída día-a-día también

    results = evaluate(make_dataset(days))
    ids = [r["id"] for r in results]
    assert "recovery_declining" in ids
    assert not any(i.startswith("change_recovery_decline") for i in ids), (
        "el evento fresco de recovery decline debía suprimirse por anti-duplicado"
    )


def test_fresh_insight_has_full_shape_for_template():
    """El insight fresh trae las claves que consume renderInsights() en el template."""
    dates = date_seq(5)
    days = [make_day(d, recovery=50) for d in dates[:-1]]
    days.append(make_day(dates[-1], recovery=75))
    results = evaluate(make_dataset(days))
    fresh = next(r for r in results if r.get("fresh"))
    for key in ("id", "severity", "category", "icon", "title", "summary", "factors", "recommendation"):
        assert key in fresh


# ── Latch (dev-harness/illness-latch) ────────────────────────────────────────
#
# `evaluate(dataset, locale="es", latch=False)` — con latch=True el insight
# illness_early_warning se envuelve con app.illness_state.apply_latch() ANTES
# del silenciado de positive_hrv. Los datasets de abajo reconstruyen el
# escenario de referencia: temp elevada con sd alta que enmascara el
# z-score (ver test_illness_alert_temp_delta_masked_by_high_sd_real_case) +
# HRV que empieza anómala (pico de la mañana) y se diluye en el mismo día
# (re-promedio, ver ROADMAP.md).

@pytest.fixture
def illness_state_isolated(tmp_path, monkeypatch):
    """Aísla app.illness_state en tmp_path para los tests con latch=True
    (nunca toca data/ real). Mismo patrón que tests/test_illness_state.py y
    tests/test_coach_store.py."""
    from app import illness_state as st
    monkeypatch.setattr(st, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(st, "_LATCH_FILE", tmp_path / "illness_latch.json")
    return st


_INCIDENT_TEMPS_PREV = [-2.84, -2.46, -2.23, -1.01, -0.29, -0.16, 0.15, 0.27,
                         0.38, 0.42, -2.01, -2.58, 0.28]


def _incident_days(today_hrv):
    """13 días previos (temp variable, sd alta) + el día de hoy (fijo:
    skin_temp=0.24, rhr=53.0, resp=15.23, spo2=95.68) con `today_hrv`
    variable — para simular la misma fecha con la HRV ya diluida por el
    re-promedio intradía. `today` queda en date_seq(14)[-1] = "2024-01-14"."""
    dates = date_seq(14)
    days = [make_day(d, skin_temp=t, rhr=51.6, hrv=57.0, resp=15.0)
            for d, t in zip(dates[:13], _INCIDENT_TEMPS_PREV)]
    days.append(make_day(dates[13], skin_temp=0.24, rhr=53.0, hrv=today_hrv,
                          resp=15.23, spo2=95.68))
    return days


def _incident_summary():
    return {"hrv_base_recent": 58.8, "hrv_sd": 7.71, "rhr_base": 51.6}


def test_latch_false_default_is_byte_identical_and_touches_no_disk(illness_state_isolated):
    """Criterios 1 y 5 (parte pura): evaluate(dataset) sin `latch` (default
    False) es idéntico a evaluate(dataset, latch=False) explícito, y NUNCA
    toca disco — illness_state sigue sin archivo tras la llamada."""
    ds = make_dataset(_incident_days(today_hrv=79.77), _incident_summary())
    r_default = evaluate(ds)
    r_explicit_false = evaluate(ds, latch=False)
    assert r_default == r_explicit_false
    assert illness_state_isolated.load_latch() is None, (
        "evaluate() sin latch=True jamás debe escribir illness_latch.json"
    )


def test_latch_true_real_case_alert_survives_intraday_dilution(illness_state_isolated):
    """Criterio 7 — el corazón del paquete. 1ª evaluación del día con la HRV
    en su pico (hrv=79.77, z≈+2.72) dispara `alert` y se persiste. 2ª
    evaluación del MISMO día calendario con la HRV ya diluida por el
    re-promedio (hrv=66.07, z≈+0.94 → ya no es señal) SIGUE devolviendo
    `alert` (latcheado) — la retractación real que exponía el roadmap."""
    summary = _incident_summary()

    morning = evaluate(make_dataset(_incident_days(today_hrv=79.77), summary), latch=True)
    morning_illness = next(r for r in morning if r["id"] == "illness_early_warning")
    assert morning_illness["severity"] == "alert"

    # Sanity check: SIN latch, la HRV diluida ya no sostiene la alerta (así
    # se reproduce la retractación real que motiva este paquete).
    evening_fresh = evaluate(make_dataset(_incident_days(today_hrv=66.07), summary), latch=False)
    evening_fresh_illness = next(r for r in evening_fresh if r["id"] == "illness_early_warning")
    assert evening_fresh_illness["severity"] == "watch", (
        "sanity check del escenario: sin latch, la HRV diluida degrada alert->watch "
        "(si ya no reprodujera la retractación, el test de abajo sería vacuo)"
    )

    evening_latched = evaluate(make_dataset(_incident_days(today_hrv=66.07), summary), latch=True)
    evening_illness = next(r for r in evening_latched if r["id"] == "illness_early_warning")
    assert evening_illness["severity"] == "alert", (
        "con latch=True, la alerta de la mañana debe seguir devolviéndose aunque "
        "la señal fresca de la 2ª llamada del mismo día ya no sea alert"
    )
    assert evening_illness["summary"] == morning_illness["summary"], (
        "debe verse el insight COMPLETO del pico (mismo mensaje/factors de la "
        "mañana), no un alert genérico reconstruido"
    )


def test_latch_true_silences_positive_hrv_when_alert_is_latched(illness_state_isolated):
    """Criterio 4: el latch se aplica ANTES del silenciado de positive_hrv.
    Un `alert` que viene del LATCH (no del cómputo fresco de ESTA llamada)
    debe seguir silenciando un positive_hrv fresco del mismo día, y el
    evento de cambio 'HRV mejoró' equivalente tampoco debe colarse."""
    dates = date_seq(14)
    hrv_rising = [50.0, 51.0, 52.0, 53.0, 54.0, 55.0, 56.0, 57.0,
                  58.0, 59.0, 60.0, 61.0, 62.0]
    prev_days = [make_day(d, skin_temp=t, rhr=51.6, hrv=h, resp=15.0)
                 for d, t, h in zip(dates[:13], _INCIDENT_TEMPS_PREV, hrv_rising)]
    summary = _incident_summary()

    # 1ª llamada: fresh alert (temp elevada + HRV anómala alta) -> persiste el latch.
    morning_days = prev_days + [make_day(dates[13], skin_temp=0.24, rhr=53.0,
                                          hrv=79.77, resp=15.23, spo2=95.68)]
    first = evaluate(make_dataset(morning_days, summary), latch=True)
    assert any(r["id"] == "illness_early_warning" and r["severity"] == "alert" for r in first)

    # 2ª llamada MISMO día: HRV diluida (66.07, ya no anómala) -> el fresco de
    # illness_early_warning baja a watch, pero la racha ascendente previa +
    # 66.07 > 62.0 SÍ dispara positive_hrv fresco.
    diluted_days = prev_days + [make_day(dates[13], skin_temp=0.24, rhr=53.0,
                                          hrv=66.07, resp=15.23, spo2=95.68)]
    ds_diluted = make_dataset(diluted_days, summary)

    without_latch = evaluate(ds_diluted, latch=False)
    assert any(r["id"] == "positive_hrv" for r in without_latch), (
        "sanity check: sin latch, este dataset diluido SÍ dispara positive_hrv "
        "(si no, el test de silenciado de abajo sería vacuo)"
    )

    second = evaluate(ds_diluted, latch=True)
    ids = [r["id"] for r in second]
    assert "illness_early_warning" in ids
    illness = next(r for r in second if r["id"] == "illness_early_warning")
    assert illness["severity"] == "alert", "el latch de la 1ª llamada debe seguir mandando"
    assert "positive_hrv" not in ids, (
        "positive_hrv fresco debe seguir silenciado cuando la alerta viene del "
        "LATCH, no solo cuando viene del cómputo fresco de esta misma llamada"
    )
    assert not any(i.startswith("change_hrv") for i in ids), (
        "el evento de cambio 'HRV mejoró' tampoco debe reintroducir el mensaje contradictorio"
    )


def test_latch_true_resets_on_new_calendar_day(illness_state_isolated):
    """Criterio 6, vía evaluate(): un latch de AYER no debe fijar HOY, y el
    estado se re-persiste con la fecha de HOY (no acumula días viejos)."""
    summary = _incident_summary()
    evaluate(make_dataset(_incident_days(today_hrv=79.77), summary), latch=True)
    assert illness_state_isolated.load_latch()["date"] == "2024-01-14"

    # "Hoy" es un día calendario nuevo (2024-01-15), totalmente plano/sano.
    today_dates = date_seq(15)
    calm_days = [make_day(d, skin_temp=0.0, rhr=51.6, hrv=58.0, resp=15.0)
                 for d in today_dates]
    result = evaluate(make_dataset(calm_days, summary), latch=True)
    assert not any(r["id"] == "illness_early_warning" for r in result), (
        "el latch de ayer no debe fijar la alerta hoy"
    )
    persisted = illness_state_isolated.load_latch()
    assert persisted["date"] == "2024-01-15", "debe re-persistirse con la fecha de HOY"
    assert persisted["severity"] == "none"


def test_latch_true_none_safe_corrupt_state_degrades_to_fresh(illness_state_isolated, tmp_path):
    """Criterio 8: illness_latch.json corrupto -> latch=True se comporta como
    latch=False para esta llamada (el cómputo fresco sigue funcionando), nunca
    crashea evaluate()."""
    (tmp_path / "illness_latch.json").write_text("{not valid json", encoding="utf-8")
    summary = _incident_summary()
    result = evaluate(make_dataset(_incident_days(today_hrv=79.77), summary), latch=True)
    illness = next(r for r in result if r["id"] == "illness_early_warning")
    assert illness["severity"] == "alert"


def test_latch_true_none_safe_missing_date_degrades_to_fresh(illness_state_isolated):
    """Criterio 8: el último día sin `date` -> apply_latch degrada a fresh sin
    persistir (no hay forma confiable de comparar "mismo día"), nunca crashea."""
    days = _incident_days(today_hrv=79.77)
    days[-1] = {k: v for k, v in days[-1].items() if k != "date"}
    summary = _incident_summary()
    result = evaluate(make_dataset(days, summary), latch=True)
    illness = next(r for r in result if r["id"] == "illness_early_warning")
    assert illness["severity"] == "alert"
    assert illness_state_isolated.load_latch() is None, (
        "sin date confiable no debe persistir nada"
    )
