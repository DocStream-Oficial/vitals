"""
app/merge.py — Fase 6A: motor de fusión multi-fuente.
Ronda 3: HRV canónico (no promediar métodos) + cumulativos por "más completo" (max).

merge_sources(fetched) recibe {source_name: dict_normalizado_de_fetch()} (una entrada
por fuente CONECTADA con fetch() exitoso ese ciclo) y devuelve UN dict con las mismas
13 claves que Source.fetch() (ver app/sources/base.py), fusionado según reglas
explícitas por tipo de métrica.

Con UNA sola fuente en el dict de entrada, el resultado es IDÉNTICO a esa fuente sola
(passthrough exacto) — es el criterio de no-regresión #3/#7 del roadmap 6A, verificado
BYTE-A-BYTE (JSON serializado) en tests/test_merge.py.

Reglas (ver roadmap `_dev/ROADMAP-vitals-fase6a-multisource-merge.md` para 6A y
`_dev/ROADMAP-vitals-ronda3-motor-honesto.md` para la revisión de Ronda 3):

- Point-value de MISMA magnitud física entre dispositivos (rhr, resp, spo2, vo2):
  PROMEDIO SIMPLE día-a-día entre las fuentes que tienen dato ese día. No se
  pondera por prioridad de fuente — son mediciones redundantes del mismo fenómeno,
  promediar reduce ruido de sensor sin favorecer un dispositivo.
- HRV (método-dependiente: RMSSD vs SDNN NO son la misma magnitud) y skin
  (base-dependiente: cada fuente construye su desviación contra una referencia
  DISTINTA — WHOOP/Google restan la media de SU PROPIA ventana de fetch, Oura
  entrega la desviación de su API — promediar desviaciones con bases incompatibles
  produce una desviación contra una base fantasma): CANÓNICOS, no promedio. Se
  elige la fuente con MÁS días de dato no-None para esa clave y se usa esa serie
  tal cual (empate -> SOURCE_PRIORITY). Promediar dos métodos distintos no
  produce ninguna de las dos magnitudes reales, y hacer fallback por-día re-mezcla
  métodos entre días (serie bimodal, rompe baselines EWMA/percentiles). Trade-off
  aceptado: se pierden los días donde SOLO la fuente no-canónica tenía dato — el motor
  ya tolera None en toda la serie (ausencia ≠ malo, patrón consistente del repo).
- Cumulativos del día (steps, distance_km, energy_kcal): gana el MAYOR valor del día
  (el dispositivo que más "vio" ese día), NO el promedio — promediar un dispositivo
  que solo captó medio día contra uno que captó el día completo diluye el dato bueno
  hacia abajo. Con 1 solo valor presente, passthrough exacto (mismo tipo, sin round).
- sleep: por noche (día), gana el registro con mayor `asleep` (sesión más completa);
  empate exacto -> desempata por SOURCE_PRIORITY. Mismo patrón `rank=(asleep, pref)`
  de app/parsers.py::parse_sleep, generalizado a N fuentes en vez de "preferida vs
  resto". No se promedian campos de sueño entre sí (no tiene sentido físico).
- exercises (workouts): concatenación + dedup simple por (date, name, |dur_min
  diff|<=5); al deduplicar gana la entrada con más campos no-None (más completa).
- azm / active_hours: siempre {} en las 4 fuentes hoy (diferido) -> fusión trivial {}.

SOURCE_PRIORITY se usa SOLO para desempates (HRV canónico, sueño, dedup de workouts
empatado en completitud) — nunca para ponderar promedios.

Proveniencia (aditivo, Ronda 3): merge_sources() sigue devolviendo exactamente las 13
claves (el contrato de build_dataset(**data) no cambia). Por separado, expone
last_merge_info() con metadatos de la última fusión (fuente elegida para HRV,
n_sources, etc.) — sync.py lo adjunta a dataset["summary"] DESPUÉS de build_dataset(),
así que build_dataset() llamado directo (regression) nunca lo ve.
"""
from __future__ import annotations

