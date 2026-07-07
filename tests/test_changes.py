"""
test_changes.py — Tests de app/changes.py (Frescura de Alertas + Coach: Paso 1).

Cubre cada factor de detect_changes(), None-safety, ayer==hoy -> [], rachas,
y que los umbrales (constantes de módulo) sí gatean correctamente.
"""
from __future__ import annotations

from app import changes as ch
from app.changes import detect_changes


def make_days(specs):
    """specs: lista de dicts SIN 'date' -> les agrega fechas secuenciales."""
    return [{"date": f"2026-01-{i+1:02d}", **s} for i, s in enumerate(specs)]


# ── None-safety ──────────────────────────────────────────────────────────────

def test_none_dataset():
    assert detect_changes(None) == []


def test_empty_dataset():
    assert detect_changes({}) == []


def test_no_days_key():
    assert detect_changes({"summary": {}}) == []


def test_empty_days_list():
    assert detect_changes({"days": [], "summary": {}}) == []


def test_single_day_no_yesterday():
    """1 solo día -> sin 'ayer' -> no dispara nada por delta día-a-día."""
    ds = {"days": [{"date": "2026-01-01", "recovery": 70, "hrv": 55}], "summary": {}}
    assert detect_changes(ds) == []


def test_all_fields_none():
    days = [{"date": "2026-01-01"}, {"date": "2026-01-02"}]
    ds = {"days": days, "summary": {}}
    assert detect_changes(ds) == []


def test_identical_days_no_change():
    """ayer == hoy (mismos valores) -> [] (no inventa cambio)."""
    days = make_days([
        {"recovery": 70, "hrv": 55, "rhr": 50, "asleep": 450, "strain": 10},
        {"recovery": 70, "hrv": 55, "rhr": 50, "asleep": 450, "strain": 10},
    ])
    assert detect_changes({"days": days, "summary": {}}) == []


def test_dataset_missing_summary_key_none_safe():
    days = [{"date": "2026-01-01", "recovery": 50}, {"date": "2026-01-02", "recovery": 90}]
    # sin 'summary' en absoluto
    evs = detect_changes({"days": days})
    assert isinstance(evs, list)
    assert any(e["factor"] == "recovery" for e in evs)


# ── Recovery: delta + cruce de banda ────────────────────────────────────────

def test_recovery_delta_triggers_improvement():
    days = make_days([{"recovery": 50}, {"recovery": 65}])  # delta 15 >= 8
    evs = detect_changes({"days": days, "summary": {}})
    rec = [e for e in evs if e["factor"] == "recovery" and e["kind"] == "improvement"]
    assert len(rec) == 1
    assert rec[0]["direction"] == "up"
    assert rec[0]["severity"] == "positive"


def test_recovery_delta_triggers_decline():
    """delta -15 cruza además de banda alta->media: ambos eventos son 'decline'."""
    days = make_days([{"recovery": 70}, {"recovery": 55}])  # delta -15
    evs = detect_changes({"days": days, "summary": {}})
    rec = [e for e in evs if e["factor"] == "recovery" and e["kind"] == "decline"]
    assert len(rec) == 2
    assert all(r["direction"] == "down" and r["severity"] == "watch" for r in rec)


def test_recovery_delta_below_threshold_no_trigger():
    days = make_days([{"recovery": 70}, {"recovery": 74}])  # delta 4 < 8
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "recovery" and e["kind"] == "improvement" for e in evs)


def test_recovery_band_crossing_up():
    days = make_days([{"recovery": 50}, {"recovery": 70}])  # media -> alta (+ delta 20 >= 8)
    evs = detect_changes({"days": days, "summary": {}})
    milestones = [e for e in evs if e["factor"] == "recovery" and e["kind"] == "milestone"]
    assert len(milestones) == 1
    assert milestones[0]["direction"] == "up"


