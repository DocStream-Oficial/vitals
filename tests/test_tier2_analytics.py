"""
test_tier2_analytics.py — Tests para las Features A/B/C del Roadmap Tier 2.

Feature A: z-scores (hrv_sd, rhr_sd, resp_sd, skin_temp_sd en summary;
           illness z-score con fallback absoluto).
Feature B: trends.py (linreg_slope, mann_kendall, trend_summary);
           mcp_tools.trends() añade _dir/_sig.
Feature C: load.py (hr_max, trimp_session, acwr, acwr_zone);
           scoring.py añade day["trimp"] y summary["acwr"/"acwr_zone"].
"""
from __future__ import annotations

import json
import math
import statistics
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent.parent / "data" / "health_compact.json"


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature B — app/trends.py ─────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestTrends:
    """Pruebas de las funciones puras de app/trends.py."""

    def test_linreg_slope_monotonic_rising(self):
        from app.trends import linreg_slope
        s = linreg_slope([1, 2, 3, 4, 5])
        assert abs(s - 1.0) < 1e-9

    def test_linreg_slope_monotonic_falling(self):
        from app.trends import linreg_slope
        s = linreg_slope([5, 4, 3, 2, 1])
        assert abs(s - (-1.0)) < 1e-9

    def test_linreg_slope_flat(self):
        from app.trends import linreg_slope
        s = linreg_slope([5.0] * 10)
        assert s == 0.0

    def test_linreg_slope_none_if_fewer_than_3(self):
        from app.trends import linreg_slope
        assert linreg_slope([]) is None
        assert linreg_slope([1.0]) is None
        assert linreg_slope([1.0, 2.0]) is None

    def test_linreg_slope_ignores_none_values(self):
        from app.trends import linreg_slope
        # None values are skipped; remaining [1,2,3] → slope=1
        s = linreg_slope([1.0, None, 2.0, None, 3.0])
        assert s is not None
        assert abs(s - 1.0) < 1e-9

    def test_mann_kendall_none_if_fewer_than_7(self):
        from app.trends import mann_kendall
        assert mann_kendall([]) is None
        assert mann_kendall([1, 2, 3, 4, 5, 6]) is None

    def test_mann_kendall_significant_rising(self):
        from app.trends import mann_kendall
        mk = mann_kendall([1, 2, 3, 4, 5, 6, 7])
        assert mk is not None
        assert mk["significant"] is True
        assert mk["z"] > 0

    def test_mann_kendall_significant_falling(self):
        from app.trends import mann_kendall
        mk = mann_kendall([7, 6, 5, 4, 3, 2, 1])
        assert mk is not None
        assert mk["significant"] is True
        assert mk["z"] < 0

    def test_mann_kendall_not_significant_flat(self):
        from app.trends import mann_kendall
        mk = mann_kendall([5.0] * 10)
        assert mk is not None
        assert mk["significant"] is False
        assert mk["z"] == 0.0

    def test_trend_summary_rising(self):
        from app.trends import trend_summary
        ts = trend_summary(list(range(1, 20)))
        assert ts["direction"] == "subiendo"
        assert ts["significant"] is True
        assert ts["slope"] is not None and ts["slope"] > 0
        assert ts["n"] == 19

    def test_trend_summary_falling(self):
        from app.trends import trend_summary
        ts = trend_summary(list(range(20, 0, -1)))
        assert ts["direction"] == "bajando"
        assert ts["significant"] is True

    def test_trend_summary_flat(self):
        from app.trends import trend_summary
        ts = trend_summary([50.0] * 30)
        assert ts["direction"] == "estable"
        assert ts["significant"] is False

    def test_trend_summary_few_points(self):
        from app.trends import trend_summary
        ts = trend_summary([1.0, 2.0])
        assert ts["direction"] == "estable"
        assert ts["significant"] is None  # n<7 → None

    def test_trend_summary_none_filtered(self):
        """None values in input are filtered out."""
        from app.trends import trend_summary
        ts = trend_summary([None, 1.0, None, 2.0, None])
        assert ts["n"] == 2  # only 2 valid values


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature C — app/load.py ───────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestLoad:
    """Pruebas de las funciones puras de app/load.py."""

    def test_hr_max_formula(self):
        from app.load import hr_max
        assert hr_max(40) == pytest.approx(185.4)
        assert hr_max(0) == 211.0
        assert hr_max(30) == pytest.approx(211 - 0.64 * 30)

    def test_trimp_none_if_missing_args(self):
        from app.load import trimp_session
        assert trimp_session(None, 150, 55, 40) is None
        assert trimp_session(60, None, 55, 40) is None
        assert trimp_session(60, 150, None, 40) is None
        assert trimp_session(60, 150, 55, None) is None

    def test_trimp_zero_when_hr_at_rest(self):
        """avg_hr = hr_rest → HRr=0 → TRIMP=0, not negative."""
        from app.load import trimp_session
        t = trimp_session(60, 55, 55, 40, sex="M")
        assert t == pytest.approx(0.0)

    def test_trimp_male_manual(self):
        from app.load import trimp_session, hr_max
        dur = 60; avg_hr = 150; hr_rest = 55; age = 40
        hmax = hr_max(age)
        hrr = max(0.0, min(1.0, (avg_hr - hr_rest) / (hmax - hr_rest)))
        factor = 0.64 * math.exp(1.92 * hrr)
        expected = dur * hrr * factor
        got = trimp_session(dur, avg_hr, hr_rest, age, sex="M")
        assert got == pytest.approx(expected, rel=1e-6)

    def test_trimp_female_manual(self):
        from app.load import trimp_session, hr_max
        dur = 45; avg_hr = 140; hr_rest = 58; age = 35
        hmax = hr_max(age)
        hrr = max(0.0, min(1.0, (avg_hr - hr_rest) / (hmax - hr_rest)))
        factor = 0.86 * math.exp(1.67 * hrr)
        expected = dur * hrr * factor
        got = trimp_session(dur, avg_hr, hr_rest, age, sex="F")
        assert got == pytest.approx(expected, rel=1e-6)

    def test_trimp_hrr_clamped_above_1(self):
        """avg_hr > hr_max should clamp HRr to 1.0."""
        from app.load import trimp_session, hr_max
        age = 40
        hmax = hr_max(age)  # 185.4
        t = trimp_session(30, 200, 55, age, sex="M")  # avg_hr > hr_max → hrr=1
        hrr = 1.0
        factor = 0.64 * math.exp(1.92 * hrr)
        expected = 30 * 1.0 * factor
        assert t == pytest.approx(expected, rel=1e-6)

    def test_acwr_none_if_too_few_days(self):
        from app.load import acwr
        assert acwr([1.0] * 13) is None   # <14 real days
        assert acwr([]) is None
        assert acwr([None] * 28) is None  # all None

    def test_acwr_none_if_chronic_zero(self):
        from app.load import acwr
        assert acwr([0.0] * 28) is None

    def test_acwr_uniform_loads(self):
        """28 days all=1.0 → ratio=1.0 (optimal zone)."""
        from app.load import acwr, acwr_zone
        r = acwr([1.0] * 28)
        assert r == pytest.approx(1.0)
        assert acwr_zone(r) == "optimo"

    def test_acwr_spike_ratio(self):
        """21 days baseline=1.0, then 7 days at 4.0 → high ACWR."""
        from app.load import acwr, acwr_zone
        loads = [1.0] * 21 + [4.0] * 7
        r = acwr(loads)
        # acute = 28; chronic = (21*1 + 7*4)/4 = 49/4 = 12.25; ratio = 28/12.25
        expected = 28.0 / 12.25
        assert r == pytest.approx(expected, rel=1e-6)
        assert acwr_zone(r) == "alto"

    def test_acwr_zone_boundaries(self):
        from app.load import acwr_zone
        assert acwr_zone(None) is None
        assert acwr_zone(0.7) == "detraining"
        assert acwr_zone(0.8) == "optimo"      # boundary inclusive
        assert acwr_zone(1.0) == "optimo"
        assert acwr_zone(1.3) == "optimo"      # boundary inclusive
        assert acwr_zone(1.31) == "precaucion"
        assert acwr_zone(1.5) == "precaucion"  # boundary inclusive
        assert acwr_zone(1.51) == "alto"

    def test_acwr_none_treated_as_missing(self):
        """None entries count as missing days, not as 0."""
        from app.load import acwr
        # 14 real days + 14 None = valid (14 >= 14)
        loads = [None] * 14 + [1.0] * 14
        r = acwr(loads)
        assert r is not None

    def test_acwr_14_boundary(self):
        """Exactly 14 real days → valid."""
        from app.load import acwr
        loads = [None] * 14 + [1.0] * 14
        r = acwr(loads)
        assert r is not None
        # 13 real → None
        loads2 = [None] * 15 + [1.0] * 13
        assert acwr(loads2) is None


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature A — _rolling_sd + SDs en summary ──────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestRollingSd:
    """Pruebas de _rolling_sd y los campos SDs en summary."""

    def test_rolling_sd_basic(self):
        from app.scoring import _rolling_sd
        series = {f"2024-01-{i+1:02d}": float(i) for i in range(10)}
        sd = _rolling_sd(series)
        assert sd is not None
        # pstdev of 0..9
        expected = statistics.pstdev(list(range(10)))
        assert abs(sd - expected) < 1e-6

    def test_rolling_sd_none_if_fewer_than_5(self):
        from app.scoring import _rolling_sd
        series = {"2024-01-01": 1.0, "2024-01-02": 2.0}
        assert _rolling_sd(series) is None

    def test_rolling_sd_uses_last_30(self):
        """rolling_sd with span=5 uses only last 5 values."""
        from app.scoring import _rolling_sd
        series = {f"2024-01-{i+1:02d}": float(i) for i in range(20)}
        sd_5 = _rolling_sd(series, span=5, min_n=5)
        expected = statistics.pstdev(list(range(15, 20)))
        assert abs(sd_5 - expected) < 1e-6

    def test_rolling_sd_ignores_none(self):
        from app.scoring import _rolling_sd
        series = {"2024-01-01": 55.0, "2024-01-02": None,
                  "2024-01-03": 60.0, "2024-01-04": 57.0,
                  "2024-01-05": 58.0, "2024-01-06": 62.0}
        sd = _rolling_sd(series)
        # Only non-None: [55, 60, 57, 58, 62]
        expected = statistics.pstdev([55.0, 60.0, 57.0, 58.0, 62.0])
        assert abs(sd - expected) < 1e-6

    def test_build_dataset_summary_has_sds(self):
        """build_dataset produce hrv_sd/rhr_sd/resp_sd/skin_temp_sd en summary."""
        from app.scoring import build_dataset
        hrv = {f"2024-01-{i+1:02d}": 50.0 + i for i in range(20)}
        rhr = {f"2024-01-{i+1:02d}": 48.0 + i * 0.1 for i in range(20)}
        resp = {f"2024-01-{i+1:02d}": 14.0 + i * 0.05 for i in range(20)}
        skin = {f"2024-01-{i+1:02d}": 35.0 + i * 0.05 for i in range(20)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(20)}
        ds = build_dataset({}, rhr, hrv, resp, {}, steps, {}, skin=skin)
        s = ds["summary"]
        assert s.get("hrv_sd") is not None
        assert s.get("rhr_sd") is not None
        assert s.get("resp_sd") is not None
        assert s.get("skin_temp_sd") is not None
        # All SDs should be positive
        assert s["hrv_sd"] > 0
        assert s["rhr_sd"] > 0

    def test_build_dataset_summary_sds_none_if_insufficient(self):
        """SDs are None when fewer than 5 readings exist."""
        from app.scoring import build_dataset
        hrv = {"2024-01-01": 55.0}  # only 1 day → <5 → None
        ds = build_dataset({}, {}, hrv, {}, {}, {}, {})
        s = ds["summary"]
        assert s.get("hrv_sd") is None


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature C (integración) — TRIMP en días + ACWR en summary ─────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestTRIMPIntegration:
    """Integración de TRIMP/ACWR en scoring.py."""

    def _make_base_ds(self, n=20):
        """20 días con hrv/rhr/steps, sin ejercicios."""
        hrv = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        rhr = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(n)}
        return hrv, rhr, steps

    def test_no_trimp_without_exercises(self):
        from app.scoring import build_dataset
        hrv, rhr, steps = self._make_base_ds()
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {})
        for d in ds["days"]:
            assert "trimp" not in d

    def test_trimp_added_when_exercise_present(self):
        from app.scoring import build_dataset
        from app.load import trimp_session, hr_max
        hrv, rhr, steps = self._make_base_ds()
        exercises = [{"date": "2024-01-10", "dur_min": 60, "avg_hr": 150}]
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {}, exercises=exercises, age=40, sex="M")
        days_by_date = {d["date"]: d for d in ds["days"]}
        d10 = days_by_date["2024-01-10"]
        assert "trimp" in d10
        # Manual check: hr_rest=55 (rhr of that day), age=40
        expected = round(trimp_session(60, 150, 55.0, 40, "M"), 2)
        assert d10["trimp"] == expected

    def test_trimp_aggregated_multiple_sessions(self):
        """Multiple sessions on same day are summed."""
        from app.scoring import build_dataset
        from app.load import trimp_session
        hrv, rhr, steps = self._make_base_ds()
        exercises = [
            {"date": "2024-01-05", "dur_min": 60, "avg_hr": 150},
            {"date": "2024-01-05", "dur_min": 30, "avg_hr": 130},
        ]
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {}, exercises=exercises, age=40)
        days_by_date = {d["date"]: d for d in ds["days"]}
        d5 = days_by_date["2024-01-05"]
        t1 = trimp_session(60, 150, 55.0, 40, "M")
        t2 = trimp_session(30, 130, 55.0, 40, "M")
        expected = round(t1 + t2, 2)
        assert d5["trimp"] == expected

    def test_trimp_absent_when_session_has_no_avg_hr(self):
        """Session without avg_hr should not add trimp for that session."""
        from app.scoring import build_dataset
        hrv, rhr, steps = self._make_base_ds()
        exercises = [{"date": "2024-01-05", "dur_min": 60}]  # no avg_hr
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {}, exercises=exercises, age=40)
        days_by_date = {d["date"]: d for d in ds["days"]}
        # No valid session → no trimp
        assert "trimp" not in days_by_date["2024-01-05"]

    def test_strain_changes_by_trimp_ronda5(self):
        """Ronda 5 (engine v2): agregar un ejercicio CON avg_hr real SÍ cambia el
        strain del día que lo tiene (y SOLO ese día) — es el punto de la ronda:
        strain v2 se basa en carga fisiológica TRIMP en vez del proxy lineal
        vigorous*0.10+steps/2500. Antes de v2 este test se llamaba
        test_strain_unchanged_by_trimp y afirmaba lo opuesto (trimp era un campo
        aditivo que NO tocaba strain); ese comportamiento era precisamente el
        proxy pobre que la Ronda 5 reemplaza. Ver ROADMAP-vitals-ronda5-engine-v2.md."""
        from app.scoring import build_dataset
        hrv, rhr, steps = self._make_base_ds()
        azm = {f"2024-01-{i+1:02d}": 20 for i in range(20)}
        exercises = [{"date": "2024-01-10", "dur_min": 60, "avg_hr": 150}]
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, azm, exercises=exercises, age=40)
        ds_no_ex = build_dataset({}, rhr, hrv, {}, {}, steps, azm)
        days1 = {d["date"]: d for d in ds["days"]}
        days2 = {d["date"]: d for d in ds_no_ex["days"]}
        for date in days1:
            if date == "2024-01-10":
                # El día con TRIMP real: strain v2 usa TRIMP (no vigorous*F_VIG,
                # anti doble-conteo) + NEAT de steps -> distinto del proxy v1.
                assert days1[date].get("trimp") is not None
                assert days1[date]["strain"] != days2[date]["strain"], (
                    "strain del día CON trimp real debe diferir del proxy v1-only"
                )
            else:
                # Días sin ejercicio: sin trimp -> misma carga (vigorous+steps) -> mismo strain.
                assert days1[date].get("strain") == days2[date].get("strain"), (
                    f"strain changed on {date} sin trimp — no debería"
                )

    def test_acwr_in_summary_with_enough_days(self):
        """summary has acwr and acwr_zone with >= 14 strain days."""
        from app.scoring import build_dataset
        # 28 days with steps → 28 strain values
        n = 28
        hrv = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        rhr = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(n)}
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {})
        s = ds["summary"]
        # All 28 days have strain=3.2 (8000/2500) → acwr=1.0 optimal
        assert s.get("acwr") is not None
        assert s.get("acwr_zone") is not None
        assert abs(s["acwr"] - 1.0) < 0.01
        assert s["acwr_zone"] == "optimo"

    def test_acwr_none_with_too_few_strain_days(self):
        """summary acwr is None when fewer than 14 days have strain."""
        from app.scoring import build_dataset
        # Only 10 days with steps → <14 strain days
        n = 10
        hrv = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        rhr = {f"2024-01-{i+1:02d}": 55.0 for i in range(n)}
        steps = {f"2024-01-{i+1:02d}": 8000 for i in range(n)}
        ds = build_dataset({}, rhr, hrv, {}, {}, steps, {})
        s = ds["summary"]
        assert s.get("acwr") is None


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature B (integración) — mcp_tools.trends() Tier2 fields ─────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestTrendsMCPIntegration:
    """mcp_tools.trends() añade _dir y _sig para recovery/hrv/rhr/sueño."""

    def _make_rising_ds(self, n=30):
        days = [{"date": f"2024-01-{i+1:02d}",
                 "recovery": 50 + i,
                 "hrv": 40.0 + i * 0.5,
                 "rhr": 60.0 - i * 0.2,
                 "strain": 10.0,
                 "asleep": 400 + i * 2} for i in range(n)]
        return {"days": days, "summary": {}}

    def test_trends_has_dir_sig_fields(self):
        from app.mcp_tools import trends
        t = trends(self._make_rising_ds())
        assert "recovery_pct_dir" in t
        assert "recovery_pct_sig" in t
        assert "hrv_ms_dir" in t
        assert "hrv_ms_sig" in t
        assert "rhr_bpm_dir" in t
        assert "sueno_h_dir" in t
        assert "sueno_h_sig" in t

    def test_trends_rising_recovery_direction(self):
        from app.mcp_tools import trends
        t = trends(self._make_rising_ds())
        assert t["recovery_pct_dir"] == "subiendo"
        assert t["recovery_pct_sig"] is True

    def test_trends_falling_rhr_direction(self):
        """rhr is designed to fall monotonically → bajando."""
        from app.mcp_tools import trends
        t = trends(self._make_rising_ds())
        assert t["rhr_bpm_dir"] == "bajando"

    def test_trends_flat_is_estable(self):
        from app.mcp_tools import trends
        days = [{"date": f"2024-01-{i+1:02d}",
                 "recovery": 65, "hrv": 55.0, "rhr": 50.0,
                 "strain": 10.0, "asleep": 440} for i in range(30)]
        t = trends({"days": days, "summary": {}})
        assert t["recovery_pct_dir"] == "estable"
        assert t["recovery_pct_sig"] is False

    def test_trends_existing_fields_preserved(self):
        """Tier 1 fields (7d/30d means, noches_menos_7h) still present."""
        from app.mcp_tools import trends
        t = trends(self._make_rising_ds())
        assert "recovery_pct_7d" in t
        assert "recovery_pct_30d" in t
        assert "noches_menos_7h" in t


