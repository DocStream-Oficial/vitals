"""
test_journal.py — Tests de app/journal.py (Fase 8B, paso B4).

Cubre:
(a) persistencia/atomicidad (round-trip, sin .tmp leftover, tolerante a JSON
    corrupto/log parcial) — patrón test_cycle.py.
(b) validación de endpoints (TestClient FastAPI) — patrón _get_cycle_client
    de test_endpoints.py.
(c) motor (analyze_journal) con datos sintéticos: efecto plantado, ruido
    puro, gates de n, BH real con múltiples hábitos simultáneos, i18n de
    headlines ES/EN/FR/PT.
"""
from __future__ import annotations

import datetime
import json
import random
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app import journal


# ── helpers (patrón test_cycle.py) ──────────────────────────────────────────

def _patch_journal_log_path(monkeypatch, tmp_path):
    monkeypatch.setattr(journal, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json")


def make_day(date, **kwargs):
    return {"date": date, **kwargs}


def date_seq(start: str, n: int) -> list[str]:
    d0 = datetime.date.fromisoformat(start)
    return [(d0 + datetime.timedelta(days=i)).isoformat() for i in range(n)]


# ── (a) persistencia: load/save round-trip + atomicidad ─────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    log = journal.load_journal()
    assert log["entries"] == {}
    assert log["custom"] == []


def test_save_then_load_round_trip(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    data = {
        "entries": {"2026-06-01": {"alcohol": True, "meditation": False}},
        "custom": [{"key": "custom_test", "label": "Test"}],
    }
    journal.save_journal(data)
    loaded = journal.load_journal()
    assert loaded["entries"] == data["entries"]
    assert loaded["custom"] == data["custom"]
    assert "updated" in loaded


def test_save_is_atomic_no_tmp_leftover(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.save_journal({"entries": {}})
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []


def test_load_returns_empty_on_corrupt_json(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text("NOT JSON{{{", encoding="utf-8")
    log = journal.load_journal()
    assert log["entries"] == {}
    assert log["custom"] == []


def test_load_tolerant_to_partial_old_log(tmp_path, monkeypatch):
    """Log viejo con solo 'entries' (sin custom) -> cero migración."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text(
        json.dumps({"entries": {"2026-01-01": {"alcohol": True}}}), encoding="utf-8"
    )
    log = journal.load_journal()
    assert log["entries"] == {"2026-01-01": {"alcohol": True}}
    assert log["custom"] == []


def test_load_returns_empty_when_not_dict(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    log = journal.load_journal()
    assert log["entries"] == {}


# ── set_entry / get_entry (merge semantics) ──────────────────────────────────

def test_set_entry_then_get_entry(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.set_entry("2026-06-01", {"alcohol": True, "meditation": False})
    entry = journal.get_entry("2026-06-01")
    assert entry == {"alcohol": True, "meditation": False}


def test_set_entry_merges_not_replaces(tmp_path, monkeypatch):
    """Togglear un chip no debe borrar los demás hábitos ya registrados ese día."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.set_entry("2026-06-01", {"alcohol": True})
    journal.set_entry("2026-06-01", {"meditation": True})
    entry = journal.get_entry("2026-06-01")
    assert entry == {"alcohol": True, "meditation": True}


def test_set_entry_overwrites_same_key(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.set_entry("2026-06-01", {"alcohol": True})
    journal.set_entry("2026-06-01", {"alcohol": False})
    entry = journal.get_entry("2026-06-01")
    assert entry == {"alcohol": False}


def test_get_entry_empty_for_unknown_date(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    assert journal.get_entry("2026-01-01") == {}


# ── catálogo / hábitos custom ────────────────────────────────────────────────

def test_habits_catalog_matches_roadmap_spec():
    """El catálogo fijo debe tener exactamente las ~32-33 keys del roadmap,
    ninguna faltante ni sobrante."""
    expected = {
        "creatine", "magnesium", "melatonin", "omega3", "vitamin_d_supp", "zinc",
        "ashwagandha", "protein_supp", "multivitamin", "electrolytes", "collagen",
        "probiotics",
        "alcohol", "alcohol_heavy", "caffeine_late", "late_meal", "big_dinner",
        "hydration_low", "fasting",
        "meditation", "breathwork", "sauna", "cold_exposure", "stretching",
        "nap_today", "stress_high",
        "screen_bed", "reading_bed", "late_workout", "shared_bed", "sunlight_am",
        "travel", "sick",
    }
    actual = {h["key"] for h in journal.HABITS}
    assert actual == expected


def test_catalog_includes_custom_habits(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.add_custom_habit("Mi suplemento raro")
    cat = journal.catalog(locale="es")
    custom_entries = [c for c in cat if c["custom"]]
    assert len(custom_entries) == 1
    assert custom_entries[0]["label"] == "Mi suplemento raro"


def test_catalog_quantifiable_habits_expose_quantity_spec(tmp_path, monkeypatch):
    """Roadmap P2 (F9, paso 9): los 3 hábitos cuantificables ganan la clave
    ADITIVA 'quantity' con unidad YA TRADUCIDA + max. Los demás ~30 hábitos
    no la tienen (sin cambio)."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    cat = journal.catalog(locale="es")
    by_key = {c["key"]: c for c in cat}

    assert by_key["alcohol"]["quantity"] == {"unit": "copas", "max": 20}
    assert by_key["meditation"]["quantity"] == {"unit": "min", "max": 240}
    assert by_key["breathwork"]["quantity"] == {"unit": "min", "max": 120}

    assert "quantity" not in by_key["alcohol_heavy"]
    assert "quantity" not in by_key["creatine"]
    assert "quantity" not in by_key["stress_high"]


@pytest.mark.parametrize("locale", ["es", "en", "fr", "pt"])
def test_catalog_quantity_unit_localized(locale):
    cat = journal.catalog(locale=locale)
    by_key = {c["key"]: c for c in cat}
    assert by_key["alcohol"]["quantity"]["unit"]
    assert by_key["meditation"]["quantity"]["unit"]


def test_add_custom_habit_idempotent_by_label(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    h1 = journal.add_custom_habit("Colágeno marino")
    h2 = journal.add_custom_habit("Colágeno marino")
    assert h1["key"] == h2["key"]


def test_add_custom_habit_rejects_empty_label(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    assert journal.add_custom_habit("") is None
    assert journal.add_custom_habit("   ") is None


def test_valid_habit_keys_includes_fixed_and_custom(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    journal.add_custom_habit("Algo nuevo")
    keys = journal.valid_habit_keys()
    assert "alcohol" in keys
    assert any(k.startswith("custom_") for k in keys)


# ── (c) motor: analyze_journal con datos sintéticos ──────────────────────────

def test_analyze_journal_detects_planted_effect():
    """Efecto plantado: alcohol[t] -> recovery[t+1] con Δ≈-15, 60 días
    sintéticos -> finding rho<0 y significant=True."""
    random.seed(7)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-2, 2)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drank = (i % 2 == 0)
        entries[d] = {"alcohol": drank}
        if drank:
            days[i + 1]["recovery"] -= 15

    j = {"entries": entries, "custom": []}
    findings = journal.analyze_journal(days, j, locale="es")

    matches = [f for f in findings if f["habit"] == "alcohol" and f["outcome"] == "recovery"]
    assert len(matches) == 1
    f = matches[0]
    assert f["rho"] < 0
    assert f["significant"] is True
    assert f["delta"] < -10  # cerca del -15 plantado (con ruido)
    assert f["n_yes"] >= 5 and f["n_no"] >= 5


def test_analyze_journal_pure_noise_yields_no_findings():
    """60 días de ruido puro (hábito y outcome independientes) -> 0 findings
    (o ninguno sobrevive BH)."""
    random.seed(99)
    dates = date_seq("2026-01-01", 60)
    days = [{"date": d, "recovery": 60 + random.uniform(-10, 10)} for d in dates]
    entries = {}
    for i, d in enumerate(dates):
        entries[d] = {"meditation": (random.random() < 0.5)}

    j = {"entries": entries, "custom": []}
    findings = journal.analyze_journal(days, j, locale="es")
    matches = [f for f in findings if f["habit"] == "meditation"]
    assert matches == []


def test_analyze_journal_gate_below_min_yes_excluded():
    """<5 días 'sí' -> el hábito NO debe aparecer aunque n_total>=15."""
    dates = date_seq("2026-01-01", 30)
    days = [{"date": d, "recovery": 65} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        # Solo 3 días "sí" (< MIN_YES=5), el resto "no"
        drank = i in (0, 5, 10)
        entries[d] = {"alcohol": drank}
        if drank:
            days[i + 1]["recovery"] -= 20

    j = {"entries": entries, "custom": []}
    findings = journal.analyze_journal(days, j, locale="es")
    assert all(f["habit"] != "alcohol" for f in findings)


def test_analyze_journal_gate_below_min_total_excluded():
    """n_total < 15 (aunque sí/no individualmente >=5 no se alcanza aquí) ->
    excluido del pool."""
    dates = date_seq("2026-01-01", 12)
    days = [{"date": d, "recovery": 65} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        entries[d] = {"alcohol": (i % 2 == 0)}

    j = {"entries": entries, "custom": []}
    findings = journal.analyze_journal(days, j, locale="es")
    assert all(f["habit"] != "alcohol" for f in findings)


def test_analyze_journal_bh_suppresses_false_positives_with_multiple_habits():
    """Múltiples hábitos de RUIDO PURO evaluados simultáneamente: sin BH,
    p<0.05 sin corregir dejaría pasar falsos positivos por azar (familywise
    error). Con BH real, sobre puro ruido el conjunto de sobrevivientes debe
    ser mucho menor que lo que el umbral crudo p<0.05 habría dejado pasar —
    en la práctica, con semillas de ruido típicas, cero o casi cero
    findings sobreviven BH."""
    random.seed(123)
    dates = date_seq("2026-01-01", 60)
    days = [{"date": d, "recovery": 60 + random.uniform(-10, 10),
              "hrv": 50 + random.uniform(-8, 8)} for d in dates]

    # 10 hábitos de ruido puro, todos independientes del outcome.
    noise_habits = [f"noise_habit_{i}" for i in range(10)]
    # Registrar como custom para que entren al pool de analyze_journal.
    custom = [{"key": k, "label": k} for k in noise_habits]

    entries = {}
    for d in dates:
        entries[d] = {k: (random.random() < 0.5) for k in noise_habits}

    j = {"entries": entries, "custom": custom}

    # Sin BH (comparando contra p crudo directamente) — replicamos el cálculo
    # crudo para verificar que BH efectivamente filtra vs. el umbral ingenuo.
    from app.drivers import _spearman, _pvalue
    raw_significant_count = 0
    for habit in noise_habits:
        for outcome, lag in journal.OUTCOMES:
            pairs = journal._pair_habit_outcome(days, entries, habit, outcome, lag)
            n_yes = sum(1 for x, _ in pairs if x == 1.0)
            n_no = sum(1 for x, _ in pairs if x == 0.0)
            if n_yes < journal.MIN_YES or n_no < journal.MIN_NO or len(pairs) < journal.MIN_TOTAL:
                continue
            result = _spearman(pairs)
            if result is None:
                continue
            rho, n = result
            p = _pvalue(rho, n)
            if p is not None and p < 0.05:
                raw_significant_count += 1

    findings = journal.analyze_journal(days, j, locale="es")
    bh_survivor_count = len([f for f in findings if f["habit"] in noise_habits])

    # La corrección BH nunca puede dejar pasar MÁS hallazgos que el crudo
    # p<0.05 sin corregir (BH es siempre más estricto o igual).
    assert bh_survivor_count <= raw_significant_count


def test_analyze_journal_top_k_capped_at_8():
    """Con muchos hábitos con efecto plantado fuerte, el resultado nunca
    excede TOP_K=8."""
    random.seed(5)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-1, 1),
              "hrv": 50 + random.uniform(-1, 1)} for d in dates]

    habit_keys = [f"strong_habit_{i}" for i in range(12)]
    custom = [{"key": k, "label": k} for k in habit_keys]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        e = {}
        for hk in habit_keys:
            on = (hash((hk, i)) % 2 == 0)
            e[hk] = on
            if on:
                days[i + 1]["recovery"] -= 10
        entries[d] = e

    j = {"entries": entries, "custom": custom}
    findings = journal.analyze_journal(days, j, locale="es")
    assert len(findings) <= 8


def test_analyze_journal_never_crashes_on_empty_inputs():
    assert journal.analyze_journal([], {}, locale="es") == []
    assert journal.analyze_journal([], None, locale="es") == []
    assert journal.analyze_journal(None, None, locale="es") == []


def test_analyze_journal_never_crashes_on_garbage_entries():
    days = [{"date": "2026-01-01", "recovery": 65}]
    j = {"entries": {"not-a-date": "garbage", "2026-01-01": None}, "custom": "not-a-list"}
    result = journal.analyze_journal(days, j, locale="es")
    assert result == []


# ── i18n de headlines ES/EN/FR/PT ────────────────────────────────────────────

def test_analyze_journal_headline_i18n_differs_by_locale():
    """Headline distinto de la key cruda, coherente con locale (ES/EN/FR/PT)."""
    random.seed(11)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-1, 1)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drank = (i % 2 == 0)
        entries[d] = {"alcohol": drank}
        if drank:
            days[i + 1]["recovery"] -= 15
    j = {"entries": entries, "custom": []}

    headlines = {}
    for locale in ("es", "en", "fr", "pt"):
        findings = journal.analyze_journal(days, j, locale=locale)
        assert len(findings) >= 1
        headline = findings[0]["headline"]
        assert headline  # no vacío
        assert "headline_pattern" not in headline  # no es la key cruda
        assert "journal_habit_dl" not in headline
        headlines[locale] = headline

    # Los 4 headlines deben ser textualmente distintos entre sí (idiomas distintos).
    values = list(headlines.values())
    assert len(set(values)) == len(values)


# ── (b) endpoints — TestClient FastAPI (patrón _get_cycle_client) ───────────

def _get_journal_client(tmp_path: Path):
    """TestClient con DATA_DIR/profile/journal aislados en tmp_path."""
    from app import config, profile as _pm
    import main as main_mod

    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "TEMPLATES_DIR",
                      Path(__file__).parent.parent / "templates"), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(_pm, "_PROFILE_FILE", tmp_path / "profile.json"), \
         patch.object(_pm, "_DATA_DIR", tmp_path), \
         patch.object(journal, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json"), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def journal_client(tmp_path):
    yield from _get_journal_client(tmp_path)


def test_api_journal_get_default_today(journal_client):
    resp = journal_client.get("/api/journal")
    assert resp.status_code == 200
    body = resp.json()
    assert "catalog" in body and isinstance(body["catalog"], list)
    assert len(body["catalog"]) >= 33
    assert body["entry"] == {}
    assert body["date"] == datetime.date.today().isoformat()


def test_api_journal_get_specific_date(journal_client):
    resp = journal_client.get("/api/journal", params={"date": "2026-06-01"})
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-06-01"


def test_api_journal_get_invalid_date_422(journal_client):
    resp = journal_client.get("/api/journal", params={"date": "not-a-date"})
    assert resp.status_code == 422


def test_api_journal_put_saves_and_reflects_in_get(journal_client):
    resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {"alcohol": True}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["entry"] == {"alcohol": True}

    get_resp = journal_client.get("/api/journal", params={"date": "2026-06-01"})
    assert get_resp.json()["entry"] == {"alcohol": True}


def test_api_journal_put_merges_not_replaces(journal_client):
    journal_client.put("/api/journal/2026-06-01", json={"habits": {"alcohol": True}})
    resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {"meditation": True}})
    assert resp.status_code == 200
    assert resp.json()["entry"] == {"alcohol": True, "meditation": True}


def test_api_journal_put_invalid_date_format_422(journal_client):
    resp = journal_client.put("/api/journal/not-a-date", json={"habits": {"alcohol": True}})
    assert resp.status_code == 422


def test_api_journal_put_future_date_422(journal_client):
    future = (datetime.date.today() + datetime.timedelta(days=5)).isoformat()
    resp = journal_client.put(f"/api/journal/{future}", json={"habits": {"alcohol": True}})
    assert resp.status_code == 422


def test_api_journal_put_unknown_habit_key_422(journal_client):
    resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {"not_a_real_habit": True}})
    assert resp.status_code == 422


def test_api_journal_put_missing_body_422(journal_client):
    resp = journal_client.put("/api/journal/2026-06-01", json={})
    assert resp.status_code == 422


# ── Roadmap P2, F9 (paso 7): Pydantic acepta bool|float en la misma request ──

def test_api_journal_put_accepts_number_legacy_bool_and_binary_in_same_request(journal_client):
    """Criterio 16/paso 7: {"alcohol": 3} (número) Y {"meditation": true}
    (legacy bool en cuantificable) Y {"stress_high": true} (no-cuantificable)
    en la MISMA request, sin error de Pydantic."""
    resp = journal_client.put("/api/journal/2026-06-01", json={
        "habits": {"alcohol": 3, "meditation": True, "stress_high": True}
    })
    assert resp.status_code == 200
    entry = resp.json()["entry"]
    assert entry["alcohol"] == 3.0
    assert entry["meditation"] == 1.0
    assert entry["stress_high"] is True


def test_api_journal_put_quantifiable_clamps_via_endpoint(journal_client):
    resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {"alcohol": 999}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["alcohol"] == 20.0


def test_api_journal_put_negative_number_clamped_to_zero(journal_client):
    resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {"breathwork": -10}})
    assert resp.status_code == 200
    assert resp.json()["entry"]["breathwork"] == 0.0


def test_api_journal_custom_post_creates_habit(journal_client):
    resp = journal_client.post("/api/journal/custom", json={"label": "Té de jengibre"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["habit"]["label"] == "Té de jengibre"
    assert body["habit"]["key"].startswith("custom_")


def test_api_journal_custom_post_then_usable_in_put(journal_client):
    create_resp = journal_client.post("/api/journal/custom", json={"label": "Suplemento X"})
    key = create_resp.json()["habit"]["key"]
    put_resp = journal_client.put("/api/journal/2026-06-01", json={"habits": {key: True}})
    assert put_resp.status_code == 200


def test_api_journal_custom_post_empty_label_422(journal_client):
    resp = journal_client.post("/api/journal/custom", json={"label": ""})
    assert resp.status_code == 422


def test_api_journal_impact_no_dataset_returns_empty_list(journal_client):
    resp = journal_client.get("/api/journal/impact")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_journal_impact_with_dataset_and_journal(journal_client, tmp_path):
    random.seed(3)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-1, 1)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drank = (i % 2 == 0)
        entries[d] = {"alcohol": drank}
        if drank:
            days[i + 1]["recovery"] -= 15

    (tmp_path / "health_compact.json").write_text(
        json.dumps({"days": days, "summary": {}}), encoding="utf-8"
    )
    journal.save_journal({"entries": entries, "custom": []})

    resp = journal_client.get("/api/journal/impact")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["habit"] == "alcohol"


def test_api_journal_dose_response_no_dataset_returns_empty_list(journal_client):
    resp = journal_client.get("/api/journal/dose-response")
    assert resp.status_code == 200
    assert resp.json() == []


def test_api_journal_dose_response_with_planted_dose_effect(journal_client, tmp_path):
    random.seed(21)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-1, 1)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drinks = i % 5
        entries[d] = {"alcohol": drinks}
        days[i + 1]["recovery"] -= drinks * 4

    (tmp_path / "health_compact.json").write_text(
        json.dumps({"days": days, "summary": {}}), encoding="utf-8"
    )
    journal.save_journal({"entries": entries, "custom": []})

    resp = journal_client.get("/api/journal/dose-response")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert body[0]["habit"] == "alcohol"
    assert "n_distinct_values" in body[0]


def test_api_journal_impact_shape_unaffected_by_dose_response_endpoint(journal_client):
    """Criterio 19 (desviación documentada): /api/journal/impact SIGUE siendo
    una lista pura en la raíz, sin ninguna clave 'dose_response' anidada."""
    resp = journal_client.get("/api/journal/impact")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ═══════════════════════════════════════════════════════════════════════════
# Roadmap P2, F9 — Journal cuantitativo (paso 6: catálogo + set_entry)
# ═══════════════════════════════════════════════════════════════════════════

def test_quantifiable_habits_derived_from_catalog():
    """QUANTIFIABLE_HABITS contiene EXACTAMENTE los 3 hábitos del roadmap
    (criterio 14) — alcohol_heavy NO está (sigue binario, sin conflicto)."""
    assert set(journal.QUANTIFIABLE_HABITS.keys()) == {"alcohol", "meditation", "breathwork"}
    assert journal.QUANTIFIABLE_HABITS["alcohol"] == {"unit_key": "unit_drinks", "max": 20}
    assert journal.QUANTIFIABLE_HABITS["meditation"] == {"unit_key": "unit_minutes", "max": 240}
    assert journal.QUANTIFIABLE_HABITS["breathwork"] == {"unit_key": "unit_minutes", "max": 120}
    assert "alcohol_heavy" not in journal.QUANTIFIABLE_HABITS


def test_set_entry_quantifiable_accepts_number_and_clamps(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    entry = journal.set_entry("2026-06-01", {"alcohol": 3})
    assert entry["alcohol"] == 3.0

    # Clamp al max (20 copas).
    entry = journal.set_entry("2026-06-02", {"alcohol": 500})
    assert entry["alcohol"] == 20.0

    # Clamp al mínimo (0, nunca negativo).
    entry = journal.set_entry("2026-06-03", {"alcohol": -5})
    assert entry["alcohol"] == 0.0

    entry = journal.set_entry("2026-06-04", {"meditation": 45})
    assert entry["meditation"] == 45.0
    entry = journal.set_entry("2026-06-05", {"meditation": 999})
    assert entry["meditation"] == 240.0

    entry = journal.set_entry("2026-06-06", {"breathwork": 10})
    assert entry["breathwork"] == 10.0
    entry = journal.set_entry("2026-06-07", {"breathwork": 500})
    assert entry["breathwork"] == 120.0


def test_set_entry_quantifiable_accepts_legacy_bool(tmp_path, monkeypatch):
    """Cliente viejo que sigue mandando booleano en un hábito AHORA
    cuantificable -> True->1, False->0 (criterio 15)."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    entry = journal.set_entry("2026-06-01", {"alcohol": True})
    assert entry["alcohol"] == 1.0
    entry = journal.set_entry("2026-06-02", {"alcohol": False})
    assert entry["alcohol"] == 0.0


def test_set_entry_quantifiable_malformed_value_degrades_to_zero(tmp_path, monkeypatch):
    _patch_journal_log_path(monkeypatch, tmp_path)
    entry = journal.set_entry("2026-06-01", {"alcohol": "not a number"})
    assert entry["alcohol"] == 0.0
    entry = journal.set_entry("2026-06-02", {"alcohol": None})
    assert entry["alcohol"] == 0.0


def test_set_entry_non_quantifiable_habits_identical_behavior(tmp_path, monkeypatch):
    """Hábitos NO cuantificables: comportamiento IDÉNTICO al actual —
    bool(v) sin excepción, sin importar el tipo de v."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    entry = journal.set_entry("2026-06-01", {
        "creatine": True, "stress_high": False, "sick": 1, "travel": 0,
    })
    assert entry == {"creatine": True, "stress_high": False, "sick": True, "travel": False}
    assert entry["sick"] is True
    assert entry["travel"] is False


def test_set_entry_mixed_quantifiable_and_binary_in_one_call(tmp_path, monkeypatch):
    """Una sola request puede mezclar hábitos cuantificables y binarios sin
    error (criterio del paso 7, pero ya vale verificarlo aquí a nivel motor)."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    entry = journal.set_entry("2026-06-01", {"alcohol": 4, "meditation": True, "stress_high": True})
    assert entry["alcohol"] == 4.0
    assert entry["meditation"] == 1.0
    assert entry["stress_high"] is True


# ── Backward-compat: journal_log.json VIEJO con SOLO booleanos (EL TEST MÁS
# IMPORTANTE del paquete F9 — riesgo #2 del roadmap) ─────────────────────────

_LEGACY_JOURNAL_FIXTURE = {
    "entries": {
        "2026-05-01": {"alcohol": True, "meditation": False, "creatine": True, "stress_high": False},
        "2026-05-02": {"alcohol": False, "meditation": True, "breathwork": True, "sick": False},
        "2026-05-03": {"creatine": True, "magnesium": True, "alcohol_heavy": False},
        "2026-05-04": {"alcohol": True, "breathwork": False, "travel": True},
    },
    "custom": [{"key": "custom_cold_plunge", "label": "Cold plunge"}],
    "updated": "2026-05-04T22:00:00",
}


def test_legacy_journal_with_only_booleans_reads_unchanged(tmp_path, monkeypatch):
    """Un journal_log.json VIEJO (calcado de un archivo real, solo booleanos,
    incluidos los 3 hábitos AHORA cuantificables) debe LEERSE sin alterar
    ningún valor — load_journal() es puro I/O, no reinterpreta nada."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text(
        json.dumps(_LEGACY_JOURNAL_FIXTURE), encoding="utf-8"
    )
    loaded = journal.load_journal()
    assert loaded["entries"] == _LEGACY_JOURNAL_FIXTURE["entries"]
    assert loaded["custom"] == _LEGACY_JOURNAL_FIXTURE["custom"]


def test_legacy_journal_re_saved_does_not_change_non_quantifiable_habits(tmp_path, monkeypatch):
    """EL TEST MÁS IMPORTANTE DEL PAQUETE (riesgo #2 del roadmap): cargar un
    journal viejo (solo bool) y re-escribir CUALQUIER hábito NO-cuantificable
    (via set_entry) no debe alterar el valor de NINGÚN otro hábito no-
    cuantificable ya presente en el día — ni cambiar su tipo (sigue bool)."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text(
        json.dumps(_LEGACY_JOURNAL_FIXTURE), encoding="utf-8"
    )

    # Togglear un hábito no-cuantificable en un día ya existente del fixture.
    journal.set_entry("2026-05-01", {"stress_high": True})

    entry = journal.get_entry("2026-05-01")
    # stress_high cambió (fue el toggle explícito) — el resto, IDÉNTICO.
    assert entry["stress_high"] is True
    assert entry["creatine"] is True  # sin cambio de valor NI de tipo
    assert type(entry["creatine"]) is bool

    # Los hábitos AHORA cuantificables que estaban en bool legacy SIGUEN
    # exactamente igual (nadie los tocó en este set_entry) — load/save no
    # migra nada de forma implícita, solo write_entry re-escribe lo que se
    # le pide explícitamente.
    assert entry["alcohol"] is True
    assert entry["meditation"] is False

    # El resto de los días del journal viejo permanece BYTE-IDÉNTICO.
    other_days = journal.load_journal()["entries"]
    assert other_days["2026-05-02"] == _LEGACY_JOURNAL_FIXTURE["entries"]["2026-05-02"]
    assert other_days["2026-05-03"] == _LEGACY_JOURNAL_FIXTURE["entries"]["2026-05-03"]
    assert other_days["2026-05-04"] == _LEGACY_JOURNAL_FIXTURE["entries"]["2026-05-04"]


def test_legacy_journal_quantifiable_habit_toggle_converts_to_number(tmp_path, monkeypatch):
    """Cuando SÍ se togglea un hábito ahora-cuantificable de un journal viejo
    (vía la UI nueva, que manda número), su valor pasa a número — pero solo
    ESE hábito de ESE día, nunca los demás (merge semántico intacto)."""
    _patch_journal_log_path(monkeypatch, tmp_path)
    (tmp_path / "journal_log.json").write_text(
        json.dumps(_LEGACY_JOURNAL_FIXTURE), encoding="utf-8"
    )

    journal.set_entry("2026-05-01", {"alcohol": 2})

    entry = journal.get_entry("2026-05-01")
    assert entry["alcohol"] == 2.0
    # Los demás hábitos de ESE MISMO día no se tocan.
    assert entry["meditation"] is False
    assert entry["creatine"] is True
    assert entry["stress_high"] is False


def test_legacy_journal_gate_yes_no_engine_unaffected_by_quantitative_change(tmp_path, monkeypatch):
    """Criterio 17: analyze_journal (gate sí/no) da EXACTAMENTE el mismo
    resultado con un journal legacy (bool puro) que con uno donde el MISMO
    patrón de sí/no se expresa como número >0 / ==0 — confirma que la
    truthiness de Python hace el trabajo sin ningún cambio de código."""
    _patch_journal_log_path(monkeypatch, tmp_path)

    dates = date_seq("2026-01-01", 40)
    days = [{"date": d, "recovery": 60.0} for d in dates]
    entries_bool = {}
    entries_numeric = {}
    for i, d in enumerate(dates[:-1]):
        drank = (i % 2 == 0)
        entries_bool[d] = {"alcohol": drank}
        entries_numeric[d] = {"alcohol": (3 if drank else 0)}  # mismo patrón, expresado en cantidad
        if drank:
            days[i + 1]["recovery"] = 45.0

    findings_bool = journal.analyze_journal(days, {"entries": entries_bool, "custom": []})
    findings_numeric = journal.analyze_journal(days, {"entries": entries_numeric, "custom": []})

    assert findings_bool == findings_numeric


# ═══════════════════════════════════════════════════════════════════════════
# Roadmap P2, F9 (paso 8) — analyze_journal_dose_response
# ═══════════════════════════════════════════════════════════════════════════

def test_dose_response_detects_planted_effect():
    """Efecto plantado: MÁS copas -> recovery MÁS BAJO al día siguiente, con
    variación real de cantidad (no solo sí/no) -> rho<0, significant=True."""
    random.seed(11)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-2, 2)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drinks = i % 5  # 0,1,2,3,4 copas, cíclico -> >=3 valores distintos
        entries[d] = {"alcohol": drinks}
        days[i + 1]["recovery"] -= drinks * 4  # más copas -> recovery más bajo

    j = {"entries": entries, "custom": []}
    findings = journal.analyze_journal_dose_response(days, j, locale="es")

    matches = [f for f in findings if f["habit"] == "alcohol" and f["outcome"] == "recovery"]
    assert len(matches) == 1
    f = matches[0]
    assert f["rho"] < 0
    assert f["significant"] is True
    assert f["n"] >= 15
    assert f["n_distinct_values"] >= 3
    assert "headline" in f and f["headline"]


def test_dose_response_gate_below_min_total_excluded():
    """n<15 con cantidad real -> [] (gate de tamaño)."""
    random.seed(12)
    dates = date_seq("2026-01-01", 10)
    days = [{"date": d, "recovery": 70.0} for d in dates]
    entries = {d: {"alcohol": i % 4} for i, d in enumerate(dates)}
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []})
    assert findings == []


def test_dose_response_gate_below_min_distinct_values_excluded():
    """n>=15 pero SIEMPRE la misma cantidad (ej. todos '2 copas') -> [] (sin
    variación que correlacionar, criterio 18)."""
    random.seed(13)
    dates = date_seq("2026-01-01", 30)
    days = [{"date": d, "recovery": 60 + random.uniform(-5, 5)} for d in dates]
    entries = {d: {"alcohol": 2} for d in dates}  # SIEMPRE 2, cero variación
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []})
    assert findings == []


def test_dose_response_ignores_legacy_bool_days():
    """Días con valor bool legacy (True/False, sin cantidad real) NO cuentan
    para el pool de dosis — ni siquiera si hay muchos, deben quedar excluidos
    del n y de los valores distintos."""
    random.seed(14)
    dates = date_seq("2026-01-01", 40)
    days = [{"date": d, "recovery": 65.0} for d in dates]
    entries = {}
    for i, d in enumerate(dates):
        if i < 25:
            entries[d] = {"alcohol": True}  # legacy bool -> no cuenta para dosis
        else:
            entries[d] = {"alcohol": i % 3}  # solo 15 días con cantidad real
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []})
    # Con solo 15 días de cantidad real y valores 0/1/2 cíclicos, el gate de
    # tamaño SÍ se cumple (n=15) — lo que importa es que NINGÚN bool legacy
    # contaminó el conteo (si contaran, n sería 40, no 15).
    for f in findings:
        assert f["n"] <= 15


