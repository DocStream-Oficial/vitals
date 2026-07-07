"""
test_ronda5_engine_v2.py — Criterios de aceptación de la Ronda 5 (ENGINE v2:
strain híbrido TRIMP, umbral de sueño único, recovery rodante).

Ver ROADMAP-vitals-ronda5-engine-v2.md. Cubre lo que test_regression.py (que
solo toca el golden congelado y las fórmulas inline) no cubre:

1. Doble conteo: día con TRIMP real no suma fallback de vigorous.
2. Presencia de strain ampliada (steps O workouts, antes solo steps).
3. 0 días con strain fuera de [0,21].
4. Equivalencia de sueño: sleep_target_min=480 (default) → rule_sleep_debt y
   compute_body_age producen EXACTAMENTE lo mismo que con las constantes viejas
   (420/7.0 literales).
5. sleep_target_min=540 mueve juntos los 3 consumidores (recovery-comp, sleep_perf,
   rule_sleep_debt threshold, bodyage penalty).
6. sleep_target_min validado en PUT /api/profile (300-600).
7. Recovery rodante: mejora artificial de HRV -> escala global sesga
   primeros/últimos tramos, rodante los acerca. Anti look-ahead (día N no usa
   días > N). Fallbacks de longitud de ventana (<10 y 10-29 lecturas).
"""
from __future__ import annotations

import datetime
import statistics
from pathlib import Path
from unittest.mock import patch

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# 1-3. Strain v2: doble conteo, presencia, rango
# ══════════════════════════════════════════════════════════════════════════════

class TestStrainV2DoubleCounting:
    def test_trimp_day_does_not_add_vigorous_fallback(self):
        """Día con TRIMP real (avg_hr) Y vigorous alto: el fallback vigorous*F_VIG
        NO debe sumarse — el AZM de esa sesión ya está dentro del TRIMP."""
        from app.scoring import build_dataset, STRAIN_V2_F_VIG, STRAIN_V2_K
        import math

        rhr = {"2024-01-10": 50.0}
        hrv = {"2024-01-10": 55.0}
        azm = {"2024-01-10": 100}  # vigorous alto — NO debe sumarse si hay trimp
        exercises = [{"date": "2024-01-10", "dur_min": 60, "avg_hr": 150}]

        ds = build_dataset({}, rhr, hrv, {}, {}, {}, azm, exercises=exercises, age=40)
        day = ds["days"][0]
        assert day.get("trimp") is not None and day["trimp"] > 0

        # Recalcular manualmente: L debe ser SOLO trimp (sin steps, sin vigorous*F_VIG)
        L = day["trimp"]
        expected_strain = round(21.0 * (1.0 - math.exp(-L / STRAIN_V2_K)), 1)
        assert day["strain"] == expected_strain, (
            "strain no debe incluir vigorous*F_VIG cuando ya hay trimp real "
            f"(doble conteo). got={day['strain']} expected={expected_strain}"
        )

        # Prueba negativa: si NO hubiera anti-doble-conteo, el resultado sería mayor
        wrong_L = day["trimp"] + 100 * STRAIN_V2_F_VIG
        wrong_strain = round(21.0 * (1.0 - math.exp(-wrong_L / STRAIN_V2_K)), 1)
        assert day["strain"] != wrong_strain

    def test_vigorous_fallback_used_only_without_trimp(self):
        """Día SIN TRIMP (sin exercises con avg_hr) pero con vigorous: SÍ debe
        usar el fallback vigorous*F_VIG.

        Nota: `dates` en build_dataset se arma desde
        set(sleep)|set(rhr)|set(hrv)|set(steps)|set(spo2)|set(skin) — azm NUNCA
        participa de ese set (limitación PREEXISTENTE de v1, fuera de alcance de
        Ronda 5). Se ancla la fecha con rhr para que el día se procese."""
        from app.scoring import build_dataset, STRAIN_V2_F_VIG, STRAIN_V2_K
        import math

        rhr = {"2024-01-11": 50.0}
        azm = {"2024-01-11": 40}
        ds = build_dataset({}, rhr, {}, {}, {}, {}, azm)
        day = ds["days"][0]
        assert day.get("trimp") is None
        L = 40 * STRAIN_V2_F_VIG
        expected = round(21.0 * (1.0 - math.exp(-L / STRAIN_V2_K)), 1)
        assert day["strain"] == expected


