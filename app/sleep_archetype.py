"""
sleep_archetype.py — Arquetipo de sueño mensual gamificado (Roadmap P2, F8).

Módulo PURO (sin I/O), mismo patrón que sleep_scores.py/sleep_coach.py:
nunca lanza, degrada a None ante datos insuficientes. Separado de report.py
siguiendo el patrón ya establecido del repo (sleep_scores.py separado de
sleep_coach.py, programs.py separado de plan_store.py) — testeable en
aislamiento; report.py solo lo invoca y adjunta el resultado.

`classify_month(days, ref_date, locale) -> dict | None`

Agrega el ÚLTIMO mes calendario COMPLETO relativo a `ref_date` (reusa
`_month_bounds`/`_days_in_range` de app.report — importados, no duplicados) y
clasifica el patrón de sueño del mes en UNO de 6 arquetipos, vía una tabla de
decisión determinista sobre 2 ejes:

    - nivel de CONSISTENCIA del mes (alta/media/baja), de
      sleep_scores.consistency_score() sobre las noches del mes.
    - relación DURACIÓN-vs-NECESIDAD (cumple/corta/excede), del ratio medio
      asleep/need*100 SIN CAP por noche del mes (deliberadamente NO se usa
      sleep_scores.sleep_score() para este eje — ese score capa a 100 por
      diseño, así que un mes que duerme sistemáticamente de más nunca
      produciría una media >100 y el eje "excede" quedaría inalcanzable). El
      `sleep_score` capado SÍ se reporta en `metrics` como dato de display
      (mismo campo que el usuario ya conoce del resto de la app).

La hora media de acostarse (`bed_min`) desempata entre "cumple" y "excede"
cuando ambos ejes coinciden en un patrón saludable pero uno se acuesta
consistentemente muy tarde o muy temprano (ver tabla `_classify`).

Gate: >=14 noches con `asleep` no-None en el mes -> si no, None (roadmap
criterio 8: "no cumple" nunca se disfraza de "arquetipo malo", simplemente no
hay dato suficiente).

Percentiles (criterio 11): de cada métrica del mes contra la distribución de
TODAS las noches individuales de `days` (histórico propio, NO población — no
hay base poblacional ni opt-in de comparar contra otros usuarios). Se derivan
en una sola pasada sobre el dataset ya cargado, sin persistir nada nuevo.

Nombres de los 6 arquetipos (decisión del implementador, roadmap criterio 10:
"a definir por el implementador, sin copiar los nombres de animales de Fitbit
tal cual"): se usan arquetipos de "clima/tiempo atmosférico" en vez de
animales — Reloj Suizo, Nocturno Templado, Madrugador Templado, Corto de
Cuerda, Ritmo Errático, Sueño Extendido. Claves i18n `archetype_<slug>_name`/
`archetype_<slug>_desc` en app/i18n.py, ×4 locales.
"""
from __future__ import annotations

import datetime
import statistics
from typing import Any, Optional

from app.i18n import tr
from app.report import _month_bounds, _days_in_range
from app.sleep_scores import sleep_score as _sleep_score
from app.sleep_scores import sleep_need_min as _sleep_need_min

# ── Gate y ventanas ──────────────────────────────────────────────────────────

_MIN_NIGHTS = 14  # criterio 8: >=14 noches con asleep no-None en el mes

# ── Umbrales de clasificación (documentados, deterministas) ─────────────────
# Consistencia (consistency_score 0-100, ya definido en sleep_scores.py).
_CONSISTENCY_HIGH = 70
_CONSISTENCY_MID = 40

# Duración vs necesidad (media de sleep_score 0-100 = % de la necesidad
# cumplida por noche, promediado sobre el mes).
_DURATION_MEETS_LOW = 90    # >=90 y <=110 -> "cumple"
_DURATION_MEETS_HIGH = 110
# <90 -> "corta"; >110 -> "excede"

# Desempate por hora de acostarse: bed_min es el offset en minutos desde
# medianoche (puede ser negativo si es antes de medianoche) — mismo campo que
# usa el resto del repo (sleep_coach.py, scoring.py). >120 (después de 02:00)
# se considera "tarde"; el resto, "normal" para efectos del desempate.
_LATE_BEDTIME_THRESHOLD_MIN = 120


