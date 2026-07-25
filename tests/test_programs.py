"""
test_programs.py — Tests de app/programs.py (Roadmap P1, F4, paso 4).

Motor PURO — sin persistencia que testear (eso vive en plan_store.py).
Cubre: catálogo localizado, día fuera de rango -> None, adaptación con
recovery bajo -> light + adapted, sin recovery -> normal, ACWR caution.

Roadmap coach-objetivo-vo2, Paso 1: se añade el programa `vo2_boost` (28
días) al catálogo -> los tests que antes asumían "4 programas" se
actualizan a 5 (ver clase TestVo2BoostProgram al final de este archivo).
"""
from __future__ import annotations

from app import programs


def test_program_ids_are_the_5_expected():
    assert set(programs.PROGRAM_IDS) == {
        "sleep_reset", "aerobic_base", "strength_3x", "stress_reset", "vo2_boost",
    }


def test_program_durations():
    assert programs.program_duration("sleep_reset") == 14
    assert programs.program_duration("aerobic_base") == 28
    assert programs.program_duration("strength_3x") == 28
    assert programs.program_duration("stress_reset") == 14
    assert programs.program_duration("vo2_boost") == 28


def test_program_duration_unknown_id_is_none():
    assert programs.program_duration("no_existe") is None
    assert programs.program_duration(None) is None


def test_program_exists():
    assert programs.program_exists("sleep_reset") is True
    assert programs.program_exists("bogus") is False
    assert programs.program_exists(123) is False
    assert programs.program_exists(None) is False


# ── get_catalog ──────────────────────────────────────────────────────────

def test_get_catalog_returns_5_localized_programs():
    cat = programs.get_catalog("es")
    assert len(cat) == 5
    for entry in cat:
        assert entry["id"] in programs.PROGRAM_IDS
        assert entry["duration_days"] > 0
        assert entry["name"] and isinstance(entry["name"], str)
        assert entry["description"] and isinstance(entry["description"], str)
        # Nunca claves crudas sin traducir en la superficie visible.
        assert not entry["name"].startswith("program_")


def test_get_catalog_different_locales_differ():
    cat_es = {c["id"]: c["name"] for c in programs.get_catalog("es")}
    cat_en = {c["id"]: c["name"] for c in programs.get_catalog("en")}
    assert cat_es["sleep_reset"] != cat_en["sleep_reset"]


# ── task_for_day: rango de días ─────────────────────────────────────────

def test_task_for_day_out_of_range_returns_none():
    assert programs.task_for_day("sleep_reset", 14) is None  # 0-13 válido, 14 fuera
    assert programs.task_for_day("sleep_reset", -1) is None
    assert programs.task_for_day("sleep_reset", 999) is None


def test_task_for_day_unknown_program_returns_none():
    assert programs.task_for_day("bogus", 0) is None


def test_task_for_day_last_valid_index():
    # duración 14 -> índices válidos 0..13
    t = programs.task_for_day("sleep_reset", 13)
    assert t is not None
    t2 = programs.task_for_day("aerobic_base", 27)
    assert t2 is not None


# ── task_for_day: adaptación determinista (criterio 2 del roadmap) ─────

def test_task_for_day_normal_without_recovery_data():
    """Sin dato de recovery -> tarea normal, adapted=False (ausencia ≠ malo)."""
    t = programs.task_for_day("sleep_reset", 0, today_row={}, summary={})
    assert t is not None
    assert t["adapted"] is False
    assert t["adapted_reason"] is None


def test_task_for_day_adapts_with_low_recovery():
    t = programs.task_for_day("aerobic_base", 0, today_row={"recovery": 20}, summary={})
    assert t["adapted"] is True
    assert t["adapted_reason"]
    assert "recuperación" in t["adapted_reason"].lower() or "recovery" in t["adapted_reason"].lower()


def test_task_for_day_normal_with_high_recovery():
    t = programs.task_for_day("aerobic_base", 0, today_row={"recovery": 70}, summary={})
    assert t["adapted"] is False


def test_task_for_day_recovery_threshold_exact_34_not_adapted():
    t = programs.task_for_day("aerobic_base", 0, today_row={"recovery": 34}, summary={})
    assert t["adapted"] is False  # umbral es <34, no <=34


def test_task_for_day_adapts_with_acwr_caution_zone():
    t = programs.task_for_day("aerobic_base", 0, today_row={}, summary={"acwr_zone": "precaucion"})
    assert t["adapted"] is True


def test_task_for_day_adapts_with_acwr_alto_zone():
    t = programs.task_for_day("aerobic_base", 0, today_row={}, summary={"acwr_zone": "alto"})
    assert t["adapted"] is True


def test_task_for_day_not_adapted_with_acwr_normal_zone():
    t = programs.task_for_day("aerobic_base", 0, today_row={}, summary={"acwr_zone": "optimo"})
    assert t["adapted"] is False


def test_task_for_day_both_reasons_combine():
    t = programs.task_for_day(
        "aerobic_base", 0,
        today_row={"recovery": 10}, summary={"acwr_zone": "alto"},
    )
    assert t["adapted"] is True
    assert t["adapted_reason"]