def test_recovery_band_crossing_without_delta_trigger():
    """Cruce de banda con delta < RECOVERY_DELTA (33->35: media, sin cruce real;
    usamos 33->41: baja->media, delta 8 alcanza el umbral) -- aislar el evento de
    banda puro requiere un delta pequeño que aun así cruce el límite de banda."""
    days = make_days([{"recovery": 32}, {"recovery": 35}])  # baja -> media, delta 3 < 8
    evs = detect_changes({"days": days, "summary": {}})
    milestones = [e for e in evs if e["factor"] == "recovery" and e["kind"] == "milestone"]
    deltas = [e for e in evs if e["factor"] == "recovery" and e["kind"] in ("improvement", "decline")]
    assert len(milestones) == 1
    assert milestones[0]["direction"] == "up"
    assert deltas == []


def test_recovery_band_crossing_down():
    days = make_days([{"recovery": 40}, {"recovery": 20}])  # media -> baja, delta -20
    evs = detect_changes({"days": days, "summary": {}})
    assert any(e["factor"] == "recovery" and e["direction"] == "down" for e in evs)


def test_recovery_no_band_data_none_safe():
    days = make_days([{"recovery": None}, {"recovery": 70}])
    assert detect_changes({"days": days, "summary": {}}) == []


# ── HRV vs base reciente ──────────────────────────────────────────────────

def test_hrv_below_base_triggers_decline():
    days = [{"date": "2026-01-01", "hrv": 45.0}]
    evs = detect_changes({"days": days, "summary": {"hrv_base_recent": 55.0}})
    hrv_evs = [e for e in evs if e["factor"] == "hrv"]
    assert len(hrv_evs) == 1
    assert hrv_evs[0]["kind"] == "decline"
    assert hrv_evs[0]["direction"] == "down"


def test_hrv_above_base_triggers_improvement():
    days = [{"date": "2026-01-01", "hrv": 65.0}]
    evs = detect_changes({"days": days, "summary": {"hrv_base_recent": 55.0}})
    hrv_evs = [e for e in evs if e["factor"] == "hrv"]
    assert len(hrv_evs) == 1
    assert hrv_evs[0]["kind"] == "improvement"


def test_hrv_within_threshold_no_trigger():
    days = [{"date": "2026-01-01", "hrv": 56.0}]
    evs = detect_changes({"days": days, "summary": {"hrv_base_recent": 55.0}})
    assert not any(e["factor"] == "hrv" for e in evs)


def test_hrv_no_base_none_safe():
    days = [{"date": "2026-01-01", "hrv": 65.0}]
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "hrv" for e in evs)


def test_hrv_falls_back_to_base_alltime():
    """Sin hrv_base_recent pero con hrv_base -> usa ese (mismo patrón que scoring.recent_base)."""
    days = [{"date": "2026-01-01", "hrv": 45.0}]
    evs = detect_changes({"days": days, "summary": {"hrv_base": 55.0}})
    assert any(e["factor"] == "hrv" for e in evs)


# ── RHR vs base reciente ──────────────────────────────────────────────────

def test_rhr_above_base_triggers_watch():
    days = [{"date": "2026-01-01", "rhr": 58.0}]
    evs = detect_changes({"days": days, "summary": {"rhr_base_recent": 50.0}})
    rhr_evs = [e for e in evs if e["factor"] == "rhr"]
    assert len(rhr_evs) == 1
    assert rhr_evs[0]["severity"] == "watch"
    assert rhr_evs[0]["direction"] == "up"


def test_rhr_below_base_triggers_positive():
    days = [{"date": "2026-01-01", "rhr": 44.0}]
    evs = detect_changes({"days": days, "summary": {"rhr_base_recent": 50.0}})
    rhr_evs = [e for e in evs if e["factor"] == "rhr"]
    assert len(rhr_evs) == 1
    assert rhr_evs[0]["severity"] == "positive"


def test_rhr_within_threshold_no_trigger():
    days = [{"date": "2026-01-01", "rhr": 51.0}]
    evs = detect_changes({"days": days, "summary": {"rhr_base_recent": 50.0}})
    assert not any(e["factor"] == "rhr" for e in evs)


# ── Sueño: delta día vs día + rachas ────────────────────────────────────────

