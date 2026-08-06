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


# ── B-b roadmap sueno-y-duraciones: _merge_sleep rellena huecos de horario ──

def test_sleep_fill_bed_min_from_loser_real_case():
    """Caso real del 5-ago: HealthKit gana la noche por `asleep` (inflado por
    el bug A) pero solo Google trae `bed_min` -- el resultado debe conservar
    AMBOS: el asleep del ganador Y el bed_min del perdedor."""
    healthkit = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 620, "inbed": 496, "bed_min": None, "bedtime": None},
    }}
    google = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 480, "inbed": 500, "bed_min": 43, "bedtime": "00:43"},
    }}
    out = merge_sources({"healthkit": healthkit, "google_health": google})
    rec = out["sleep"]["2024-05-02"]
    assert rec["asleep"] == 620  # ganador intacto
    assert rec["bed_min"] == 43  # hueco relleno desde el perdedor
    assert rec["bedtime"] == "00:43"


def test_sleep_fill_never_overwrites_winner_value():
    """Si el ganador YA trae bed_min no-None, el perdedor NUNCA lo pisa."""
    healthkit = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 620, "bed_min": 10},
    }}
    google = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 480, "bed_min": 99},
    }}
    out = merge_sources({"healthkit": healthkit, "google_health": google})
    assert out["sleep"]["2024-05-02"]["bed_min"] == 10


def test_sleep_fill_whitelist_does_not_leak_phase_fields():
    """La lista blanca es la garantía de contención: deep/rem/light/eff/inbed/
    segments NUNCA se rellenan entre fuentes, aunque el ganador los traiga en
    None y el perdedor sí tenga dato."""
    healthkit = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 620, "deep": None, "rem": None, "light": None,
                        "eff": None, "inbed": None, "segments": None},
    }}
    google = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 480, "deep": 88, "rem": 77, "light": 66,
                        "eff": 90, "inbed": 500, "segments": [{"s": 0, "e": 10, "st": "deep"}]},
    }}
    out = merge_sources({"healthkit": healthkit, "google_health": google})
    rec = out["sleep"]["2024-05-02"]
    assert rec["deep"] is None
    assert rec["rem"] is None
    assert rec["light"] is None
    assert rec["eff"] is None
    assert rec["inbed"] is None
    assert rec["segments"] is None


def test_sleep_fill_single_source_unchanged():
    """Fuente única -> resultado idéntico al de hoy (sin relleno posible, no
    hay perdedor de quien tomar nada)."""
    a = {**_empty_source(), "sleep": {
        "2024-05-02": {"asleep": 400, "bed_min": None, "bedtime": None, "waketime": "07:00"},
    }}
    out = merge_sources({"healthkit": a})
    assert out["sleep"]["2024-05-02"] == {"asleep": 400, "bed_min": None, "bedtime": None, "waketime": "07:00"}


def test_sleep_fill_does_not_mutate_fetched_dicts():
    """`_merge_sleep` recibe estructuras que merge_sources sigue usando
    después -- rellenar huecos NUNCA debe mutar en sitio los dicts de entrada."""
    healthkit_rec = {"asleep": 620, "bed_min": None}
    google_rec = {"asleep": 480, "bed_min": 43}
    healthkit = {**_empty_source(), "sleep": {"2024-05-02": healthkit_rec}}
    google = {**_empty_source(), "sleep": {"2024-05-02": google_rec}}
    merge_sources({"healthkit": healthkit, "google_health": google})
    assert healthkit_rec == {"asleep": 620, "bed_min": None}
    assert google_rec == {"asleep": 480, "bed_min": 43}


# ── Dedup de workouts ─────────────────────────────────────────────────────────