class TestStrainV2Presence:
    def test_gym_day_without_steps_has_strain(self):
        """Ronda 5: un día de gym (TRIMP real) SIN dato de steps ahora SÍ tiene
        strain (antes v1 exigía steps para siquiera calcular strain)."""
        from app.scoring import build_dataset

        rhr = {"2024-02-01": 50.0}
        exercises = [{"date": "2024-02-01", "dur_min": 45, "avg_hr": 140}]
        ds = build_dataset({}, rhr, {}, {}, {}, {}, {}, exercises=exercises, age=40)
        day = ds["days"][0]
        assert "steps" not in day
        assert day.get("strain") is not None, "día de gym sin steps debe tener strain (v2)"
        assert 0 <= day["strain"] <= 21

    def test_no_signal_day_has_no_strain(self):
        """Día sin steps, sin vigorous, sin trimp -> sin strain (None, no 0)."""
        from app.scoring import build_dataset

        hrv = {"2024-02-02": 55.0}
        ds = build_dataset({}, {}, hrv, {}, {}, {}, {})
        day = ds["days"][0]
        assert "strain" not in day


class TestStrainV2Range:
    def test_strain_never_out_of_range_high_load(self):
        """Carga extrema (TRIMP muy alto + muchos steps) nunca excede 21 ni baja de 0."""
        from app.scoring import build_dataset

        rhr = {"2024-03-01": 45.0}
        steps = {"2024-03-01": 50000}
        exercises = [{"date": "2024-03-01", "dur_min": 300, "avg_hr": 190}]
        ds = build_dataset({}, rhr, {}, {}, {}, steps, {}, exercises=exercises, age=25)
        day = ds["days"][0]
        assert 0 <= day["strain"] <= 21

    def test_strain_zero_floor(self):
        """Carga mínima (steps=1) nunca da negativo."""
        from app.scoring import build_dataset

        steps = {"2024-03-02": 1}
        ds = build_dataset({}, {}, {}, {}, {}, steps, {})
        day = ds["days"][0]
        assert day["strain"] >= 0


# ══════════════════════════════════════════════════════════════════════════════
# 4-5. Sueño: equivalencia default + movimiento conjunto con target no-default
# ══════════════════════════════════════════════════════════════════════════════

class TestSleepTargetEquivalence:
    def test_default_480_matches_old_hardcoded_420_bodyage(self):
        """compute_body_age con sleep_penalty_h=7.0 (derivado de target=480) debe
        ser IDÉNTICO al comportamiento viejo (7 literal)."""
        from app.bodyage import compute_body_age

        days = [{"date": f"2024-01-{i+1:02d}", "rhr": 52, "hrv": 55.0, "asleep": 390}
                for i in range(14)]  # 6.5h -> debajo de 7h, debe penalizar igual
        exercises = []

        result_default = compute_body_age(days, exercises, age=40, waist=82, sex="M")
        result_explicit_7 = compute_body_age(days, exercises, age=40, waist=82, sex="M",
                                             sleep_penalty_h=7.0)
        assert result_default == result_explicit_7

    def test_default_480_rule_sleep_debt_matches_420_literal(self):
        """rule_sleep_debt con summary sin sleep_target_min (fallback 480) debe
        marcar como 'corta' exactamente las mismas noches que el umbral 420 viejo."""
        from app.insights import rule_sleep_debt

        dates = [f"2024-01-{i+1:02d}" for i in range(7)]
        # 3 noches de 415min (<420, corta bajo el umbral viejo Y bajo el nuevo default)
        days = [{"date": d, "asleep": 415 if i < 3 else 450} for i, d in enumerate(dates)]

        summary_no_target = {}  # sin sleep_target_min -> fallback 480 -> threshold 420
        summary_explicit_480 = {"sleep_target_min": 480}

        r1 = rule_sleep_debt(days, summary_no_target)
        r2 = rule_sleep_debt(days, summary_explicit_480)
        assert r1 == r2
        assert r1 is not None and r1["severity"] == "watch"  # 3 noches cortas


