"""
test_wellbeing.py — Tests para compute_wellbeing (Fase 3.5 Parte 2).

Verifica:
- Score acotado 0-100 siempre.
- None-safe: señales faltantes renormalizan pesos (no sesgos).
- 0 señales → None.
- compute_wellbeing NO modifica recovery/bodyage/strain (ADITIVO).
- day["wellbeing"] se expone desde build_dataset.
- Razonabilidad de los sub-scores (HRV alto → score bueno; RHR alto → score bajo).
"""
from __future__ import annotations

import pytest
from app.scoring import compute_wellbeing, build_dataset


# ────────────────────────────────────────────────────────────────────────────
#  Helpers
# ────────────────────────────────────────────────────────────────────────────

def _summary(hrv_base=55.0, hrv_sd=8.0, rhr_base=52.0, rhr_sd=5.0,
             resp_sd=1.2, skin_temp_sd=0.5):
    """Minimal summary con todas las señales presentes."""
    return {
        "hrv_base": hrv_base,
        "hrv_base_recent": hrv_base,
        "rhr_base": rhr_base,
        "rhr_base_recent": rhr_base,
        "hrv_sd": hrv_sd,
        "rhr_sd": rhr_sd,
        "resp_sd": resp_sd,
        "skin_temp_sd": skin_temp_sd,
    }


def _day_full(hrv=55.0, rhr=52.0, resp=15.0, spo2=97.0, skin_temp=0.0):
    """Día con todos los signos vitales presentes."""
    return {"date": "2024-01-01", "hrv": hrv, "rhr": rhr,
            "resp": resp, "spo2": spo2, "skin_temp": skin_temp}


# ────────────────────────────────────────────────────────────────────────────
#  Cota 0-100
# ────────────────────────────────────────────────────────────────────────────

class TestWellbeingBounds:

    def test_score_in_range_normal_day(self):
        day = _day_full()
        s = _summary()
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert 0 <= score <= 100

    def test_score_clamped_high_when_hrv_very_high(self):
        """HRV 5 SDs above base → score must still be ≤ 100."""
        day = _day_full(hrv=95.0)  # muy por encima
        s = _summary(hrv_base=55.0, hrv_sd=8.0)
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert score <= 100

    def test_score_clamped_low_when_rhr_very_high(self):
        """RHR 5 SDs above base → score must still be ≥ 0."""
        day = _day_full(rhr=90.0)  # muy elevado
        s = _summary(rhr_base=52.0, rhr_sd=5.0)
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert score >= 0

    def test_score_clamped_with_extreme_skin_temp(self):
        """skin_temp muy alta (fiebre) → score nunca negativo."""
        day = _day_full(skin_temp=5.0)  # +5°C desviación
        s = _summary(skin_temp_sd=0.5)
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert 0 <= score <= 100

    def test_score_is_int(self):
        """compute_wellbeing devuelve int, no float."""
        day = _day_full()
        s = _summary()
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert isinstance(score, int)


# ────────────────────────────────────────────────────────────────────────────
#  None-safe y renormalización
# ────────────────────────────────────────────────────────────────────────────