def test_sleep_delta_up_triggers_improvement():
    days = make_days([{"asleep": 350}, {"asleep": 470}])  # delta 120 >= 45
    evs = detect_changes({"days": days, "summary": {}})
    sleep_evs = [e for e in evs if e["factor"] == "sleep" and e["kind"] == "improvement"]
    assert len(sleep_evs) == 1


def test_sleep_delta_down_triggers_decline():
    days = make_days([{"asleep": 470}, {"asleep": 350}])
    evs = detect_changes({"days": days, "summary": {}})
    sleep_evs = [e for e in evs if e["factor"] == "sleep" and e["kind"] == "decline"]
    assert len(sleep_evs) == 1


def test_sleep_delta_below_threshold_no_trigger():
    days = make_days([{"asleep": 450}, {"asleep": 470}])  # delta 20 < 45
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "sleep" and e["kind"] == "improvement" for e in evs)


def test_sleep_streak_good():
    days = make_days([{"asleep": 500} for _ in range(4)])
    evs = detect_changes({"days": days, "summary": {"sleep_target_min": 480}})
    streaks = [e for e in evs if e["factor"] == "sleep" and e["kind"] == "streak"]
    assert len(streaks) == 1
    assert streaks[0]["direction"] == "up"


def test_sleep_streak_bad():
    days = make_days([{"asleep": 300} for _ in range(4)])
    evs = detect_changes({"days": days, "summary": {"sleep_target_min": 480}})
    streaks = [e for e in evs if e["factor"] == "sleep" and e["kind"] == "streak"]
    assert len(streaks) == 1
    assert streaks[0]["direction"] == "down"


def test_sleep_streak_below_min_no_trigger():
    """Solo 2 noches seguidas (< STREAK_MIN=3) -> no dispara racha."""
    days = make_days([{"asleep": 500}, {"asleep": 500}])
    evs = detect_changes({"days": days, "summary": {"sleep_target_min": 480}})
    assert not any(e["factor"] == "sleep" and e["kind"] == "streak" for e in evs)


def test_sleep_streak_broken_by_opposite_night():
    """Racha buena rota por 1 noche mala en medio -> streak actual es solo 1 (no dispara)."""
    days = make_days([{"asleep": 500}, {"asleep": 500}, {"asleep": 300}, {"asleep": 500}])
    evs = detect_changes({"days": days, "summary": {"sleep_target_min": 480}})
    assert not any(e["factor"] == "sleep" and e["kind"] == "streak" for e in evs)


# ── Strain: delta día vs día ─────────────────────────────────────────────────

def test_strain_up_triggers():
    days = make_days([{"strain": 8.0}, {"strain": 15.0}])  # delta 7 >= 3
    evs = detect_changes({"days": days, "summary": {}})
    assert any(e["factor"] == "strain" and e["direction"] == "up" for e in evs)


def test_strain_down_triggers():
    days = make_days([{"strain": 15.0}, {"strain": 8.0}])
    evs = detect_changes({"days": days, "summary": {}})
    assert any(e["factor"] == "strain" and e["direction"] == "down" for e in evs)


def test_strain_below_threshold_no_trigger():
    days = make_days([{"strain": 8.0}, {"strain": 9.5}])  # delta 1.5 < 3
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "strain" for e in evs)


# ── Pasos: vs media 7d ───────────────────────────────────────────────────────

def test_steps_up_vs_avg():
    days = make_days([{"steps": 8000} for _ in range(7)] + [{"steps": 15000}])
    evs = detect_changes({"days": days, "summary": {}})
    assert any(e["factor"] == "steps" and e["direction"] == "up" for e in evs)


def test_steps_down_vs_avg():
    days = make_days([{"steps": 8000} for _ in range(7)] + [{"steps": 2000}])
    evs = detect_changes({"days": days, "summary": {}})
    assert any(e["factor"] == "steps" and e["direction"] == "down" for e in evs)


def test_steps_within_threshold_no_trigger():
    days = make_days([{"steps": 8000} for _ in range(7)] + [{"steps": 8500}])
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "steps" for e in evs)


