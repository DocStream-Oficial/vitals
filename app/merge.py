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
- exercises (workouts): concatenación + dedup por `_same_workout` = regla_kcal OR
  regla_dur_vieja (Roadmap fusion-workouts, 2026-07-29):
    regla_kcal (nueva): mismo `date`, `kcal` no-None e igual en ambas (tolerancia
      ±1) Y nombres/tipos "de la misma familia" (`_names_equivalent`: normalizado
      de uno CONTIENE al del otro, ej. "strengthtraining" contiene "strength").
    regla_dur_vieja (preservada tal cual, como OR): `date` + `name` EXACTO +
      `dur_min` presente en ambos + |diff| <= 5 -- el contrato/tests viejos de
      dedup siguen pasando sin tocarse.
  Al detectar match, se FUNDE campo a campo (`_fuse_workouts`) en vez de
  descartar el registro "menos completo": cada campo toma el valor no-None; si
  ambas fuentes traen el mismo campo con valores DISTINTOS, gana la de mayor
  SOURCE_PRIORITY. Motivo: cada reloj suele traer la MITAD del dato de una
  misma sesión (ej. HealthKit trae dur_min sin avg_hr, Google Health trae
  avg_hr sin dur_min) -- el criterio viejo de "más completo" descartaba la
  mitad que traía el perdedor; fundir produce un registro completo (y permite
  computar TRIMP, que exige dur_min Y avg_hr juntos). Ver
  `_dev-harness/fusion-workouts/ROADMAP.md`.
- azm / active_hours: siempre {} en las 4 fuentes hoy (diferido) -> fusión trivial {}.

SOURCE_PRIORITY se usa SOLO para desempates (HRV canónico, sueño, conflictos de
campo al fundir workouts) — nunca para ponderar promedios.

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


_KCAL_TOLERANCE = 1  # absorbe redondeos entre fuentes (roadmap fusion-workouts)


def _norm_activity(w: dict) -> str:
    """Normaliza el 'nombre de actividad' de un workout para comparar equivalencia
    por familia: minúsculas, sin espacios/guiones/underscores. Se compara sobre
    `name`; si falta (o es vacío), cae a `type` (mismo campo de fallback que usa
    `strength_minutes` en app/load.py: f"{type} {name}") -- roadmap fusion-workouts
    Paso 1."""
    raw = w.get("name") or w.get("type") or ""
    return "".join(ch for ch in str(raw).lower() if ch.isalnum())


def _names_equivalent(a: dict, b: dict) -> bool:
    """Dos nombres/tipos de actividad son 'de la misma familia' si el normalizado
    de uno CONTIENE al del otro ("strengthtraining" contiene "strength" ->
    equivalentes; "tennis" vs "yoga" -> no). Vacío en cualquiera de los dos ->
    nunca equivalente (evita que dos workouts sin name/type colapsen por default)."""
    na, nb = _norm_activity(a), _norm_activity(b)
    if not na or not nb:
        return False
    return na in nb or nb in na


def _same_workout(a: dict, b: dict) -> bool:
    """Dos workouts son 'el mismo' si comparten `date` Y (regla_kcal OR
    regla_dur_vieja) -- roadmap fusion-workouts Paso 1.

    regla_kcal (nueva): `kcal` no-None e igual en ambos (tolerancia ±1, absorbe
        redondeos) Y nombres/tipos equivalentes por familia (_names_equivalent).
        Justificación: kcal EXACTAS iguales el mismo día con actividad
        equivalente es identidad, no coincidencia (verificado en prod).
    regla_dur_vieja (preservada tal cual, sin tocar): `name` EXACTO igual Y
        `dur_min` presente en AMBOS Y |diff| <= 5. Se mantiene como OR
        precisamente para que el contrato/tests viejos de dedup sigan pasando
        sin modificarse (roadmap, criterio 4)."""
    if a.get("date") != b.get("date"):
        return False

    kcal_a, kcal_b = a.get("kcal"), b.get("kcal")
    if kcal_a is not None and kcal_b is not None and abs(kcal_a - kcal_b) <= _KCAL_TOLERANCE:
        if _names_equivalent(a, b):
            # Guardia añadida tras la validación: si AMBAS traen duración y NO
            # son compatibles (mismo criterio de ±5 min de la regla vieja), NO
            # es la misma sesión — kcal iguales con duraciones muy distintas
            # (p.ej. 30 vs 120 min) son dos entrenamientos reales que casualmente
            # quemaron lo mismo, y fundirlos PERDERÍA uno en silencio (peor que
            # el duplicado visible que este roadmap vino a quitar).
            # No afecta al caso real que motivó la regla: ahí la entrada de
            # Google SIEMPRE trae dur_min None, así que esta guardia no aplica.
            dur_a, dur_b = a.get("dur_min"), b.get("dur_min")
            if dur_a is not None and dur_b is not None and abs(dur_a - dur_b) > 5:
                return False
            return True

    if a.get("name") == b.get("name"):
        dur_a, dur_b = a.get("dur_min"), b.get("dur_min")
        if dur_a is not None and dur_b is not None and abs(dur_a - dur_b) <= 5:
            return True

    return False


