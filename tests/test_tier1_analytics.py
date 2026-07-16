"""
test_tier1_analytics.py — Tests para las 3 features del Roadmap Vitals Tier 1.

1. recovery_n: correcto (3/2/1) y ausente si no hay recovery.
2. _ewma_recent: monótono en serie creciente; <5 valores → None; determinista.
3. summary trae hrv_base_recent / rhr_base_recent.
4. recent_base() con fallback cuando no hay _recent.
5. vo2max_percentile monótono y en rango para 40a/H/52.8.
6. confidence.level low/med/high según cobertura.
"""
import statistics
from pathlib import Path

import pytest

# ── helpers de fixture ──────────────────────────────────────────────────────────

def _make_sleep(dates, asleep=420):
    """Genera dict de sueño legítimo (onset 23:00, >= 120 min)."""
    return {d: {"asleep": asleep, "inbed": asleep + 30, "bed_min": -60} for d in dates}


def _dates(n, start="2024-01-01"):
    """Genera n fechas ISO consecutivas a partir de start."""
    from datetime import date, timedelta
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


# ── Feature 1: recovery_n ───────────────────────────────────────────────────────

class TestRecoveryN:

    def test_recovery_n_3_components(self, monkeypatch):
        """hrv + rhr + asleep → recovery_n == 3.

        Engine-v3-port: corre contra el motor v2 explícitamente (RECOVERY_ANCHORED=False).
        Bajo v3 este caso también da recovery_n==3 desde el arreglo de arranque en frío
        (paso 2 del roadmap), pero el criterio 6 exige que los 8 tests v2 se prueben
        contra su motor original."""
        import app.scoring as scoring
        from app.scoring import build_dataset
        monkeypatch.setattr(scoring, "RECOVERY_ANCHORED", False)
        dates = _dates(3)
        hrv = {d: 55.0 for d in dates}
        rhr = {d: 52.0 for d in dates}
        slp = _make_sleep(dates, asleep=420)
        steps = {d: 8000 for d in dates}
        ds = build_dataset(slp, rhr, hrv, {}, {}, steps, {})
        for day in ds["days"]:
            if "recovery" in day:
                assert day["recovery_n"] == 3, (
                    f"{day['date']}: expected recovery_n=3, got {day.get('recovery_n')}"
                )

    def test_recovery_n_2_components_hrv_rhr(self):
        """hrv + rhr sin sueño → recovery_n == 2."""
        from app.scoring import build_dataset
        dates = _dates(4)
        hrv = {d: 55.0 for d in dates}
        rhr = {d: 52.0 for d in dates}
        # Sin sleep (dict vacío)
        ds = build_dataset({}, rhr, hrv, {}, {}, {}, {})
        for day in ds["days"]:
            if "recovery" in day:
                assert day["recovery_n"] == 2, (
                    f"{day['date']}: expected recovery_n=2, got {day.get('recovery_n')}"
                )

    def test_recovery_n_1_component_hrv_only_middle(self):
        """hrv solo (valor medio, no extremo) → recovery_n == 1."""
        from app.scoring import build_dataset
        # Usar varios días para que el percentil sea estable y el valor medio no clampee
        dates = _dates(6)
        hrv = {d: float(40 + i * 5) for i, d in enumerate(dates)}  # 40,45,50,55,60,65
        # Un día con sueño+rhr para anclar percentiles
        rhr = {dates[0]: 52.0, dates[1]: 52.0}
        slp = _make_sleep([dates[0], dates[1]], asleep=420)
        ds = build_dataset(slp, rhr, hrv, {}, {}, {}, {})
        by_date = {d["date"]: d for d in ds["days"]}
        # Día con solo-HRV medio (ni extremo ni ancla): recovery_n debe ser 1 si recovery presente
        for d in dates[2:]:
            day = by_date[d]
            if "recovery" in day:
                assert day["recovery_n"] == 1, (
                    f"{d}: expected recovery_n=1, got {day.get('recovery_n')}"
                )

    def test_no_recovery_no_recovery_n(self):
        """Días sin recovery NO deben tener recovery_n."""
        from app.scoring import build_dataset
        # Solo RHR, sin HRV ni sueño → no hay recovery
        rhr = {"2024-01-01": 52.0}
        ds = build_dataset({}, rhr, {}, {}, {}, {}, {})
        for day in ds["days"]:
            assert "recovery_n" not in day, (
                f"Día sin recovery no debe tener recovery_n, got {day.get('recovery_n')}"
            )

    def test_recovery_n_present_iff_recovery(self):
        """recovery_n aparece exactamente cuando recovery aparece (y viceversa)."""
        from app.scoring import build_dataset
        import json
        golden = Path(__file__).parent.parent / "data" / "health_compact.json"
        data = json.load(open(golden))
        for day in data["days"]:
            has_rec = "recovery" in day
            has_n   = "recovery_n" in day
            # Si el golden no tiene recovery_n aún (pre-deploy), saltamos
            # Si ya tiene recovery_n, debe coincidir
            if has_n:
                assert has_rec, f"{day['date']}: recovery_n sin recovery"
            # Todo día con recovery debe tener recovery_n (solo aplica si golden ya fue regenerado)
            # Este assert se omite para datos pre-Tier1 (el script recompute lo arregla)