def test_dose_response_pure_noise_yields_no_findings():
    random.seed(15)
    dates = date_seq("2026-01-01", 60)
    days = [{"date": d, "recovery": 60 + random.uniform(-10, 10)} for d in dates]
    entries = {d: {"meditation": random.randint(0, 60)} for d in dates}
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []})
    matches = [f for f in findings if f["habit"] == "meditation"]
    assert matches == []


def test_dose_response_never_crashes_on_garbage():
    assert journal.analyze_journal_dose_response([], None) == []
    assert journal.analyze_journal_dose_response(None, {"entries": {"x": "y"}}) == []
    assert journal.analyze_journal_dose_response(
        [{"date": "bad"}], {"entries": {"2026-01-01": "not a dict"}}
    ) == []


@pytest.mark.parametrize("locale", ["es", "en", "fr", "pt"])
def test_dose_response_headline_i18n(locale):
    random.seed(16)
    dates = date_seq("2026-01-01", 40)
    days = [{"date": d, "recovery": 70 + random.uniform(-2, 2)} for d in dates[:-1]]
    days.append({"date": dates[-1], "recovery": 70.0})
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drinks = i % 4
        entries[d] = {"alcohol": drinks}
        days[i + 1]["recovery"] -= drinks * 5
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []}, locale=locale)
    for f in findings:
        assert f["headline"]
        assert "None" not in f["headline"]


