"""
test_programs.py — Tests de app/programs.py (Roadmap P1, F4, paso 4).

Motor PURO — sin persistencia que testear (eso vive en plan_store.py).
Cubre: catálogo localizado, día fuera de rango -> None, adaptación con
recovery bajo -> light + adapted, sin recovery -> normal, ACWR caution.
"""
from __future__ import annotations

from app import programs


def test_program_ids_are_the_4_expected():
    assert set(programs.PROGRAM_IDS) == {
        "sleep_reset", "aerobic_base", "strength_3x", "stress_reset",
    }


def test_program_durations():
    assert programs.program_duration("sleep_reset") == 14
    assert programs.program_duration("aerobic_base") == 28
    assert programs.program_duration("strength_3x") == 28
    assert programs.program_duration("stress_reset") == 14


def test_program_duration_unknown_id_is_none():
    assert programs.program_duration("no_existe") is None
    assert programs.program_duration(None) is None


def test_program_exists():
    assert programs.program_exists("sleep_reset") is True
    assert programs.program_exists("bogus") is False
    assert programs.program_exists(123) is False
    assert programs.program_exists(None) is False


# ── get_catalog ──────────────────────────────────────────────────────────

def test_get_catalog_returns_4_localized_programs():
    cat = programs.get_catalog("es")
    assert len(cat) == 4
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