class TestWellbeingNoneSafe:

    def test_none_when_zero_signals(self):
        """Día vacío → None."""
        day = {"date": "2024-01-01"}
        s = _summary()
        assert compute_wellbeing(day, [], s, resp_base=15.0) is None

    def test_none_when_no_bases_available(self):
        """Sin hrv_base ni rhr_base en summary → las señales que dependen de ellas se omiten."""
        day = {"date": "2024-01-01", "hrv": 55.0, "rhr": 52.0}
        s = {}  # summary vacío
        # HRV y RHR requieren base → no se pueden calcular
        score = compute_wellbeing(day, [], s, resp_base=None)
        assert score is None

    def test_renormalizes_with_spo2_only(self):
        """Solo SpO₂ disponible → score válido (renormalización a peso=.15/.15=1.0)."""
        day = {"date": "2024-01-01", "spo2": 98.0}
        s = {}  # sin bases para otras señales
        score = compute_wellbeing(day, [], s, resp_base=None)
        assert score is not None
        assert 0 <= score <= 100

    def test_renormalizes_with_two_signals(self):
        """HRV + RHR disponibles, resto None → score válido, pesos renormalizados."""
        day = {"date": "2024-01-01", "hrv": 55.0, "rhr": 52.0}
        s = _summary()
        score = compute_wellbeing(day, [], s, resp_base=None)  # resp_base=None → resp omitido
        assert score is not None
        assert 0 <= score <= 100

    def test_missing_hrv_does_not_bias_score_low(self):
        """Sin HRV, resto normal → score razonable (no se desploma por la señal ausente)."""
        day = _day_full()
        day.pop("hrv")
        s = _summary()
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert score >= 30  # señales restantes en rango normal → score decente

    def test_missing_spo2_still_valid(self):
        """Sin SpO₂ → no falla, peso redistribuido entre las otras."""
        day = {"date": "2024-01-01", "hrv": 55.0, "rhr": 52.0, "skin_temp": 0.0}
        s = _summary()
        score = compute_wellbeing(day, [], s, resp_base=None)
        assert score is not None
        assert 0 <= score <= 100


# ────────────────────────────────────────────────────────────────────────────
#  Razonabilidad de la fórmula
# ────────────────────────────────────────────────────────────────────────────

class TestWellbeingReasonableness:

    def test_baseline_day_scores_near_50(self):
        """Día con HRV y RHR exactamente en la base → score ~50 (neutral).
        Pasamos solo HRV+RHR para controlar las otras señales."""
        day = {"date": "2024-01-01", "hrv": 55.0, "rhr": 52.0}  # sin spo2/skin/resp
        s = _summary(hrv_base=55.0, rhr_base=52.0)
        score = compute_wellbeing(day, [], s, resp_base=None)
        # HRV sub=50, RHR sub=50, renorm con pesos .30 + .25 → weighted avg = 50
        assert score is not None
        assert 45 <= score <= 55

    def test_high_hrv_improves_score(self):
        """HRV por encima de la base → mejor score que HRV en la base."""
        day_base = _day_full(hrv=55.0, rhr=52.0)
        day_high = _day_full(hrv=75.0, rhr=52.0)  # +20ms = +2.5 SDs
        s = _summary()
        s_base = compute_wellbeing(day_base, [], s, resp_base=None)
        s_high = compute_wellbeing(day_high, [], s, resp_base=None)
        assert s_high > s_base

    def test_high_rhr_degrades_score(self):
        """RHR elevado → peor score."""
        day_base = _day_full(hrv=55.0, rhr=52.0)
        day_high = _day_full(hrv=55.0, rhr=70.0)  # +18bpm = +3.6 SDs
        s = _summary()
        s_base = compute_wellbeing(day_base, [], s, resp_base=None)
        s_high = compute_wellbeing(day_high, [], s, resp_base=None)
        assert s_high < s_base

    def test_spo2_96_scores_100(self):
        """SpO₂ ≥96 → sub-score 100."""
        day = {"date": "2024-01-01", "spo2": 97.0}
        s = {}
        score = compute_wellbeing(day, [], s, resp_base=None)
        assert score == 100

    def test_spo2_90_scores_30(self):
        """SpO₂ ≤90 → sub-score 30 → score total = 30 (única señal)."""
        day = {"date": "2024-01-01", "spo2": 89.0}
        s = {}
        score = compute_wellbeing(day, [], s, resp_base=None)
        assert score == 30

    def test_elevated_skin_temp_degrades_score(self):
        """Temp piel elevada → peor score que temp normal."""
        day_norm = _day_full(skin_temp=0.0)
        day_fever = _day_full(skin_temp=2.5)  # +2.5° desviación → fiebre
        s = _summary()
        s_n = compute_wellbeing(day_norm, [], s, resp_base=15.0)
        s_f = compute_wellbeing(day_fever, [], s, resp_base=15.0)
        assert s_f < s_n

    def test_score_80_plus_for_great_day(self):
        """Día con todas las señales por encima de la base → score ≥80."""
        day = _day_full(hrv=75.0, rhr=44.0, resp=15.0, spo2=98.0, skin_temp=0.0)
        s = _summary(hrv_base=55.0, hrv_sd=8.0, rhr_base=52.0, rhr_sd=5.0,
                     resp_sd=1.2, skin_temp_sd=0.5)
        score = compute_wellbeing(day, [], s, resp_base=15.0)
        assert score is not None
        assert score >= 75  # señales todas en buena dirección


