"""
test_merge.py — Fase 6A: app/merge.py::merge_sources (el corazón del roadmap 6A).
Ronda 3: HRV pasó de promedio a CANÓNICO; steps/distance_km/energy_kcal de promedio a MAX.

Cubre, por regla (ver roadmap _dev/ROADMAP-vitals-fase6a-multisource-merge.md para 6A
y _dev/ROADMAP-vitals-ronda3-motor-honesto.md para la revisión de Ronda 3):
- Passthrough exacto con 1 sola fuente (criterio de no-regresión #3/#7 — el más importante).
- Promedio simple point-value de MISMA magnitud (rhr/resp/spo2/vo2).
- HRV y skin: CANÓNICOS (HRV desde Ronda 3; skin desde auditoría 2026-07-05 — cada
  fuente centra su desviación contra una base DISTINTA, promediar bases incompatibles
  da una desviación contra base fantasma). Gana la fuente con más días de dato.
- Cumulativos (steps/distance_km/energy_kcal): MAX del día (Ronda 3), NO promedio ni suma.
- Sueño: rank=(asleep, priority), gana la noche más larga; empate desempata por SOURCE_PRIORITY.
- Dedup de workouts por (date, name, |dur_min diff|<=5), gana el más completo.
- None-safety: fuentes vacías/faltantes no rompen nada.
- Caso "todas las fuentes vacías" -> dict con las 13 claves, vacío/[] según corresponda.
"""
from __future__ import annotations

import json

import pytest

from app.merge import merge_sources, last_merge_info, SOURCE_PRIORITY, _priority_rank


ALL_KEYS = (
    "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
    "exercises", "distance_km", "energy_kcal", "active_hours",
)


def _empty_source() -> dict:
    return {
        "sleep": {}, "rhr": {}, "hrv": {}, "resp": {}, "vo2": {}, "steps": {},
        "azm": {}, "spo2": {}, "skin": {}, "exercises": [], "distance_km": {},
        "energy_kcal": {}, "active_hours": {},
    }


# ── SOURCE_PRIORITY / desempate ──────────────────────────────────────────────

def test_source_priority_order():
    assert SOURCE_PRIORITY == ["healthkit", "whoop", "oura", "google_health"]


def test_priority_rank_lower_is_better():
    assert _priority_rank("healthkit") < _priority_rank("whoop")
    assert _priority_rank("whoop") < _priority_rank("oura")
    assert _priority_rank("oura") < _priority_rank("google_health")


def test_priority_rank_unknown_source_worst():
    assert _priority_rank("mystery_device") > _priority_rank("google_health")


# ── Passthrough exacto con 1 sola fuente (criterio de no-regresión) ─────────

def test_single_source_is_exact_passthrough():
    """Con 1 sola fuente en el input, el output debe ser BYTE-A-BYTE idéntico a esa
    fuente. Se compara vía JSON serializado (no dict==) — criterio de no-regresión
    explícito de Ronda 3: protege contra diffs "invisibles" en dict== (p.ej. orden de
    claves, tipos numéricos que comparan igual pero serializan distinto)."""
    sample = {
        "sleep": {"2026-06-28": {"asleep": 372, "inbed": 402, "deep": 54, "rem": 86,
                                  "light": 232, "eff": 92, "bedtime": "01:01", "waketime": "07:03"}},
        "rhr": {"2026-06-28": 52.0, "2026-06-27": 54.0},
        "hrv": {"2026-06-28": 54.6},
        "resp": {"2026-06-28": 14.1},
        "vo2": {"2026-06-28": 47.3},
        "steps": {"2026-06-28": 8423},
        "azm": {},
        "spo2": {"2026-06-28": 97.0},
        "skin": {"2026-06-28": -0.3},
        "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40, "kcal": 380,
                        "distance_km": 6.21}],
        "distance_km": {"2026-06-28": 6.21},
        "energy_kcal": {"2026-06-28": 2480},
        "active_hours": {},
    }
    out = merge_sources({"google_health": sample})
    assert out == sample, f"Passthrough NO exacto.\nesperado={sample}\nobtenido={out}"
    assert json.dumps(out, sort_keys=True) == json.dumps(sample, sort_keys=True), (
        "Passthrough NO byte-a-byte (JSON serializado difiere)."
    )


def test_single_source_passthrough_preserves_int_type():
    """steps=8423 (int) no debe convertirse a 8423.0 (float) en el passthrough de 1 fuente."""
    sample = {**_empty_source(), "steps": {"2026-06-28": 8423}}
    out = merge_sources({"healthkit": sample})
    assert out["steps"]["2026-06-28"] == 8423
    assert isinstance(out["steps"]["2026-06-28"], int)


