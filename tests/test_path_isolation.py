"""
test_path_isolation.py — deuda R2 (aislamiento definitivo de rutas de data).

Cubre el criterio 2 del roadmap ROADMAP.md (vitals-test-isolation): las 4
constantes legacy (sync.DATA_OUT, auth.TOKEN_PATH, coach_headline._CACHE_PATH,
report._CACHE_PATH) ahora son sentinels (`None` en reposo) que sus accessors
resuelven en RUNTIME contra settings.DATA_DIR. Antes de este trabajo, las
constantes se calculaban a IMPORT-TIME (`settings.DATA_DIR / "..."`); un
`importlib.reload(modulo)` recalculaba la expresión con el settings.DATA_DIR
vigente EN ESE MOMENTO, y si ese reload ocurría después de que un test ya
había restaurado settings.DATA_DIR al valor real (o en un proceso nuevo sin
ningún monkeypatch activo todavía), la constante quedaba re-ligada a la ruta
REAL de data/ — la causa raíz del incidente R2.

Cada test aquí: (a) patchea settings.DATA_DIR a un tmp_path, (b) hace
importlib.reload() del módulo (simulando el escenario de riesgo), (c) verifica
que el accessor sigue devolviendo una ruta bajo el tmp_path patcheado — NUNCA
una ruta congelada de antes del reload.
"""
from __future__ import annotations

import importlib

import pytest


def test_sync_data_out_survives_reload(tmp_path, monkeypatch):
    """Paso 1 — app.sync: tras patchear settings.DATA_DIR y recargar el
    módulo, _data_out_path() debe caer bajo el tmp_path (no bajo el data/
    real congelado en un import previo)."""
    from app import config
    import app.sync as sync_mod

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    importlib.reload(sync_mod)
    try:
        resolved = sync_mod._data_out_path()
        assert resolved == tmp_path / "health_compact.json"
        assert resolved.is_relative_to(tmp_path)
        # El sentinel en reposo es None (no una ruta congelada) tras el reload.
        assert sync_mod.DATA_OUT is None
    finally:
        importlib.reload(sync_mod)  # higiene entre tests


def test_auth_token_path_survives_reload(tmp_path, monkeypatch):
    """Paso 2 — app.auth: NO usa importlib.reload(auth_mod) a propósito
    (desviación documentada, ver IMPL-REPORT.md) — app.auth define las
    clases TokenExpired/NoToken que main.py importa UNA VEZ a import-time
    (`from app.auth import TokenExpired, NoToken`) y usa en sus `except`.
    Un reload real de app.auth crea clases NUEVAS con distinta identidad;
    cualquier excepción lanzada después (incluso por un mock que hace
    `from app.auth import NoToken` fresco) deja de hacer match con el
    `except NoToken`/`except TokenExpired` de main.py, degradando a
    'status: error' genérico — se confirmó reproduciendo el fallo real en
    tests/test_endpoints.py::test_api_sync_no_token_controlled /
    test_api_sync_expired_token_controlled con pytest-randomly (seed
    1487098238) durante el desarrollo de este mismo roadmap. En vez de
    reload, simulamos el estado post-reload (sentinel en None) sin volver
    a crear el módulo: mismo comportamiento del accessor, cero riesgo de
    contaminar la identidad de clases para el resto de la suite."""
    from app import config
    import app.auth as auth_mod

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(auth_mod, "TOKEN_PATH", None)  # estado "en reposo" post-reload
    resolved = auth_mod._token_path()
    assert resolved == tmp_path / "token.json"
    assert resolved.is_relative_to(tmp_path)
    assert auth_mod.TOKEN_PATH is None


def test_coach_headline_cache_path_survives_reload(tmp_path, monkeypatch):
    """Paso 3 — app.coach_headline: mismo patrón para _cache_path()."""
    from app import config
    import app.coach_headline as headline_mod

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    importlib.reload(headline_mod)
    try:
        resolved = headline_mod._cache_path()
        assert resolved == tmp_path / "coach_headline.json"
        assert resolved.is_relative_to(tmp_path)
        assert headline_mod._CACHE_PATH is None
    finally:
        importlib.reload(headline_mod)


def test_report_cache_path_survives_reload(tmp_path, monkeypatch):
    """Paso 3 — app.report: mismo patrón para _cache_path()."""
    from app import config
    import app.report as report_mod

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    importlib.reload(report_mod)
    try:
        resolved = report_mod._cache_path()
        assert resolved == tmp_path / "reports.json"
        assert resolved.is_relative_to(tmp_path)
        assert report_mod._CACHE_PATH is None
    finally:
        importlib.reload(report_mod)


@pytest.mark.parametrize("module_name,attr_name,filename", [
    ("app.sync", "DATA_OUT", "health_compact.json"),
    ("app.auth", "TOKEN_PATH", "token.json"),
    ("app.coach_headline", "_CACHE_PATH", "coach_headline.json"),
    ("app.report", "_CACHE_PATH", "reports.json"),
])
def test_sentinel_override_still_wins(tmp_path, monkeypatch, module_name, attr_name, filename):
    """Criterio 5 — compat: patch.object(modulo, ATTR, ruta) (el patrón que ya
    usan los tests preexistentes) sigue ganando sobre la resolución runtime,
    SIN modificar esos tests."""
    mod = importlib.import_module(module_name)
    override = tmp_path / "override" / filename
    monkeypatch.setattr(mod, attr_name, override)

    accessor_by_module = {
        "app.sync": "_data_out_path",
        "app.auth": "_token_path",
        "app.coach_headline": "_cache_path",
        "app.report": "_cache_path",
    }
    accessor = getattr(mod, accessor_by_module[module_name])
    assert accessor() == override


def test_main_data_path_resolves_at_runtime(tmp_path, monkeypatch):
    """Cierre del punto escalado por el validador de vitals-test-isolation:
    main.DATA_PATH adopta el MISMO patrón sentinel que los otros 4. NO se
    recarga main a propósito (recrearía la app FastAPI y rompería identidad
    de clases/rutas registradas, mismo motivo que app.auth); se verifica el
    estado de reposo real del módulo (sentinel None) y que el accessor
    resuelve contra settings.DATA_DIR EN RUNTIME."""
    from app import config
    import main as main_mod

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    # Estado de reposo real: el sentinel es None (ninguna ruta congelada).
    assert main_mod.DATA_PATH is None
    assert main_mod._data_path() == tmp_path / "health_compact.json"
    # El override explícito de un test (patch.object) sigue mandando.
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "override.json")
    assert main_mod._data_path() == tmp_path / "override.json"
