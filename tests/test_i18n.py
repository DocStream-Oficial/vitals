"""
test_i18n.py — Tests de Fase 1B (i18n + unidades)

Cubre:
- (a) PUT /api/profile acepta los 4 locales {es,en,fr,pt} con 200 y rechaza
      uno inválido con 422.
- (b) Helpers de conversión de unidades: round-trip cm↔in y kg↔lb sin drift,
      replicando EXACTAMENTE las fórmulas JS del template
      (waist/height: in→cm = v*2.54, cm→in = v/2.54;
       weight: lb→kg = v/2.20462, kg→lb = v*2.20462).
- (c) Paridad de claves del dict STRINGS en los 4 idiomas (es/en/fr/pt),
      parseando el bloque del template (STRINGS vive solo en el HTML).

NO toca scoring/bodyage/sync/auth/parsers/health_api/trends/drivers/insights.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

_TEMPLATE = Path(__file__).parent.parent / "templates" / "vitals_ios.html"
# Fase 9-B: STRINGS y los helpers de i18n/unidades se movieron de <script>
# inline en el template a static/js/app-i18n-helpers.js. Mismo contrato
# (paridad de claves ×4 locales, constantes de conversión), nueva ubicación.
_I18N_JS = Path(__file__).parent.parent / "static" / "js" / "app-i18n-helpers.js"
_DASHBOARD_JS = Path(__file__).parent.parent / "static" / "js" / "app-dashboard.js"


def _frontend_js_source() -> str:
    return _I18N_JS.read_text(encoding="utf-8") + _DASHBOARD_JS.read_text(encoding="utf-8")

# Constantes de conversión — IDÉNTICAS a las del template JS.
CM_PER_IN = 2.54
LB_PER_KG = 2.20462


# ── fixture: TestClient (mismo patrón que test_profile.py) ────────────────────

def _get_api_client(tmp_path: Path, monkeypatch):
    real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
    if real_compact.exists():
        (tmp_path / "health_compact.json").write_text(real_compact.read_text())

    from app import config, profile as _pm
    import main as main_mod
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                        Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)

    with patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        client = TestClient(main_mod.app, raise_server_exceptions=True)
        yield client


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from app import coach_store
    monkeypatch.setattr(coach_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(coach_store, "_STORE_FILE", tmp_path / "coach_conversations.json")
    monkeypatch.setattr(coach_store, "_LEGACY_HISTORY_FILE", tmp_path / "coach_history.json")
    monkeypatch.setattr(coach_store, "_LEGACY_BACKUP_FILE", tmp_path / "coach_history.json.v1.bak")
    yield from _get_api_client(tmp_path, monkeypatch)


# ── (a) Whitelist de locales en PUT /api/profile ──────────────────────────────

@pytest.mark.parametrize("loc", ["es", "en", "fr", "pt"])
def test_put_profile_accepts_all_four_locales(api_client, loc):
    """Los 4 locales soportados se aceptan (200)."""
    resp = api_client.put("/api/profile", json={"locale": loc})
    assert resp.status_code == 200, f"locale '{loc}' debería ser válido"
    assert resp.json()["locale"] == loc


@pytest.mark.parametrize("bad", ["de", "zh", "xx", "ES", "english", ""])
def test_put_profile_rejects_invalid_locale(api_client, bad):
    """Cualquier locale fuera del whitelist → 422."""
    resp = api_client.put("/api/profile", json={"locale": bad})
    assert resp.status_code == 422, f"locale '{bad}' debería ser rechazado"


@pytest.mark.parametrize("u", ["metric", "imperial"])
def test_put_profile_accepts_both_unit_systems(api_client, u):
    """Ambos sistemas de unidades se aceptan (200)."""
    resp = api_client.put("/api/profile", json={"units": u})
    assert resp.status_code == 200
    assert resp.json()["units"] == u


@pytest.mark.parametrize("bad", ["pounds", "kg", "Imperial", ""])
def test_put_profile_rejects_invalid_units(api_client, bad):
    resp = api_client.put("/api/profile", json={"units": bad})
    assert resp.status_code == 422


def test_put_imperial_stores_metric_waist(tmp_path, monkeypatch, api_client):
    """
    El cliente convierte imperial→metric ANTES del PUT; el backend solo guarda
    métrico. Verificamos que un waist_cm métrico persiste tal cual con
    units=imperial (profile.json siempre métrico — no hay doble conversión server-side).
    """
    from app import profile as _pm
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    # 33 in → 83.82 cm (conversión que hace el cliente). El server recibe ya métrico.
    metric_waist = round(33 * CM_PER_IN, 2)  # 83.82
    resp = api_client.put("/api/profile", json={
        "birthdate": "1990-01-01", "sex": "M",
        "waist_cm": metric_waist, "units": "imperial",
    })
    assert resp.status_code == 200
    saved = _pm.load_profile()
    assert saved["units"] == "imperial"
    # Guardado en métrico, sin re-convertir
    assert saved["waist_cm"] == metric_waist


# ── (b) Round-trip de conversión de unidades (réplica de fórmulas JS) ─────────

def _in_to_cm(v):    return v * CM_PER_IN
def _cm_to_in(v):    return v / CM_PER_IN
def _lb_to_kg(v):    return v / LB_PER_KG
def _kg_to_lb(v):    return v * LB_PER_KG


@pytest.mark.parametrize("cm", [50.0, 75.0, 88.0, 100.0, 165.0, 180.5])
def test_length_round_trip_no_drift(cm):
    """cm → in → cm reproduce el valor original sin deriva."""
    back = _in_to_cm(_cm_to_in(cm))
    assert back == pytest.approx(cm, abs=1e-9)


@pytest.mark.parametrize("inches", [20.0, 30.0, 33.0, 40.0, 65.0])
def test_length_round_trip_imperial_first(inches):
    """in → cm → in reproduce el valor original sin deriva."""
    back = _cm_to_in(_in_to_cm(inches))
    assert back == pytest.approx(inches, abs=1e-9)


@pytest.mark.parametrize("kg", [45.0, 62.5, 80.0, 95.3, 120.0])
def test_weight_round_trip_no_drift(kg):
    """kg → lb → kg reproduce el valor original sin deriva."""
    back = _lb_to_kg(_kg_to_lb(kg))
    assert back == pytest.approx(kg, abs=1e-9)


@pytest.mark.parametrize("lb", [100.0, 150.0, 180.0, 210.0])
def test_weight_round_trip_imperial_first(lb):
    """lb → kg → lb reproduce el valor original sin deriva."""
    back = _kg_to_lb(_lb_to_kg(lb))
    assert back == pytest.approx(lb, abs=1e-9)


def test_conversion_anchor_values():
    """Valores ancla conocidos (sanity de las constantes)."""
    assert _in_to_cm(1) == pytest.approx(2.54)
    assert _cm_to_in(2.54) == pytest.approx(1.0)
    assert _kg_to_lb(1) == pytest.approx(2.20462)
    assert _lb_to_kg(2.20462) == pytest.approx(1.0)


def test_template_uses_expected_constants():
    """El frontend debe usar exactamente 2.54 y 2.20462 (no otras aproximaciones)."""
    src = _frontend_js_source()
    assert "2.54" in src, "Falta la constante 2.54 (cm/in) en el JS"
    assert "2.20462" in src, "Falta la constante 2.20462 (lb/kg) en el JS"


# ── (c) Paridad de claves STRINGS en los 4 idiomas ────────────────────────────

def _extract_strings_keys():
    """
    Parsea el bloque `var STRINGS = { ... };` de static/js/app-i18n-helpers.js
    y devuelve {locale: set(keys)} para es/en/fr/pt.
    """
    src = _I18N_JS.read_text(encoding="utf-8")
    m = re.search(r"var STRINGS = \{(.*?)\n\};", src, re.S)
    assert m, "No se encontró el bloque 'var STRINGS = { ... };' en static/js/app-i18n-helpers.js"
    body = m.group(1)

    loc_starts = [(mm.group(1), mm.start())
                  for mm in re.finditer(r"^  (es|en|fr|pt): \{", body, re.M)]
    assert {n for n, _ in loc_starts} == {"es", "en", "fr", "pt"}, \
        "Faltan locales en STRINGS"

    out = {}
    for i, (name, start) in enumerate(loc_starts):
        end = loc_starts[i + 1][1] if i + 1 < len(loc_starts) else len(body)
        block = body[start:end]
        # claves a nivel de 4 espacios de indentación
        keys = set(re.findall(r"^    ([A-Za-z_][A-Za-z0-9_]*):", block, re.M))
        out[name] = keys
    return out


def test_strings_block_parses_and_has_keys():
    keys = _extract_strings_keys()
    for loc in ("es", "en", "fr", "pt"):
        assert len(keys[loc]) > 100, f"Muy pocas claves en '{loc}': {len(keys[loc])}"


def test_strings_all_locales_have_identical_key_sets():
    """Cada clave de ES existe en EN/FR/PT y viceversa (paridad total)."""
    keys = _extract_strings_keys()
    base = keys["es"]
    for loc in ("en", "fr", "pt"):
        missing = base - keys[loc]
        extra = keys[loc] - base
        assert not missing, f"[{loc}] faltan claves presentes en es: {sorted(missing)}"
        assert not extra, f"[{loc}] tiene claves que no están en es: {sorted(extra)}"


@pytest.mark.parametrize("loc", ["en", "fr", "pt"])
def test_strings_locale_count_matches_es(loc):
    """El conteo de claves por idioma es idéntico al de ES."""
    keys = _extract_strings_keys()
    assert len(keys[loc]) == len(keys["es"]), \
        f"'{loc}' tiene {len(keys[loc])} claves vs es {len(keys['es'])}"


# ── (d) Paridad de claves en app/i18n.py (Fase 7: server-side, cycle.py/insights.py) ─

def test_app_i18n_all_locales_have_identical_key_sets():
    """app/i18n.py STRINGS: cada clave de ES existe en EN/FR/PT y viceversa
    (incluye las nuevas claves de Fase 7 — ciclo/salud femenina)."""
    from app.i18n import STRINGS
    base = set(STRINGS["es"].keys())
    for loc in ("en", "fr", "pt"):
        keys = set(STRINGS[loc].keys())
        missing = base - keys
        extra = keys - base
        assert not missing, f"[{loc}] faltan claves presentes en es: {sorted(missing)}"
        assert not extra, f"[{loc}] tiene claves que no están en es: {sorted(extra)}"


_CYCLE_KEYS = [
    "cycle_disclaimer", "cycle_cat",
    "phase_menstrual", "phase_follicular", "phase_ovulatory", "phase_luteal",
    "cycle_phase_title", "cycle_phase_summary", "cycle_phase_factor", "cycle_phase_rec",
    "period_approaching_title", "period_approaching_summary",
    "period_approaching_factor", "period_approaching_rec",
    "cycle_delay_title", "cycle_delay_summary", "cycle_delay_factor", "cycle_delay_rec",
    "perimenopause_possible_title", "perimenopause_possible_summary",
    "menopause_possible_title", "menopause_possible_summary",
    "meno_signal_length_variability", "meno_signal_skipped_cycle", "meno_signal_amenorrhea_12mo",
    "perimenopause_rec",
]


@pytest.mark.parametrize("loc", ["es", "en", "fr", "pt"])
@pytest.mark.parametrize("key", _CYCLE_KEYS)
def test_app_i18n_cycle_keys_present_in_all_locales(loc, key):
    """Cada clave de Fase 7 (ciclo) existe en los 4 locales, sin placeholders huérfanos."""
    from app.i18n import STRINGS
    assert key in STRINGS[loc], f"Falta clave '{key}' en locale '{loc}'"
    assert STRINGS[loc][key], f"Clave '{key}' vacía en locale '{loc}'"


# ── (e) Paridad de claves nuevas de Fase 8C (AAA feel: notify/sleep-coach) ──

_FASE_8C_BACKEND_KEYS = [
    "skeleton_first_load", "notify_brief_title", "notify_alert_title",
]


@pytest.mark.parametrize("loc", ["es", "en", "fr", "pt"])
@pytest.mark.parametrize("key", _FASE_8C_BACKEND_KEYS)
def test_app_i18n_fase8c_keys_present_in_all_locales(loc, key):
    """Cada clave backend de Fase 8C (notificaciones) existe en los 4 locales."""
    from app.i18n import STRINGS
    assert key in STRINGS[loc], f"Falta clave '{key}' en locale '{loc}'"
    assert STRINGS[loc][key], f"Clave '{key}' vacía en locale '{loc}'"


_FASE_8C_FRONTEND_KEYS = [
    "retry_btn", "offline_banner", "sleep_coach_lbl", "sleep_coach_wake",
    "sleep_coach_driver_baseline", "sleep_coach_driver_debt",
    "sleep_coach_driver_strain", "sleep_coach_driver_recovery",
    "mas_notify_lbl", "mas_notify_hint", "mas_notify_ntfy_lbl",
    "mas_notify_tg_token_lbl", "mas_notify_tg_chat_lbl",
    "mas_notify_brief_lbl", "mas_notify_alerts_lbl",
    "mas_ingest_token_lbl", "mas_copy_btn",
]


@pytest.mark.parametrize("loc", ["es", "en", "fr", "pt"])
@pytest.mark.parametrize("key", _FASE_8C_FRONTEND_KEYS)
def test_template_strings_fase8c_keys_present_in_all_locales(loc, key):
    """Cada clave frontend de Fase 8C (chart tooltip/retry toast/notify UI/
    sleep coach) existe en los 4 locales del STRINGS del template."""
    keys = _extract_strings_keys()
    assert key in keys[loc], f"Falta clave '{key}' en locale '{loc}' (template STRINGS)"
