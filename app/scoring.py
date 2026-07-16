"""
scoring.py — build_dataset portado EXACTO de vitals_sync.py.
NO cambiar fórmulas, percentiles, pesos ni NEED (excepción: Ronda 5, ver abajo).

Cambios de higiene (compuertas, NO fórmulas):
- Recovery: exige ≥2 componentes reales de {hrv, rhr, asleep}. Un día con solo HRV
  no recibe recovery (quedaba 0 espurio al clampearse el componente único).
- Siesta-como-noche: un registro de sueño se ignora (no se setean campos de sueño)
  si su onset cae en ventana diurna (bed_min > 240 → después de 04:00, o
  bed_min < -300 → antes de 19:00 del día anterior) O si asleep < 120 min.
  Umbral documentado: [-300, 240] en minutos desde medianoche (–300=19:00, 240=04:00).

Tier 2 añadidos (aditivos, NO modifican fórmulas golden):
- _rolling_sd: pstdev de las últimas ~30 lecturas no-None.
- summary: hrv_sd, rhr_sd, resp_sd, skin_temp_sd (Feature A).
- day["trimp"]: TRIMP de Banister agregado de sesiones del día (Feature C).
- summary["acwr"] / summary["acwr_zone"]: ratio agudo:crónico 7d:28d sobre strain (Feature C).

Ronda 5 (ENGINE v2 — ÚNICA ronda que toca fórmulas núcleo, versionada explícitamente):
- Strain v2: reemplaza el proxy lineal `vigorous*0.10 + steps/2500` por carga
  fisiológica TRIMP + fallback calibrado + NEAT, comprimida asintóticamente a 0-21.
  Ver STRAIN_V2_* constantes abajo (procedencia: scripts/calibrate_strain_v2.py).
- Recovery: escala rodante trailing-90d en vez de percentil sobre la serie completa
  (mata look-ahead bias). Pesos 0.55/0.25/0.20 y compuerta quirúrgica INTACTOS.
- Sueño: NEED se parametriza vía `sleep_target_min` (default 480 = comportamiento
  idéntico a antes).
- summary["engine"] = {"version": 2, ...} — tag de versión del motor.
"""
from __future__ import annotations

import datetime
import math
import statistics

# ── Rolling SD (Tier 2 Feature A) ────────────────────────────────────────────
_ROLLING_SPAN = 30
_ROLLING_MIN_N = 5


def _rolling_sd(series_dict: dict, span: int = _ROLLING_SPAN, min_n: int = _ROLLING_MIN_N):
    """
    Desviación estándar poblacional (pstdev) sobre las últimas ~span lecturas
    no-None de series_dict {date: value}.
    Devuelve None si hay <min_n lecturas válidas.
    """
    vals = [v for _, v in sorted(series_dict.items()) if v is not None]
    vals = vals[-span:]  # últimas span lecturas
    if len(vals) < min_n:
        return None
    return statistics.pstdev(vals)


# ── EWMA baseline rodante ──────────────────────────────────────────────────────
# span=30 lecturas → alpha = 2/(30+1) ≈ 0.0645
# Se computa sobre lecturas ordenadas cronológicamente, NO sobre días-calendario
# (la serie tiene huecos — RHR ~25% histórico — y el relleno de días sería frágil).
# Las últimas ~30 lecturas densas reflejan el baseline reciente sin distorsión.
_EWMA_SPAN = 30


def _ewma_recent(series_dict: dict, span: int = _EWMA_SPAN, min_n: int = 5):
    """
    EWMA exponencial sobre lecturas ordenadas de series_dict {date: value}.
    Devuelve round(s, 1) o None si hay <min_n lecturas válidas.
    """
    vals = [v for _, v in sorted(series_dict.items()) if v is not None]
    if len(vals) < min_n:
        return None
    a = 2 / (span + 1)
    s = vals[0]
    for x in vals[1:]:
        s = a * x + (1 - a) * s
    return round(s, 1)


def recent_base(summary: dict, metric: str):
    """
    Devuelve la base reciente (EWMA ~30 lecturas) para 'metric' in {'hrv','rhr'},
    con fallback a la base all-time si no existe o es None.
    Esto garantiza backward-compat: summaries que solo traen hrv_base/rhr_base
    se comportan igual que antes.
    """
    return summary.get(f"{metric}_base_recent") or summary.get(f"{metric}_base")


