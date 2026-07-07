"""
trends.py — Análisis de tendencia estadístico (solo stdlib).

Feature B del Roadmap Tier 2:
  linreg_slope(ys)     → pendiente OLS (None si n<3)
  mann_kendall(ys)     → {S, z, significant} (None si n<7)
  trend_summary(ys)    → {slope, direction, significant, n}

Reglas de dirección:
  - Si significant=True y slope>0  → "subiendo"
  - Si significant=True y slope<0  → "bajando"
  - En cualquier otro caso          → "estable"

Sin dependencias externas (solo math, statistics stdlib).
"""
from __future__ import annotations

import math
from typing import Optional


def linreg_slope(ys: list) -> Optional[float]:
    """
    Pendiente de regresión OLS simple con x = 0..n-1.
    Devuelve None si n < 3 o si la varianza de x es 0.
    """
    vals = [v for v in ys if v is not None]
    n = len(vals)
    if n < 3:
        return None

    xs = list(range(n))
    x_mean = (n - 1) / 2.0  # = sum(0..n-1)/n
    y_mean = sum(vals) / n

    num = sum((xs[i] - x_mean) * (vals[i] - y_mean) for i in range(n))
    den = sum((x - x_mean) ** 2 for x in xs)

    if den == 0:
        return None

    return num / den


def mann_kendall(ys: list) -> Optional[dict]:
    """
    Test de Mann-Kendall no-paramétrico.
    Devuelve None si n < 7.
    Devuelve dict {S, var, z, significant} donde:
      S           = estadístico de concordancia
      var         = varianza bajo H0
      z           = estadístico normalizado
      significant = |z| > 1.96 (p < 0.05, dos colas)
    """
    vals = [v for v in ys if v is not None]
    n = len(vals)
    if n < 7:
        return None

    # S = Σ_{i<j} sign(yj - yi)
    S = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            diff = vals[j] - vals[i]
            if diff > 0:
                S += 1
            elif diff < 0:
                S -= 1

    # Var(S) = n(n-1)(2n+5) / 18
    var = n * (n - 1) * (2 * n + 5) / 18.0

    # z con corrección de continuidad: (S - sign(S)) / sqrt(var)
    if S > 0:
        z = (S - 1) / math.sqrt(var)
    elif S < 0:
        z = (S + 1) / math.sqrt(var)
    else:
        z = 0.0

    significant = abs(z) > 1.96

    return {"S": S, "var": var, "z": round(z, 4), "significant": significant}


def trend_summary(ys: list) -> dict:
    """
    Resumen de tendencia combinando OLS + Mann-Kendall.

    Devuelve:
        slope      (float | None)  — pendiente OLS por período
        direction  (str)           — "subiendo" | "bajando" | "estable"
        significant (bool | None)  — True/False si n>=7, None si n<7
        n          (int)           — número de valores válidos (no-None)
    """
    vals = [v for v in ys if v is not None]
    n = len(vals)

    slope = linreg_slope(vals)
    mk = mann_kendall(vals)

    significant: Optional[bool] = mk["significant"] if mk is not None else None

    if significant and slope is not None:
        direction = "subiendo" if slope > 0 else "bajando"
    else:
        direction = "estable"

    return {
        "slope": round(slope, 4) if slope is not None else None,
        "direction": direction,
        "significant": significant,
        "n": n,
    }