# ══════════════════════════════════════════════════════════════════════════════
# ── Feature A (insights z-score) ──────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestIllnessZScore:
    """Verifica que illness_early_warning usa z-score con fallback absoluto."""

    def test_fallback_absolute_when_no_sd(self):
        """Sin hrv_sd/rhr_sd en summary → fallback absoluto (mismo comportamiento anterior)."""
        from app.insights import evaluate
        # rhr 58 > 50+5=55 → fires; hrv 44 < 57*0.85=48.45 → fires
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 50.0, "hrv": 57.0} for i in range(4)]
        days.append({"date": "2024-01-05", "rhr": 58.0, "hrv": 44.0})
        summary = {"hrv_base": 57.0, "rhr_base": 50.0}  # no SDs
        results = evaluate({"days": days, "summary": summary})
        ids = [r["id"] for r in results]
        assert "illness_early_warning" in ids
        insight = next(r for r in results if r["id"] == "illness_early_warning")
        assert insight["severity"] == "watch"

    def test_zscore_fires_when_sd_large(self):
        """Con SD grande, una desviación >1.5 SD dispara (aunque < umbral absoluto)."""
        from app.insights import evaluate
        # rhr_base=50, rhr_sd=3.0 → z=(53-50)/3=1.0 no fire; z=(55-50)/3=1.67 → fires
        # hrv_base=60, hrv_sd=4.0 → z=(53-60)/4=-1.75 < -1.5 → fires
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 50.0, "hrv": 60.0} for i in range(4)]
        days.append({"date": "2024-01-05", "rhr": 55.0, "hrv": 53.0})
        # rhr delta = 5 (equals old absolute +5 but z=5/3=1.67 > 1.5 fires)
        # hrv delta = -7 → 53/60 = 0.88 > 0.85 (would NOT fire with absolute threshold)
        # but with z = (53-60)/4 = -1.75 → fires
        summary = {"hrv_base": 60.0, "rhr_base": 50.0, "hrv_sd": 4.0, "rhr_sd": 3.0}
        results = evaluate({"days": days, "summary": summary})
        ids = [r["id"] for r in results]
        assert "illness_early_warning" in ids

    def test_zscore_no_fire_within_1_5sd(self):
        """Valores dentro de 1.5 SD no disparan (aunque excedan umbral absoluto bajo SD grande)."""
        from app.insights import evaluate
        # rhr_base=50, rhr_sd=5.0 → z=(53-50)/5=0.6 < 1.5 → no fire
        # hrv_base=60, hrv_sd=10.0 → z=(55-60)/10=-0.5 > -1.5 → no fire
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 50.0, "hrv": 60.0} for i in range(4)]
        days.append({"date": "2024-01-05", "rhr": 53.0, "hrv": 55.0})
        summary = {"hrv_base": 60.0, "rhr_base": 50.0, "hrv_sd": 10.0, "rhr_sd": 5.0}
        results = evaluate({"days": days, "summary": summary})
        ids = [r["id"] for r in results]
        assert "illness_early_warning" not in ids

    def test_sd_zero_falls_back_to_absolute(self):
        """SD=0 → fallback absoluto (sin división por cero)."""
        from app.insights import evaluate
        # All days same rhr=50. rhr_sd=0 → fallback: 58>50+5 → fires
        # hrv_sd=0 → fallback: 44<57*0.85=48.45 → fires
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 50.0, "hrv": 57.0} for i in range(4)]
        days.append({"date": "2024-01-05", "rhr": 58.0, "hrv": 44.0})
        summary = {"hrv_base": 57.0, "rhr_base": 50.0, "hrv_sd": 0.0, "rhr_sd": 0.0}
        results = evaluate({"days": days, "summary": summary})
        ids = [r["id"] for r in results]
        assert "illness_early_warning" in ids