from datetime import date

# Prioridad fija de fuente, usada SOLO como desempate (nunca para pesos de promedio).
# Apple Watch/WHOOP dan fases de sueño más finas de fábrica que Fitbit-vía-Google.
SOURCE_PRIORITY = ["healthkit", "whoop", "oura", "google_health"]

# Claves con semántica de PROMEDIO simple día-a-día: misma magnitud física entre
# dispositivos, mediciones redundantes del mismo fenómeno.
_AVERAGE_KEYS = ("rhr", "resp", "spo2", "vo2")

# HRV es método-dependiente (RMSSD vs SDNN no son la misma magnitud) y skin es
# base-dependiente (cada fuente centra su desviación contra una referencia
# distinta) -> canónicos, nunca se mezclan entre fuentes.
_CANONICAL_KEYS = ("hrv", "skin")

# Cumulativos del día: gana el dispositivo "más completo" (mayor valor), no el
# promedio -- ver docstring de módulo.
_MAX_KEYS = ("steps", "distance_km", "energy_kcal")

# Las 13 claves del contrato de Source.fetch() / build_dataset(**data).
_ALL_KEYS = (
    "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
    "exercises", "distance_km", "energy_kcal", "active_hours",
)

# Metadatos de la última fusión (proveniencia) -- ver last_merge_info().
_last_merge_info: dict = {}
_HRV_FRESHNESS_MAX_LAG_DAYS = 3


def _priority_rank(source_name: str) -> int:
    """Menor índice = mayor prioridad. Fuentes desconocidas van al final (peor prioridad)."""
    try:
        return SOURCE_PRIORITY.index(source_name)
    except ValueError:
        return len(SOURCE_PRIORITY)


def _ordered_sources(fetched: dict[str, dict]) -> list[str]:
    """Nombres de fuente en fetched, ordenados por SOURCE_PRIORITY (orden estable/determinista)."""
    return sorted(fetched.keys(), key=_priority_rank)


def _parse_iso_date(value: str) -> date | None:
    """Parsea YYYY-MM-DD; devuelve None para claves no ISO o inválidas."""
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _merge_average(fetched: dict[str, dict], key: str) -> dict[str, float]:
    """Promedio simple día-a-día de `key` entre las fuentes que tienen valor ese día.
    None-safe: fuentes sin la clave o con dict vacío/None no rompen nada.

    Con un solo valor presente ese día (caso típico de 1-fuente-conectada), se
    devuelve el valor TAL CUAL (mismo tipo, sin pasar por round()) — necesario para
    el passthrough byte-a-byte exacto (ej. steps=8423 int no debe volverse 8423.0)."""
    by_date: dict[str, list] = {}
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get(key) or {}
        for date, value in data.items():
            if value is None:
                continue
            by_date.setdefault(date, []).append(value)
    out = {}
    for date, vals in by_date.items():
        out[date] = vals[0] if len(vals) == 1 else round(sum(vals) / len(vals), 2)
    return out