def test_single_source_passthrough_empty_dicts():
    """Fuente única sin ningún dato -> las 13 claves presentes, vacías."""
    out = merge_sources({"google_health": _empty_source()})
    for key in ALL_KEYS:
        assert key in out
    assert out["exercises"] == []
    assert out["sleep"] == {}


def test_empty_fetched_dict_returns_all_13_keys():
    """merge_sources({}) no debe explotar; devuelve las 13 claves vacías."""
    out = merge_sources({})
    assert set(out.keys()) == set(ALL_KEYS)
    assert out["exercises"] == []
    for key in ALL_KEYS:
        if key != "exercises":
            assert out[key] == {}


# ── HRV: CANÓNICO, no promedio (Ronda 3) ────────────────────────────────────

def test_hrv_canonical_not_averaged_two_sources_same_day():
    """RONDA 3 — actualizado de promedio (55.0) a canónico.
    Motivo: hrv es método-dependiente (RMSSD vs SDNN); promediar dos métodos no
    produce ninguna magnitud real. Con empate en días de dato, desempata
    SOURCE_PRIORITY (healthkit > google_health) -> gana healthkit (50.0), NO 55.0."""
    a = {**_empty_source(), "hrv": {"2026-06-28": 50.0}}
    b = {**_empty_source(), "hrv": {"2026-06-28": 60.0}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["hrv"]["2026-06-28"] == 50.0


def test_hrv_canonical_source_with_more_days_wins():
    """La fuente canónica es la que tiene MÁS DÍAS con hrv, sin importar prioridad."""
    a = {**_empty_source(), "hrv": {"2026-06-27": 48.0, "2026-06-28": 50.0}}  # healthkit: 2 días
    b = {**_empty_source(), "hrv": {"2026-06-28": 60.0}}  # google_health: 1 día
    out = merge_sources({"healthkit": a, "google_health": b})
    # healthkit gana por más días -> ningún valor es promedio de ambas fuentes.
    assert out["hrv"] == {"2026-06-27": 48.0, "2026-06-28": 50.0}


def test_hrv_canonical_source_with_more_days_wins_even_if_lower_priority():
    """Empate roto por N DÍAS, no por prioridad: google_health (menor prioridad) con
    más días de dato gana sobre healthkit (mayor prioridad) con menos días."""
    a = {**_empty_source(), "hrv": {"2026-06-28": 50.0}}  # healthkit: 1 día
    b = {**_empty_source(), "hrv": {"2026-06-26": 58.0, "2026-06-27": 59.0,
                                     "2026-06-28": 60.0}}  # google_health: 3 días
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["hrv"] == {"2026-06-26": 58.0, "2026-06-27": 59.0, "2026-06-28": 60.0}


def test_hrv_canonical_tie_breaks_by_source_priority():
    """Empate exacto en n_días -> SOURCE_PRIORITY (healthkit > google_health)."""
    a = {**_empty_source(), "hrv": {"2026-06-27": 48.0, "2026-06-28": 50.0}}  # healthkit
    b = {**_empty_source(), "hrv": {"2026-06-27": 58.0, "2026-06-28": 60.0}}  # google_health
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["hrv"] == {"2026-06-27": 48.0, "2026-06-28": 50.0}


def test_hrv_canonical_last_merge_info_reports_source():
    """last_merge_info() expone qué fuente ganó HRV (proveniencia, aditivo)."""
    a = {**_empty_source(), "hrv": {"2026-06-27": 48.0, "2026-06-28": 50.0}}
    b = {**_empty_source(), "hrv": {"2026-06-28": 60.0}}
    merge_sources({"healthkit": a, "google_health": b})
    info = last_merge_info()
    assert info["hrv_source"] == "healthkit"
    assert info["n_sources"] == 2


# ── Promedio point-value (rhr/resp/spo2/vo2) ────────────────────────────────

def test_average_point_value_three_sources():
    a = {**_empty_source(), "rhr": {"2026-06-28": 50.0}}
    b = {**_empty_source(), "rhr": {"2026-06-28": 52.0}}
    c = {**_empty_source(), "rhr": {"2026-06-28": 54.0}}
    out = merge_sources({"healthkit": a, "whoop": b, "oura": c})
    assert out["rhr"]["2026-06-28"] == 52.0


def test_average_point_value_ignores_missing_day():
    """Fuente A tiene dato 6/28, fuente B no -> resultado = valor de A solo (no imputa)."""
    a = {**_empty_source(), "spo2": {"2026-06-28": 96.0}}
    b = {**_empty_source(), "spo2": {"2026-06-29": 97.0}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["spo2"]["2026-06-28"] == 96.0
    assert out["spo2"]["2026-06-29"] == 97.0


def test_average_vo2_skin_canonical():
    """vo2 se promedia; skin es CANÓNICO (auditoría 2026-07-05): las desviaciones de
    cada fuente se centran contra bases distintas y no son promediables entre sí."""
    a = {**_empty_source(), "vo2": {"2026-06-28": 46.0}, "skin": {"2026-06-28": -0.5}}
    b = {**_empty_source(), "vo2": {"2026-06-28": 48.0}, "skin": {"2026-06-28": 0.1}}
    out = merge_sources({"healthkit": a, "oura": b})
    assert out["vo2"]["2026-06-28"] == 47.0
    # empate en nº de días (1 vs 1) -> gana healthkit por SOURCE_PRIORITY, serie tal cual
    assert out["skin"]["2026-06-28"] == pytest.approx(-0.5, abs=1e-9)


def test_skin_canonical_more_days_wins():
    """La fuente con MÁS días de skin gana la canónica aunque tenga peor prioridad."""
    a = {**_empty_source(), "skin": {"2026-06-28": -0.5}}
    b = {**_empty_source(), "skin": {"2026-06-27": 0.2, "2026-06-28": 0.1}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["skin"] == {"2026-06-27": 0.2, "2026-06-28": 0.1}


# ── Cumulativos (steps/distance_km/energy_kcal) — MAX del día, NO promedio ──

def test_cumulative_steps_takes_max_not_average():
    """RONDA 3 — actualizado de promedio (8200.0) a MAX (8400).
    Motivo: cumulativos ganan con el dispositivo 'más completo' del día; promediar
    un tracker que vio medio día contra uno que vio el día completo diluye el dato
    bueno. Criterio de aceptación del roadmap: 'medio día' (3000) vs completo (9000)
    -> 9000, no 6000."""
    a = {**_empty_source(), "steps": {"2026-06-28": 8000}}
    b = {**_empty_source(), "steps": {"2026-06-28": 8400}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["steps"]["2026-06-28"] == 8400


def test_cumulative_steps_half_day_vs_full_day():
    """Ejemplo textual del roadmap: dispositivo 'medio día' (3000) vs completo (9000) -> 9000."""
    half_day = {**_empty_source(), "steps": {"2026-06-28": 3000}}
    full_day = {**_empty_source(), "steps": {"2026-06-28": 9000}}
    out = merge_sources({"healthkit": half_day, "google_health": full_day})
    assert out["steps"]["2026-06-28"] == 9000


def test_cumulative_steps_preserves_int_type_multi_source():
    """El tipo (int) se preserva incluso con múltiples fuentes -- max() no castea a float."""
    a = {**_empty_source(), "steps": {"2026-06-28": 8000}}
    b = {**_empty_source(), "steps": {"2026-06-28": 8400}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert isinstance(out["steps"]["2026-06-28"], int)


def test_cumulative_distance_and_energy_take_max():
    """RONDA 3 — actualizado de promedio (6.2 / 2450.0) a MAX (6.4 / 2500)."""
    a = {**_empty_source(), "distance_km": {"2026-06-28": 6.0}, "energy_kcal": {"2026-06-28": 2400}}
    b = {**_empty_source(), "distance_km": {"2026-06-28": 6.4}, "energy_kcal": {"2026-06-28": 2500}}
    out = merge_sources({"healthkit": a, "whoop": b})
    assert out["distance_km"]["2026-06-28"] == 6.4
    assert out["energy_kcal"]["2026-06-28"] == 2500


# ── Sueño: rank=(asleep, priority) ───────────────────────────────────────────

def test_sleep_longest_session_wins():
    """La noche MÁS LARGA gana, independientemente de la fuente."""
    a = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 300, "inbed": 330}}}
    b = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 420, "inbed": 450}}}
    # 'a' es healthkit (prioridad alta) pero tiene la sesión MÁS CORTA -> pierde.
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["sleep"]["2026-06-28"]["asleep"] == 420