def test_task_for_day_light_variant_has_different_params_when_defined():
    """aerobic_base día 0 (cardio_easy) tiene light con params reducidos."""
    normal = programs.task_for_day("aerobic_base", 0, today_row={}, summary={})
    light = programs.task_for_day("aerobic_base", 0, today_row={"recovery": 10}, summary={})
    assert normal["params"].get("min") != light["params"].get("min")


def test_task_for_day_shape():
    t = programs.task_for_day("sleep_reset", 0, today_row={}, summary={})
    assert set(t.keys()) == {"task_key", "kind", "params", "label", "adapted", "adapted_reason"}
    assert t["kind"] in ("sleep", "cardio", "strength", "habit")
    assert t["label"] and not t["label"].startswith("task_")


# ── nunca lanza ──────────────────────────────────────────────────────────

def test_task_for_day_never_raises_on_garbage():
    # Nunca lanza — con basura total puede degradar a None (fail-safe), pero
    # con un today_row bien tipado y un valor de recovery no numérico dentro,
    # sigue devolviendo una tarea normal (el campo malformado se ignora).
    assert programs.task_for_day(None, None) is None
    assert programs.task_for_day(123, "x") is None
    programs.task_for_day("sleep_reset", 0, today_row="garbage", summary=12345)  # no debe lanzar
    t = programs.task_for_day("sleep_reset", 0, today_row={"recovery": "garbage"}, summary={})
    assert t is not None
    assert t["adapted"] is False


def test_get_catalog_never_raises():
    assert programs.get_catalog(None) is not None
    assert programs.get_catalog("xx") is not None


# ── vo2_boost (Roadmap coach-objetivo-vo2, Paso 1) ──────────────────────────

class TestVo2BoostProgram:
    """28 días: zona 2 + 1 caminata outdoor de calibración/semana + el
    deporte del usuario como su día de intensidad + descansos (criterio 4
    del roadmap)."""

    def test_in_catalog_with_28_days(self):
        cat = {c["id"]: c for c in programs.get_catalog("es")}
        assert "vo2_boost" in cat
        assert cat["vo2_boost"]["duration_days"] == 28
        assert not cat["vo2_boost"]["name"].startswith("program_")
        assert not cat["vo2_boost"]["description"].startswith("program_")

    def test_localized_in_all_4_locales(self):
        for locale in ("es", "en", "fr", "pt"):
            cat = {c["id"]: c for c in programs.get_catalog(locale)}
            assert cat["vo2_boost"]["name"]
            assert cat["vo2_boost"]["description"]

    def test_task_for_day_valid_across_full_range(self):
        for i in range(28):
            t = programs.task_for_day("vo2_boost", i, today_row={}, summary={})
            assert t is not None, f"día {i} no debería ser None"
            assert t["kind"] in ("sleep", "cardio", "strength", "habit")

    def test_task_for_day_out_of_range(self):
        assert programs.task_for_day("vo2_boost", 28) is None
        assert programs.task_for_day("vo2_boost", -1) is None

    def test_week_has_exactly_one_outdoor_calibration_task(self):
        """Criterio 4: la semana (7 días) contiene EXACTAMENTE 1 tarea
        task_run_outdoor_calibrate (día 2, 0-based)."""
        week_keys = [
            programs.task_for_day("vo2_boost", i, today_row={}, summary={})["task_key"]
            for i in range(7)
        ]
        assert week_keys.count("task_run_outdoor_calibrate") == 1

    def test_28_days_has_exactly_4_outdoor_calibration_tasks(self):
        """4 semanas × 1 caminata de calibración por semana = 4 en total."""
        all_keys = [
            programs.task_for_day("vo2_boost", i, today_row={}, summary={})["task_key"]
            for i in range(28)
        ]
        assert all_keys.count("task_run_outdoor_calibrate") == 4

    def test_play_sport_task_present_once_per_week(self):
        week_keys = [
            programs.task_for_day("vo2_boost", i, today_row={}, summary={})["task_key"]
            for i in range(7)
        ]
        assert week_keys.count("task_play_sport") == 1

    def test_outdoor_calibration_has_light_variant_with_reduced_params(self):
        normal = programs.task_for_day("vo2_boost", 2, today_row={}, summary={})
        light = programs.task_for_day("vo2_boost", 2, today_row={"recovery": 10}, summary={})
        assert normal["task_key"] == "task_run_outdoor_calibrate"
        assert light["adapted"] is True
        assert normal["params"]["min"] != light["params"]["min"]

    def test_play_sport_light_variant_falls_back_to_cardio_easy(self):
        light = programs.task_for_day("vo2_boost", 3, today_row={"recovery": 5}, summary={})
        assert light["adapted"] is True
        assert light["task_key"] == "task_cardio_easy"

    def test_task_labels_are_translated_not_raw_keys(self):
        for i in range(7):
            t = programs.task_for_day("vo2_boost", i, today_row={}, summary={})
            assert not t["label"].startswith("task_")
