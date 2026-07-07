"""
drivers.py — Tier 3: análisis de drivers por correlación de Spearman rezagada.

Módulo 100% aditivo, solo stdlib. NO modifica nada de Tier 1/2.
Descubre asociaciones entre comportamientos (drivers) y resultados biométricos
(outcomes) usando correlación de Spearman sobre pares rezagados.

Constantes:
    MIN_N        = 25   — n mínimo de pares para reportar
    MIN_ABS_RHO  = 0.2  — correlación mínima (|ρ|) para reportar
    TOP_K        = 5    — máximo de findings a devolver

Funciones públicas:
    pair_lagged(days, driver, outcome, lag=1) -> list[(x, y)]
    analyze_drivers(days, locale="es") -> list[finding]

finding = {
    driver, outcome, lag, rho, n, significant, direction, headline, strength
}
"""
from __future__ import annotations

import math
from typing import Optional

from app.i18n import tr

# ── Constantes ────────────────────────────────────────────────────────────────

MIN_N = 25
MIN_ABS_RHO = 0.2
TOP_K = 5


# ── Funciones estadísticas puras ──────────────────────────────────────────────

def _rank(xs: list) -> list:
    """
    Devuelve los rangos (1-based) de cada elemento de xs usando promedio en empates.
    Equivalente a scipy rankdata method='average' pero sin dependencias externas.

    Ejemplo:
        _rank([10, 30, 20, 30]) → [1.0, 3.5, 2.0, 3.5]
    """
    n = len(xs)
    # Ordenar índices por valor
    sorted_idx = sorted(range(n), key=lambda i: xs[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        # Encontrar el bloque de empates
        j = i
        while j < n and xs[sorted_idx[j]] == xs[sorted_idx[i]]:
            j += 1
        # Promedio de rangos (1-based) para los empates
        avg_rank = (i + j + 1) / 2.0  # (i+1 + j) / 2 = (i+j+1)/2
        for k in range(i, j):
            ranks[sorted_idx[k]] = avg_rank
        i = j
    return ranks


def _spearman(pairs: list) -> Optional[tuple]:
    """
    Calcula ρ de Spearman sobre una lista de pares (x, y).
    Retorna (rho, n) o None si n < 3 o si la varianza de rangos es cero.

    Implementación: Pearson sobre los rangos (equivalente a la definición clásica
    cuando no hay empates, y correcto con empates via _rank).
    """
    if len(pairs) < 3:
        return None

    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    n = len(xs)

    rx = _rank(xs)
    ry = _rank(ys)

    # Pearson sobre rangos
    mean_rx = sum(rx) / n
    mean_ry = sum(ry) / n

    cov = sum((rx[i] - mean_rx) * (ry[i] - mean_ry) for i in range(n))
    var_x = sum((r - mean_rx) ** 2 for r in rx)
    var_y = sum((r - mean_ry) ** 2 for r in ry)

    if var_x == 0.0 or var_y == 0.0:
        # Varianza cero en rangos → serie constante → ρ indefinido
        return None

    rho = cov / math.sqrt(var_x * var_y)
    # Clamp numérico para evitar float fuera de [-1, 1]
    rho = max(-1.0, min(1.0, rho))
    return (rho, n)


def _sig(rho: float, n: int) -> bool:
    """
    Verifica significancia estadística (aprox. p < 0.05, dos colas) vía t-test.

    t = ρ * sqrt((n-2) / (1 - ρ²))
    |t| > ~2.0 ≈ p < 0.05 para n moderado.

    Devuelve False si n < MIN_N (por convención, aunque rho fuera válido).
    Maneja ρ = ±1 sin crash (t → ∞ → significativo).

    CONSERVADO por compat (trends/tests lo usan) — Ronda 3: analyze_drivers ya NO
    decide con este umbral fijo de 0.05 sin corregir; usa Benjamini-Hochberg sobre
    los p-values reales de _pvalue() en su lugar (ver _pvalue y analyze_drivers).
    """
    if n < MIN_N:
        return False
    denom = 1.0 - rho ** 2
    if denom <= 0.0:
        # ρ = ±1 → t → infinito → significativo
        return True
    t = rho * math.sqrt((n - 2) / denom)
    return abs(t) > 2.0


def _pvalue(rho: float, n: int) -> Optional[float]:
    """
    p-value de dos colas para ρ de Spearman, vía aproximación normal del t-test:

        t = ρ·sqrt((n−2)/(1−ρ²))
        p ≈ 2·(1−Φ(|t|)),  Φ(x) = 0.5·(1+erf(x/√2))   (math.erf, stdlib)

    Válida para n>=MIN_N (usada tal cual: no se llama con n menor en analyze_drivers).
    ρ=±1 -> p=0.0 (sin división por cero).

    Nota de honestidad (Ronda 3): esta es la aproximación normal del t-test de
    Student, razonable para n>=25 (nuestro MIN_N) pero NO modela autocorrelación
    serial entre observaciones consecutivas de series de tiempo fisiológicas — ante
    autocorrelación, estos p son optimistas (subestiman el p real). Mitigado en la
    práctica por: corrección Benjamini-Hochberg sobre múltiples tests, el piso
    |ρ|>=0.2, y quedarnos solo con el TOP_K. Modelar n-efectivo queda fuera de
    alcance de esta ronda.
    """
    if n < 3:
        return None
    denom = 1.0 - rho ** 2
    if denom <= 0.0:
        return 0.0
    t = rho * math.sqrt((n - 2) / denom)
    p = 2.0 * (1.0 - _norm_cdf(abs(t)))
    return max(0.0, min(1.0, p))


def _norm_cdf(x: float) -> float:
    """Φ(x) — CDF de la normal estándar, vía math.erf (stdlib, sin dependencias)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _benjamini_hochberg(pvalues: list, alpha: float = 0.05) -> list:
    """
    Procedimiento de Benjamini-Hochberg para controlar la tasa de falsos
    descubrimientos (FDR) sobre m tests simultáneos.

    Args:
        pvalues: lista de p-values (float), uno por test evaluado (los m EFECTIVAMENTE
                 evaluados, no un número fijo).
        alpha:   nivel de FDR objetivo (default 0.05).

    Returns:
        Lista de bool del mismo largo/orden que `pvalues`: True si ese test sobrevive
        la corrección BH.

    Algoritmo (evita el clásico off-by-one k/m vs (k+1)/m):
        1. Ordenar p ascendente, indexando 1..m.
        2. k* = el MAYOR k tal que p_(k) <= (k/m)·alpha.
        3. Sobreviven los k* PRIMEROS del orden por p (no "cada p <= su propio
           umbral" evaluado suelto -- ese approach individual puede dejar huecos
           y no es el procedimiento BH real).
    """
    m = len(pvalues)
    if m == 0:
        return []

    # (índice original, p) ordenado por p ascendente
    order = sorted(range(m), key=lambda i: pvalues[i])

    k_star = 0
    for k, idx in enumerate(order, start=1):
        threshold = (k / m) * alpha
        if pvalues[idx] <= threshold:
            k_star = k  # el mayor k que cumple la condición

    survives = [False] * m
    for k in range(k_star):
        survives[order[k]] = True
    return survives


# ── pair_lagged ────────────────────────────────────────────────────────────────

def pair_lagged(days: list, driver: str, outcome: str, lag: int = 1) -> list:
    """
    Construye pares (driver[t], outcome[t+lag]) donde ambos valores son no-None.

    Usa un índice por fecha para ser robusto a huecos en la serie temporal.
    Solo empareja cuando existe el día t y el día t+lag en el dataset.

    Args:
        days:    lista de dicts con al menos {"date": "YYYY-MM-DD", ...}
        driver:  nombre del campo driver (p.ej. "bed_min")
        outcome: nombre del campo resultado (p.ej. "hrv")
        lag:     desplazamiento en días (0=mismo día, 1=día siguiente)

    Returns:
        Lista de tuplas (x, y) donde x=driver[t], y=outcome[t+lag].
    """
    # Construir índice fecha → día
    date_to_day = {}
    for d in days:
        dt = d.get("date")
        if dt:
            date_to_day[dt] = d

    pairs = []
    for d in days:
        dt = d.get("date")
        if not dt:
            continue
        x = d.get(driver)
        if x is None:
            continue
        # Calcular la fecha del outcome (t + lag días)
        try:
            from datetime import date as _date, timedelta
            t_date = _date.fromisoformat(dt)
            lag_date = (t_date + timedelta(days=lag)).isoformat()
        except Exception:
            continue

        lag_day = date_to_day.get(lag_date)
        if lag_day is None:
            continue
        y = lag_day.get(outcome)
        if y is None:
            continue

        pairs.append((float(x), float(y)))

    return pairs


# ── DRIVER_SPECS ───────────────────────────────────────────────────────────────
# Cada spec: (driver, outcome, lag, driver_label_key, outcome_label_key, good_direction)
# driver_label_key / outcome_label_key son claves en STRINGS de i18n.
# good_direction: "higher_is_better" o "lower_is_better" desde la perspectiva del DRIVER
#                 (más de este driver → mejor outcome)
# Para bed_min: mayor bed_min = acostarse más tarde → peor resultado → "higher_is_worse"
# Para asleep: mayor asleep → mejor recovery/hrv → "higher_is_better"

DRIVER_SPECS = [
    # (driver, outcome, lag, driver_label_key, outcome_label_key, driver_higher_is_better)
    # bed_min más alto = acostarse más tarde → resultado esperado peor
    ("bed_min",  "hrv",      1, "driver_bed_late",      "driver_hrv_next",  False),
    ("bed_min",  "recovery", 1, "driver_bed_late",      "driver_rec_next",  False),
    # asleep más → mejor resultado
    ("asleep",   "recovery", 1, "driver_more_sleep",    "driver_rec_next",  True),
    ("asleep",   "hrv",      1, "driver_more_sleep",    "driver_hrv_next",  True),
    # strain alto → peor recovery
    ("strain",   "recovery", 1, "driver_more_strain",   "driver_rec_next",  False),
    # pasos → recovery
    ("steps",    "recovery", 1, "driver_more_steps",    "driver_rec_next",  True),
    # vigorous → hrv
    ("vigorous", "hrv",      1, "driver_more_vigorous", "driver_hrv_next",  True),
    # mismo día: asleep → recovery (lag=0)
    ("asleep",   "recovery", 0, "driver_more_sleep",    "driver_rec_same",  True),
]


# ── analyze_drivers ────────────────────────────────────────────────────────────

def analyze_drivers(days: list, locale: str = "es") -> list:
    """
    Analiza todos los DRIVER_SPECS y devuelve los findings que pasan los filtros:
        BH-survivor (Benjamini-Hochberg sobre los m specs efectivamente testeados)
        AND n >= MIN_N   AND   |ρ| >= MIN_ABS_RHO

    Ronda 3: reemplaza el umbral fijo p<0.05 sin corregir (8 tests simultáneos ≈ 34%
    falso positivo familiar) por BH real. BH corre sobre TODOS los specs que
    alcanzaron a tener un (rho, n, p) calculable (m efectivo, no el 8 fijo de
    DRIVER_SPECS) — un spec sin pares suficientes ni siquiera entra al pool de BH.
    Los filtros n>=MIN_N y |ρ|>=MIN_ABS_RHO se aplican DESPUÉS de BH (no se aflojan).

    Ordenados por |ρ| descendente, máximo TOP_K.

    Cada finding:
        {
            driver, outcome, lag,
            rho (float, 2 dec), n (int), p (float, 4 dec),
            significant (bool), direction (str), headline (str), strength (str)
        }
    """
    # ── Paso 1: computar (rho, n, p) para cada spec con pares suficientes ──────
    candidates = []  # cada item: dict con spec + rho/n/p
    for spec in DRIVER_SPECS:
        driver, outcome, lag, driver_label_key, outcome_label_key, driver_higher_is_better = spec

        pairs = pair_lagged(days, driver, outcome, lag)
        if not pairs:
            continue

        result = _spearman(pairs)
        if result is None:
            continue

        rho, n = result
        p = _pvalue(rho, n)
        if p is None:
            continue

        candidates.append({
            "spec": spec, "rho": rho, "n": n, "p": p,
        })

    # ── Paso 2: Benjamini-Hochberg sobre los m specs EFECTIVAMENTE evaluados ───
    pvalues = [c["p"] for c in candidates]
    survives_bh = _benjamini_hochberg(pvalues, alpha=0.05)

    findings = []
    for candidate, survived in zip(candidates, survives_bh):
        if not survived:
            continue

        spec = candidate["spec"]
        driver, outcome, lag, driver_label_key, outcome_label_key, driver_higher_is_better = spec
        rho, n, p = candidate["rho"], candidate["n"], candidate["p"]

        # Filtros existentes, aplicados DESPUÉS de BH (sin aflojarlos).
        if n < MIN_N:
            continue
        if abs(rho) < MIN_ABS_RHO:
            continue

        sig = True  # sobrevivió BH -> significativo (reemplaza el _sig() de umbral fijo)

        # Determinar dirección semántica del resultado
        # Si driver_higher_is_better=True: rho>0 → "mejora", rho<0 → "empeora"
        # Si driver_higher_is_better=False: rho<0 → "mejora", rho>0 → "empeora"
        if driver_higher_is_better:
            direction = tr("direction_improve", locale) if rho > 0 else tr("direction_worsen", locale)
        else:
            direction = tr("direction_improve", locale) if rho < 0 else tr("direction_worsen", locale)

        # Strength
        abs_rho = abs(rho)
        if abs_rho >= 0.4:
            strength = tr("strength_strong", locale)
        elif abs_rho >= 0.3:
            strength = tr("strength_moderate", locale)
        else:
            strength = tr("strength_weak", locale)

        # Headline localizado (ASOCIACIÓN, no causa)
        headline = _build_headline(
            driver, outcome, lag, driver_label_key, outcome_label_key,
            driver_higher_is_better, rho, n, locale
        )

        findings.append({
            "driver": driver,
            "outcome": outcome,
            "lag": lag,
            "rho": round(rho, 2),
            "n": n,
            "p": round(p, 4),
            "significant": sig,
            "direction": direction,
            "headline": headline,
            "strength": strength,
        })

    # Ordenar por |ρ| descendente, limitar a TOP_K
    findings.sort(key=lambda f: abs(f["rho"]), reverse=True)
    return findings[:TOP_K]


# ── _build_headline ────────────────────────────────────────────────────────────

def _build_headline(
    driver: str,
    outcome: str,
    lag: int,
    driver_label_key: str,
    outcome_label_key: str,
    driver_higher_is_better: bool,
    rho: float,
    n: int,
    locale: str = "es",
) -> str:
    """
    Construye un headline legible indicando la dirección de la asociación.
    Siempre etiquetado como ASOCIACIÓN (no causa).
    """
    rho_sign = "+" if rho > 0 else "−"
    rho_str = f"ρ={rho_sign}{abs(rho):.2f}, n={n}"

    # Determinar si el outcome sube o baja.
    pos = rho > 0
    outcome_sube = pos

    # Nombre legible del outcome
    if outcome == "hrv":
        outcome_name = tr("outcome_hrv", locale)
    elif outcome == "recovery":
        outcome_name = tr("outcome_recovery", locale)
    else:
        outcome_name = outcome

    if outcome_sube:
        outcome_dir = tr("outcome_higher", locale, outcome_name=outcome_name)
    else:
        outcome_dir = tr("outcome_lower", locale, outcome_name=outcome_name)

    # Lag string
    if lag == 0:
        lag_str = tr("lag_same_day", locale)
    elif lag == 1:
        lag_str = tr("lag_next_day", locale)
    else:
        lag_str = tr("lag_n_days", locale, lag=lag)

    # Nombre legible del driver, descrito desde el extremo correcto del signo de ρ
    if driver == "bed_min":
        dl = tr("dl_bed_late", locale) if pos else tr("dl_bed_early", locale)
    elif driver == "asleep":
        dl = tr("dl_more_sleep", locale) if pos else tr("dl_less_sleep", locale)
    elif driver == "strain":
        dl = tr("dl_more_strain", locale) if pos else tr("dl_less_strain", locale)
    elif driver == "steps":
        dl = tr("dl_more_steps", locale) if pos else tr("dl_less_steps", locale)
    elif driver == "vigorous":
        dl = tr("dl_more_vigorous", locale) if pos else tr("dl_less_vigorous", locale)
    else:
        dl = tr(driver_label_key, locale)

    return tr("headline_pattern", locale, dl=dl, outcome_dir=outcome_dir, lag_str=lag_str, rho_str=rho_str)