# ── Umbrales de higiene (no son fórmulas: son compuertas) ──────────────────────
# Siesta: onset fuera de ventana nocturna [-300, 240] (19:00–04:00) O sueño < 120 min
_NAP_BED_MIN_LO = -300   # onset antes de 19:00 del día anterior
_NAP_BED_MIN_HI =  240   # onset después de 04:00 (05:12 en adelante ya es diurno)
_NAP_MIN_ASLEEP = 120    # menos de 2h → no cuenta como noche


def _is_nap(sleep_rec: dict) -> bool:
    """True si el registro de sueño parece una siesta (onset diurno o muy corto)."""
    bm = sleep_rec.get("bed_min")
    asleep = sleep_rec.get("asleep") or 0
    if bm is not None and (bm > _NAP_BED_MIN_HI or bm < _NAP_BED_MIN_LO):
        return True
    if asleep < _NAP_MIN_ASLEEP:
        return True
    return False


# ── Strain v2 — híbrido TRIMP (Ronda 5) ─────────────────────────────────────────
# Constantes calibradas por scripts/calibrate_strain_v2.py, corrido UNA vez sobre
# data/health_compact.json (histórico real local, 347 días, 42 con TRIMP real).
# Ver informe de Ronda 5 para la tabla v1-vs-v2 completa (medianas, p10/p90,
# correlación). Resultado de la corrida: Δmediana(v2-v1)=-0.05 (dentro de ±0.5),
# corr(strain,trimp) sube de 0.855 (v1) a 0.966 (v2), 0 días fuera de [0,21].
#
# F_VIG: el histórico real local NO trae Active Zone Minutes en NINGÚN día
# (0/347, incluidos los 42 días con TRIMP real) — no hay overlap trimp+vigorous
# con el que regresionar la pendiente. Se usa un valor de arranque documentado
# (no medido): F_VIG=2.5 TRIMP-equivalente por minuto vigoroso. Si una fuente
# futura aporta AZM real, re-correr calibrate_strain_v2.py para regresionar de
# verdad y actualizar este comentario.
STRAIN_V2_F_VIG = 2.5      # TRIMP-equiv por minuto "vigorous" (AZM) — fallback sin HR
# F_STEPS: arranque roadmap (10000 pasos ≈ 20 unidades TRIMP-equiv de NEAT).
STRAIN_V2_F_STEPS = 500.0  # TRIMP-equiv = steps / F_STEPS
# K: búsqueda binaria para que mediana(strain_v2) ≈ mediana(strain_v1) sobre el
# histórico real (continuidad de escala — el usuario no ve saltar su strain típico).
# ⚠️ RE-CALIBRADO post-deploy 2026-07-02: el K=244.63 original se calibró sobre el
# dataset LOCAL de la Mac (solo 46 días con strain, ventana corta de steps). En
# PRODUCCIÓN (394 días, steps de todo el año vía HealthKit) la mediana v2 caía a
# 1.6 vs 3.8 de v1 — fuera del criterio ±0.5. K=96.87 sale de la misma búsqueda
# binaria pero sobre la serie de producción real (n=363 días con señal):
# mediana v2 = 3.8 = mediana v1, p90 7.3, max 18.4. Lección: calibrar SIEMPRE
# contra el dataset más denso disponible (producción), no el sandbox local.
STRAIN_V2_K = 96.87


def _strain_v2(trimp_day, vigorous_min, steps) -> float | None:
    """
    Carga diaria L(d) en unidades TRIMP + compresión asintótica a la escala 0-21.

      L(d) = trimp_day                              si trimp_day > 0 (HR real ese día)
           + vigorous_min * F_VIG                    si NO hay trimp_day (evita doble
                                                       conteo: el AZM de una sesión con
                                                       HR ya está dentro del TRIMP)
           + steps / F_STEPS                          NEAT — siempre suma si hay steps

      strain = 21 * (1 - exp(-L / K))  — asintótica, monotónica, nunca satura de golpe.

    Devuelve None si no hay NINGUNA señal (ni trimp, ni vigorous, ni steps) — v2
    amplía la presencia de v1 (antes solo steps) a "steps O workouts".
    """
    has_signal = bool(trimp_day) or bool(vigorous_min) or steps is not None
    if not has_signal:
        return None

    L = 0.0
    if trimp_day:
        L += trimp_day
    elif vigorous_min:
        # Solo fallback si NO hay TRIMP real ese día — anti doble-conteo.
        L += vigorous_min * STRAIN_V2_F_VIG
    if steps:
        L += steps / STRAIN_V2_F_STEPS

    if L <= 0:
        return 0.0
    return round(21.0 * (1.0 - math.exp(-L / STRAIN_V2_K)), 1)