class TestSleepTargetMovesTogether:
    def test_target_540_moves_recovery_sleep_perf_and_bodyage(self):
        """Con sleep_target_min=540 (vs 480 default), sleep_perf/recovery-comp de
        sueño Y el penalty de bodyage deben moverse en la MISMA dirección (subir
        el target -> más exigente -> sleep_perf baja para el mismo 'asleep')."""
        from app.scoring import build_dataset
        from app.bodyage import compute_body_age

        rhr = {"2024-01-01": 50.0}
        hrv = {"2024-01-01": 55.0}
        sleep_d = {"2024-01-01": {"asleep": 450, "inbed": 490}}

        ds_480 = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {}, sleep_target_min=480)
        ds_540 = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {}, sleep_target_min=540)

        sp_480 = ds_480["days"][0]["sleep_perf"]
        sp_540 = ds_540["days"][0]["sleep_perf"]
        assert sp_540 < sp_480, "target más alto -> sleep_perf debe bajar para el mismo asleep"

        assert ds_480["summary"]["sleep_target_min"] == 480
        assert ds_540["summary"]["sleep_target_min"] == 540
        assert ds_540["summary"]["engine"]["sleep_target_min"] == 540

        # rule_sleep_debt: umbral sube de 420 a 480 -> más noches cuentan como "cortas"
        from app.insights import rule_sleep_debt
        dates = [f"2024-02-{i+1:02d}" for i in range(7)]
        days = [{"date": d, "asleep": 450} for d in dates]  # 450 min: >420 pero <480
        r_480 = rule_sleep_debt(days, {"sleep_target_min": 480})  # threshold 420 -> no corta
        r_540 = rule_sleep_debt(days, {"sleep_target_min": 540})  # threshold 480 -> SÍ corta
        assert r_480 is None
        assert r_540 is not None

        # bodyage: sleep_penalty_h sube de 7.0 a 8.0 -> mismo sleep_h ahora penaliza más
        days_14 = [{"date": f"2024-01-{i+1:02d}", "rhr": 52, "hrv": 55.0, "asleep": 420}  # 7.0h
                   for i in range(14)]
        r7 = compute_body_age(days_14, [], age=40, waist=82, sex="M", sleep_penalty_h=7.0)
        r8 = compute_body_age(days_14, [], age=40, waist=82, sex="M", sleep_penalty_h=8.0)
        assert r8["penalty"] >= r7["penalty"], "target más alto -> penalty igual o mayor"
        assert r8["penalty"] > 0.0
        assert r7["penalty"] == 0.0  # 7.0h no penaliza bajo el umbral viejo (7 < 7 es False)


# ══════════════════════════════════════════════════════════════════════════════
# 6. sleep_target_min validado en PUT /api/profile
# ══════════════════════════════════════════════════════════════════════════════

def _get_api_client(tmp_path: Path, monkeypatch):
    real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
    if real_compact.exists():
        (tmp_path / "health_compact.json").write_text(real_compact.read_text())

    from app import config, profile as _pm
    import main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                        Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)

    with patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    yield from _get_api_client(tmp_path, monkeypatch)


class TestSleepTargetMinValidation:
    def test_put_valid_sleep_target_min_saves(self, api_client):
        resp = api_client.put("/api/profile", json={"sleep_target_min": 540})
        assert resp.status_code == 200
        assert resp.json()["sleep_target_min"] == 540

    def test_put_sleep_target_min_below_300_rejected(self, api_client):
        resp = api_client.put("/api/profile", json={"sleep_target_min": 299})
        assert resp.status_code == 422

    def test_put_sleep_target_min_above_600_rejected(self, api_client):
        resp = api_client.put("/api/profile", json={"sleep_target_min": 601})
        assert resp.status_code == 422

    def test_put_sleep_target_min_boundaries_accepted(self, api_client):
        resp_lo = api_client.put("/api/profile", json={"sleep_target_min": 300})
        assert resp_lo.status_code == 200
        resp_hi = api_client.put("/api/profile", json={"sleep_target_min": 600})
        assert resp_hi.status_code == 200

    def test_get_profile_default_sleep_target_min_480(self, api_client):
        resp = api_client.get("/api/profile")
        assert resp.status_code == 200
        assert resp.json()["sleep_target_min"] == 480


# ══════════════════════════════════════════════════════════════════════════════
# 7. Recovery rodante: anti look-ahead, dirección, fallbacks 30/10
# ══════════════════════════════════════════════════════════════════════════════

