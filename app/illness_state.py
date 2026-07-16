"""
illness_state.py — Persistencia del LATCH de la alerta `illness_early_warning`
(dev-harness/illness-latch, ver `_dev-harness/illness-latch/ROADMAP.md`).

Por qué existe: la alerta de posible enfermedad se retracta sola conforme
avanza el día (HRV se guarda como un solo float diario que se re-promedia en
cada sync → el pico parasimpático de la mañana se diluye para la noche).
`rule_illness_early_warning` (app/insights.py) es y sigue siendo stateless —
ESTE módulo es la capa de persistencia que la ENVUELVE: si la alerta disparó
hoy, queda fijada (latched) el resto del día calendario. No toca umbrales ni
lógica de la regla.

Storage: <data_dir>/illness_latch.json =
  {"date": "YYYY-MM-DD", "severity": "alert"|"watch"|"none", "insight": {...}|null}

Mismo patrón que app/coach_store.py: `current_data_dir()` vía userctx cuando
hay contexto household activo, fallback a la ruta legacy fuera de contexto
(tests preexistentes, scripts, scheduler sin uid fijado); lock de threading;
escritura ATÓMICA (.tmp + os.replace); None-safe (nunca lanza, loguea y
degrada — con `latch=True` un fallo de disco se comporta como `latch=False`).
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vitals.illness_state")

# Lock de módulo (re-entrante, mismo criterio que _STORE_LOCK de coach_store):
# serializa el read-modify-write de un solo archivo pequeño. Dos requests
# concurrentes (/ y /api/insights) pueden llamar apply_latch() casi a la vez;
# el lock evita que se pisen a medio escribir.
_STATE_LOCK = threading.RLock()

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR
_LATCH_FILE = _DATA_DIR / "illness_latch.json"  # legacy — ver _latch_file()

# Rank de severidad para el fijado (roadmap §3). "none"/ausente = 0 vía .get().
_SEVERITY_RANK = {"alert": 2, "watch": 1}


def _user_data_dir() -> Optional[Path]:
    """Directorio del usuario activo si hay un contexto household-aware, o
    None si no (tests preexistentes, scripts, scheduler sin uid) — en ese
    caso los callers usan la constante legacy tal cual. Copiado literal del
    mismo helper en app/coach_store.py."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir()
    except Exception:
        pass
    return None


def _latch_file() -> Path:
    d = _user_data_dir()
    return (d / "illness_latch.json") if d is not None else _LATCH_FILE


def _atomic_write(data: dict) -> None:
    latch_file = _latch_file()
    latch_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = latch_file.with_suffix(latch_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, latch_file)


# ── API pública ────────────────────────────────────────────────────────────

def load_latch() -> Optional[dict]:
    """Estado persistido, o None si no hay (archivo ausente, vacío, corrupto,
    con forma inesperada, o `current_data_dir` inaccesible). Nunca lanza."""
    try:
        with _STATE_LOCK:
            latch_file = _latch_file()
            if not latch_file.exists():
                return None
            text = latch_file.read_text(encoding="utf-8")
            if not text.strip():
                return None
            data = json.loads(text)
            if not isinstance(data, dict) or "date" not in data:
                logger.warning("illness_latch.json con forma inesperada; ignorando.")
                return None
            return data
    except Exception as exc:
        logger.warning("load_latch falló (%s); degradando a sin latch.", exc)
        return None


def save_latch(data: dict) -> None:
    """Persiste el estado del latch (escritura atómica). Nunca lanza — un
    fallo de disco/permiso queda logueado y degrada con gracia (el próximo
    load_latch() simplemente no lo verá)."""
    try:
        with _STATE_LOCK:
            _atomic_write(data)
    except Exception as exc:
        logger.error("save_latch falló: %s", exc)


def _rank(insight: Optional[dict]) -> int:
    if not isinstance(insight, dict):
        return 0
    return _SEVERITY_RANK.get(insight.get("severity"), 0)


def apply_latch(fresh_insight: Optional[dict], today_date: Optional[str]) -> Optional[dict]:
    """
    Lógica de fijado (roadmap §"La lógica del latch"):
    - Carga el latch persistido. Si su `date` != today_date -> se IGNORA (día nuevo).
    - `effective` = el de MAYOR rank entre (fresh, persistido-de-hoy). Empate
      (mismo rank) -> gana el persistido (peak estable: no re-flip-flopea
      dentro del mismo día por una segunda lectura de igual severidad).
    - Persiste el peak del día = `effective` (con date=today_date), SIEMPRE
      (incluso si `effective` es None -> severity "none") para que el archivo
      no acumule días viejos (ver criterio de reset del roadmap).
    - Devuelve `effective` (None si rank 0).
    - Caso "fresh None pero hay alert persistido de HOY": ya cubierto por la
      comparación de rank (persisted_rank=2 >= fresh_rank=0 -> gana persisted).

    None-safe: sin `today_date` no hay forma confiable de comparar "mismo
    día" -> degrada a devolver `fresh_insight` tal cual, SIN persistir
    (equivalente a latch=False para ese llamado). Cualquier excepción interna
    (disco, JSON, current_data_dir) degrada igual, nunca propaga.
    """
    if not today_date:
        return fresh_insight

    try:
        persisted = load_latch()
        persisted_today = None
        if persisted is not None and persisted.get("date") == today_date:
            persisted_today = persisted.get("insight")

        fresh_rank = _rank(fresh_insight)
        persisted_rank = _rank(persisted_today)

        effective = persisted_today if persisted_rank >= fresh_rank else fresh_insight

        save_latch({
            "date": today_date,
            "severity": (effective or {}).get("severity") or "none",
            "insight": effective,
        })

        return effective
    except Exception as exc:
        logger.warning("apply_latch falló (%s); degradando a fresh sin persistir.", exc)
        return fresh_insight
