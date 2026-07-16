"""
conftest.py — fixtures globales de la suite de tests.

Fase 8D (paso D3, household): salvaguarda de aislamiento de datos.

`main.py::on_startup()` ahora corre `userctx.migrate_legacy_layout_if_needed()`
en CADA arranque de la app FastAPI — incluyendo cualquier test que instancie
`TestClient(main.app)`. Antes de esta fase, un test que olvidara monkeypatchear
`settings.DATA_DIR`/las rutas legacy de un módulo era inofensivo (a lo sumo leía
el `data/` real sin escribir). Con la migración automática en startup, ese
mismo descuido puede MOVER los archivos reales del usuario a `data/users/default/`
— exactamente el riesgo #1 del roadmap de Fase D ("puede romper CUALQUIER path
de datos"). Ocurrió una vez durante el desarrollo de este mismo paso (D3) y se
recuperó desde el backup `data.bak-fase8d/` — este fixture es la prevención
para que no vuelva a pasar, en este desarrollo ni en el futuro.

`_isolate_userctx_data_dir` (autouse, todos los tests): apunta
`userctx._DATA_DIR` a un `tmp_path` fresco ANTES de cada test y lo revierte
después. Esto es puramente ADITIVO a las rutas legacy que cada test ya
monkeypatchea (_PROFILE_FILE, DATA_OUT, settings.DATA_DIR, etc.) — un test que
monkeypatchea esas rutas explícitamente sigue funcionando exactamente igual
(userctx.should_use_household_paths() sigue siendo False ahí porque el
tmp_path aislado de userctx nunca tiene data/users/, salvo en
tests/test_userctx.py y tests/test_household.py que lo hacen a propósito
sobre su PROPIO tmp_path). Lo único que cambia es que, si un test se olvida de
aislar rutas y dispara on_startup() vía TestClient, la migración automática
opera sobre un tmp_path descartable en vez del data/ real del repo.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_userctx_data_dir(tmp_path, monkeypatch):
    """Autouse: userctx._DATA_DIR apunta a un tmp_path fresco en CADA test,
    salvo que el test mismo lo sobrescriba explícitamente después (los tests
    de test_userctx.py/test_household.py hacen su propio monkeypatch, que
    gana por ser posterior en el setup del test)."""
    from app import userctx as _userctx
    isolated_dir = tmp_path / "_conftest_userctx_isolation"
    monkeypatch.setattr(_userctx, "_DATA_DIR", isolated_dir)
    yield


@pytest.fixture(autouse=True)
def _isolate_illness_state_data_dir(tmp_path, monkeypatch):
    """Autouse (dev-harness/illness-latch): illness_state._DATA_DIR/_LATCH_FILE
    apuntan a un tmp_path fresco en CADA test.

    Mismo riesgo que _isolate_userctx_data_dir de arriba, pero disparado por
    request normal en vez de startup: main.py::/ y app/routes/insights.py::
    GET /api/insights ahora llaman evaluate(..., latch=True) SIEMPRE (paso 3
    del roadmap illness-latch), y app/illness_state.py captura _DATA_DIR de
    settings.DATA_DIR al importar (mismo patrón que app/coach_store.py) — sin
    este aislamiento, CUALQUIER test que instancie TestClient(main.app) y
    pegue GET / o GET /api/insights escribiría illness_latch.json en el
    data/ real del repo, incluso tests que no saben que este módulo existe.
    Salvo que el test mismo lo sobrescriba explícitamente después (mismo
    criterio: gana por ser posterior en el setup)."""
    from app import illness_state as _illness_state
    isolated_dir = tmp_path / "_conftest_illness_state_isolation"
    monkeypatch.setattr(_illness_state, "_DATA_DIR", isolated_dir)
    monkeypatch.setattr(_illness_state, "_LATCH_FILE", isolated_dir / "illness_latch.json")
    yield