def _fuse_workouts(a: dict, b: dict, a_is_priority: bool) -> dict:
    """Funde dos registros de workout reconocidos como 'el mismo' (_same_workout)
    campo a campo -- roadmap fusion-workouts Paso 2. Reemplaza el criterio viejo
    "gana el más completo" (que DESCARTABA el registro perdedor entero, perdiendo
    los campos que solo él traía) por una fusión real:

    - Para cada clave de la UNIÓN de llaves de `a` y `b`: si solo uno de los dos
      tiene valor no-None, ese valor gana (rellena el hueco).
    - Si ambos traen valor no-None e IGUAL, se conserva ese valor.
    - Si ambos traen valor no-None y DISTINTO (conflicto real, ej. dur_min 114
      vs 118), gana la fuente de mayor prioridad: `a` si `a_is_priority=True`,
      si no `b`.

    `a_is_priority`: `_merge_workouts` itera las fuentes en orden de
    SOURCE_PRIORITY (_ordered_sources) y `a` es siempre el registro YA
    ACUMULADO en `deduped` (visto primero, por tanto de mayor o igual
    prioridad que cualquier `b` que llegue después a hacer match) -> el
    llamador SIEMPRE debe pasar `a_is_priority=True`. Documentado explícito
    para que nadie invierta el orden en un cambio futuro."""
    out: dict = {}
    for key in set(a.keys()) | set(b.keys()):
        va, vb = a.get(key), b.get(key)
        if va is None:
            out[key] = vb
        elif vb is None:
            out[key] = va
        elif va == vb:
            out[key] = va
        else:
            out[key] = va if a_is_priority else vb
    return out


def _conflicts_with_members(candidate: dict, members: list[dict]) -> bool:
    """True si `candidate` CONTRADICE a algún miembro original del acumulado, en
    cuyo caso no puede ser la misma sesión y NO debe fundirse.

    Añadido tras la validación (hallazgo de fusión EN CADENA). El dedup es
    greedy: compara el candidato solo contra el ACUMULADO fundido, no contra los
    registros que lo formaron. Eso permitía este encadenamiento, que BORRABA una
    sesión real en silencio:
        A: Tennis kcal 400, dur None      (sesión 1, del reloj sin duración)
        B: Tennis kcal 400, dur 60        (sesión 1, del otro reloj -> funde con A
                                           por kcal; el acumulado queda dur 60)
        C: Tennis kcal 250, dur 62        (sesión 2, REAL Y DISTINTA -> engancha
                                           con el acumulado por la regla vieja
                                           |60-62|<=5 y su kcal 250 se pierde)
    Criterio de contradicción: ambos traen `kcal` y difieren más de la tolerancia.
    Las kcal son la huella más fiable de identidad de una sesión (misma sesión
    vista por dos relojes da kcal casi idénticas); dos kcal distintas dentro de un
    mismo grupo significan que ahí hay dos sesiones, no una.

    Nota: esto también cierra un agujero PRE-EXISTENTE (ya estaba en producción
    antes de la regla de kcal): dos sesiones reales del mismo deporte y el mismo
    día con duraciones a menos de 5 minutos se fundían por la regla vieja aunque
    sus kcal fueran claramente distintas.
    """
    c_kcal = candidate.get("kcal")
    if c_kcal is None:
        return False
    for m in members:
        m_kcal = m.get("kcal")
        if m_kcal is not None and abs(c_kcal - m_kcal) > _KCAL_TOLERANCE:
            return True
    return False