def test_workouts_dedup_same_workout_close_duration():
    """Mismo date+name, dur_min 108 vs 110 (diff=2 <=5) -> se juntan en 1.

    Post roadmap fusion-workouts (validación 2026-07-30): el resultado ya NO
    viene de "gana el más completo" (esa lógica se retiró) sino de fusión
    campo a campo (`_fuse_workouts`, criterio 6). `kcal` no tiene conflicto
    (a=None, b=450 -> se rellena el hueco con 450). `dur_min` SÍ es un
    conflicto real (108 vs 110, ambos no-None) -> gana la fuente de mayor
    SOURCE_PRIORITY, healthkit ('a', dur_min=108) sobre google_health ('b',
    dur_min=110). Ambas aserciones (kcal Y dur_min) quedan explícitas para que
    un futuro cambio en la resolución de conflictos no pase inadvertido aquí."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-06-28", "name": "Tennis", "dur_min": 108, "kcal": None}
    ]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-06-28", "name": "Tennis", "dur_min": 110, "kcal": 450, "distance_km": None}
    ]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 1
    # kcal: sin conflicto (solo 'b' lo trae) -> se rellena el hueco.
    assert out["exercises"][0]["kcal"] == 450
    # dur_min: conflicto real (108 vs 110) -> gana healthkit por SOURCE_PRIORITY.
    assert out["exercises"][0]["dur_min"] == 108


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


# ── Roadmap fusion-workouts — identidad por kcal + fusión campo a campo ────────
# Datos reales de prod (usuario `default`, ver ROADMAP.md _dev-harness/fusion-workouts):
#   HK  2026-07-28 | Strength          | dur 42   | kcal 165 | hr None | start None
#   GG  2026-07-28 | Strength training | dur None | kcal 165 | hr 92   | start 13:49
#   HK  2026-07-28 | Tennis            | dur 114  | kcal 550 | hr None | start None
#   GG  2026-07-28 | Tennis            | dur None | kcal 550 | hr 107  | start 21:09
#   HK  2026-07-29 | Strength          | dur 75   | kcal 282 | hr None | start None
#   GG  2026-07-29 | Strength training | dur None | kcal 282 | hr 79   | start 15:57

def test_workouts_fused_by_kcal_equivalent_names_criterio1():
    """Criterio 1 (caso real del Doc, 29-jul): 'Strength' (HK, dur 75) y 'Strength
    training' (GG, avg_hr 79) mismo kcal 282 -> se reconocen como el MISMO
    workout Y se funden campo a campo: la salida trae dur_min 75 Y avg_hr 79
    (ninguno de los dos solo tenía las dos cosas)."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 75, "kcal": 282},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": None, "kcal": 282, "avg_hr": 79, "start": "15:57"},
    ]}
    out = merge_sources({"healthkit": hk, "google_health": gg})
    assert len(out["exercises"]) == 1
    fused = out["exercises"][0]
    assert fused["dur_min"] == 75
    assert fused["avg_hr"] == 79
    assert fused["kcal"] == 282


def test_workouts_fused_same_name_missing_dur_criterio2():
    """Criterio 2 (28-jul real): 'Tennis' con dur_min 114 (HK) y 'Tennis' con
    dur_min None (GG) pero mismo kcal 550 -> se reconocen como el MISMO
    workout (la regla vieja los rechaza, dur_min ausente en un lado) Y se
    funden: dur_min 114 Y avg_hr 107 en la misma salida."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-28", "name": "Tennis", "dur_min": 114, "kcal": 550},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-28", "name": "Tennis", "type": "TENNIS",
         "dur_min": None, "kcal": 550, "avg_hr": 107, "start": "21:09"},
    ]}
    out = merge_sources({"healthkit": hk, "google_health": gg})
    assert len(out["exercises"]) == 1
    fused = out["exercises"][0]
    assert fused["dur_min"] == 114
    assert fused["avg_hr"] == 107


def test_workouts_fuse_conflict_resolved_by_source_priority_criterio6():
    """Criterio 6: si AMBAS fuentes traen el MISMO campo con valores DISTINTOS
    (conflicto real, no hueco), gana la de mayor SOURCE_PRIORITY (healthkit
    sobre google_health) -- incluido el campo `name`."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 114, "kcal": 282},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": 118, "kcal": 282, "avg_hr": 79},
    ]}
    out = merge_sources({"healthkit": hk, "google_health": gg})
    assert len(out["exercises"]) == 1
    fused = out["exercises"][0]
    # dur_min difiere (114 vs 118) -> gana healthkit (114), no google_health.
    assert fused["dur_min"] == 114
    # name también viene de la fuente prioritaria (healthkit).
    assert fused["name"] == "Strength"
    # avg_hr solo lo trae google_health -> sin conflicto, se conserva.
    assert fused["avg_hr"] == 79


