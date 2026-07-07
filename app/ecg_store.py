"""
app/ecg_store.py — Storage AISLADO de lecturas de ECG (HKElectrocardiogram).

El ECG es un VISOR independiente, NO una métrica del motor: es un evento episódico
con forma de onda (~15k puntos de voltaje), no un escalar diario. Este módulo es la
ÚNICA pieza de código que lee/escribe `data/ecg/`. A propósito NO importa ni es
importado por scoring.py / bodyage.py / merge.py / build_dataset / coach_chat.py /
mcp_tools.py — los voltajes JAMÁS deben llegar a health_compact.json ni al contexto
del coach. Ver ROADMAP-vitals-ecg.md.

Layout en disco (una lectura = dos archivos, para que el listado sea barato):
    data/ecg/<uuid>.json           — META (sin voltajes): uuid, date, classification,
                                      avg_hr, sampling_frequency, sample_count,
                                      symptoms_status.
    data/ecg/<uuid>.voltages.json  — SOLO la onda: {"voltages": [float, ...]}.

Decisión documentada (roadmap dejaba la separación meta/onda a criterio del
implementador): se separan en dos archivos en vez de un único JSON con `voltages`
adentro, para que `list_ecg()` (usado por GET /api/ecg) pueda leer únicamente los
archivos `.json` de meta (típicamente <1 KB) sin tocar los `.voltages.json` (~150-
200 KB cada uno) — evita cargar y descartar ~15k floats por lectura solo para
listar. `get_ecg(uuid)` sí lee ambos para el render completo.

Escritura SIEMPRE atómica vía app.fsutil.atomic_write_text (.tmp + os.replace).
Idempotente por UUID: reingresar el mismo uuid sobreescribe ambos archivos, nunca
duplica ni acumula.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.fsutil import atomic_write_text

logger = logging.getLogger("vitals.ecg_store")

# UUID de HealthKit: típicamente formato UUID estándar, pero no forzamos el regex
# exacto de RFC 4122 (Apple no lo garantiza por escrito) — solo caracteres seguros
# para nombre de archivo, para evitar path traversal (../../etc/passwd) o nombres
# que rompan el filesystem.
_SAFE_UUID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

# Claves de META permitidas (todo lo demás del payload se descarta silenciosamente,
# 'voltages' se separa aparte — nunca queda en el archivo de meta).
_META_KEYS = (
    "uuid", "date", "classification", "avg_hr", "sampling_frequency",
    "sample_count", "symptoms_status",
)


def _ecg_dir() -> Path:
    """Carpeta ecg/ del usuario activo (Fase 8D, paso D3: household). Fuera de
    un request household-aware (is_context_active()=False — tests
    preexistentes que hacen patch.object(settings, "DATA_DIR", tmp_path),
    scripts), usa settings.DATA_DIR/ecg tal cual: comportamiento idéntico a
    antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            d = _userctx.current_data_dir() / "ecg"
            d.mkdir(parents=True, exist_ok=True)
            return d
    except Exception:
        pass
    d = settings.DATA_DIR / "ecg"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_safe_uuid(uuid: str) -> bool:
    return bool(uuid) and bool(_SAFE_UUID_RE.match(uuid))


def _meta_path(uuid: str) -> Path:
    return _ecg_dir() / f"{uuid}.json"


def _voltages_path(uuid: str) -> Path:
    return _ecg_dir() / f"{uuid}.voltages.json"


def _clean_voltages(raw: Any) -> list[float]:
    """Sanea la lista de voltajes: solo floats finitos (NaN/Inf/None -> 0.0), y
    cualquier cosa no-numérica se descarta. Nunca lanza — un payload roto produce
    una onda plana en vez de romper el render aguas abajo."""
    if not isinstance(raw, list):
        return []
    out: list[float] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            out.append(0.0)
            continue
        if math.isnan(f) or math.isinf(f):
            out.append(0.0)
        else:
            out.append(f)
    return out


def save_ecg(payload: dict) -> dict:
    """Guarda una lectura de ECG. Idempotente por `uuid` (sobreescribe, no duplica).
    Escritura atómica de los DOS archivos (meta + voltajes).

    Valida el shape mínimo: `uuid` (string no vacío, seguro para nombre de archivo)
    es obligatorio. Todo lo demás es best-effort / None-safe.

    Devuelve {"status": "ok", "uuid": ...} o {"status": "error", "message": ...}
    (nunca lanza — el caller en main.py decide el código HTTP).
    """
    if not isinstance(payload, dict):
        return {"status": "error", "message": "El payload debe ser un objeto JSON."}

    uuid = payload.get("uuid")
    if not isinstance(uuid, str) or not _is_safe_uuid(uuid):
        return {"status": "error", "message": "uuid inválido o ausente."}

    meta: dict[str, Any] = {k: payload.get(k) for k in _META_KEYS if k != "voltages"}
    meta["uuid"] = uuid

    voltages = _clean_voltages(payload.get("voltages"))

    try:
        atomic_write_text(_meta_path(uuid), json.dumps(meta, ensure_ascii=False))
        atomic_write_text(
            _voltages_path(uuid), json.dumps({"voltages": voltages}, ensure_ascii=False)
        )
    except Exception as e:
        logger.error(f"save_ecg falló para uuid={uuid}: {e}")
        return {"status": "error", "message": "No se pudo guardar el ECG."}

    return {"status": "ok", "uuid": uuid}


def list_ecg() -> list[dict]:
    """Lista LIGERA de todas las lecturas (sin voltajes), ordenada por fecha desc.
    Lee SOLO los archivos de meta — nunca abre `.voltages.json`. Meta corrupta o
    ilegible se omite en silencio (no tumba el listado completo por una lectura mala).
    Sin lecturas -> []."""
    out: list[dict] = []
    d = _ecg_dir()
    for p in d.glob("*.json"):
        if p.name.endswith(".voltages.json"):
            continue
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            logger.warning(f"Meta ECG corrupta, se omite: {p.name}")
            continue
        if not isinstance(meta, dict) or not meta.get("uuid"):
            continue
        out.append(meta)

    out.sort(key=lambda m: m.get("date") or "", reverse=True)
    return out


def get_ecg(uuid: str) -> Optional[dict]:
    """Meta + voltajes completos de una lectura, para el render de la tira.
    UUID inexistente o inválido -> None (el caller responde 404).
    Meta o voltajes corruptos -> None (no un dict a medias)."""
    if not _is_safe_uuid(uuid):
        return None

    meta_path = _meta_path(uuid)
    if not meta_path.exists():
        return None

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning(f"Meta ECG corrupta al leer uuid={uuid}")
        return None

    voltages: list[float] = []
    v_path = _voltages_path(uuid)
    if v_path.exists():
        try:
            v_data = json.loads(v_path.read_text(encoding="utf-8"))
            voltages = _clean_voltages(v_data.get("voltages") if isinstance(v_data, dict) else None)
        except Exception:
            logger.warning(f"Voltajes ECG corruptos al leer uuid={uuid}")
            voltages = []

    result = dict(meta)
    result["voltages"] = voltages
    return result