# ══════════════════════════════════════════════════════════════════════════════
# ── Asserts sobre golden (datos reales) ───────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestGoldenAsserts:
    """Verifica invariantes sobre data/health_compact.json.

    Nota: estos tests son sobre el golden TAL COMO ESTÁ — no requieren
    regenerar el archivo. Comprueban que los campos Tier 1 siguen presentes
    y que las fórmulas de scoring producen los mismos resultados.
    """

    @pytest.fixture(scope="class")
    def golden(self):
        if not GOLDEN.exists():
            pytest.skip("Golden file not found")
        with open(GOLDEN) as f:
            return json.load(f)

    def test_golden_has_tier1_fields(self, golden):
        """Campos Tier 1 siguen presentes en el golden."""
        s = golden["summary"]
        assert "hrv_base" in s
        assert "rhr_base" in s
        assert "hrv_base_recent" in s
        assert "rhr_base_recent" in s
        assert "bodyage" in s
        # recovery_n en días con recovery
        days_with_rec = [d for d in golden["days"] if d.get("recovery") is not None]
        assert len(days_with_rec) > 0
        for d in days_with_rec[:5]:  # check first 5
            assert "recovery_n" in d, f"recovery_n missing on {d['date']}"

    def test_golden_strain_formula_intact(self, golden):
        """strain v2 (Ronda 5): L = trimp_day, o vigorous*F_VIG si no hay trimp
        (anti doble-conteo) + steps/F_STEPS; strain = 21*(1-exp(-L/K)). Recalcula
        con las constantes EN VIVO del módulo (mismo estilo que test_regression.py
        test 2) — para el gate anti-mutación real ver
        test_regression.py::test_build_dataset_reproduces_golden_days."""
        from app.scoring import _strain_v2

        checked = 0
        for d in golden["days"]:
            if d.get("strain") is None:
                continue
            expected = _strain_v2(d.get("trimp"), d.get("vigorous"), d.get("steps"))
            assert d["strain"] == expected, (
                f"strain formula mismatch on {d['date']}: got {d['strain']}, expected {expected}"
            )
            checked += 1
        assert checked > 0, "el golden no tiene ningún día con strain — nada se verificó"

    def test_golden_acwr_zone_plausible(self, golden):
        """Si el golden tiene acwr, debe estar en una zona válida."""
        s = golden.get("summary", {})
        if s.get("acwr") is not None:
            zone = s.get("acwr_zone")
            assert zone in ("detraining", "optimo", "precaucion", "alto"), (
                f"acwr_zone value unexpected: {zone}"
            )
            acwr_val = s["acwr"]
            assert 0.0 < acwr_val < 10.0, f"acwr value implausible: {acwr_val}"

    def test_golden_sds_positive_or_none(self, golden):
        """SDs del golden son positivas o None — nunca negativas."""
        s = golden.get("summary", {})
        for field in ("hrv_sd", "rhr_sd", "resp_sd", "skin_temp_sd"):
            v = s.get(field)
            if v is not None:
                assert v > 0, f"{field} should be positive, got {v}"