def _canonical_choice(fetched: dict[str, dict], key: str) -> tuple[str | None, dict]:
    """Selecciona la fuente canónica de `key` y devuelve (source, serie_filtrada).

    Para HRV aplica una guardia de frescura: una fuente cuyo último dato válido quede
    >3 días detrás del dato HRV más reciente disponible queda descartada antes del
    ranking histórico. Si alguna fecha válida no es parseable, degrada con seguridad
    al ranking histórico para no cambiar el comportamiento previo por datos raros.
    """
    candidates: list[tuple[str, dict, int, date | None]] = []
    latest_overall: date | None = None
    saw_unparseable = False

    for source_name in _ordered_sources(fetched):
        series = fetched[source_name].get(key) or {}
        filtered = {day: value for day, value in series.items() if value is not None}
        latest_for_source: date | None = None
        for day in filtered:
            parsed = _parse_iso_date(day)
            if parsed is None:
                saw_unparseable = True
                continue
            if latest_for_source is None or parsed > latest_for_source:
                latest_for_source = parsed
            if latest_overall is None or parsed > latest_overall:
                latest_overall = parsed
        candidates.append((source_name, filtered, len(filtered), latest_for_source))

    eligible = candidates
    if key == "hrv" and latest_overall is not None and not saw_unparseable:
        fresh_candidates = [
            candidate
            for candidate in candidates
            if candidate[3] is not None
            and (latest_overall - candidate[3]).days <= _HRV_FRESHNESS_MAX_LAG_DAYS
        ]
        if fresh_candidates:
            eligible = fresh_candidates

    best_source = None
    best_series: dict = {}
    best_rank = None
    for source_name, filtered, n_days, _latest_for_source in eligible:
        if n_days == 0:
            continue
        rank = (n_days, -_priority_rank(source_name))
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_source = source_name
            best_series = filtered

    return best_source, best_series


def _merge_canonical(fetched: dict[str, dict], key: str) -> dict:
    """Elige la fuente con MÁS días con dato no-None para `key` y devuelve su serie
    (mono-método, sin promediar, sin round, sin tocar tipos -- passthrough natural de
    los valores de esa fuente). Empate en número de días -> SOURCE_PRIORITY.

    Los días con valor None se DESCARTAN (mismo criterio que _merge_average/_merge_max):
    build_dataset consume esta serie con `list(hrv.values())` y pct()/median() -> un
    None colado ahí revienta el motor. Descartar None mantiene la serie mono-método
    (no re-mezcla nada) y preserva la invariante de todo el merge: las series fundidas
    nunca contienen None.

    Razón (ver docstring de módulo): una serie mono-método es coherente para
    baselines EWMA/percentiles; el promedio inter-método no es ninguna de las dos
    magnitudes reales, y el fallback per-día re-mezcla métodos entre días (bimodal).
    """
    _, series = _canonical_choice(fetched, key)
    return series


def _merge_max(fetched: dict[str, dict], key: str) -> dict:
    """Por día, el MAYOR valor entre las fuentes que tienen dato ese día (gana el
    dispositivo más completo del día). Con 1 solo valor presente, se devuelve TAL
    CUAL (mismo tipo, sin round) -- passthrough exacto, mismo criterio que
    _merge_average para el caso de 1 fuente."""
    by_date: dict[str, list] = {}
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get(key) or {}
        for date, value in data.items():
            if value is None:
                continue
            by_date.setdefault(date, []).append(value)
    out = {}
    for date, vals in by_date.items():
        out[date] = vals[0] if len(vals) == 1 else max(vals)
    return out


def _merge_sleep(fetched: dict[str, dict]) -> dict[str, dict]:
    """Por noche (día), gana el registro con mayor `asleep` (sesión más completa);
    empate exacto en `asleep` desempata por SOURCE_PRIORITY.
    Generalización a N fuentes de app/parsers.py::parse_sleep (rank=(asleep, pref))."""
    best: dict[str, tuple[tuple[int, int], dict]] = {}
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get("sleep") or {}
        pref = -_priority_rank(source_name)  # mayor pref = mayor prioridad (index 0 -> pref 0, mejor)
        for date, rec in data.items():
            if not rec:
                continue
            asleep = rec.get("asleep") or 0
            rank = (asleep, pref)
            cur = best.get(date)
            if cur is None or rank > cur[0]:
                best[date] = (rank, rec)
    return {date: rec for date, (rank, rec) in best.items()}


def _completeness(rec: dict) -> int:
    """Número de campos no-None en un registro de workout — usado para elegir
    la entrada 'más completa' al deduplicar."""
    return sum(1 for v in rec.values() if v is not None)