# ── Recovery rodante (Ronda 5) — mata look-ahead bias del percentil global ──────
_ROLLING_RECOVERY_WINDOW = 90   # días trailing (solo pasado, incluye el día d)
_ROLLING_RECOVERY_MIN_FULL = 30  # ≥30 lecturas en la ventana → percentiles de la ventana
_ROLLING_RECOVERY_MIN_PARTIAL = 10  # 10-29 → percentiles de TODA la historia hasta d
# <10 → fallback a los defaults actuales (40,70)/(48,60), igual que build_dataset
# cuando no hay datos en absoluto.


def _pct(a, p):
    """Percentil por interpolación lineal (idéntico al pct() interno histórico)."""
    if not a:
        return 0
    a = sorted(a)
    k = (len(a) - 1) * p / 100
    f = int(k)
    return a[f] if f + 1 >= len(a) else a[f] + (a[f + 1] - a[f]) * (k - f)


def _rolling_percentile_ranges(dates_sorted: list, hrv_by_date: dict, rhr_by_date: dict):
    """
    Para cada fecha en dates_sorted (orden cronológico), calcula el rango
    percentil 5-95 de HRV y RHR usando SOLO datos hasta esa fecha inclusive
    (anti look-ahead): ventana trailing de _ROLLING_RECOVERY_WINDOW días.

      len(ventana) >= 30  → percentiles de la ventana de 90 días
      10 <= len < 30       → percentiles de TODA la historia hasta d (inclusive)
      len < 10              → fallback (40,70) / (48,60)

    Devuelve dict {date: (hlo, hhi, rlo, rhi)}.
    """
    result = {}
    # Listas ordenadas cronológicamente de (fecha, valor) para HRV y RHR.
    hrv_hist: list = []
    rhr_hist: list = []
    hrv_idx = 0
    rhr_idx = 0
    hrv_sorted_dates = sorted(hrv_by_date)
    rhr_sorted_dates = sorted(rhr_by_date)

    for d in dates_sorted:
        # Avanzar los índices para incluir todo dato con fecha <= d (solo pasado).
        while hrv_idx < len(hrv_sorted_dates) and hrv_sorted_dates[hrv_idx] <= d:
            hrv_hist.append((hrv_sorted_dates[hrv_idx], hrv_by_date[hrv_sorted_dates[hrv_idx]]))
            hrv_idx += 1
        while rhr_idx < len(rhr_sorted_dates) and rhr_sorted_dates[rhr_idx] <= d:
            rhr_hist.append((rhr_sorted_dates[rhr_idx], rhr_by_date[rhr_sorted_dates[rhr_idx]]))
            rhr_idx += 1

        cutoff = (datetime.date.fromisoformat(d) -
                  datetime.timedelta(days=_ROLLING_RECOVERY_WINDOW - 1)).isoformat()

        hrv_window = [v for dt, v in hrv_hist if dt >= cutoff]
        rhr_window = [v for dt, v in rhr_hist if dt >= cutoff]
        hrv_all = [v for _, v in hrv_hist]
        rhr_all = [v for _, v in rhr_hist]

        if len(hrv_window) >= _ROLLING_RECOVERY_MIN_FULL:
            hlo, hhi = _pct(hrv_window, 5), _pct(hrv_window, 95)
        elif len(hrv_all) >= _ROLLING_RECOVERY_MIN_PARTIAL:
            hlo, hhi = _pct(hrv_all, 5), _pct(hrv_all, 95)
        else:
            hlo, hhi = 40, 70

        if len(rhr_window) >= _ROLLING_RECOVERY_MIN_FULL:
            rlo, rhi = _pct(rhr_window, 5), _pct(rhr_window, 95)
        elif len(rhr_all) >= _ROLLING_RECOVERY_MIN_PARTIAL:
            rlo, rhi = _pct(rhr_all, 5), _pct(rhr_all, 95)
        else:
            rlo, rhi = 48, 60

        if hhi == hlo:
            hhi = hlo + 1
        if rhi == rlo:
            rhi = rlo + 1

        result[d] = (hlo, hhi, rlo, rhi)

    return result