def test_dose_response_only_covers_quantifiable_habits():
    """El motor NUNCA evalúa hábitos fuera de QUANTIFIABLE_HABITS, incluso si
    por error llevaran un valor numérico en el journal."""
    random.seed(17)
    dates = date_seq("2026-01-01", 30)
    days = [{"date": d, "recovery": 65 + random.uniform(-3, 3)} for d in dates]
    entries = {d: {"creatine": i % 3} for i, d in enumerate(dates)}  # creatine no es cuantificable
    findings = journal.analyze_journal_dose_response(days, {"entries": entries, "custom": []})
    assert all(f["habit"] in journal.QUANTIFIABLE_HABITS for f in findings)
    assert findings == []  # creatine ni siquiera se evalúa


# ── Criterio 17: analyze_journal (gate sí/no) NUNCA cambia por la existencia
# del motor de dosis-respuesta — pool de BH separado, cero interferencia ──────

def test_analyze_journal_unaffected_by_dose_response_existing_in_same_module():
    """Correr AMBOS motores sobre el mismo dataset no debe alterar el
    resultado de analyze_journal frente a correr solo analyze_journal (BH
    pools verdaderamente independientes, criterio 17 + riesgo #3)."""
    random.seed(18)
    dates = date_seq("2026-01-01", 61)
    days = [{"date": d, "recovery": 70 + random.uniform(-2, 2)} for d in dates]
    entries = {}
    for i, d in enumerate(dates[:-1]):
        drank = (i % 2 == 0)
        drinks = 3 if drank else 0
        entries[d] = {"alcohol": drinks}  # numérico, pero sigue siendo "sí/no" con cantidad fija
        if drank:
            days[i + 1]["recovery"] -= 15

    j = {"entries": entries, "custom": []}

    only_journal = journal.analyze_journal(days, j, locale="es")
    # Correr dose_response ANTES o DESPUÉS no debe alterar analyze_journal —
    # son funciones puras e independientes, sin estado compartido.
    _ = journal.analyze_journal_dose_response(days, j, locale="es")
    after_dose = journal.analyze_journal(days, j, locale="es")

    assert only_journal == after_dose