def test_steps_insufficient_window_none_safe():
    days = make_days([{"steps": 8000}, {"steps": 20000}])  # solo 1 día de ventana
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "steps" for e in evs)


# ── skin_temp: señal de enfermedad aparece/desaparece ───────────────────────

def test_skin_temp_signal_appears():
    days = make_days([{"skin_temp": 35.0} for _ in range(15)] + [{"skin_temp": 36.0}])
    evs = detect_changes({"days": days, "summary": {}})
    st = [e for e in evs if e["factor"] == "skin_temp"]
    assert len(st) == 1
    assert st[0]["kind"] == "decline"


def test_skin_temp_signal_resolves():
    days = (
        make_days([{"skin_temp": 35.0} for _ in range(14)])
        + make_days([{"skin_temp": 36.0}])[:1]
        + make_days([{"skin_temp": 35.0}])[:1]
    )
    # Reconstituir fechas secuenciales correctas
    days = [{"date": f"2026-01-{i+1:02d}", "skin_temp": v["skin_temp"]} for i, v in enumerate(days)]
    evs = detect_changes({"days": days, "summary": {}})
    st = [e for e in evs if e["factor"] == "skin_temp"]
    assert len(st) == 1
    assert st[0]["kind"] == "improvement"


def test_skin_temp_no_data_none_safe():
    days = make_days([{} for _ in range(15)])
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "skin_temp" for e in evs)


# ── bedtime: consistencia mejora/empeora ────────────────────────────────────

def test_bedtime_consistency_improves():
    prev = [{"bed_min": 0 if i % 2 == 0 else 180} for i in range(21)]
    curr = [{"bed_min": 30 + (i % 3)} for i in range(21)]
    days = [{"date": f"2026-01-{i+1:02d}", **v} for i, v in enumerate(prev)]
    days += [{"date": f"2026-02-{i+1:02d}", **v} for i, v in enumerate(curr)]
    evs = detect_changes({"days": days, "summary": {}})
    bt = [e for e in evs if e["factor"] == "bedtime"]
    assert len(bt) == 1
    assert bt[0]["kind"] == "improvement"


def test_bedtime_consistency_worsens():
    prev = [{"bed_min": 30 + (i % 3)} for i in range(21)]
    curr = [{"bed_min": 0 if i % 2 == 0 else 180} for i in range(21)]
    days = [{"date": f"2026-01-{i+1:02d}", **v} for i, v in enumerate(prev)]
    days += [{"date": f"2026-02-{i+1:02d}", **v} for i, v in enumerate(curr)]
    evs = detect_changes({"days": days, "summary": {}})
    bt = [e for e in evs if e["factor"] == "bedtime"]
    assert len(bt) == 1
    assert bt[0]["kind"] == "decline"


def test_bedtime_insufficient_data_none_safe():
    days = make_days([{"bed_min": 30} for _ in range(5)])
    evs = detect_changes({"days": days, "summary": {}})
    assert not any(e["factor"] == "bedtime" for e in evs)


# ── fuerza: 1ª sesión en N días ──────────────────────────────────────────────

def test_strength_first_session_after_gap():
    days = [{"date": f"2026-01-{i+1:02d}"} for i in range(10)]
    exercises = [
        {"date": "2026-01-01", "type": "strength_training", "name": "Gym", "dur_min": 30},
        {"date": "2026-01-10", "type": "strength_training", "name": "Gym", "dur_min": 30},
    ]
    evs = detect_changes({"days": days, "summary": {}, "exercises": exercises})
    st = [e for e in evs if e["factor"] == "strength"]
    assert len(st) == 1
    assert st[0]["kind"] == "milestone"


def test_strength_gap_below_threshold_no_trigger():
    days = [{"date": f"2026-01-{i+1:02d}"} for i in range(5)]
    exercises = [
        {"date": "2026-01-03", "type": "strength_training", "name": "Gym", "dur_min": 30},
        {"date": "2026-01-05", "type": "strength_training", "name": "Gym", "dur_min": 30},
    ]
    evs = detect_changes({"days": days, "summary": {}, "exercises": exercises})
    assert not any(e["factor"] == "strength" for e in evs)