# ── Recovery v3 — anclado a línea base (estándar de mercado: WHOOP/Oura/Fitbit) ──
# Reemplaza la normalización percentil-lineal (v2) por z-score vs la base personal
# + curva logística. Filosofía de mercado: "en tu base = listo/verde (~70)", arriba
# de base → sube, abajo → baja, con saturación suave en los extremos (mata el zigzag
# 25→95 de la escala percentil y el rango se vuelve intuitivo).
# Calibración: A elegido para que la MEDIANA de 30 días del perfil real ≈ 70 (día
# típico verde). Corrida sobre producción (perfil default, historia con HRV real):
# A=1.06 → median30≈69.5, rango histórico 12–94. B/A y pisos documentados abajo.
# Pesos 0.55/0.25/0.20 y la compuerta (recovery solo si hay HRV o sueño) INTACTOS.
RECOVERY_ANCHORED = True             # False → vuelve al motor v2 percentil (revert 1 línea)
RECOVERY_V3_A = 1.06                 # ancla logística: base (W=0) → ~74; median30 ≈ 70
RECOVERY_V3_B = 0.85                 # sensibilidad: pendiente de la logística sobre W
RECOVERY_V3_HSD_FLOOR = 3.0          # piso de sd de HRV (ms) — evita z gigante en series planas
RECOVERY_V3_RSD_FLOOR = 1.5          # piso de sd de RHR (bpm)
RECOVERY_V3_SLEEP_SPREAD = 0.12      # 1 sd ≈ 12% de desvío vs NEED de sueño
_ROLLING_BASELINE_WINDOW = 90        # días trailing (solo pasado, incluye el día d)
_ROLLING_BASELINE_TAKE = 30          # últimas N lecturas dentro de la ventana
_ROLLING_BASELINE_SD_MIN_N = 2       # pstdev necesita ≥2 puntos (hecho matemático, no umbral elegido)


def _rolling_baseline_ranges(dates_sorted: list, hrv_by_date: dict, rhr_by_date: dict):
    """Por cada fecha (orden cronológico), media y desviación (pstdev) TRAILING de
    HRV y RHR usando SOLO datos hasta esa fecha inclusive (anti look-ahead, igual
    criterio que _rolling_percentile_ranges): ventana de 90 días, últimas 30 lecturas.
    Devuelve {date: (hb, hsd, rb, rsd)} con None donde no haya historia suficiente."""
    result = {}
    hrv_hist: list = []
    rhr_hist: list = []
    hi = ri = 0
    hd = sorted(hrv_by_date)
    rd = sorted(rhr_by_date)
    for d in dates_sorted:
        while hi < len(hd) and hd[hi] <= d:
            hrv_hist.append((hd[hi], hrv_by_date[hd[hi]])); hi += 1
        while ri < len(rd) and rd[ri] <= d:
            rhr_hist.append((rd[ri], rhr_by_date[rd[ri]])); ri += 1
        cutoff = (datetime.date.fromisoformat(d) -
                  datetime.timedelta(days=_ROLLING_BASELINE_WINDOW - 1)).isoformat()
        hw = [v for dt, v in hrv_hist if dt >= cutoff][-_ROLLING_BASELINE_TAKE:]
        rw = [v for dt, v in rhr_hist if dt >= cutoff][-_ROLLING_BASELINE_TAKE:]
        # Arranque en frío: media y sd de las lecturas que HAYA, sin exigir un mínimo
        # (antes se exigían 5 y por debajo se devolvía None, lo que hacía que el
        # consumidor descartara el componente en silencio aunque el día tuviera el dato).
        #   >=2 lecturas -> media y sd reales de la ventana. Con >=5 el resultado es
        #                   idéntico al de antes: mismas expresiones, mismos valores.
        #   1 lectura    -> media = esa lectura; sd = None -> el consumidor aplica el
        #                   piso RECOVERY_V3_*_FLOOR -> z=0 -> "estás en tu base".
        #   0            -> (None, None) -> el componente se OMITE (ausencia ≠ base).
        # 🔴 La sd DEBE ser la real, no el piso: el piso (3.0 ms) está calibrado para
        # series PLANAS. Aplicarlo a 2 lecturas separadas (p.ej. 119 y 52 ms, sd real
        # ~34) subestima el ancho ~11x y dispara z≈-11 -> recovery satura en 0/1.
        # Medido sobre un histórico real: con el piso daba 74,0,0,19,2; con la sd real
        # da 74,55,59,69,63.
        hb = statistics.mean(hw) if hw else None
        hsd = statistics.pstdev(hw) if len(hw) >= _ROLLING_BASELINE_SD_MIN_N else None
        rb = statistics.mean(rw) if rw else None
        rsd = statistics.pstdev(rw) if len(rw) >= _ROLLING_BASELINE_SD_MIN_N else None
        result[d] = (hb, hsd, rb, rsd)
    return result