def _same_workout(a: dict, b: dict) -> bool:
    """Dos workouts son 'el mismo' si comparten date Y name Y |dur_min diff| <= 5."""
    if a.get("date") != b.get("date"):
        return False
    if a.get("name") != b.get("name"):
        return False
    dur_a, dur_b = a.get("dur_min"), b.get("dur_min")
    if dur_a is None or dur_b is None:
        return False
    return abs(dur_a - dur_b) <= 5


def _merge_workouts(fetched: dict[str, dict]) -> list[dict]:
    """Concatena workouts de todas las fuentes y deduplica por (date, name,
    |dur_min diff|<=5), quedándose con la entrada más completa (más campos no-None)."""
    all_workouts: list[dict] = []
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get("exercises") or []
        all_workouts.extend(data)

    deduped: list[dict] = []
    for w in all_workouts:
        match_idx = None
        for i, existing in enumerate(deduped):
            if _same_workout(w, existing):
                match_idx = i
                break
        if match_idx is None:
            deduped.append(w)
        else:
            if _completeness(w) > _completeness(deduped[match_idx]):
                deduped[match_idx] = w
    return deduped


def _contributing_sources_average_or_max(fetched: dict[str, dict], key: str) -> list[str]:
    """Fuentes que aportaron AL MENOS un día con dato no-None para `key`
    (usado tanto para _AVERAGE_KEYS como _MAX_KEYS — mismo criterio de
    contribución: ¿tuvo la fuente algún valor no-None en la serie?). Orden
    estable por SOURCE_PRIORITY (mismo criterio de _ordered_sources)."""
    out = []
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get(key) or {}
        if any(v is not None for v in data.values()):
            out.append(source_name)
    return out


def _contributing_sources_sleep(fetched: dict[str, dict]) -> list[str]:
    """Fuentes que ganaron AL MENOS una noche en _merge_sleep (no basta con
    tener datos de sleep — importa haber contribuido al resultado fusionado,
    ya que sleep es 'gana el más completo' por noche, no un promedio)."""
    winners: dict[str, tuple] = {}
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get("sleep") or {}
        pref = -_priority_rank(source_name)
        for date, rec in data.items():
            if not rec:
                continue
            asleep = rec.get("asleep") or 0
            rank = (asleep, pref)
            cur = winners.get(date)
            if cur is None or rank > cur[0]:
                winners[date] = (rank, source_name)
    out = []
    seen = set()
    for source_name in _ordered_sources(fetched):
        if any(src == source_name for _, src in winners.values()) and source_name not in seen:
            out.append(source_name)
            seen.add(source_name)
    return out


def _contributing_sources_workouts(fetched: dict[str, dict]) -> list[str]:
    """Fuentes que aportaron al menos un workout que SOBREVIVIÓ al dedup de
    _merge_workouts (si una fuente solo aportó duplicados perdedores, no
    'contribuyó' en el sentido de proveniencia — mismo criterio que sleep)."""
    deduped = _merge_workouts(fetched)
    contributing_ids = {id(w) for w in deduped}
    # _merge_workouts no preserva de qué fuente vino cada entrada tras el
    # dedup (los dicts son los objetos originales de `fetched`, reusados tal
    # cual -- ver _completeness/_same_workout), así que basta con verificar
    # membership por identidad de objeto contra la lista original por fuente.
    out = []
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get("exercises") or []
        if any(id(w) in contributing_ids for w in data):
            out.append(source_name)
    return out


def _canonical_source_for(fetched: dict[str, dict], key: str) -> str | None:
    """Nombre de la fuente elegida como canónica para `key` (igual criterio que
    _merge_canonical: más días con dato, empate -> SOURCE_PRIORITY). None si
    ninguna fuente tiene ese día con dato (o fetched vacío)."""
    best_source, _series = _canonical_choice(fetched, key)
    return best_source