def test_strength_no_session_today_no_trigger():
    days = [{"date": f"2026-01-{i+1:02d}"} for i in range(10)]
    exercises = [{"date": "2026-01-01", "type": "strength_training", "name": "Gym", "dur_min": 30}]
    evs = detect_changes({"days": days, "summary": {}, "exercises": exercises})
    assert not any(e["factor"] == "strength" for e in evs)


def test_strength_first_session_ever_no_gap_to_report():
    """Única sesión de la historia (sin sesión previa) -> no hay 'gap' que reportar."""
    days = [{"date": f"2026-01-{i+1:02d}"} for i in range(3)]
    exercises = [{"date": "2026-01-03", "type": "strength_training", "name": "Gym", "dur_min": 30}]
    evs = detect_changes({"days": days, "summary": {}, "exercises": exercises})
    assert not any(e["factor"] == "strength" for e in evs)


def test_strength_no_exercises_none_safe():
    days = [{"date": "2026-01-01"}]
    evs = detect_changes({"days": days, "summary": {}, "exercises": []})
    assert not any(e["factor"] == "strength" for e in evs)


# ── VO2max ────────────────────────────────────────────────────────────────

def test_vo2max_up():
    ds = {"days": [{"date": "2026-01-01"}], "summary": {"bodyage": {"vo2max": 45.0}, "_prev_vo2max": 42.0}}
    evs = detect_changes(ds)
    assert any(e["factor"] == "vo2max" and e["direction"] == "up" for e in evs)


def test_vo2max_down():
    ds = {"days": [{"date": "2026-01-01"}], "summary": {"bodyage": {"vo2max": 40.0}, "_prev_vo2max": 44.0}}
    evs = detect_changes(ds)
    assert any(e["factor"] == "vo2max" and e["direction"] == "down" for e in evs)


def test_vo2max_no_prev_none_safe():
    ds = {"days": [{"date": "2026-01-01"}], "summary": {"bodyage": {"vo2max": 45.0}}}
    evs = detect_changes(ds)
    assert not any(e["factor"] == "vo2max" for e in evs)


def test_vo2max_below_threshold_no_trigger():
    ds = {"days": [{"date": "2026-01-01"}], "summary": {"bodyage": {"vo2max": 45.0}, "_prev_vo2max": 44.8}}
    evs = detect_changes(ds)
    assert not any(e["factor"] == "vo2max" for e in evs)


# ── i18n: cada evento trae title/summary/recommendation en el locale pedido ──

def test_events_localized_en():
    days = make_days([{"recovery": 50}, {"recovery": 70}])
    evs = detect_changes({"days": days, "summary": {}}, locale="en")
    rec = next(e for e in evs if e["factor"] == "recovery" and e["kind"] == "improvement")
    assert "improved" in rec["title"].lower()


def test_events_localized_fr():
    days = make_days([{"recovery": 50}, {"recovery": 70}])
    evs = detect_changes({"days": days, "summary": {}}, locale="fr")
    rec = next(e for e in evs if e["factor"] == "recovery" and e["kind"] == "improvement")
    assert "récupération" in rec["title"].lower() or "amélior" in rec["title"].lower()


def test_events_localized_pt():
    days = make_days([{"recovery": 50}, {"recovery": 70}])
    evs = detect_changes({"days": days, "summary": {}}, locale="pt")
    rec = next(e for e in evs if e["factor"] == "recovery" and e["kind"] == "improvement")
    assert "melhorou" in rec["title"].lower() or "recupera" in rec["title"].lower()


# ── shape del evento: claves requeridas ─────────────────────────────────────

def test_event_shape_has_required_keys():
    days = make_days([{"recovery": 50}, {"recovery": 70}])
    evs = detect_changes({"days": days, "summary": {}})
    assert evs, "se esperaban eventos"
    for e in evs:
        for key in ("factor", "kind", "direction", "delta", "magnitude", "severity", "text"):
            assert key in e, f"falta clave '{key}' en evento {e}"
        assert e["kind"] in ("improvement", "decline", "milestone", "streak")