# Campos de sueño que se omiten cuando se detecta siesta
_SLEEP_FIELDS = ("asleep", "inbed", "awake", "deep", "rem", "light", "eff",
                 "bedtime", "waketime", "bed_min", "sleep_perf")


def build_dataset(sleep, rhr, hrv, resp, vo2, steps, azm, spo2=None, skin=None, exercises=None,
                  age: float = 40, sex: str = "M", rhr_fallback: float = 55.0,
                  distance_km=None, energy_kcal=None, active_hours=None,
                  sleep_target_min: int = 480):
    """
    Build the daily dataset.

    Tier 2 new parameters (all optional, backward-compatible):
      age           — user age in years for TRIMP HRmax (default 40)
      sex           — "M" or "F" for TRIMP factor (default "M")
      rhr_fallback  — fallback resting HR when day rhr is missing (default 55)

    Fase 3.5 new parameters (all optional, tolerate None / missing → field is None in day):
      distance_km   — dict {date: float km} from daily_rollup (meters→km)
      energy_kcal   — dict {date: float kcal} from daily_rollup
      active_hours  — dict {date: int hours} from intraday (DIFERIDO — always {} for now)

    Ronda 5 new parameter:
      sleep_target_min — umbral único de sueño (NEED) en minutos, default 480 (8h).
                         Alimenta recovery-comp de sueño y sleep_perf. Con el
                         default 480 el comportamiento es BYTE-IDÉNTICO a antes
                         (NEED era 480 literal). Validado 300-600 en el PUT
                         (app/profile.py + main.py), no aquí.
    """
    spo2 = spo2 or {}; skin = skin or {}; exercises = exercises or []
    distance_km = distance_km or {}
    energy_kcal = energy_kcal or {}
    active_hours = active_hours or {}
    dates = set(sleep) | set(rhr) | set(hrv) | set(steps) | set(spo2) | set(skin)
    days = []
    hrv_vals = [v for v in hrv.values()]
    rhr_vals = [v for v in rhr.values()]

    def pct(a, p):
        if not a: return 0
        a = sorted(a); k = (len(a)-1)*p/100; f = int(k)
        return a[f] if f+1 >= len(a) else a[f] + (a[f+1]-a[f])*(k-f)

    # hlo/hhi/rlo/rhi GLOBALES: se conservan para summary["hrv_range"]/["rhr_range"]
    # (display histórico, documentado como NO-escala en el roadmap Ronda 5). El
    # cómputo de recovery POR DÍA ahora usa el rango RODANTE (_rolling_percentile_ranges),
    # no estos globales — ver bucle principal abajo.
    hlo, hhi = (pct(hrv_vals, 5), pct(hrv_vals, 95)) if hrv_vals else (40, 70)
    rlo, rhi = (pct(rhr_vals, 5), pct(rhr_vals, 95)) if rhr_vals else (48, 60)  # percentil 5-95: robusto, menos extremos pegados a 0/100
    if hhi == hlo: hhi = hlo + 1
    if rhi == rlo: rhi = rlo + 1
    NEED = sleep_target_min

    def clamp(x, a=0, b=100): return max(a, min(b, x))

    # ── Ronda 5: TRIMP por día calculado ANTES del bucle principal ──────────────
    # (reordenado desde el bloque Tier 2 Feature C que vivía DESPUÉS del bucle —
    # strain v2 lo necesita disponible por-día DURANTE el bucle). Import local
    # preservado (anti-circular: tests que importan scoring.py solo, sin el resto
    # del package, no deben romperse).
    try:
        from app.load import trimp_session as _trimp_session
        _trimp_available = True
    except ImportError:
        _trimp_available = False

    trimp_by_date: dict = {}
    if _trimp_available and exercises:
        ex_by_date: dict = {}
        for ex in exercises:
            ex_date = ex.get("date")
            if ex_date:
                ex_by_date.setdefault(ex_date, []).append(ex)

        # hr_rest fallback: rhr del día propio (se resuelve por fecha abajo) →
        # ewma_recent de la serie completa de rhr → rhr_fallback fijo.
        rhr_base_recent_val = _ewma_recent(rhr) if rhr else None
        hr_rest_fallback = rhr_base_recent_val if rhr_base_recent_val is not None else rhr_fallback

        for ex_date, sessions in ex_by_date.items():
            # hr_rest: rhr del día (de la serie cruda `rhr`, disponible antes del
            # bucle) → fallback ewma_recent → fallback fijo.
            hr_rest_today = rhr.get(ex_date, hr_rest_fallback)
            total_trimp = 0.0
            valid = False
            for sess in sessions:
                t = _trimp_session(
                    dur_min=sess.get("dur_min"),
                    avg_hr=sess.get("avg_hr"),
                    hr_rest=hr_rest_today,
                    age=age,
                    sex=sex,
                )
                if t is not None:
                    total_trimp += t
                    valid = True
            if valid:
                trimp_by_date[ex_date] = round(total_trimp, 2)

    # ── Ronda 5: rangos de recovery RODANTES (trailing 90d, anti look-ahead) ────
    sorted_dates = sorted(d for d in dates if d >= "2000-01-01")
    rolling_ranges = _rolling_percentile_ranges(sorted_dates, hrv, rhr)
    # Recovery v3 (anclado a base): media/sd trailing por día (anti look-ahead).
    rolling_baselines = _rolling_baseline_ranges(sorted_dates, hrv, rhr)

    for d in sorted_dates:
        o = {"date": d}
        if d in sleep:
            rec = sleep[d]
            if not _is_nap(rec):
                # Noche legítima: copiar todos los campos no-None
                o.update({k: v for k, v in rec.items() if v is not None})
            # Si es siesta: no se setean campos de sueño (no contaminar promedios)
        if d in rhr:  o["rhr"]  = rhr[d]
        if d in hrv:  o["hrv"]  = hrv[d]
        if d in resp: o["resp"] = resp[d]
        if d in steps: o["steps"] = steps[d]
        if d in azm:  o["vigorous"] = azm[d]
        if d in spo2: o["spo2"] = spo2[d]
        if d in skin: o["skin_temp"] = skin[d]
        # Fase 3.5 — campos nuevos (tolerantes a None/ausentes)
        o["distance_km"]  = distance_km.get(d)   # None si no disponible
        o["energy_kcal"]  = energy_kcal.get(d)   # None si no disponible
        o["active_hours"] = active_hours.get(d)  # None (diferido)
        # Ronda 5: TRIMP del día (calculado arriba, antes del bucle)
        if d in trimp_by_date:
            o["trimp"] = trimp_by_date[d]
        # scores — recovery si hay señal real (HRV o sueño). COMPUERTA QUIRÚRGICA:
        # se computa con ≥1 componente (HRV pesa 0.55, es la señal dominante), PERO se
        # suprime SOLO el caso patológico: 1 sola señal que clampea a un extremo (0 o 100)
        # — una lectura única en el borde del percentil es ruido, no recovery real.
        # (Conserva los recovery razonables de HRV-sola; quita solo los ~18 espurios.)
        # Ronda 5: hlo/hhi/rlo/rhi ahora vienen del rango RODANTE de este día (trailing
        # 90d, solo pasado) en vez del percentil global — pesos 0.55/0.25/0.20 y
        # compuerta quirúrgica INTACTOS.
        if RECOVERY_ANCHORED:
            # ── Recovery v3: z-score vs base personal + logística anclada ──────
            # comps guardan el z (signo: + = mejor recovery), no un 0-100. La
            # logística 100/(1+exp(-(A+B*W))) mapea W (z ponderado) a 0-100,
            # anclando la base (W=0) al verde. Sin clamp por-componente: la
            # logística ya satura suave los extremos (fin del zigzag percentil).
            hb, hsd, rb, rsd = rolling_baselines[d]
            comps = []
            if "hrv" in o and hb is not None:
                comps.append(((o["hrv"] - hb) / max(hsd or 0, RECOVERY_V3_HSD_FLOOR), 0.55))
            if "rhr" in o and rb is not None:
                comps.append(((rb - o["rhr"]) / max(rsd or 0, RECOVERY_V3_RSD_FLOOR), 0.25))
            if "asleep" in o:
                comps.append((((o["asleep"] / NEED) - 1) / RECOVERY_V3_SLEEP_SPREAD, 0.20))
            if comps and ("hrv" in o or "asleep" in o):
                w = sum(x[1] for x in comps)
                W = sum(z * wt for z, wt in comps) / w
                o["recovery"] = round(100.0 / (1.0 + math.exp(-(RECOVERY_V3_A + RECOVERY_V3_B * W))))
                o["recovery_n"] = len(comps)
        else:
            # ── Recovery v2 (percentil rodante) — conservado para revert ───────
            d_hlo, d_hhi, d_rlo, d_rhi = rolling_ranges[d]
            comps = []
            if "hrv" in o: comps.append((clamp((o["hrv"]-d_hlo)/(d_hhi-d_hlo)*100), 0.55))
            if "rhr" in o: comps.append((clamp((d_rhi-o["rhr"])/(d_rhi-d_rlo)*100), 0.25))
            if "asleep" in o: comps.append((clamp(o["asleep"]/NEED*100), 0.20))
            if comps and ("hrv" in o or "asleep" in o):
                w = sum(x[1] for x in comps)
                rec = round(sum(v*wt for v, wt in comps)/w)
                if not (len(comps) == 1 and rec in (0, 100)):
                    o["recovery"] = rec
                    o["recovery_n"] = len(comps)
        if "asleep" in o: o["sleep_perf"] = round(clamp(o["asleep"]/NEED*100))
        # Ronda 5: strain v2 — híbrido TRIMP (reemplaza vigorous*0.10 + steps/2500).
        # Presencia ampliada: steps O trimp O vigorous (antes solo steps).
        strain_v2 = _strain_v2(o.get("trimp"), o.get("vigorous"), o.get("steps"))
        if strain_v2 is not None:
            o["strain"] = strain_v2
        days.append(o)

    # ── Tier 2 Feature A: SDs rolling ────────────────────────────────────────
    # Construir series desde days (los mismos datos que procesamos)
    hrv_series_full = {d["date"]: d["hrv"] for d in days if d.get("hrv") is not None}
    rhr_series_full = {d["date"]: d["rhr"] for d in days if d.get("rhr") is not None}
    resp_series_full = {d["date"]: d["resp"] for d in days if d.get("resp") is not None}
    skin_series_full = {d["date"]: d["skin_temp"] for d in days if d.get("skin_temp") is not None}

    hrv_sd = _rolling_sd(hrv_series_full)
    rhr_sd = _rolling_sd(rhr_series_full)
    resp_sd = _rolling_sd(resp_series_full)
    skin_temp_sd = _rolling_sd(skin_series_full)

    # ── Tier 2 Feature C: ACWR sobre serie strain ─────────────────────────────
    try:
        from app.load import acwr as _acwr, acwr_zone as _acwr_zone
        # Serie strain de los últimos 28 días (None si no hay strain ese día)
        strain_series = [d.get("strain") for d in days[-28:]]
        acwr_val = _acwr(strain_series)
        acwr_zone_val = _acwr_zone(acwr_val)
    except ImportError:
        acwr_val = None
        acwr_zone_val = None

    summary = {
        "hrv_base": round(statistics.median(hrv_vals), 1) if hrv_vals else 0,
        "rhr_base": round(statistics.median(rhr_vals), 1) if rhr_vals else 0,
        "hrv_base_recent": _ewma_recent(hrv),
        "rhr_base_recent": _ewma_recent(rhr),
        "hrv_range": [round(hlo, 1), round(hhi, 1)],
        "rhr_range": [round(rlo, 1), round(rhi, 1)],
        "n_days": len(days),
        "updated": datetime.date.today().isoformat(),
        # Tier 2 Feature A — rolling SDs
        "hrv_sd": round(hrv_sd, 2) if hrv_sd is not None else None,
        "rhr_sd": round(rhr_sd, 2) if rhr_sd is not None else None,
        "resp_sd": round(resp_sd, 2) if resp_sd is not None else None,
        "skin_temp_sd": round(skin_temp_sd, 2) if skin_temp_sd is not None else None,
        # Tier 2 Feature C — ACWR
        "acwr": round(acwr_val, 3) if acwr_val is not None else None,
        "acwr_zone": acwr_zone_val,
        # Ronda 5 — versionado del motor (strain híbrido TRIMP, recovery rodante,
        # umbral de sueño configurable). Ver docstring del módulo.
        "sleep_target_min": sleep_target_min,
        "engine": {
            "version": 3 if RECOVERY_ANCHORED else 2,
            "strain": "trimp-hybrid-v2",
            "recovery_scale": "baseline-anchored-v3" if RECOVERY_ANCHORED else "rolling-90d",
            "sleep_target_min": sleep_target_min,
        },
    }

    # ── Fase 3.5 PARTE 2: Wellbeing por día (ADITIVO — NO toca recovery/bodyage) ─
    # resp_base: media rodante de los últimos ~30 días de resp (calculada aquí desde days)
    resp_rolling_vals = [d["resp"] for d in days if d.get("resp") is not None]
    resp_rolling_vals = resp_rolling_vals[-30:]  # últimas ~30 lecturas
    resp_base_val = (sum(resp_rolling_vals) / len(resp_rolling_vals)) if len(resp_rolling_vals) >= 3 else None

    for day in days:
        day["wellbeing"] = compute_wellbeing(day, days, summary,
                                             resp_base=resp_base_val)

    return {"summary": summary, "days": days, "exercises": exercises[-40:]}