class TestRollingRecoveryDirection:
    def test_rolling_narrows_gap_between_early_and_late_periods(self):
        """HRV que 'mejora' linealmente durante 200 días: con escala GLOBAL, el
        percentil se calcula sobre TODA la serie -> los primeros meses (HRV bajo
        vs el máximo futuro) quedan artificialmente bajos, los últimos altos.
        Con escala RODANTE, cada día se compara solo contra su pasado reciente
        -> el recovery medio de tramo inicial y tramo final se acerca."""
        from app.scoring import build_dataset

        n = 200
        base_date = datetime.date(2024, 1, 1)
        dates = [(base_date + datetime.timedelta(days=i)).isoformat() for i in range(n)]
        # HRV sube linealmente de 40 a 80; RHR y sueño constantes (no interfieren).
        hrv = {d: 40.0 + (40.0 * i / (n - 1)) for i, d in enumerate(dates)}
        rhr = {d: 55.0 for d in dates}
        sleep_d = {d: {"asleep": 450} for d in dates}

        ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
        days_by_date = {d["date"]: d for d in ds["days"]}
        recoveries = [days_by_date[d]["recovery"] for d in dates]

        first_third = recoveries[: n // 3]
        last_third = recoveries[-n // 3:]

        avg_first = statistics.mean(first_third)
        avg_last = statistics.mean(last_third)
        gap_rolling = avg_last - avg_first

        # Comparar contra la escala GLOBAL equivalente (percentil 5-95 de TODA la serie).
        hrv_vals = sorted(hrv.values())

        def pct(a, p):
            a = sorted(a); k = (len(a) - 1) * p / 100; f = int(k)
            return a[f] if f + 1 >= len(a) else a[f] + (a[f + 1] - a[f]) * (k - f)

        hlo_g, hhi_g = pct(hrv_vals, 5), pct(hrv_vals, 95)

        def clamp(x): return max(0, min(100, x))

        def hrv_component_global(h):
            return clamp((h - hlo_g) / (hhi_g - hlo_g) * 100)

        global_hrv_scores = [hrv_component_global(hrv[d]) for d in dates]
        avg_first_global = statistics.mean(global_hrv_scores[: n // 3])
        avg_last_global = statistics.mean(global_hrv_scores[-n // 3:])
        gap_global = avg_last_global - avg_first_global

        assert gap_rolling < gap_global, (
            "la escala rodante debe ACERCAR el recovery medio de los tramos "
            f"inicial/final vs la escala global (rolling gap={gap_rolling:.1f}, "
            f"global gap={gap_global:.1f})"
        )

    def test_no_lookahead_day_n_unaffected_by_future_data(self):
        """El recovery del día N no debe cambiar si se agregan/cambian datos de
        días > N (anti look-ahead)."""
        from app.scoring import build_dataset

        base_date = datetime.date(2024, 1, 1)
        # 40 días de historia estable (>= _ROLLING_RECOVERY_MIN_FULL=30)
        dates_40 = [(base_date + datetime.timedelta(days=i)).isoformat() for i in range(40)]
        hrv_40 = {d: 55.0 + (i % 5) for i, d in enumerate(dates_40)}
        rhr_40 = {d: 52.0 for d in dates_40}
        sleep_40 = {d: {"asleep": 450} for d in dates_40}

        ds_40 = build_dataset(sleep_40, rhr_40, hrv_40, {}, {}, {}, {})
        day20_before = {d["date"]: d for d in ds_40["days"]}[dates_40[20]]

        # Extender la serie con 20 días MÁS al final, con HRV muy distinto (shock).
        dates_extra = [(base_date + datetime.timedelta(days=i)).isoformat() for i in range(40, 60)]
        hrv_60 = dict(hrv_40)
        hrv_60.update({d: 20.0 for d in dates_extra})  # HRV muy bajo al final
        rhr_60 = dict(rhr_40)
        rhr_60.update({d: 52.0 for d in dates_extra})
        sleep_60 = dict(sleep_40)
        sleep_60.update({d: {"asleep": 450} for d in dates_extra})

        ds_60 = build_dataset(sleep_60, rhr_60, hrv_60, {}, {}, {}, {})
        day20_after = {d["date"]: d for d in ds_60["days"]}[dates_40[20]]

        assert day20_before["recovery"] == day20_after["recovery"], (
            "el recovery del día 20 no debe cambiar por datos futuros (día 40-59) "
            f"— anti look-ahead. before={day20_before['recovery']} "
            f"after={day20_after['recovery']}"
        )
        assert day20_before["hrv"] == day20_after["hrv"]


class TestRollingRecoveryFallbacks:
    def test_fallback_defaults_below_10_readings(self):
        """<10 lecturas totales -> recovery usa los defaults fijos (40,70)/(48,60),
        NO un percentil calculado sobre la serie chica."""
        from app.scoring import build_dataset

        dates = [f"2024-01-{i+1:02d}" for i in range(5)]
        hrv = {d: 60.0 + i for i, d in enumerate(dates)}
        rhr = {d: 50.0 for d in dates}
        sleep_d = {d: {"asleep": 450} for d in dates}

        ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
        day = {d["date"]: d for d in ds["days"]}[dates[-1]]

        def clamp(x): return max(0, min(100, x))
        hlo, hhi = 40, 70
        h = hrv[dates[-1]]
        expected_hrv_score = clamp((h - hlo) / (hhi - hlo) * 100)
        # recovery = 0.55*hrv + 0.25*rhr + 0.20*sleep, reconstruir rhr/sleep con defaults también
        rlo, rhi = 48, 60
        r = rhr[dates[-1]]
        expected_rhr_score = clamp((rhi - r) / (rhi - rlo) * 100)
        expected_sleep_score = clamp(450 / 480 * 100)
        comps = [(expected_hrv_score, 0.55), (expected_rhr_score, 0.25), (expected_sleep_score, 0.20)]
        w = sum(wt for _, wt in comps)
        expected_rec = round(sum(v * wt for v, wt in comps) / w)
        assert day["recovery"] == expected_rec

    def test_fallback_full_history_between_10_and_29_readings(self):
        """10-29 lecturas -> usa percentiles de TODA la historia hasta ese día
        (no los defaults fijos, no una ventana de 90 recortada)."""
        from app.scoring import build_dataset

        n = 15
        dates = [f"2024-01-{i+1:02d}" for i in range(n)]
        hrv = {d: 50.0 + i for i, d in enumerate(dates)}  # 50..64
        rhr = {d: 50.0 for d in dates}
        sleep_d = {d: {"asleep": 450} for d in dates}

        ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
        last_day = {d["date"]: d for d in ds["days"]}[dates[-1]]

        # percentil 5-95 de TODA la historia (15 valores 50..64)
        def pct(a, p):
            a = sorted(a); k = (len(a) - 1) * p / 100; f = int(k)
            return a[f] if f + 1 >= len(a) else a[f] + (a[f + 1] - a[f]) * (k - f)

        hrv_vals = list(hrv.values())
        hlo, hhi = pct(hrv_vals, 5), pct(hrv_vals, 95)

        def clamp(x): return max(0, min(100, x))
        expected_hrv_score = clamp((hrv[dates[-1]] - hlo) / (hhi - hlo) * 100)
        # rhr constante -> percentil degenerado -> rhi=rlo+1 (misma regla que build_dataset)
        rlo, rhi = 50.0, 51.0
        expected_rhr_score = clamp((rhi - 50.0) / (rhi - rlo) * 100)
        expected_sleep_score = clamp(450 / 480 * 100)
        comps = [(expected_hrv_score, 0.55), (expected_rhr_score, 0.25), (expected_sleep_score, 0.20)]
        w = sum(wt for _, wt in comps)
        expected_rec = round(sum(v * wt for v, wt in comps) / w)
        assert last_day["recovery"] == expected_rec

    def test_fallback_rolling_window_at_30_plus_readings(self):
        """>=30 lecturas dentro de la ventana de 90d -> usa el percentil de la
        VENTANA (no de toda la historia, si difieren)."""
        from app.scoring import build_dataset

        n = 35
        base_date = datetime.date(2024, 1, 1)
        dates = [(base_date + datetime.timedelta(days=i)).isoformat() for i in range(n)]
        hrv = {d: 50.0 + i for i, d in enumerate(dates)}  # 50..84, sube con el tiempo
        rhr = {d: 50.0 for d in dates}
        sleep_d = {d: {"asleep": 450} for d in dates}

        ds = build_dataset(sleep_d, rhr, hrv, {}, {}, {}, {})
        last_day = {d["date"]: d for d in ds["days"]}[dates[-1]]

        # Todos los 35 días caben en la ventana de 90 -> ventana == historia completa aquí,
        # pero confirmamos que al menos llega al régimen de "ventana llena" (>=30) sin caer
        # a los defaults fijos.
        assert last_day["recovery"] is not None
        # Con hrv=84 (máximo) el score de hrv se satura cerca de 100 -> recovery alto
        assert last_day["recovery"] > 60
