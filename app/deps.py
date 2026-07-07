"""
app/deps.py — pegamento compartido entre routers (Fase 9, paso A1).

Centraliza los helpers que ANTES vivían sueltos en main.py y que son usados
por múltiples rutas: `_active_source`, `_data_path`, `_load_dataset`,
`_load_demo_dataset`, `_demo_blocked_response`. Se mueven aquí TAL CUAL (mismo
cuerpo, misma firma, mismos nombres) — este es un refactor estructural, no una
reescritura (ver ROADMAP-vitals-fase9-desmonolitizar.md).

`main.py` sigue exponiendo `DATA_PATH` (el sentinel de override SOLO para
tests) como atributo de MÓDULO propio — decenas de tests parchean
`main_mod.DATA_PATH` por nombre (`patch.object(main_mod, "DATA_PATH", ...)` /
`monkeypatch.setattr(main_mod, "DATA_PATH", ...)`). Para que ese patch siga
siendo observado, `_data_path()` (aquí) importa `main` DIFERIDO (dentro de la
función, no al tope del módulo — evita el import circular main<->deps) y lee
`main.DATA_PATH` dinámicamente en cada llamada, en vez de capturar una copia
local en import time.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi.responses import JSONResponse

from app.config import settings
from app import userctx as _userctx
from app import profile as _profile
from app.sources import get_source

logger = logging.getLogger("vitals.main")


def _active_source():
    """La fuente de datos activa según el perfil (default google_health). Fase 5A."""
    return get_source(_profile.effective("source") or "google_health")


def _data_path() -> Path:
    """Ruta a health_compact.json del usuario ACTIVO del request (Fase 8D,
    paso D3: household). El middleware de userctx (ver abajo) fija el
    contextvar en TODO request real, pero should_use_household_paths()
    exige ADEMÁS que exista data/users/ (instancia ya migrada o con ≥1
    usuario creado) — instalaciones/tests sin household resuelven contra
    settings.DATA_DIR EN RUNTIME (patrón sentinel, mismo diseño que
    sync.DATA_OUT/auth.TOKEN_PATH): un reload no puede dejar esta ruta
    apuntando a data real. Nunca lanza."""
    try:
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "health_compact.json"
    except Exception:
        pass
    import main as _main  # deferred: evita import circular main <-> deps
    if _main.DATA_PATH is not None:  # override explícito de un test
        return _main.DATA_PATH
    return settings.DATA_DIR / "health_compact.json"


_DEMO_DATASET_FILE = settings.ROOT_DIR / "tests" / "fixtures" / "demo_dataset.json"
_demo_dataset_cache: dict | None = None


def _load_demo_dataset() -> dict | None:
    """Fase 8A (paso A1): dataset 100% sintético servido cuando VITALS_DEMO=1
    (ver scripts/gen_demo_data.py). Cacheado en memoria del proceso — se lee
    UNA vez de tests/fixtures/demo_dataset.json (nunca de data/ real). Si el
    fixture no existe (repo sin correr gen_demo_data.py todavía) degrada a
    None -> el dashboard cae al shimmer de "sin datos", nunca 500."""
    global _demo_dataset_cache
    if _demo_dataset_cache is not None:
        return _demo_dataset_cache
    try:
        text = _DEMO_DATASET_FILE.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            _demo_dataset_cache = data
            return data
    except Exception as exc:
        logger.warning("No pude cargar el dataset demo (%s): %s", _DEMO_DATASET_FILE, exc)
    return None


def _load_dataset() -> dict | None:
    if settings.VITALS_DEMO:
        return _load_demo_dataset()
    path = _data_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# Fase 9 (paso A2): _KNOWN_SOURCES se descubrió compartido entre 2 dominios de
# ruta (profile: validación de /api/profile, sources: /api/sources*) al
# trocear main.py por routers — se centraliza aquí, mismo motivo que
# _clean_str_list arriba.
_KNOWN_SOURCES = ("google_health", "oura", "whoop", "healthkit")


def _demo_blocked_response() -> JSONResponse:
    """Respuesta uniforme (200, nunca 500/401) para cualquier endpoint de
    escritura sensible (auth/sync/ingest/sources) cuando VITALS_DEMO=1 — el
    roadmap Fase 8A exige que estos devuelvan 200 con nota 'demo mode' sin
    efectos, para que un frontend que no conoce el flag no truene con un
    error inesperado al probar el botón de sync/conectar en la demo pública."""
    return JSONResponse(
        {"status": "demo", "message": "Demo mode: esta acción está deshabilitada. Los datos son sintéticos."},
        status_code=200,
    )


# Fase 9 (paso A2): _clean_str_list se descubrió compartido entre 2 dominios de
# ruta (cycle: /api/cycle/symptom, profile: /api/profile) al trocear main.py
# por routers — se centraliza aquí (mismo motivo que el resto de este archivo:
# pegamento compartido entre >1 router) en vez de duplicarlo o crear un import
# circular entre app/routes/cycle.py y app/routes/profile.py.
_CLINICAL_FIELDS = ("goals", "injuries", "conditions", "medications")
_CLINICAL_MAX_ITEMS = 10
_CLINICAL_MAX_LEN = 120


def _clean_str_list(v) -> list[str]:
    """Valida y normaliza una lista de strings del intake clínico (goals/injuries/
    conditions/medications). Acepta SOLO una lista de strings: trimea cada item,
    filtra vacíos, corta a _CLINICAL_MAX_ITEMS items de máx _CLINICAL_MAX_LEN chars.

    Cualquier otra cosa (no-lista, o lista con items no-string) → ValueError con
    mensaje controlado, para que el caller lo capture y devuelva 422 (nunca 500).
    """
    if not isinstance(v, list):
        raise ValueError("debe ser una lista de strings")
    out = []
    for item in v:
        if not isinstance(item, str):
            raise ValueError("cada elemento debe ser texto")
        s = item.strip()
        if not s:
            continue
        out.append(s[:_CLINICAL_MAX_LEN])
        if len(out) >= _CLINICAL_MAX_ITEMS:
            break
    return out