# ── Fase 3.5 PARTE 2: Score de Wellbeing 0-100 (ADITIVO) ────────────────────

# Tabla de interpolación SpO₂ → sub-score
_SPO2_POINTS = [(90, 30), (92, 55), (94, 75), (95, 88), (96, 100)]


def _spo2_score(spo2: float) -> float:
    """Interpola linealmente entre los puntos de referencia de SpO₂."""
    if spo2 >= 96:
        return 100.0
    if spo2 <= 90:
        return 30.0
    # Interpolación lineal entre pares consecutivos
    for i in range(len(_SPO2_POINTS) - 1):
        lo_v, lo_s = _SPO2_POINTS[i]
        hi_v, hi_s = _SPO2_POINTS[i + 1]
        if lo_v <= spo2 <= hi_v:
            frac = (spo2 - lo_v) / (hi_v - lo_v)
            return lo_s + frac * (hi_s - lo_s)
    return 30.0  # fallback


def compute_wellbeing(day: dict, days: list, summary: dict,
                      resp_base=None):
    """
    Calcula el score de Wellbeing 0-100 del día, ADITIVO — no toca recovery/bodyage/strain.

    Sub-scores (0-100) por señal según desviación vs base personal:
      HRV   (↑ mejor): z=(hrv-hrv_base)/max(hrv_sd,1);   s=clamp(50+20*z, 0,100)
      RHR   (↓ mejor): z=(rhr-rhr_base)/max(rhr_sd,1);   s=clamp(50-20*z, 0,100)
      Resp  (estable): resp_base=media rodante ~30d;       z=(resp-resp_base)/max(resp_sd,1); s=clamp(100-18*|z|,0,100)
      SpO₂  (umbral): interpolación lineal ≥96→100 … ≤90→30
      Temp  (estable; ya es desviación): z=skin_temp/max(skin_temp_sd,0.3); s=clamp(100-18*|z|,0,100)

    Pesos: HRV .30, RHR .25, Resp .15, SpO₂ .15, Temp .15
    None-safe: solo señales con dato; renormaliza pesos.
    Si 0 señales → None.
    """
    def _clamp(x: float) -> float:
        return max(0.0, min(100.0, x))

    hrv_base  = summary.get("hrv_base_recent") or summary.get("hrv_base")
    rhr_base  = summary.get("rhr_base_recent") or summary.get("rhr_base")
    hrv_sd    = summary.get("hrv_sd")   or 1.0
    rhr_sd    = summary.get("rhr_sd")   or 1.0
    resp_sd   = summary.get("resp_sd")  or 1.0
    skin_sd   = summary.get("skin_temp_sd") or 0.3

    signals: list[tuple[float, float]] = []  # (sub_score, weight)

    # HRV
    hrv = day.get("hrv")
    if hrv is not None and hrv_base is not None:
        z = (hrv - hrv_base) / max(hrv_sd, 1.0)
        signals.append((_clamp(50.0 + 20.0 * z), 0.30))

    # RHR
    rhr = day.get("rhr")
    if rhr is not None and rhr_base is not None:
        z = (rhr - rhr_base) / max(rhr_sd, 1.0)
        signals.append((_clamp(50.0 - 20.0 * z), 0.25))

    # Respiración
    resp = day.get("resp")
    if resp is not None and resp_base is not None:
        z = (resp - resp_base) / max(resp_sd, 1.0)
        signals.append((_clamp(100.0 - 18.0 * abs(z)), 0.15))

    # SpO₂
    spo2 = day.get("spo2")
    if spo2 is not None:
        signals.append((_clamp(_spo2_score(float(spo2))), 0.15))

    # Temp piel (skin_temp ya es desviación, ~0 normal)
    skin_temp = day.get("skin_temp")
    if skin_temp is not None:
        z = skin_temp / max(skin_sd, 0.3)
        signals.append((_clamp(100.0 - 18.0 * abs(z)), 0.15))

    if not signals:
        return None

    total_w = sum(w for _, w in signals)
    score = sum(s * w for s, w in signals) / total_w
    return int(round(_clamp(score)))