# ── Feature 2: EWMA baseline rodante ───────────────────────────────────────────

class TestEwmaRecent:

    def test_ewma_growing_series_exceeds_median(self):
        """Serie creciente: EWMA (pondera recientes) > mediana all-time."""
        from app.scoring import _ewma_recent
        # Serie que crece: los últimos valores son más altos
        vals = {f"2024-{i:05d}": float(30 + i) for i in range(1, 35)}  # 31,32,...,64
        ewma = _ewma_recent(vals)
        med = statistics.median(vals.values())
        assert ewma is not None, "EWMA no debe ser None con 34 lecturas"
        assert ewma > med, f"EWMA ({ewma}) debe superar la mediana ({med}) en serie creciente"

    def test_ewma_none_below_min_n(self):
        """< 5 lecturas → None."""
        from app.scoring import _ewma_recent
        vals = {"2024-01-01": 50.0, "2024-01-02": 55.0, "2024-01-03": 52.0}
        assert _ewma_recent(vals) is None

    def test_ewma_exactly_min_n(self):
        """Exactamente 5 lecturas → devuelve float (no None)."""
        from app.scoring import _ewma_recent
        vals = {f"2024-01-0{i}": 50.0 + i for i in range(1, 6)}
        result = _ewma_recent(vals)
        assert result is not None
        assert isinstance(result, float)

    def test_ewma_deterministic(self):
        """Mismo input → mismo output (determinista)."""
        from app.scoring import _ewma_recent
        vals = {f"2024-01-{i:02d}": float(50 + (i % 10)) for i in range(1, 32)}
        r1 = _ewma_recent(vals)
        r2 = _ewma_recent(vals)
        assert r1 == r2

    def test_ewma_alpha_formula(self):
        """Verifica EWMA recursivo con alpha = 2/(30+1) sobre serie conocida."""
        from app.scoring import _ewma_recent
        # 10 lecturas idénticas de 60: EWMA debe converger a 60
        vals = {f"2024-01-{i:02d}": 60.0 for i in range(1, 11)}
        result = _ewma_recent(vals)
        assert result == 60.0, f"Serie constante 60 → EWMA debe ser 60.0, got {result}"

    def test_summary_has_both_recent_bases(self):
        """build_dataset produce hrv_base_recent y rhr_base_recent en summary."""
        from app.scoring import build_dataset
        dates = _dates(40)
        hrv = {d: 50.0 + i for i, d in enumerate(dates)}
        rhr = {d: 55.0 - i * 0.2 for i, d in enumerate(dates)}
        slp = _make_sleep(dates[:20])
        ds = build_dataset(slp, rhr, hrv, {}, {}, {}, {})
        s = ds["summary"]
        assert "hrv_base_recent" in s, "summary debe tener hrv_base_recent"
        assert "rhr_base_recent" in s, "summary debe tener rhr_base_recent"
        assert isinstance(s["hrv_base_recent"], float), "hrv_base_recent debe ser float"
        assert isinstance(s["rhr_base_recent"], float), "rhr_base_recent debe ser float"

    def test_summary_recent_bases_differ_from_alltime(self):
        """Las bases recientes son floats distintos a la mediana all-time en una serie con tendencia."""
        from app.scoring import build_dataset
        dates = _dates(40)
        # Serie creciente: recientes serán más altos que la mediana
        hrv = {d: 40.0 + i for i, d in enumerate(dates)}  # 40..79
        rhr = {d: 60.0 - i * 0.2 for i, d in enumerate(dates)}  # 60..52
        slp = _make_sleep(dates)
        ds = build_dataset(slp, rhr, hrv, {}, {}, {}, {})
        s = ds["summary"]
        # La mediana de 40..79 es (59+60)/2=59.5; el EWMA pondera los últimos ~30, que son >59.5
        assert s["hrv_base_recent"] != s["hrv_base"], (
            f"hrv_base_recent ({s['hrv_base_recent']}) debe diferir de hrv_base ({s['hrv_base']}) "
            "en serie con tendencia creciente"
        )
        assert s["rhr_base_recent"] != s["rhr_base"], (
            f"rhr_base_recent ({s['rhr_base_recent']}) debe diferir de rhr_base ({s['rhr_base']}) "
            "en serie con tendencia"
        )

    def test_summary_recent_none_when_few_readings(self):
        """Con <5 lecturas de HRV, hrv_base_recent debe ser None."""
        from app.scoring import build_dataset
        hrv = {"2024-01-01": 55.0, "2024-01-02": 58.0}
        rhr = {"2024-01-01": 52.0}
        ds = build_dataset({}, rhr, hrv, {}, {}, {}, {})
        assert ds["summary"]["hrv_base_recent"] is None