def test_workouts_fuse_conflict_google_priority_over_unknown_source():
    """Complemento del criterio 6: si la fuente 'ganadora' (mayor prioridad)
    NO es healthkit sino google_health (p.ej. frente a una fuente desconocida
    peor rankeada), el conflicto lo sigue resolviendo SOURCE_PRIORITY, no el
    orden de inserción arbitrario."""
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "dur_min": 100, "kcal": 282},
    ]}
    mystery = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 105, "kcal": 282},
    ]}
    out = merge_sources({"mystery_device": mystery, "google_health": gg})
    assert len(out["exercises"]) == 1
    # google_health tiene mejor rank que una fuente desconocida -> gana su dur_min.
    assert out["exercises"][0]["dur_min"] == 100


def test_workouts_kcal_match_but_names_not_equivalent_criterio3():
    """Criterio 3: kcal igual (300) el mismo día pero nombres NO equivalentes
    ('Tennis' vs 'Yoga') -> coincidencia de kcal, NO identidad -> 2 entradas."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-28", "name": "Tennis", "dur_min": 60, "kcal": 300},
    ]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-07-28", "name": "Yoga", "dur_min": 55, "kcal": 300},
    ]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2


def test_workouts_two_real_distinct_strength_sessions_not_fused_criterio5():
    """Criterio 5 (29-jul real): DOS sesiones de fuerza LEGÍTIMAS y distintas el
    mismo día (25min/83kcal a las 15:09 y 75min/282kcal a las 15:57, esta
    última duplicada entre HK y GG) deben seguir siendo DOS entradas, no
    fundirse entre sí -- el kcal distinto (83 vs 282) blinda el falso positivo."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 75, "kcal": 282},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": None, "kcal": 282, "avg_hr": 79, "start": "15:57"},
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": 25, "kcal": 83, "avg_hr": 130, "start": "15:09"},
    ]}
    out = merge_sources({"healthkit": hk, "google_health": gg})
    assert len(out["exercises"]) == 2
    kcals = sorted(w["kcal"] for w in out["exercises"])
    assert kcals == [83, 282]


def test_workouts_names_not_equivalent_bike_vs_yoga_no_false_positive():
    """Guarda anti-falso-positivo (riesgo #2 del roadmap): actividades sin
    relación de substring nunca se funden aunque kcal coincida."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-20", "name": "Bike", "dur_min": 40, "kcal": 200},
    ]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-07-20", "name": "Body weight", "dur_min": 40, "kcal": 200},
    ]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2


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


# ── Roadmap fusion-workouts Paso 3 — merge_info coherente tras la FUSIÓN ───────
# (criterio 9 / riesgo #3 del roadmap: _contributing_sources_workouts identificaba
# contribución por id(w) contra las listas originales -- si la fusión crea dicts
# NUEVOS, esa lógica deja de reconocer a nadie y `sources` sale vacío.)

def test_by_metric_exercises_sources_survive_field_fusion_criterio9():
    """El caso real del Doc (29-jul, criterio 1): tras fundir 'Strength' (HK)
    con 'Strength training' (GG) en UN dict nuevo, by_metric.exercises.sources
    debe seguir listando AMBAS fuentes -- no vacío, que es el bug más fácil de
    introducir (roadmap, riesgo #3)."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 75, "kcal": 282},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": None, "kcal": 282, "avg_hr": 79, "start": "15:57"},
    ]}
    merge_sources({"healthkit": hk, "google_health": gg})
    info = last_merge_info()
    assert info["by_metric"]["exercises"]["mode"] == "dedup"
    assert set(info["by_metric"]["exercises"]["sources"]) == {"healthkit", "google_health"}


def test_fused_workout_keys_do_not_leak_internal_provenance_marker():
    """Blindaje explícito: el registro fusionado que sale en `exercises` no debe
    traer NINGUNA clave que no viniera de alguna de las dos fuentes originales
    -- en particular, ninguna clave interna de procedencia (el diseño elegido
    aquí NO marca los dicts, calcula la procedencia aparte en _merge_workouts,
    pero este test la blinda por si un cambio futuro reintroduce el marcado)."""
    hk = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 75, "kcal": 282},
    ]}
    gg = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "type": "STRENGTH_TRAINING",
         "dur_min": None, "kcal": 282, "avg_hr": 79, "start": "15:57"},
    ]}
    out = merge_sources({"healthkit": hk, "google_health": gg})
    fused = out["exercises"][0]
    expected_keys = {"date", "name", "type", "dur_min", "kcal", "avg_hr", "start"}
    assert set(fused.keys()) == expected_keys


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