def test_sleep_tie_breaks_by_source_priority():
    """Empate exacto en asleep -> desempata por SOURCE_PRIORITY (healthkit > whoop > oura > google_health)."""
    a = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 400, "inbed": 430, "tag": "google"}}}
    b = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 400, "inbed": 430, "tag": "healthkit"}}}
    out = merge_sources({"google_health": a, "healthkit": b})
    assert out["sleep"]["2026-06-28"]["tag"] == "healthkit"


def test_sleep_tie_whoop_beats_oura():
    a = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 400, "tag": "oura"}}}
    b = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 400, "tag": "whoop"}}}
    out = merge_sources({"oura": a, "whoop": b})
    assert out["sleep"]["2026-06-28"]["tag"] == "whoop"


def test_sleep_no_averaging_of_fields():
    """Los campos de sueño NUNCA se promedian entre sí (se queda 1 registro completo)."""
    a = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 300, "deep": 40, "rem": 50}}}
    b = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 420, "deep": 90, "rem": 100}}}
    out = merge_sources({"healthkit": a, "google_health": b})
    rec = out["sleep"]["2026-06-28"]
    # Debe ser exactamente el registro de 'b' (ganó por asleep mayor), no un promedio.
    assert rec["deep"] == 90
    assert rec["rem"] == 100