def _merge_workouts(fetched: dict[str, dict]) -> tuple[list[dict], list[frozenset[str]]]:
    """Concatena workouts de todas las fuentes y deduplica por `_same_workout`
    (regla_kcal OR regla_dur_vieja -- roadmap fusion-workouts Paso 1), fundiendo
    campo a campo (`_fuse_workouts`, Paso 2) en vez de descartar el 'menos
    completo'.

    Devuelve (deduped, provenance): `provenance[i]` es el set de nombres de
    fuente que contribuyeron al workout `deduped[i]` (una fuente si no hubo
    match, varias si se fundió). Roadmap Paso 3 / criterio 9: la fusión crea
    dicts NUEVOS, así que `_contributing_sources_workouts` ya no puede
    identificar contribución por `id(w)` contra las listas originales de
    `fetched` -- por eso este mapa de procedencia se calcula AQUÍ, en el único
    lugar que sabe de verdad qué fuente aportó qué, y se pasa explícito en vez
    de reconstruirlo por identidad de objeto."""
    all_workouts: list[tuple[str, dict]] = []
    for source_name in _ordered_sources(fetched):
        data = fetched[source_name].get("exercises") or []
        for w in data:
            all_workouts.append((source_name, w))

    deduped: list[dict] = []
    provenance: list[set[str]] = []
    # Miembros ORIGINALES de cada acumulado (añadido tras la validación, ver
    # _conflicts_with_members): el dedup es greedy y compara el candidato solo
    # contra el ACUMULADO, no contra los registros que lo formaron -> una entrada
    # "puente" podía encadenar dos sesiones REALES distintas y borrar una en
    # silencio. Guardar los miembros permite rechazar esos encadenamientos.
    members: list[list[dict]] = []
    for source_name, w in all_workouts:
        match_idx = None
        for i, existing in enumerate(deduped):
            if _same_workout(w, existing) and not _conflicts_with_members(w, members[i]):
                match_idx = i
                break
        if match_idx is None:
            deduped.append(w)
            provenance.append({source_name})
            members.append([w])
        else:
            members[match_idx].append(w)
            # `deduped[match_idx]` es siempre el acumulado de mayor prioridad
            # vista hasta ahora (se itera en orden SOURCE_PRIORITY) -> a_is_priority=True.
            deduped[match_idx] = _fuse_workouts(deduped[match_idx], w, a_is_priority=True)
            provenance[match_idx].add(source_name)
    return deduped, [frozenset(p) for p in provenance]


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


def _contributing_sources_workouts(provenance: list[frozenset[str]]) -> list[str]:
    """Fuentes que aportaron al menos un workout que SOBREVIVIÓ al dedup/fusión
    de _merge_workouts.

    Roadmap fusion-workouts Paso 3 / criterio 9: ANTES de este roadmap se
    identificaba contribución por `id(w)` de los dicts deduplicados contra las
    listas originales de `fetched` -- funcionaba porque `_merge_workouts`
    reusaba tal cual el objeto ganador. Desde la fusión campo a campo
    (`_fuse_workouts`), el registro resultante puede ser un dict NUEVO que no
    es idéntico (por identidad) a NINGUNO de los dos originales -- esa
    comparación por `id()` dejaría `sources` vacío para cualquier workout
    fundido. Por eso `_merge_workouts` ahora devuelve el mapa de procedencia
    explícito (`provenance`, una fuente por match) y esta función solo
    aplana ese mapa a una lista ordenada por SOURCE_PRIORITY -- ya no
    recibe `fetched` ni vuelve a ejecutar el dedup."""
    all_sources = {source_name for sources in provenance for source_name in sources}
    return sorted(all_sources, key=_priority_rank)


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
    result["exercises"], workout_provenance = _merge_workouts(fetched)
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
    workout_srcs = _contributing_sources_workouts(workout_provenance)
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