def _combined_stdev_for_days(subset: list) -> Optional[float]:
    """Réplica de sleep_scores._combined_stdev pero sobre un subconjunto de
    días YA filtrado (el mes), no sobre 'las últimas n noches' del array
    completo — sleep_scores.consistency_score() solo sabe operar por ventana
    final, así que aquí se reimplementa la misma fórmula (documentada,
    idéntica) sobre el subset arbitrario. Puro, nunca lanza."""
    bed_vals: list = []
    wake_vals: list = []
    for d in subset or []:
        if not isinstance(d, dict):
            continue
        bed_min = d.get("bed_min")
        wake_min = d.get("waketime")
        wake_parsed = None
        if isinstance(wake_min, str) and ":" in wake_min:
            try:
                h, m = wake_min.split(":", 1)
                h, m = int(h), int(m)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    wake_parsed = h * 60 + m
            except Exception:
                wake_parsed = None
        if bed_min is None or wake_parsed is None:
            continue
        try:
            bed_vals.append(float(bed_min))
        except (TypeError, ValueError):
            continue
        wake_vals.append(float(wake_parsed))

    if len(bed_vals) < 5:  # mismo mínimo que _CONSISTENCY_MIN_NIGHTS de sleep_scores.py
        return None
    try:
        return (statistics.pstdev(bed_vals) + statistics.pstdev(wake_vals)) / 2.0
    except Exception:
        return None


def _consistency_score_for_month(subset: list) -> Optional[int]:
    """Mismo mapeo lineal que sleep_scores.consistency_score(), aplicado sobre
    el subset del mes (no la ventana final del array completo)."""
    sigma = _combined_stdev_for_days(subset)
    if sigma is None:
        return None
    _PERFECT, _ZERO = 20, 120
    if sigma <= _PERFECT:
        return 100
    if sigma >= _ZERO:
        return 0
    frac = (sigma - _PERFECT) / (_ZERO - _PERFECT)
    return int(round(100 * (1 - frac)))


def _percentile_of_value(value: Optional[float], population: list) -> Optional[int]:
    """Percentil (0-100) de `value` dentro de `population` (lista de floats),
    por conteo de valores <= value / n * 100 (definición simple, sin
    interpolación — suficiente para un badge informativo, no un test
    estadístico). None si value o population están vacíos. Puro."""
    if value is None or not population:
        return None
    try:
        n = len(population)
        n_le = sum(1 for v in population if v <= value)
        return int(round(n_le / n * 100))
    except Exception:
        return None


# ── Catálogo de arquetipos (criterio 10) ─────────────────────────────────────
# slugs -> claves i18n `archetype_<slug>_name` / `archetype_<slug>_desc`.
_ARCHETYPES = (
    "swiss_clock",       # alta consistencia + duración cumple
    "warm_night_owl",    # alta consistencia + duración cumple/excede pero se acuesta tarde
    "early_riser",       # alta consistencia + duración cumple/excede, hora normal/temprana
    "wound_too_tight",    # duración corta (consistente o no)
    "erratic_rhythm",    # baja consistencia
    "extended_stay",     # duración excede claramente, consistencia media/alta, sin desempate de tarde
)


def _duration_bucket(mean_raw_ratio: Optional[float]) -> str:
    """Bucket de duración a partir del ratio SIN CAP asleep/need*100 (ver
    docstring del módulo — sleep_score() capado no sirve para detectar
    'excede')."""
    if mean_raw_ratio is None:
        return "unknown"
    if mean_raw_ratio < _DURATION_MEETS_LOW:
        return "short"
    if mean_raw_ratio > _DURATION_MEETS_HIGH:
        return "excess"
    return "meets"


def _consistency_bucket(score: Optional[int]) -> str:
    if score is None:
        return "unknown"
    if score >= _CONSISTENCY_HIGH:
        return "high"
    if score >= _CONSISTENCY_MID:
        return "mid"
    return "low"


def _classify(consistency_bucket: str, duration_bucket: str, mean_bed_min: Optional[float]) -> str:
    """Tabla de decisión determinista (criterio 10): 2 ejes (consistencia x
    duración-vs-need) con hora de acostarse como desempate. Nunca devuelve
    algo fuera de _ARCHETYPES."""
    is_late = mean_bed_min is not None and mean_bed_min > _LATE_BEDTIME_THRESHOLD_MIN

    # Duración corta domina el mensaje (lo urgente primero) salvo que la
    # consistencia sea tan baja que el problema real sea el ritmo, no el total.
    if duration_bucket == "short" and consistency_bucket != "low":
        return "wound_too_tight"

    if consistency_bucket == "low":
        return "erratic_rhythm"

    if duration_bucket == "excess":
        # Alta/media consistencia pero duerme de más de forma sostenida.
        return "extended_stay"

    # duration_bucket == "meets" (o "unknown", degradado abajo por el gate).
    if consistency_bucket == "high":
        return "warm_night_owl" if is_late else "swiss_clock"
    # consistency_bucket == "mid"
    return "warm_night_owl" if is_late else "early_riser"


