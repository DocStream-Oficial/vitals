"""
hrv_trail.py — Probe de MEDICIÓN de la "HRV matutina" (backend, ADITIVO).

Registra, en cada sync, la HRV POR FUENTE (healthkit/google_health/…) y la
canónica de los últimos días, con timestamp. Sirve para medir empíricamente si
la HRV de la MAÑANA (cron 9am, con Google Health jalado fresco) es más estable
que la del final del día — antes de decidir re-priorizar el motor hacia ella.

Por qué existe: la HRV canónica hoy la gana HealthKit (push), cuya deriva
intradía un cron de backend NO puede ver (solo reprocesa el último push del
teléfono). Google Health es PULL: el cron lo controla y lo lee fresco cada
corrida. Este probe captura ambas para comparar.

🔴 NO toca el motor: solo escribe un log de medición. Best-effort — cualquier
fallo se loguea y se traga (nunca rompe el sync).
"""
from __future__ import annotations

import datetime
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("vitals.hrv_trail")

_MAX_SNAPSHOTS = 500   # ~2 crons/día * varios meses; se evicta lo más viejo
_TRACK_DAYS = 3        # cuántos de los últimos días con HRV se registran por snapshot


def _trail_path() -> Path:
    """Ruta a hrv_morning_trail.json del usuario activo (household-aware, igual
    patrón que healthkit._ingest_path / coach_store). Nunca lanza."""
    from app.config import settings
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "hrv_morning_trail.json"
    except Exception:
        pass
    return settings.DATA_DIR / "hrv_morning_trail.json"


def record_snapshot(fetched: dict, merged: dict) -> None:
    """Registra un snapshot de la HRV actual por fuente + canónica.

    fetched: {source_name: {'hrv': {date: value}, ...}}  (datos POR fuente, pre-merge)
    merged:  {'hrv': {date: value}, ...}                 (canónico, post-merge)

    Escritura atómica (.tmp + os.replace). Best-effort: nunca lanza."""
    try:
        canon = (merged or {}).get("hrv", {}) or {}
        if not canon:
            return
        recent = sorted(canon.keys())[-_TRACK_DAYS:]
        by_date: dict = {}
        for d in recent:
            entry: dict = {"canonical": canon.get(d)}
            for src, sdata in (fetched or {}).items():
                shrv = (sdata or {}).get("hrv", {}) or {}
                if d in shrv:
                    entry[src] = shrv[d]
            by_date[d] = entry
        snap = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "by_date": by_date,
        }

        path = _trail_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        log: list = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, list):
                    log = loaded
            except Exception:
                log = []
        log.append(snap)
        log = log[-_MAX_SNAPSHOTS:]

        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("hrv_trail snapshot falló (no bloqueante): %s", exc)
