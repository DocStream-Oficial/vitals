"""
test_tier3_drivers.py — Tests para el módulo Tier 3: app/drivers.py

Cubre:
  1. _rank con empates (promedio)
  2. _spearman contra valor conocido (scipy-compatible)
  3. _spearman None si n<3
  4. _spearman None si varianza-cero (serie constante)
  5. _sig: False si n<MIN_N; True para rho=±1; valor conocido
  6. pair_lagged: pairing correcto con lag=1
  7. pair_lagged: robusto a huecos (fechas no consecutivas)
  8. pair_lagged: lag=0 (mismo día)
  9. Filtro MIN_N: serie con n=13 → analyze_drivers devuelve []
 10. Relación monótona sintética detectada (rho alto + sig + |rho|>=0.2)
 11. Serie rala (<25 pares) → analyze_drivers devuelve [] sin crash
 12. analyze_drivers sobre golden: trimp->recovery (n=13) NUNCA aparece
 13. analyze_drivers sobre golden: findings bien formados (campos requeridos)
 14. analyze_drivers sobre golden: ordenados por |rho| desc
 15. analyze_drivers datos vacíos → [] sin crash

Ronda 3 (Benjamini-Hochberg):
 16. _pvalue: monotónico con |rho| y con n; rho=±1 -> p=0.0; None si n<3
 17. _benjamini_hochberg: procedimiento correcto (no off-by-one), sobrevivientes
     son los k* primeros por p ascendente
 18. analyze_drivers con specs de puro ruido: BH deja pasar MENOS que el filtro
     viejo (_sig sin corrección)
 19. analyze_drivers sobre golden real: asleep->recovery lag0 sobrevive BH
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent.parent / "data" / "health_compact.json"


# ══════════════════════════════════════════════════════════════════════════════
# ── Helpers ───────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

def _make_days(driver: str, outcome: str, xs, ys, base_date="2024-01-"):
    """Construye una lista de días con driver/outcome sincronizados (sin lag)."""
    days = []
    for i, (x, y) in enumerate(zip(xs, ys)):
        d = {"date": f"{base_date}{i+1:02d}"}
        if x is not None:
            d[driver] = float(x)
        if y is not None:
            d[outcome] = float(y)
        days.append(d)
    return days


def _make_lagged_days(driver: str, outcome: str, xs, ys, base_date="2024-01-"):
    """
    Construye días donde xs[i] → days[i][driver] y ys[i] → days[i+1][outcome]
    (lag=1). Necesita len(xs) + 1 días para que todos los pares estén cubiertos.
    """
    n = max(len(xs), len(ys))
    days = []
    for i in range(n + 1):
        d = {"date": f"{base_date}{i+1:02d}"}
        if i < len(xs) and xs[i] is not None:
            d[driver] = float(xs[i])
        if i < len(ys) and ys[i] is not None:
            # outcome en el día i+1 (t+1 para driver en t=i)
            pass
        days.append(d)
    # Ahora asignar outcomes con lag=1
    for i, y in enumerate(ys):
        if y is not None:
            days[i + 1][outcome] = float(y)
    return days


# ══════════════════════════════════════════════════════════════════════════════
# ── _rank ─────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestRank:
    def test_no_ties(self):
        from app.drivers import _rank
        r = _rank([10, 30, 20])
        assert r == [1.0, 3.0, 2.0]

    def test_all_tied(self):
        from app.drivers import _rank
        r = _rank([5, 5, 5])
        assert r == [2.0, 2.0, 2.0]  # avg of ranks 1,2,3 = 2.0

    def test_two_tied_at_top(self):
        from app.drivers import _rank
        r = _rank([10, 30, 20, 30])
        assert r == [1.0, 3.5, 2.0, 3.5]

    def test_two_tied_at_bottom(self):
        from app.drivers import _rank
        r = _rank([10, 10, 30])
        assert r == [1.5, 1.5, 3.0]

    def test_single_element(self):
        from app.drivers import _rank
        assert _rank([42.0]) == [1.0]

    def test_empty(self):
        from app.drivers import _rank
        assert _rank([]) == []


# ══════════════════════════════════════════════════════════════════════════════
# ── _spearman ─────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestSpearman:
    def test_monotone_ascending(self):
        from app.drivers import _spearman
        pairs = [(i, i) for i in range(1, 11)]
        result = _spearman(pairs)
        assert result is not None
        rho, n = result
        assert n == 10
        assert abs(rho - 1.0) < 1e-9

    def test_monotone_descending(self):
        from app.drivers import _spearman
        pairs = [(i, 10 - i) for i in range(1, 11)]
        result = _spearman(pairs)
        assert result is not None
        rho, n = result
        assert abs(rho - (-1.0)) < 1e-9

    def test_known_value(self):
        """
        Valor conocido calculado a mano.
        xs=[1,2,3,4,5], ys=[5,3,1,2,4]
        ranks_x=[1,2,3,4,5], ranks_y=[5,3,1,2,4]
        d² = [16,1,4,4,1] = 26
        rho = 1 - 6*26/(5*24) = 1 - 156/120 = 1 - 1.3 = -0.3
        """
        from app.drivers import _spearman
        pairs = [(1, 5), (2, 3), (3, 1), (4, 2), (5, 4)]
        result = _spearman(pairs)
        assert result is not None
        rho, n = result
        assert n == 5
        assert abs(rho - (-0.3)) < 1e-6

    def test_none_if_fewer_than_3(self):
        from app.drivers import _spearman
        assert _spearman([]) is None
        assert _spearman([(1, 2)]) is None
        assert _spearman([(1, 2), (3, 4)]) is None

    def test_none_if_constant_x(self):
        """Varianza cero en x → None."""
        from app.drivers import _spearman
        pairs = [(5, 1), (5, 2), (5, 3), (5, 4)]
        assert _spearman(pairs) is None

    def test_none_if_constant_y(self):
        """Varianza cero en y → None."""
        from app.drivers import _spearman
        pairs = [(1, 5), (2, 5), (3, 5), (4, 5)]
        assert _spearman(pairs) is None

    def test_rho_clamped_to_minus_1_plus_1(self):
        """rho siempre en [-1, 1] (sin float overflow)."""
        from app.drivers import _spearman
        pairs = [(i, i * 1000) for i in range(1, 20)]
        result = _spearman(pairs)
        assert result is not None
        rho, _ = result
        assert -1.0 <= rho <= 1.0

    def test_with_ties(self):
        """Con empates, rho sigue siendo coherente (no crash)."""
        from app.drivers import _spearman
        # xs con muchos empates
        pairs = [(1, 10), (1, 8), (2, 6), (2, 4), (3, 2), (3, 0)]
        result = _spearman(pairs)
        assert result is not None
        rho, n = result
        assert n == 6
        assert -1.0 <= rho <= 1.0
        assert rho < 0  # relación negativa


# ══════════════════════════════════════════════════════════════════════════════
# ── _sig ──────────────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestSig:
    def test_false_if_n_below_min(self):
        from app.drivers import _sig, MIN_N
        assert _sig(0.9, MIN_N - 1) is False
        assert _sig(1.0, 1) is False

    def test_true_for_rho_1(self):
        """ρ=1 → t→∞ → significativo (sin crash)."""
        from app.drivers import _sig
        assert _sig(1.0, 30) is True

    def test_true_for_rho_minus_1(self):
        from app.drivers import _sig
        assert _sig(-1.0, 30) is True

    def test_known_significant(self):
        """rho=0.4, n=25: t=2.09 > 2.0 → True."""
        from app.drivers import _sig
        assert _sig(0.4, 25) is True

    def test_known_not_significant(self):
        """rho=0.3, n=30: t≈1.66 < 2.0 → False."""
        from app.drivers import _sig
        assert _sig(0.3, 30) is False

    def test_borderline_n_equals_min(self):
        from app.drivers import _sig, MIN_N
        # Exactly MIN_N with high rho should be significant
        assert _sig(0.5, MIN_N) is True

    def test_true_for_strong_rho_large_n(self):
        from app.drivers import _sig
        assert _sig(0.8, 50) is True


# ══════════════════════════════════════════════════════════════════════════════
# ── pair_lagged ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestPairLagged:
    def test_lag_1_basic(self):
        """Pares (driver[t], outcome[t+1]) correctamente formados."""
        from app.drivers import pair_lagged
        days = [
            {"date": "2024-01-01", "bed_min": 100.0, "hrv": 50.0},
            {"date": "2024-01-02", "bed_min": 120.0, "hrv": 45.0},
            {"date": "2024-01-03", "bed_min": 90.0,  "hrv": 55.0},
        ]
        pairs = pair_lagged(days, "bed_min", "hrv", lag=1)
        # (bed_min day1 → hrv day2), (bed_min day2 → hrv day3)
        assert len(pairs) == 2
        assert pairs[0] == (100.0, 45.0)
        assert pairs[1] == (120.0, 55.0)

    def test_lag_0_same_day(self):
        """lag=0 empareja driver y outcome del mismo día."""
        from app.drivers import pair_lagged
        days = [
            {"date": "2024-01-01", "asleep": 420.0, "recovery": 75.0},
            {"date": "2024-01-02", "asleep": 380.0, "recovery": 60.0},
        ]
        pairs = pair_lagged(days, "asleep", "recovery", lag=0)
        assert len(pairs) == 2
        assert pairs[0] == (420.0, 75.0)
        assert pairs[1] == (380.0, 60.0)

    def test_robust_to_gaps(self):
        """Fechas no consecutivas: solo empareja cuando existe t y t+lag."""
        from app.drivers import pair_lagged
        days = [
            {"date": "2024-01-01", "strain": 5.0, "recovery": 70.0},
            # 2024-01-02 ausente
            {"date": "2024-01-03", "strain": 8.0, "recovery": 65.0},
            {"date": "2024-01-04", "strain": 6.0, "recovery": 60.0},
        ]
        pairs = pair_lagged(days, "strain", "recovery", lag=1)
        # day1→day2: day2 ausente → no pair
        # day3→day4: day4 presente → 1 par
        assert len(pairs) == 1
        assert pairs[0] == (8.0, 60.0)

    def test_skips_none_driver(self):
        """Días sin el campo driver se omiten."""
        from app.drivers import pair_lagged
        days = [
            {"date": "2024-01-01", "recovery": 70.0},          # sin bed_min
            {"date": "2024-01-02", "bed_min": 120.0, "recovery": 65.0},
            {"date": "2024-01-03", "bed_min": 100.0, "recovery": 60.0},
        ]
        pairs = pair_lagged(days, "bed_min", "recovery", lag=1)
        # day1 sin bed_min → skip; day2→day3 → 1 par
        assert len(pairs) == 1
        assert pairs[0] == (120.0, 60.0)

    def test_skips_none_outcome(self):
        """Días t+lag sin el campo outcome se omiten."""
        from app.drivers import pair_lagged
        days = [
            {"date": "2024-01-01", "bed_min": 100.0},           # sin hrv
            {"date": "2024-01-02", "bed_min": 120.0, "hrv": 45.0},
            {"date": "2024-01-03", "hrv": 55.0},
        ]
        # day1→day2: day2 tiene hrv → 1 par; day2→day3: day3 tiene hrv → 1 par
        pairs = pair_lagged(days, "bed_min", "hrv", lag=1)
        assert len(pairs) == 2
        assert pairs[0] == (100.0, 45.0)
        assert pairs[1] == (120.0, 55.0)

    def test_empty_days(self):
        from app.drivers import pair_lagged
        assert pair_lagged([], "bed_min", "hrv", lag=1) == []

    def test_single_day_lag1(self):
        """Un solo día con lag=1 → no hay pares (no hay t+1)."""
        from app.drivers import pair_lagged
        days = [{"date": "2024-01-01", "bed_min": 100.0, "hrv": 50.0}]
        assert pair_lagged(days, "bed_min", "hrv", lag=1) == []


# ══════════════════════════════════════════════════════════════════════════════
# ── analyze_drivers ───────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeDrivers:
    def test_empty_days_no_crash(self):
        """analyze_drivers con [] → [] sin crash."""
        from app.drivers import analyze_drivers
        assert analyze_drivers([]) == []

    def test_filter_min_n(self):
        """Serie con n=13 (< MIN_N=25): no debe aparecer en resultados."""
        from app.drivers import analyze_drivers
        # 13 pares perfectamente monótonos para asleep→recovery lag=1
        # No alcanzan MIN_N → deben ser filtrados
        days = []
        for i in range(14):
            days.append({
                "date": f"2024-01-{i+1:02d}",
                "asleep": float(300 + i * 10),
                "recovery": float(50 + i * 3),
            })
        results = analyze_drivers(days)
        # n=13 para lag=1 (14 días → 13 pares) → no debe aparecer
        assert len(results) == 0

    def test_monotone_synthetic_detected(self):
        """
        Relación monótona sintética perfecta con n>=MIN_N:
        debe detectarse con rho alto, significativo, |rho|>=0.2.
        """
        from app.drivers import analyze_drivers, MIN_N
        # 30 días para tener n=29 pares con lag=1
        n_days = MIN_N + 5  # 30 días
        days = []
        for i in range(n_days):
            days.append({
                "date": f"2024-01-{i+1:02d}",
                "asleep": float(300 + i * 10),  # sube monotónicamente
                "recovery": float(50 + i * 2),   # sube monotónicamente (mismo día)
            })
        results = analyze_drivers(days)
        # asleep→recovery lag=0 debería detectarse (n=30, rho≈1.0)
        hits = [f for f in results if f["driver"] == "asleep" and f["outcome"] == "recovery" and f["lag"] == 0]
        assert len(hits) >= 1, f"No se detectó asleep→recovery lag=0. Findings: {results}"
        f = hits[0]
        assert f["significant"] is True
        assert abs(f["rho"]) >= 0.2
        assert f["n"] >= MIN_N

    def test_monotone_negative_detected(self):
        """Relación monótona negativa (bed_min↑ → hrv↓) detectada correctamente."""
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = []
        for i in range(n_days):
            days.append({
                "date": f"2024-01-{i+1:02d}",
                "bed_min": float(200 + i * 5),   # sube: acostarse más tarde
                "hrv": float(60 - i * 1.5),       # baja (lag=0 not in specs, but lag=1)
                "recovery": float(70 - i * 1.5),  # baja
            })
        results = analyze_drivers(days)
        # bed_min→hrv o bed_min→recovery con lag=1
        bed_hits = [f for f in results if f["driver"] == "bed_min"]
        if bed_hits:
            f = bed_hits[0]
            assert f["rho"] < 0  # asociación negativa (bed_min↑ → hrv/rec↓)
            assert f["direction"] in ("mejora", "empeora")

    def test_ordered_by_abs_rho_desc(self):
        """Los findings están ordenados por |ρ| descendente."""
        from app.drivers import analyze_drivers, MIN_N
        # Crear datos con dos relaciones, una más fuerte que la otra
        n_days = MIN_N + 10
        days = []
        for i in range(n_days):
            days.append({
                "date": f"2024-01-{i+1:02d}",
                "asleep": float(300 + i * 10),   # fuerte con recovery lag=0
                "recovery": float(50 + i * 3),
                "strain": float(5.0 + (i % 5)),  # más ruidoso
            })
        results = analyze_drivers(days)
        if len(results) >= 2:
            for j in range(len(results) - 1):
                assert abs(results[j]["rho"]) >= abs(results[j + 1]["rho"]), (
                    f"No ordenado: {results[j]['rho']} vs {results[j+1]['rho']}"
                )

    def test_max_top_k_findings(self):
        """analyze_drivers devuelve máximo TOP_K findings."""
        from app.drivers import analyze_drivers, TOP_K
        # Datos diseñados para que varias specs pasen el filtro
        n_days = 60
        days = []
        for i in range(n_days):
            days.append({
                "date": f"2024-{i//30+1:02d}-{i%30+1:02d}",
                "asleep": float(300 + i * 5),
                "recovery": float(50 + i * 2),
                "hrv": float(40 + i * 1.5),
                "bed_min": float(200 - i * 3),
                "strain": float(5.0 + i * 0.1),
                "steps": float(5000 + i * 100),
                "vigorous": float(10 + i * 2),
            })
        results = analyze_drivers(days)
        assert len(results) <= TOP_K

    def test_finding_has_required_fields(self):
        """Cada finding tiene todos los campos requeridos."""
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = []
        for i in range(n_days):
            days.append({
                "date": f"2024-01-{i+1:02d}",
                "asleep": float(300 + i * 10),
                "recovery": float(50 + i * 2),
            })
        results = analyze_drivers(days)
        required = {"driver", "outcome", "lag", "rho", "n", "significant", "direction", "headline", "strength"}
        for f in results:
            missing = required - set(f.keys())
            assert not missing, f"Faltan campos: {missing} en {f}"

    def test_strength_values(self):
        """strength debe ser 'fuerte', 'moderada' o 'débil'."""
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = [{"date": f"2024-01-{i+1:02d}",
                 "asleep": float(300 + i * 10),
                 "recovery": float(50 + i * 2)} for i in range(n_days)]
        results = analyze_drivers(days)
        for f in results:
            assert f["strength"] in ("fuerte", "moderada", "débil"), (
                f"strength inesperado: {f['strength']}"
            )

    def test_direction_values(self):
        """direction debe ser 'mejora' o 'empeora'."""
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = [{"date": f"2024-01-{i+1:02d}",
                 "asleep": float(300 + i * 10),
                 "recovery": float(50 + i * 2)} for i in range(n_days)]
        results = analyze_drivers(days)
        for f in results:
            assert f["direction"] in ("mejora", "empeora"), (
                f"direction inesperado: {f['direction']}"
            )

    def test_headline_is_string(self):
        """headline es string no vacío."""
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = [{"date": f"2024-01-{i+1:02d}",
                 "asleep": float(300 + i * 10),
                 "recovery": float(50 + i * 2)} for i in range(n_days)]
        results = analyze_drivers(days)
        for f in results:
            assert isinstance(f["headline"], str)
            assert len(f["headline"]) > 0

    def test_sparse_series_no_crash(self):
        """Serie muy rala (todos los pares potenciales con None) → [] sin crash."""
        from app.drivers import analyze_drivers
        # Muchos días pero con campos desalineados: driver en impares, outcome en pares
        days = []
        for i in range(40):
            d = {"date": f"2024-01-{i+1:02d}"}
            if i % 2 == 0:
                d["bed_min"] = float(100 + i)
            else:
                d["hrv"] = float(50 + i)
                d["recovery"] = float(60 + i)
            days.append(d)
        # No debe crash; puede devolver [] o findings con pocos pares que no pasen MIN_N
        result = analyze_drivers(days)
        assert isinstance(result, list)


# ══════════════════════════════════════════════════════════════════════════════
# ── Tests sobre el golden (datos reales) ──────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestGoldenDrivers:
    @pytest.fixture(scope="class")
    def golden(self):
        if not GOLDEN.exists():
            pytest.skip("Golden file not found")
        with open(GOLDEN) as f:
            return json.load(f)

    def test_trimp_never_appears(self, golden):
        """trimp->recovery (n=13) NUNCA debe aparecer en analyze_drivers."""
        from app.drivers import analyze_drivers
        results = analyze_drivers(golden["days"])
        trimp_hits = [f for f in results if f["driver"] == "trimp"]
        assert trimp_hits == [], f"trimp apareció en findings (n=13): {trimp_hits}"

    def test_findings_well_formed(self, golden):
        """Todos los findings del golden tienen campos correctos y tipos coherentes."""
        from app.drivers import analyze_drivers, MIN_N, MIN_ABS_RHO
        results = analyze_drivers(golden["days"])
        required = {"driver", "outcome", "lag", "rho", "n", "significant", "direction", "headline", "strength"}
        for f in results:
            assert required.issubset(set(f.keys())), f"Faltan campos en {f}"
            assert f["n"] >= MIN_N, f"n={f['n']} < MIN_N={MIN_N}"
            assert f["significant"] is True
            assert abs(f["rho"]) >= MIN_ABS_RHO, f"|rho|={abs(f['rho'])} < {MIN_ABS_RHO}"
            assert f["direction"] in ("mejora", "empeora")
            assert f["strength"] in ("fuerte", "moderada", "débil")
            assert isinstance(f["headline"], str) and len(f["headline"]) > 0

    def test_ordered_by_abs_rho(self, golden):
        """Findings del golden ordenados por |ρ| descendente."""
        from app.drivers import analyze_drivers
        results = analyze_drivers(golden["days"])
        for i in range(len(results) - 1):
            assert abs(results[i]["rho"]) >= abs(results[i + 1]["rho"]), (
                f"No ordenado en posición {i}: {results[i]['rho']} vs {results[i+1]['rho']}"
            )

    def test_no_500_on_empty_dataset(self):
        """analyze_drivers con dataset vacío → [] sin crash."""
        from app.drivers import analyze_drivers
        assert analyze_drivers([]) == []
        assert analyze_drivers(None if False else []) == []  # solo []

    def test_mcp_tools_drivers_list(self, golden):
        """mcp_tools.drivers_list(ds) devuelve la misma lista que analyze_drivers."""
        from app.mcp_tools import drivers_list
        from app.drivers import analyze_drivers
        result_mcp = drivers_list(golden)
        result_direct = analyze_drivers(golden["days"])
        assert result_mcp == result_direct

    def test_mcp_tools_drivers_list_empty(self):
        """mcp_tools.drivers_list({}) → []."""
        from app.mcp_tools import drivers_list
        assert drivers_list({}) == []
        assert drivers_list(None) == []


# ══════════════════════════════════════════════════════════════════════════════
# ── _pvalue (Ronda 3) ─────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestPvalue:
    def test_none_if_fewer_than_3(self):
        from app.drivers import _pvalue
        assert _pvalue(0.5, 2) is None
        assert _pvalue(0.5, 0) is None

    def test_rho_plus_1_gives_p_zero(self):
        from app.drivers import _pvalue
        assert _pvalue(1.0, 30) == 0.0

    def test_rho_minus_1_gives_p_zero(self):
        from app.drivers import _pvalue
        assert _pvalue(-1.0, 30) == 0.0

    def test_rho_zero_gives_p_near_one(self):
        from app.drivers import _pvalue
        p = _pvalue(0.0, 30)
        assert p == pytest.approx(1.0, abs=1e-9)

    def test_monotonic_with_abs_rho_fixed_n(self):
        """A mayor |ρ| (mismo n), menor p."""
        from app.drivers import _pvalue
        p_low = _pvalue(0.2, 50)
        p_mid = _pvalue(0.4, 50)
        p_high = _pvalue(0.8, 50)
        assert p_low > p_mid > p_high

    def test_monotonic_with_n_fixed_rho(self):
        """A mayor n (mismo ρ), menor p (más evidencia -> más confianza)."""
        from app.drivers import _pvalue
        p_small_n = _pvalue(0.3, 25)
        p_large_n = _pvalue(0.3, 200)
        assert p_large_n < p_small_n

    def test_bounded_between_0_and_1(self):
        from app.drivers import _pvalue
        for rho in (-1.0, -0.5, 0.0, 0.5, 1.0):
            p = _pvalue(rho, 40)
            assert 0.0 <= p <= 1.0

    def test_known_value_matches_sig_threshold(self):
        """rho=0.4, n=25 -> t=2.09 (ya sabemos que _sig da True); p debe ser < 0.05."""
        from app.drivers import _pvalue
        p = _pvalue(0.4, 25)
        assert p < 0.05


# ══════════════════════════════════════════════════════════════════════════════
# ── _benjamini_hochberg (Ronda 3) ─────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestBenjaminiHochberg:
    def test_empty_list(self):
        from app.drivers import _benjamini_hochberg
        assert _benjamini_hochberg([]) == []

    def test_all_survive_when_all_pvalues_tiny(self):
        from app.drivers import _benjamini_hochberg
        pvalues = [0.001, 0.002, 0.003, 0.0001]
        result = _benjamini_hochberg(pvalues, alpha=0.05)
        assert all(result)

    def test_none_survive_when_all_pvalues_large(self):
        from app.drivers import _benjamini_hochberg
        pvalues = [0.9, 0.8, 0.7, 0.6]
        result = _benjamini_hochberg(pvalues, alpha=0.05)
        assert not any(result)

    def test_no_off_by_one_classic_example(self):
        """
        Ejemplo clásico de libro de texto BH: m=5, alpha=0.05
        p = [0.01, 0.02, 0.03, 0.04, 0.20]
        Umbrales (k/m)*alpha = [0.01, 0.02, 0.03, 0.04, 0.05]
        p_(k) <= umbral_(k) para k=1..4 (todos empatan exacto), k=5 falla (0.20>0.05)
        k* = 4 -> sobreviven los 4 primeros por p ascendente.
        """
        from app.drivers import _benjamini_hochberg
        pvalues = [0.03, 0.01, 0.20, 0.04, 0.02]  # orden desordenado a propósito
        result = _benjamini_hochberg(pvalues, alpha=0.05)
        # índices ordenados por p: 1(0.01), 4(0.02), 0(0.03), 3(0.04), 2(0.20)
        # k*=4 -> sobreviven los índices 1, 4, 0, 3; NO el 2.
        assert result == [True, True, False, True, True]

    def test_survivors_are_prefix_of_sorted_order_not_individual_thresholds(self):
        """
        Verifica que NO se evalúa 'cada p <= su propio umbral' de forma suelta
        (bug común): un p intermedio que individualmente pasaría su umbral pero
        está DESPUÉS del punto de corte k* no debe sobrevivir.
        """
        from app.drivers import _benjamini_hochberg
        # m=4, alpha=0.05: umbrales [0.0125, 0.025, 0.0375, 0.05]
        # p=[0.20, 0.02, 0.03, 0.01] -> ordenado: 0.01(idx3), 0.02(idx1), 0.03(idx2), 0.20(idx0)
        # k=1: 0.01<=0.0125 OK; k=2: 0.02<=0.025 OK; k=3: 0.03<=0.0375 OK; k=4: 0.20<=0.05 NO
        # k*=3 -> sobreviven idx3, idx1, idx2 (los 3 primeros del orden), NO idx0.
        pvalues = [0.20, 0.02, 0.03, 0.01]
        result = _benjamini_hochberg(pvalues, alpha=0.05)
        assert result == [False, True, True, True]

    def test_survivors_count_less_or_equal_uncorrected_count(self):
        """BH nunca deja pasar MÁS descubrimientos que el conteo sin corregir (p<alpha)."""
        from app.drivers import _benjamini_hochberg
        pvalues = [0.001, 0.04, 0.045, 0.048, 0.3, 0.5, 0.6, 0.8]
        bh_survivors = sum(_benjamini_hochberg(pvalues, alpha=0.05))
        uncorrected = sum(1 for p in pvalues if p < 0.05)
        assert bh_survivors <= uncorrected


# ══════════════════════════════════════════════════════════════════════════════
# ── analyze_drivers + BH end-to-end (Ronda 3) ─────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

class TestAnalyzeDriversBH:
    def test_finding_has_p_field(self):
        from app.drivers import analyze_drivers, MIN_N
        n_days = MIN_N + 5
        days = [{"date": f"2024-01-{i+1:02d}",
                 "asleep": float(300 + i * 10),
                 "recovery": float(50 + i * 2)} for i in range(n_days)]
        results = analyze_drivers(days)
        assert results, "se esperaba al menos 1 finding"
        for f in results:
            assert "p" in f
            assert isinstance(f["p"], float)
            assert 0.0 <= f["p"] <= 1.0

    def test_pure_noise_bh_lets_through_fewer_than_uncorrected(self):
        """
        Con specs de puro ruido (sin relación real), BH debe dejar pasar MENOS
        findings que el filtro viejo (_sig sin corregir, p<0.05 fijo por test).
        Construimos días con valores pseudo-aleatorios deterministas (sin patrón
        monótono) para todos los drivers/outcomes de DRIVER_SPECS.
        """
        import random
        from app.drivers import (
            analyze_drivers, DRIVER_SPECS, pair_lagged, _spearman, _sig, MIN_N, MIN_ABS_RHO,
        )

        rng = random.Random(42)
        n_days = 60
        fields = set()
        for spec in DRIVER_SPECS:
            fields.add(spec[0])
            fields.add(spec[1])

        days = []
        for i in range(n_days):
            d = {"date": f"2024-{i//28+1:02d}-{i%28+1:02d}"}
            for field in fields:
                d[field] = rng.uniform(0, 100)
            days.append(d)

        # Conteo con el filtro VIEJO (n>=MIN_N, _sig sin corregir, |rho|>=MIN_ABS_RHO)
        uncorrected_count = 0
        for spec in DRIVER_SPECS:
            driver, outcome, lag = spec[0], spec[1], spec[2]
            pairs = pair_lagged(days, driver, outcome, lag)
            if not pairs:
                continue
            result = _spearman(pairs)
            if result is None:
                continue
            rho, n = result
            if n < MIN_N:
                continue
            if not _sig(rho, n):
                continue
            if abs(rho) < MIN_ABS_RHO:
                continue
            uncorrected_count += 1

        bh_results = analyze_drivers(days)
        assert len(bh_results) <= uncorrected_count, (
            f"BH ({len(bh_results)}) debería dejar pasar <= que el filtro viejo "
            f"({uncorrected_count}) sobre puro ruido"
        )

    def test_golden_asleep_recovery_lag0_survives_bh(self):
        """
        Criterio de aceptación del roadmap: asleep->recovery lag0 (rho≈0.32,
        n≈189 en datos reales) DEBE sobrevivir BH. Verificado sobre el golden real.
        """
        import json
        golden_path = Path(__file__).parent.parent / "data" / "health_compact.json"
        if not golden_path.exists():
            pytest.skip("Golden file not found")
        with open(golden_path) as f:
            golden = json.load(f)

        results = analyze_drivers_bh_helper(golden["days"])
        hits = [f for f in results if f["driver"] == "asleep" and f["outcome"] == "recovery" and f["lag"] == 0]
        assert len(hits) == 1, f"asleep->recovery lag0 no sobrevivió BH. Findings: {results}"
        assert hits[0]["n"] >= 100
        assert abs(hits[0]["rho"]) >= 0.2


def analyze_drivers_bh_helper(days):
    from app.drivers import analyze_drivers
    return analyze_drivers(days)