def classify_month(days: list, ref_date: Optional[datetime.date] = None,
                    locale: str = "es") -> Optional[dict]:
    """Clasifica el ÚLTIMO mes calendario COMPLETO relativo a `ref_date` (hoy
    si None) en uno de 6 arquetipos de sueño. None si el gate de >=14 noches
    con `asleep` no-None no se cumple (incluye el caso de CERO datos ese mes
    -> nunca crashea). Nunca lanza."""
    try:
        days = days or []
        ref_date = ref_date or datetime.date.today()

        # Último mes COMPLETO: si ref_date cae dentro del mes en curso, se usa
        # el mes calendario ANTERIOR (mismo criterio que _last_complete_period
        # de report.py, pero SIEMPRE monthly aquí — F8 es mensual por diseño).
        start, end = _month_bounds(ref_date)
        if end >= ref_date:
            prev_ref = start - datetime.timedelta(days=1)
            start, end = _month_bounds(prev_ref)

        month_days = _days_in_range(days, start, end)

        nights_with_sleep = [d for d in month_days if d.get("asleep") is not None]
        if len(nights_with_sleep) < _MIN_NIGHTS:
            return None

        # ── Métricas del mes (criterio 9) ────────────────────────────────────
        asleep_vals = [float(d["asleep"]) for d in nights_with_sleep]
        mean_asleep = sum(asleep_vals) / len(asleep_vals)

        eff_vals = [float(d["eff"]) for d in nights_with_sleep if d.get("eff") is not None]
        mean_eff = (sum(eff_vals) / len(eff_vals)) if eff_vals else None

        bed_vals = [float(d["bed_min"]) for d in nights_with_sleep if d.get("bed_min") is not None]
        mean_bed_min = (sum(bed_vals) / len(bed_vals)) if bed_vals else None

        # sleep_score por noche (display, CAPADO a 100 — mismo campo que ya
        # conoce el usuario del resto de la app, ver /api/sleep-coach) y ratio
        # SIN CAP (uso interno, solo para clasificar): sleep_score() capa a
        # 100 por diseño ("% de la necesidad cumplida, nunca más de 100%"),
        # así que un mes que DUERME DE MÁS jamás produciría un promedio >100
        # si se usara el capado para el bucket de duración — el eje
        # "excede" de la tabla de decisión (criterio 10) quedaría inalcanzable.
        # need del día: sub-serie de 'days' hasta esa fecha (inclusive) —
        # sleep_need_min usa la deuda de los 7 días previos + strain/recovery
        # de "hoy" (=esa noche), consistente con cómo se calcula en vivo en
        # /api/sleep-coach. Si no hay suficiente historial para el need, esa
        # noche no participa (degrada, no crashea).
        sleep_scores: list = []
        raw_ratios: list = []
        for d in nights_with_sleep:
            date_str = d.get("date")
            try:
                idx = next((i for i, dd in enumerate(days) if dd.get("date") == date_str), None)
            except Exception:
                idx = None
            if idx is None:
                continue
            days_up_to = days[: idx + 1]
            need = _sleep_need_min(days_up_to, {}, None)
            score = _sleep_score(d.get("asleep"), need)
            if score is not None:
                sleep_scores.append(score)
            try:
                asleep_val = d.get("asleep")
                if asleep_val is not None and need is not None and float(need) > 0:
                    raw_ratios.append(float(asleep_val) / float(need) * 100.0)
            except (TypeError, ValueError):
                continue

        mean_sleep_score = (sum(sleep_scores) / len(sleep_scores)) if sleep_scores else None
        mean_raw_ratio = (sum(raw_ratios) / len(raw_ratios)) if raw_ratios else None

        consistency = _consistency_score_for_month(nights_with_sleep)

        duration_bucket = _duration_bucket(mean_raw_ratio)
        consistency_bucket = _consistency_bucket(consistency)
        archetype_slug = _classify(consistency_bucket, duration_bucket, mean_bed_min)
        if archetype_slug not in _ARCHETYPES:
            archetype_slug = "erratic_rhythm"  # defensivo, nunca debería dispararse

        # ── Percentiles vs histórico propio (criterio 11) ────────────────────
        all_asleep = [float(d["asleep"]) for d in days if isinstance(d, dict) and d.get("asleep") is not None]
        all_eff = [float(d["eff"]) for d in days if isinstance(d, dict) and d.get("eff") is not None]

        pct_asleep = _percentile_of_value(mean_asleep, all_asleep)
        pct_eff = _percentile_of_value(mean_eff, all_eff)

        return {
            "period_key": start.strftime("%Y-%m"),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "n_nights": len(nights_with_sleep),
            "archetype": archetype_slug,
            "name": tr(f"archetype_{archetype_slug}_name", locale),
            "description": tr(f"archetype_{archetype_slug}_desc", locale),
            "metrics": {
                "mean_asleep_min": round(mean_asleep, 1),
                "mean_sleep_score": round(mean_sleep_score, 1) if mean_sleep_score is not None else None,
                "consistency_score": consistency,
                "mean_efficiency_pct": round(mean_eff, 1) if mean_eff is not None else None,
                "mean_bedtime_min": round(mean_bed_min, 1) if mean_bed_min is not None else None,
            },
            "percentiles": {
                "mean_asleep_min": pct_asleep,
                "mean_efficiency_pct": pct_eff,
            },
        }
    except Exception:
        return None