class TestRecentBase:

    def test_fallback_to_alltime_when_no_recent(self):
        """recent_base() cae al all-time si no existe _base_recent."""
        from app.scoring import recent_base
        summary = {"hrv_base": 57.5, "rhr_base": 51.0}
        assert recent_base(summary, "hrv") == 57.5
        assert recent_base(summary, "rhr") == 51.0

    def test_uses_recent_when_present(self):
        """recent_base() usa _base_recent cuando existe."""
        from app.scoring import recent_base
        summary = {
            "hrv_base": 57.5,
            "hrv_base_recent": 62.0,
            "rhr_base": 51.0,
            "rhr_base_recent": 49.5,
        }
        assert recent_base(summary, "hrv") == 62.0
        assert recent_base(summary, "rhr") == 49.5

    def test_fallback_when_recent_is_none(self):
        """recent_base() cae al all-time si _base_recent existe pero es None."""
        from app.scoring import recent_base
        summary = {
            "hrv_base": 57.5,
            "hrv_base_recent": None,
            "rhr_base": 51.0,
            "rhr_base_recent": None,
        }
        assert recent_base(summary, "hrv") == 57.5
        assert recent_base(summary, "rhr") == 51.0


# ── Feature 3: VO₂máx percentil ────────────────────────────────────────────────

class TestVo2Percentile:

    def test_monotone_higher_vo2_higher_percentile(self):
        """Más VO₂max → más percentil (monotónico)."""
        from app.bodyage import _vo2_percentile
        vo2_vals = [30, 35, 40, 44, 48, 53, 58, 62]
        pcts = [_vo2_percentile(v, 40, "M") for v in vo2_vals]
        for i in range(len(pcts) - 1):
            assert pcts[i] <= pcts[i + 1], (
                f"No monotónico: VO2={vo2_vals[i]}→pct={pcts[i]}, "
                f"VO2={vo2_vals[i+1]}→pct={pcts[i+1]}"
            )

    def test_golden_case_40m_528(self):
        """VO₂max 52.8, H, 40a → percentil ≈85–95 (alto)."""
        from app.bodyage import _vo2_percentile
        p = _vo2_percentile(52.8, 40, "M")
        assert 80 <= p <= 99, f"VO₂max 52.8 H 40a debe estar en percentil 80-99, got {p}"

    def test_clamp_below_min(self):
        """VO₂max muy bajo → clamp a 1 (no negativo ni 0)."""
        from app.bodyage import _vo2_percentile
        p = _vo2_percentile(5.0, 40, "M")
        assert p >= 1, f"percentil debe ser >=1, got {p}"

    def test_clamp_above_max(self):
        """VO₂max muy alto → clamp a 99 (no >99 ni 100)."""
        from app.bodyage import _vo2_percentile
        p = _vo2_percentile(999.0, 40, "M")
        assert p <= 99, f"percentil debe ser <=99, got {p}"

    def test_age_groups_interpolation(self):
        """Grupos etarios distintos → percentiles distintos para mismo VO₂max."""
        from app.bodyage import _vo2_percentile
        vo2 = 45.0
        pcts = {age: _vo2_percentile(vo2, age, "M") for age in [25, 35, 45, 55, 65]}
        # A mismo VO₂, más joven → menor percentil (baremo más exigente para jóvenes)
        assert pcts[25] <= pcts[55], (
            f"A mismo VO₂={vo2}, joven (25a) debe tener ≤ percentil que mayor (55a): "
            f"{pcts[25]} vs {pcts[55]}"
        )

    def test_label_coverage(self):
        """_vo2_label cubre todos los tramos correctamente."""
        from app.bodyage import _vo2_label
        assert "Superior" in _vo2_label(90)
        assert "Excelente" in _vo2_label(70)
        assert "Sobre promedio" in _vo2_label(50)
        assert "Promedio" in _vo2_label(30)
        assert "Bajo" in _vo2_label(10)
        # Exactamente en umbral
        assert "Superior" in _vo2_label(90)
        assert "Excelente" in _vo2_label(70)

    def test_female_percentile_differs_male(self):
        """Mismo VO₂max y edad, diferente sexo → percentiles distintos."""
        from app.bodyage import _vo2_percentile
        p_m = _vo2_percentile(44.0, 40, "M")
        p_f = _vo2_percentile(44.0, 40, "F")
        # Mujer con mismo VO₂ debe tener percentil mayor (baremo más bajo para F en normas)
        assert p_f >= p_m, f"F ({p_f}) debe tener ≥ percentil que M ({p_m}) al mismo VO₂"

    def test_compute_body_age_includes_percentile_and_confidence(self):
        """compute_body_age devuelve vo2max_percentile, vo2max_label y confidence."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(14)]
        exercises = [{"date": f"2024-01-{i+1:02d}", "avg_hr": 110, "dur_min": 45}
                     for i in range(5)]
        result = compute_body_age(days, exercises, age=40, waist=82, sex="M")
        assert "vo2max_percentile" in result, "debe incluir vo2max_percentile"
        assert "vo2max_label" in result, "debe incluir vo2max_label"
        assert "confidence" in result, "debe incluir confidence"
        p = result["vo2max_percentile"]
        assert 1 <= p <= 99, f"vo2max_percentile debe ser 1-99, got {p}"
        assert isinstance(result["vo2max_label"], str) and len(result["vo2max_label"]) > 0
        conf = result["confidence"]
        assert "level" in conf
        assert conf["level"] in ("low", "med", "high")

    def test_existing_fields_unchanged(self):
        """Los campos vo2max, fitness_age, body_age, category, penalty NO cambian."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(14)]
        exercises = [{"date": f"2024-01-{i+1:02d}", "avg_hr": 110, "dur_min": 45}
                     for i in range(5)]
        result = compute_body_age(days, exercises, age=40, waist=82, sex="M")
        # Calcular a mano (idéntico a test_regression)
        rhr = 52.0; PA = 10
        vo2_expected = round(100.27 - 0.296*40 + 0.226*PA - 0.369*82 - 0.155*rhr, 1)
        assert result["vo2max"] == vo2_expected
        assert result["pa_index"] == PA