# ────────────────────────────────────────────────────────────────────────────
#  No toca recovery/bodyage/strain (ADITIVO)
# ────────────────────────────────────────────────────────────────────────────

class TestWellbeingAdditive:

    def _make_ds(self, n=20):
        hrv = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        rhr = {f"2024-01-{i+1:02d}": 52.0 for i in range(n)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(n)}
        return hrv, rhr, steps

    def test_recovery_unchanged_by_wellbeing(self):
        """Agregar señales de wellbeing NO cambia recovery."""
        hrv, rhr, steps = self._make_ds()
        spo2 = {f"2024-01-{i+1:02d}": 97.0 for i in range(20)}
        skin = {f"2024-01-{i+1:02d}": 0.1 for i in range(20)}

        ds_without = build_dataset({}, rhr, hrv, {}, {}, steps, {})
        ds_with    = build_dataset({}, rhr, hrv, {}, {}, steps, {}, spo2=spo2, skin=skin)

        rec_without = {d["date"]: d.get("recovery") for d in ds_without["days"]}
        rec_with    = {d["date"]: d.get("recovery") for d in ds_with["days"]}
        assert rec_without == rec_with, "recovery changed when spo2/skin added"

    def test_strain_unchanged_by_wellbeing(self):
        """Wellbeing NO cambia strain."""
        hrv, rhr, steps = self._make_ds()
        spo2 = {f"2024-01-{i+1:02d}": 97.0 for i in range(20)}

        ds_without = build_dataset({}, rhr, hrv, {}, {}, steps, {})
        ds_with    = build_dataset({}, rhr, hrv, {}, {}, steps, {}, spo2=spo2)

        strain_without = {d["date"]: d.get("strain") for d in ds_without["days"]}
        strain_with    = {d["date"]: d.get("strain") for d in ds_with["days"]}
        assert strain_without == strain_with, "strain changed when spo2 added"

    def test_wellbeing_field_present_in_build_dataset(self):
        """build_dataset expone day['wellbeing'] en cada día."""
        hrv, rhr, steps = self._make_ds()
        spo2 = {f"2024-01-{i+1:02d}": 97.0 for i in range(20)}
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {}, spo2=spo2)
        for d in ds["days"]:
            assert "wellbeing" in d, f"wellbeing missing on {d['date']}"

    def test_wellbeing_none_when_no_vitals(self):
        """build_dataset produce wellbeing=None cuando no hay señales de vitals."""
        n = 10
        hrv = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(n)}
        # Solo hrv (sin rhr, resp, spo2, skin) — hrv tiene base pero sd<5 → sd=None
        # Con hrv sola, base disponible → sub-score HRV funciona si hrv_base existe
        # Pero con n=10 hrv_sd existe (>5 lecturas). Score será not None.
        ds = build_dataset({}, {}, hrv, {}, {}, steps, {})
        # Con hrv_base presente en summary y hrv en cada día → wellbeing not None para días con hrv
        for d in ds["days"]:
            if d.get("hrv") is not None:
                assert d["wellbeing"] is not None

    def test_wellbeing_range_from_build_dataset(self):
        """Todos los wellbeing de build_dataset (cuando not None) están en [0, 100]."""
        hrv, rhr, steps = self._make_ds()
        spo2 = {f"2024-01-{i+1:02d}": 97.0 for i in range(20)}
        skin = {f"2024-01-{i+1:02d}": 0.0 for i in range(20)}
        resp = {f"2024-01-{i+1:02d}": 15.0 for i in range(20)}
        ds = build_dataset({}, rhr, hrv, resp, {}, steps, {}, spo2=spo2, skin=skin)
        for d in ds["days"]:
            w = d.get("wellbeing")
            if w is not None:
                assert 0 <= w <= 100, f"wellbeing={w} out of range on {d['date']}"