def test_workouts_kcal_rule_requires_compatible_duration_when_both_present():
    """Guardia añadida tras la validación: kcal IGUALES pero duraciones muy
    distintas (30 vs 120 min) son dos entrenamientos REALES que casualmente
    quemaron lo mismo, NO la misma sesión. Fundirlos perdería uno en silencio,
    que es peor que el duplicado visible. El baseline (antes de la regla de
    kcal) daba 2 entradas aquí; la regla nueva sin esta guardia daba 1."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 30, "kcal": 400}]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 120, "kcal": 400,
         "avg_hr": 110, "start": "19:00"}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 2, (
        f"kcal iguales con duraciones incompatibles NO deben fundirse: {out['exercises']}"
    )


def test_workouts_kcal_rule_still_fuses_when_one_duration_missing():
    """El caso REAL de prod que motivó la regla de kcal sigue funcionando: la
    entrada de Google llega SIN dur_min, así que la guardia de compatibilidad
    de duración no aplica y la fusión ocurre (duración de Apple + pulso de
    Google en un solo registro, que es lo que habilita el cálculo de TRIMP)."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength", "dur_min": 75, "kcal": 282}]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-07-29", "name": "Strength training", "dur_min": None,
         "kcal": 282, "avg_hr": 79, "start": "15:57"}]}
    out = merge_sources({"healthkit": a, "google_health": b})
    assert len(out["exercises"]) == 1, out["exercises"]
    w = out["exercises"][0]
    assert w["dur_min"] == 75 and w["avg_hr"] == 79, w


def test_workouts_chain_fusion_does_not_swallow_a_real_session():
    """Hallazgo del validador (fusión EN CADENA): el dedup greedy compara el
    candidato solo contra el ACUMULADO, no contra los registros que lo formaron.
    Una entrada 'puente' encadenaba dos sesiones REALES distintas y borraba una:
        A: Tennis kcal 400 dur None  (sesión 1, reloj sin duración)
        B: Tennis kcal 400 dur 60    (sesión 1, otro reloj -> funde por kcal)
        C: Tennis kcal 250 dur 62    (sesión 2 REAL -> enganchaba con el
                                      acumulado por |60-62|<=5 y su kcal se perdía)
    _conflicts_with_members lo impide: C contradice al miembro con kcal 400."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-30", "name": "Tennis", "dur_min": None, "kcal": 400}]}
    b = {**_empty_source(), "exercises": [
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 60, "kcal": 400,
         "start": "09:00", "avg_hr": 100},
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 62, "kcal": 250,
         "start": "19:00", "avg_hr": 95}]}
    out = merge_sources({"healthkit": a, "google_health": b})["exercises"]
    assert len(out) == 2, f"la cadena se tragó una sesión real: {out}"
    kcals = sorted(w["kcal"] for w in out)
    assert kcals == [250, 400], f"kcal perdidas en la cadena: {out}"


def test_workouts_same_sport_similar_duration_but_different_kcal_not_fused():
    """Agujero PRE-EXISTENTE cerrado de paso (ya estaba en producción antes de la
    regla de kcal): dos sesiones reales del mismo deporte y día cuyas duraciones
    caen a menos de 5 min se fundían por la regla vieja aunque sus kcal fueran
    claramente distintas (400 vs 250) -> una desaparecía. Ahora kcal distintas
    dentro del grupo bloquean la fusión."""
    a = {**_empty_source(), "exercises": [
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 60, "kcal": 400, "start": "09:00"},
        {"date": "2026-07-30", "name": "Tennis", "dur_min": 62, "kcal": 250, "start": "19:00"}]}
    out = merge_sources({"google_health": a})["exercises"]
    assert len(out) == 2, f"dos sesiones reales fundidas en una: {out}"
    assert sorted(w["kcal"] for w in out) == [250, 400]