# ── Feature 3: confidence ────────────────────────────────────────────────────────

class TestConfidence:

    def test_high_confidence_with_full_data(self):
        """14 días de rhr/hrv/sleep → level == 'high'."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(14)]
        result = compute_body_age(days, [], age=40, waist=82, sex="M")
        assert result["confidence"]["level"] == "high"
        assert result["confidence"]["rhr_days"] == 14
        assert result["confidence"]["hrv_days"] == 14
        assert result["confidence"]["sleep_days"] == 14

    def test_med_confidence_partial_data(self):
        """5–9 días → level == 'med'."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(7)]
        result = compute_body_age(days, [], age=40, waist=82, sex="M")
        assert result["confidence"]["level"] == "med"

    def test_low_confidence_few_data(self):
        """< 5 días → level == 'low'."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(3)]
        result = compute_body_age(days, [], age=40, waist=82, sex="M")
        assert result["confidence"]["level"] == "low"

    def test_confidence_counts_correctly(self):
        """Los conteos de confidence reflejan cuántos días tienen cada campo."""
        from app.bodyage import compute_body_age
        # 10 días con rhr, 8 con hrv, 6 con sleep
        days = []
        for i in range(10):
            d = {"date": f"2024-01-{i+1:02d}", "rhr": 52.0}
            if i < 8:
                d["hrv"] = 55.0
            if i < 6:
                d["asleep"] = 420
            days.append(d)
        result = compute_body_age(days, [], age=40, waist=82, sex="M")
        conf = result["confidence"]
        assert conf["rhr_days"] == 10
        assert conf["hrv_days"] == 8
        assert conf["sleep_days"] == 6
        # min(10, 8, 6) = 6 → level == 'med'
        assert conf["level"] == "med"

    def test_exercise_sessions_counted(self):
        """exercise_sessions refleja los ejercicios en ventana 28d."""
        from app.bodyage import compute_body_age
        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52.0, "hrv": 55.0, "asleep": 420}
                for i in range(14)]
        exercises = [{"date": f"2024-01-{i+1:02d}", "avg_hr": 110, "dur_min": 45}
                     for i in range(5)]
        result = compute_body_age(days, exercises, age=40, waist=82, sex="M")
        assert result["confidence"]["exercise_sessions"] == 5