def merge_sources(fetched: dict[str, dict]) -> dict:
    """Funde los dicts normalizados de múltiples fuentes en UN dict de 13 claves.

    Args:
        fetched: {source_name: dict_normalizado_de_fetch()} — solo fuentes con
            fetch() exitoso ese ciclo. Puede tener 1 sola fuente (passthrough exacto)
            o varias.

    Returns:
        dict con exactamente las 13 claves de Source.fetch() (encaja directo en
        build_dataset(**data)).

    Efecto secundario: actualiza el estado de proveniencia consultable vía
    last_merge_info() (n_sources, la fuente elegida como canónica de HRV, y
    -aditivo desde Roadmap P1 F7- by_metric: qué fuentes contribuyeron a
    cada una de las 13 claves).
    """
    global _last_merge_info

    if not fetched:
        _last_merge_info = {"n_sources": 0, "hrv_source": None, "by_metric": {}}
        return {key: ({} if key != "exercises" else []) for key in _ALL_KEYS}

    result: dict = {}
    for key in _AVERAGE_KEYS:
        result[key] = _merge_average(fetched, key)
    for key in _CANONICAL_KEYS:
        result[key] = _merge_canonical(fetched, key)
    for key in _MAX_KEYS:
        result[key] = _merge_max(fetched, key)
    result["sleep"] = _merge_sleep(fetched)
    result["exercises"] = _merge_workouts(fetched)
    # Diferido en las 4 fuentes hoy -> fusión trivial.
    result["azm"] = {}
    result["active_hours"] = {}

    # ── Proveniencia por métrica (Roadmap P1, F7, paso 10) — ADITIVO, no
    # cambia la lógica de fusión de arriba, solo instrumenta QUÉ fuentes
    # contribuyeron. Con 1 sola fuente, by_metric no se usa en UI (gate >1
    # fuente en el frontend) pero se calcula igual (barato, observabilidad
    # honesta) — el passthrough byte-a-byte de `result` no se toca.
    by_metric: dict = {}
    for key in _AVERAGE_KEYS:
        srcs = _contributing_sources_average_or_max(fetched, key)
        if srcs:
            by_metric[key] = {"mode": "avg", "sources": srcs}
    for key in _CANONICAL_KEYS:
        canonical = _canonical_source_for(fetched, key)
        if canonical:
            by_metric[key] = {"mode": "canonical", "source": canonical}
    for key in _MAX_KEYS:
        srcs = _contributing_sources_average_or_max(fetched, key)
        if srcs:
            by_metric[key] = {"mode": "max", "sources": srcs}
    sleep_srcs = _contributing_sources_sleep(fetched)
    if sleep_srcs:
        by_metric["sleep"] = {"mode": "per-night", "sources": sleep_srcs}
    workout_srcs = _contributing_sources_workouts(fetched)
    if workout_srcs:
        by_metric["exercises"] = {"mode": "dedup", "sources": workout_srcs}

    _last_merge_info = {
        "n_sources": len(fetched),
        "hrv_source": _canonical_source_for(fetched, "hrv"),
        "by_metric": by_metric,
    }
    return result


def last_merge_info() -> dict:
    """Metadatos de proveniencia de la última llamada a merge_sources() (módulo,
    NO thread-safe -- suficiente para el single-flight de sync.py). Semilla de la
    "transparencia de procedencia" de 6B: permite verificar en /api/sync qué fuente
    ganó HRV y cuántas fuentes se fusionaron, sin cambiar el contrato de 13 claves
    de merge_sources() ni el golden de build_dataset().

    Roadmap P1 F7 (paso 10, ADITIVO): además de n_sources/hrv_source, expone
    `by_metric` = {clave: {mode, source|sources}} — SOLO para las claves donde
    ALGUNA fuente contribuyó al menos un día/registro ese merge (claves sin
    ningún dato en ninguna fuente simplemente no aparecen en by_metric, nunca
    se inventa proveniencia vacía). `mode` refleja la regla real de fusión de
    esa clave (avg/canonical/max/per-night/dedup) — ver docstring de módulo."""
    return dict(_last_merge_info)