def test_sleep_different_nights_both_kept():
    a = {**_empty_source(), "sleep": {"2026-06-27": {"asleep": 300}}}
    b = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 400}}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert set(out["sleep"].keys()) == {"2026-06-27", "2026-06-28"}


# ── Dedup de workouts ─────────────────────────────────────────────────────────

def test_workouts_dedup_same_workout_close_duration():
    """Mismo date+name, dur_min 108 vs 110 (diff=2 <=5) -> se juntan en 1."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-06-28", "name": "Tennis", "dur_min": 108, "kcal": None}
    ]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-06-28", "name": "Tennis", "dur_min": 110, "kcal": 450, "distance_km": None}
    ]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 1
    # Gana el más completo (más campos no-None): el de 'b' trae kcal.
    assert out["exercises"][0]["kcal"] == 450


def test_workouts_dedup_boundary_exactly_5_min_diff():
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 45}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 1


def test_workouts_not_deduped_when_diff_exceeds_5_min():
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 46}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2


def test_workouts_different_names_same_day_not_deduped():
    """Tennis AM + Fuerza PM el mismo día -> nombres distintos, NO se juntan."""
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Tennis", "dur_min": 60}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Fuerza", "dur_min": 45}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2
    names = {w["name"] for w in out["exercises"]}
    assert names == {"Tennis", "Fuerza"}


def test_workouts_different_dates_not_deduped():
    a = {**_empty_source(), "exercises": [{"date": "2026-06-27", "name": "Run", "dur_min": 40}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2


def test_workouts_dedup_missing_dur_min_not_matched():
    """Si dur_min falta en cualquiera de los dos, no se puede comparar -> no se dedup."""
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": None}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2


def test_workouts_concatenates_multiple_sources_no_overlap():
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-29", "name": "Swim", "dur_min": 30}]}
    c = {**_empty_source(), "exercises": [{"date": "2026-06-30", "name": "Bike", "dur_min": 60}]}
    out = merge_sources({"healthkit": a, "whoop": b, "oura": c})
    assert len(out["exercises"]) == 3


# ── azm / active_hours triviales ─────────────────────────────────────────────

def test_azm_and_active_hours_always_empty():
    a = {**_empty_source(), "azm": {"2026-06-28": 15}, "active_hours": {"2026-06-28": 3}}
    out = merge_sources({"google_health": a})
    # El roadmap dice: SIEMPRE {} en las 4 fuentes hoy -> fusión trivial {} (incluso si
    # alguna fuente sintética de test trajera algo, merge.py las ignora deliberadamente).
    assert out["azm"] == {}
    assert out["active_hours"] == {}


# ── None-safety ───────────────────────────────────────────────────────────────

def test_none_safe_missing_keys_in_one_source():
    """Una fuente con dict incompleto (faltan claves) no debe romper merge_sources."""
    a = {"sleep": {"2026-06-28": {"asleep": 300}}, "hrv": {"2026-06-28": 50.0}}
    b = _empty_source()
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["sleep"]["2026-06-28"]["asleep"] == 300
    assert out["hrv"]["2026-06-28"] == 50.0
    assert out["rhr"] == {}


def test_none_safe_none_values_in_dict():
    """RONDA 3 — actualizado: hrv ahora es canónico, no promedio.
    Valores None dentro de un dict de métrica no deben romper la elección de fuente
    canónica ni contar como 'día con dato'. 'a' tiene 1 día real (2026-06-29; el
    2026-06-28 es None) vs 'b' con 1 día real (2026-06-28) -> empate en n_días=1,
    desempata SOURCE_PRIORITY (healthkit > google_health) -> gana 'a'.
    Los días None de la fuente canónica se DESCARTAN (misma invariante que
    _merge_average/_merge_max): build_dataset consume la serie con pct()/median() y
    un None colado ahí revienta el motor — verificado en Fase 3 de validación."""
    a = {**_empty_source(), "hrv": {"2026-06-28": None, "2026-06-29": 55.0}}
    b = {**_empty_source(), "hrv": {"2026-06-28": 50.0}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert out["hrv"] == {"2026-06-29": 55.0}
    assert None not in out["hrv"].values()


def test_none_safe_source_with_none_exercises():
    a = {**_empty_source(), "exercises": None}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 1


def test_output_always_has_13_keys_multi_source():
    a = {**_empty_source(), "hrv": {"2026-06-28": 50.0}}
    b = {**_empty_source(), "steps": {"2026-06-28": 8000}}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert set(out.keys()) == set(ALL_KEYS)


# ── F2 roadmap P0: hipnograma — segments viajan intactos por el merge ────────

def test_sleep_segments_travel_with_winning_rec():
    """La noche ganadora (mayor `asleep`) pasa ENTERA — sus segments llegan
    idénticos al merged rec. La fuente perdedora con segments DISTINTOS no
    contamina (nunca se mezclan segments de dos fuentes — invariante gratis
    del merge por-rec, criterio 13 del roadmap)."""
    segs_winner = [
        {"s": 0, "e": 90, "st": "deep"},
        {"s": 90, "e": 200, "st": "light"},
        {"s": 200, "e": 260, "st": "rem"},
    ]
    segs_loser = [{"s": 0, "e": 400, "st": "awake"}]  # deliberadamente absurdo

    winner = {**_empty_source(), "sleep": {
        "2026-06-28": {"asleep": 420, "deep": 90, "segments": segs_winner},
    }}
    loser = {**_empty_source(), "sleep": {
        "2026-06-28": {"asleep": 380, "deep": 60, "segments": segs_loser},
    }}
    # google_health gana por asleep pese a tener peor SOURCE_PRIORITY que healthkit.
    out = merge_sources({"healthkit": loser, "google_health": winner})
    merged_night = out["sleep"]["2026-06-28"]
    assert merged_night["segments"] == segs_winner
    assert merged_night["asleep"] == 420


def test_sleep_without_segments_stays_without_segments():
    """Fuente ganadora SIN segments -> el merged rec tampoco los lleva (aunque
    la perdedora sí tuviera): jamás se 'rescatan' segments de la fuente
    perdedora hacia la noche de otra fuente."""
    loser = {**_empty_source(), "sleep": {
        "2026-06-28": {"asleep": 300, "segments": [{"s": 0, "e": 300, "st": "light"}]},
    }}
    winner = {**_empty_source(), "sleep": {
        "2026-06-28": {"asleep": 450},
    }}
    out = merge_sources({"oura": loser, "google_health": winner})
    merged_night = out["sleep"]["2026-06-28"]
    assert "segments" not in merged_night


def test_sleep_segments_single_source_passthrough():
    """1 sola fuente con segments -> passthrough byte-a-byte (JSON), mismo
    criterio de no-regresión que el resto del merge."""
    src = {**_empty_source(), "sleep": {
        "2026-06-28": {
            "asleep": 400,
            "segments": [{"s": 0, "e": 100, "st": "deep"}, {"s": 100, "e": 400, "st": "light"}],
        },
    }}
    out = merge_sources({"oura": src})
    assert json.dumps(out["sleep"], sort_keys=True) == json.dumps(src["sleep"], sort_keys=True)


# ── Proveniencia por métrica: last_merge_info()["by_metric"] (Roadmap P1, F7) ─

def test_by_metric_single_source_passthrough_byte_identical():
    """Con 1 sola fuente, el CONTRATO de las 13 claves de merge_sources() sigue
    byte-idéntico — by_metric es SOLO observabilidad aditiva en last_merge_info(),
    nunca toca el dict devuelto por merge_sources()."""
    sample = {**_empty_source(), "rhr": {"2026-06-28": 52.0}, "hrv": {"2026-06-28": 54.6}}
    out = merge_sources({"oura": sample})
    assert json.dumps(out, sort_keys=True) == json.dumps(sample, sort_keys=True)
    info = last_merge_info()
    assert info["by_metric"]["rhr"] == {"mode": "avg", "sources": ["oura"]}
    assert info["by_metric"]["hrv"] == {"mode": "canonical", "source": "oura"}


def test_by_metric_average_mode_two_sources():
    a = {**_empty_source(), "rhr": {"2026-06-28": 50.0}}
    b = {**_empty_source(), "rhr": {"2026-06-28": 54.0}}
    merge_sources({"healthkit": a, "oura": b})
    info = last_merge_info()
    assert info["by_metric"]["rhr"]["mode"] == "avg"
    assert set(info["by_metric"]["rhr"]["sources"]) == {"healthkit", "oura"}


def test_by_metric_canonical_mode_reports_only_winner():
    a = {**_empty_source(), "hrv": {"2026-06-27": 48.0, "2026-06-28": 50.0}}  # gana (más días)
    b = {**_empty_source(), "hrv": {"2026-06-28": 60.0}}
    merge_sources({"healthkit": a, "google_health": b})
    info = last_merge_info()
    assert info["by_metric"]["hrv"] == {"mode": "canonical", "source": "healthkit"}


def test_by_metric_max_mode_two_sources():
    a = {**_empty_source(), "steps": {"2026-06-28": 4000}}
    b = {**_empty_source(), "steps": {"2026-06-28": 8000}}
    merge_sources({"healthkit": a, "google_health": b})
    info = last_merge_info()
    assert info["by_metric"]["steps"]["mode"] == "max"
    assert set(info["by_metric"]["steps"]["sources"]) == {"healthkit", "google_health"}


def test_by_metric_sleep_per_night_only_winners():
    """sleep: solo las fuentes que GANARON al menos una noche aparecen —
    una fuente que solo aportó noches perdedoras no 'contribuyó'."""
    loser = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 300}}}
    winner = {**_empty_source(), "sleep": {"2026-06-28": {"asleep": 450}}}
    merge_sources({"oura": loser, "google_health": winner})
    info = last_merge_info()
    assert info["by_metric"]["sleep"] == {"mode": "per-night", "sources": ["google_health"]}


def test_by_metric_sleep_both_sources_win_different_nights():
    a = {**_empty_source(), "sleep": {
        "2026-06-27": {"asleep": 450},  # a gana esta noche
        "2026-06-28": {"asleep": 200},  # a pierde esta noche
    }}
    b = {**_empty_source(), "sleep": {
        "2026-06-28": {"asleep": 400},  # b gana esta noche
    }}
    merge_sources({"healthkit": a, "oura": b})
    info = last_merge_info()
    assert info["by_metric"]["sleep"]["mode"] == "per-night"
    assert set(info["by_metric"]["sleep"]["sources"]) == {"healthkit", "oura"}


def test_by_metric_exercises_dedup_mode():
    a = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 30}]}
    b = {**_empty_source(), "exercises": [{"date": "2026-06-28", "name": "Swim", "dur_min": 20}]}
    merge_sources({"healthkit": a, "oura": b})
    info = last_merge_info()
    assert info["by_metric"]["exercises"]["mode"] == "dedup"
    assert set(info["by_metric"]["exercises"]["sources"]) == {"healthkit", "oura"}


def test_by_metric_absent_for_metric_with_no_data_anywhere():
    """Una clave sin dato en NINGUNA fuente no aparece en by_metric — nunca se
    inventa proveniencia vacía."""
    empty = _empty_source()
    merge_sources({"oura": empty})
    info = last_merge_info()
    assert "rhr" not in info["by_metric"]
    assert "hrv" not in info["by_metric"]
    assert "sleep" not in info["by_metric"]
    assert "exercises" not in info["by_metric"]


def test_by_metric_empty_fetched_dict():
    merge_sources({})
    info = last_merge_info()
    assert info["by_metric"] == {}


def test_by_metric_never_breaks_13_key_contract_multi_source():
    """Regresión explícita: agregar by_metric NO debe cambiar las 13 claves
    de merge_sources() en un escenario multi-fuente."""
    a = {**_empty_source(), "rhr": {"2026-06-28": 50.0}, "steps": {"2026-06-28": 4000}}
    b = {**_empty_source(), "rhr": {"2026-06-28": 54.0}, "steps": {"2026-06-28": 8000}}
    out = merge_sources({"healthkit": a, "oura": b})
    assert set(out.keys()) == set(ALL_KEYS)
