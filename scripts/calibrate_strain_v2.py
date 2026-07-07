"""
calibrate_strain_v2.py — Ronda 5: calibra F_VIG, F_STEPS, K para strain v2
(híbrido TRIMP) sobre el histórico REAL local (data/health_compact.json).

Corrida ÚNICA por el implementador. Imprime la tabla v1-vs-v2 (medianas, p10/p90,
correlación con TRIMP en días con HR) que se pega en el informe de la ronda.
Las constantes elegidas quedan HARDCODEADAS en app/scoring.py con comentario
de procedencia (este script + fecha + valores fuente).

Uso:
    .venv/bin/python scripts/calibrate_strain_v2.py

Si no existe data/health_compact.json local, aborta con mensaje claro (no hay
histórico real para calibrar contra datos sintéticos).
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "health_compact.json"


def _pct(a, p):
    if not a:
        return 0
    a = sorted(a)
    k = (len(a) - 1) * p / 100
    f = int(k)
    return a[f] if f + 1 >= len(a) else a[f] + (a[f + 1] - a[f]) * (k - f)


def _pearson(xs, ys):
    if len(xs) < 2 or len(ys) < 2:
        return None
    try:
        return statistics.correlation(xs, ys)
    except Exception:
        n = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        vx = sum((x - mx) ** 2 for x in xs)
        vy = sum((y - my) ** 2 for y in ys)
        if vx == 0 or vy == 0:
            return None
        return cov / math.sqrt(vx * vy)


def main():
    if not DATA_FILE.exists():
        print(f"ABORTA: no existe {DATA_FILE} (histórico real local requerido "
              f"para calibrar — no se calibra contra datos sintéticos).",
              file=sys.stderr)
        sys.exit(1)

    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    days = data["days"]

    strain_v1_vals = [d["strain"] for d in days if d.get("strain") is not None]
    trimp_vals_all = [d.get("trimp") for d in days]
    vigorous_vals_all = [d.get("vigorous") for d in days]
    steps_vals_all = [d.get("steps") for d in days]

    n_trimp_days = sum(1 for t in trimp_vals_all if t)
    n_vigorous_days = sum(1 for v in vigorous_vals_all if v)
    n_both = sum(1 for d in days if d.get("trimp") and d.get("vigorous"))

    print("=" * 78)
    print("CALIBRACIÓN strain v2 — datos: ", DATA_FILE)
    print("=" * 78)
    print(f"n_days totales: {len(days)}")
    print(f"días con trimp>0: {n_trimp_days}")
    print(f"días con vigorous (AZM)>0: {n_vigorous_days}")
    print(f"días con AMBOS trimp Y vigorous (para regresión F_VIG): {n_both}")
    print()

    # ── F_VIG: regresión trimp_day ≈ F_VIG × vigorous_min sobre días con AMBOS ──
    pairs = [(d["vigorous"], d["trimp"]) for d in days if d.get("trimp") and d.get("vigorous")]
    if pairs:
        slopes = [t / v for v, t in pairs if v > 0]
        F_VIG = round(statistics.median(slopes), 3)
        f_vig_source = f"mediana de pendientes trimp/vigorous sobre {len(slopes)} días reales"
    else:
        # DESVIACIÓN DOCUMENTADA: el histórico real local (fuentes actuales:
        # Google Health + HealthKit) NUNCA trae Active Zone Minutes (vigorous=0
        # en el 100% de los 347 días, incluidos los 42 con trimp real) — no hay
        # overlap con el que regresionar. Se usa un F_VIG de arranque razonable
        # basado en la escala de TRIMP observada: TRIMP mediana en sesión real
        # ronda ~76 para sesiones de ~45-60 min de intensidad moderada-alta, que
        # en minutos "vigorosos" equivalentes (AZM cuenta 1:1 min moderados,
        # 2:1 min intensos) se aproxima a un factor de ~2.5-3 TRIMP/min-vigoroso.
        # Se documenta como estimación, no medición — no hay AZM real para
        # verificarla en este histórico. Si en el futuro una fuente aporta AZM,
        # recalibrar con este mismo script.
        F_VIG = 2.5
        f_vig_source = ("SIN OVERLAP trimp+vigorous en histórico real (0 días) → "
                         "fallback de arranque documentado (no medido)")

    print(f"F_VIG elegido: {F_VIG}  ({f_vig_source})")
    print()

    # ── F_STEPS: que 10k pasos ≈ 20 unidades TRIMP-equivalentes ─────────────────
    # Arranque roadmap: 500 (10000/500=20). Verificamos que mantenga el rango de
    # "días sin ejercicio" razonable comparando contra p50 de steps reales.
    F_STEPS = 500.0
    print(f"F_STEPS elegido: {F_STEPS}  (10000 pasos / 500 = 20 TRIMP-equiv; "
          f"arranque roadmap, ver nota abajo)")
    steps_median = statistics.median([s for s in steps_vals_all if s is not None]) \
        if any(s is not None for s in steps_vals_all) else None
    if steps_median:
        print(f"  (contexto: mediana de steps reales en días con dato = {steps_median:.0f} "
              f"→ NEAT-equiv {steps_median / F_STEPS:.1f})")
    print()

    # ── Carga diaria L(d) bajo v2, sobre TODO el histórico (no solo días-strain-v1) ──
    def daily_load(d):
        trimp = d.get("trimp")
        vigorous = d.get("vigorous")
        steps = d.get("steps")
        L = 0.0
        if trimp:
            L += trimp
        elif vigorous:
            # Fallback solo si NO hay trimp real ese día (evita doble conteo).
            L += vigorous * F_VIG
        if steps:
            L += steps / F_STEPS
        return L

    loads = [daily_load(d) for d in days]
    loads_with_signal = [
        L for d, L in zip(days, loads)
        if d.get("trimp") or d.get("vigorous") or d.get("steps") is not None
    ]

    # ── K: que mediana(strain_v2) ≈ mediana(strain_v1) sobre el histórico real ──
    # strain_v2 = 21 * (1 - exp(-L/K)) computado SOLO en días con señal (igual
    # criterio de presencia que v1 aplicaba con steps, ahora extendido a workouts).
    target_median = statistics.median(strain_v1_vals)

    def median_strain_v2_for_k(k):
        vals = [21 * (1 - math.exp(-L / k)) for L in loads_with_signal if L > 0]
        return statistics.median(vals) if vals else 0.0

    # Búsqueda binaria simple sobre K en un rango amplio.
    lo_k, hi_k = 1.0, 500.0
    for _ in range(60):
        mid = (lo_k + hi_k) / 2
        m = median_strain_v2_for_k(mid)
        if m > target_median:
            lo_k = mid
        else:
            hi_k = mid
    K = round((lo_k + hi_k) / 2, 2)

    print(f"K elegido: {K}  (búsqueda binaria: mediana(strain_v2) ≈ mediana(strain_v1)="
          f"{target_median:.2f} sobre {len(loads_with_signal)} días con señal)")
    print()

    # ── Tabla v1 vs v2 ────────────────────────────────────────────────────────
    strain_v2_all = []
    trimp_hr_pairs_v1 = []
    trimp_hr_pairs_v2 = []
    n_strain_v2_present = 0
    n_out_of_range = 0

    for d, L in zip(days, loads):
        has_signal = bool(d.get("trimp") or d.get("vigorous") or d.get("steps") is not None)
        if not has_signal:
            continue
        v2 = round(21 * (1 - math.exp(-L / K)), 1) if L > 0 else 0.0
        strain_v2_all.append(v2)
        n_strain_v2_present += 1
        if v2 > 21 or v2 < 0:
            n_out_of_range += 1
        if d.get("trimp"):
            if d.get("strain") is not None:
                trimp_hr_pairs_v1.append((d["trimp"], d["strain"]))
            trimp_hr_pairs_v2.append((d["trimp"], v2))

    corr_v1 = _pearson([p[0] for p in trimp_hr_pairs_v1], [p[1] for p in trimp_hr_pairs_v1])
    corr_v2 = _pearson([p[0] for p in trimp_hr_pairs_v2], [p[1] for p in trimp_hr_pairs_v2])

    print("-" * 78)
    print(f"{'métrica':32s} {'v1':>12s} {'v2':>12s}")
    print("-" * 78)
    print(f"{'n días con strain/señal':32s} {len(strain_v1_vals):>12d} {n_strain_v2_present:>12d}")
    print(f"{'mediana':32s} {statistics.median(strain_v1_vals):>12.2f} "
          f"{statistics.median(strain_v2_all):>12.2f}")
    print(f"{'p10':32s} {_pct(strain_v1_vals, 10):>12.2f} {_pct(strain_v2_all, 10):>12.2f}")
    print(f"{'p90':32s} {_pct(strain_v1_vals, 90):>12.2f} {_pct(strain_v2_all, 90):>12.2f}")
    print(f"{'días fuera de rango [0,21]':32s} {'0':>12s} {n_out_of_range:>12d}")
    print(f"{'corr(strain, trimp) en días c/HR':32s} "
          f"{('%.3f' % corr_v1) if corr_v1 is not None else 'N/D':>12s} "
          f"{('%.3f' % corr_v2) if corr_v2 is not None else 'N/D':>12s} "
          f"(n={len(trimp_hr_pairs_v1)})")
    print("-" * 78)
    print()

    delta_median = statistics.median(strain_v2_all) - statistics.median(strain_v1_vals)
    print(f"Δ mediana (v2 - v1) = {delta_median:+.2f}  "
          f"({'DENTRO' if abs(delta_median) <= 0.5 else 'FUERA'} de ±0.5, criterio de aceptación)")
    if corr_v1 is not None and corr_v2 is not None:
        print(f"corr v2 {'>' if corr_v2 > corr_v1 else '<='} corr v1: "
              f"{'CUMPLE' if corr_v2 > corr_v1 else 'NO CUMPLE'} el punto de la ronda")

    print()
    print("=" * 78)
    print("CONSTANTES FINALES A HARDCODEAR EN app/scoring.py:")
    print(f"  F_VIG   = {F_VIG}")
    print(f"  F_STEPS = {F_STEPS}")
    print(f"  K       = {K}")
    print("=" * 78)


if __name__ == "__main__":
    main()
