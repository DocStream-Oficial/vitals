"""
labs.py — Laboratorios de sangre manuales (Fase 8D, paso D1).

Tracking honesto, no correlación forzada: los análisis de sangre son esparsos
(2-6/año); correlacionarlos con recovery sería estadística deshonesta. Valor
real v1: serie temporal por marcador + rangos de referencia + flag
fuera-de-rango + inyección al contexto del coach.

Persistencia: data/labs_log.json — patrón EXACTO cycle.py/journal.py:
    {entries: [{id, date, marker, value, unit, ref_low, ref_high, note}], updated}
load/save NUNCA lanzan; escritura atómica vía fsutil.atomic_write_text.

Catálogo MARKERS (~20 biomarcadores comunes) con unidad canónica + rango de
referencia por sexo donde aplica + clave i18n `marker_<key>`.
"""
from __future__ import annotations

import datetime
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from app.i18n import tr

logger = logging.getLogger("vitals.labs")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_LABS_LOG_FILE = _DATA_DIR / "labs_log.json"


# ── Catálogo de biomarcadores (D1) ──────────────────────────────────────────
# unit: unidad canónica mostrada en la UI.
# ref: rango de referencia. Si es tupla (low, high) aplica a ambos sexos; si
# es dict {"M": (low, high), "F": (low, high)} el rango depende del sexo
# efectivo del perfil. None en low/high = sin límite en ese extremo.
MARKERS: list[dict] = [
    {"key": "glucose", "unit": "mg/dL", "ref": (70, 99)},
    {"key": "hba1c", "unit": "%", "ref": (4.0, 5.6)},
    {"key": "ldl", "unit": "mg/dL", "ref": (0, 100)},
    {"key": "hdl", "unit": "mg/dL", "ref": {"M": (40, None), "F": (50, None)}},
    {"key": "triglycerides", "unit": "mg/dL", "ref": (0, 150)},
    {"key": "total_chol", "unit": "mg/dL", "ref": (0, 200)},
    {"key": "crp", "unit": "mg/L", "ref": (0, 3.0)},
    {"key": "ferritin", "unit": "ng/mL", "ref": {"M": (24, 336), "F": (11, 307)}},
    {"key": "vitamin_d", "unit": "ng/mL", "ref": (30, 100)},
    {"key": "b12", "unit": "pg/mL", "ref": (200, 900)},
    {"key": "tsh", "unit": "mIU/L", "ref": (0.4, 4.0)},
    {"key": "t4", "unit": "ng/dL", "ref": (0.8, 1.8)},
    {"key": "testosterone", "unit": "ng/dL", "ref": {"M": (264, 916), "F": (8, 60)}},
    {"key": "estradiol", "unit": "pg/mL", "ref": {"M": (10, 40), "F": (15, 350)}},
    {"key": "cortisol", "unit": "µg/dL", "ref": (6, 23)},
    {"key": "creatinine", "unit": "mg/dL", "ref": {"M": (0.74, 1.35), "F": (0.59, 1.04)}},
    {"key": "alt", "unit": "U/L", "ref": {"M": (7, 55), "F": (7, 45)}},
    {"key": "ast", "unit": "U/L", "ref": {"M": (8, 48), "F": (8, 43)}},
    {"key": "hemoglobin", "unit": "g/dL", "ref": {"M": (13.5, 17.5), "F": (12.0, 15.5)}},
    {"key": "uric_acid", "unit": "mg/dL", "ref": {"M": (3.4, 7.0), "F": (2.4, 6.0)}},
]

MARKER_KEYS = {m["key"] for m in MARKERS}
_MARKER_BY_KEY = {m["key"]: m for m in MARKERS}

# Correlación solo si un marcador acumula ≥8 tomas (raro; gate duro) — v1 no
# ejecuta ninguna correlación, se deja como constante documentada para futuras
# rondas (roadmap §Arquitectura, "Labs: tracking honesto").
MIN_TOMAS_FOR_CORRELATION = 8


def _labs_log_path() -> Path:
    """Ruta a labs_log.json del usuario activo (Fase 8D, paso D3: household).
    Fuera de un request household-aware (is_context_active()=False — tests
    preexistentes que monkeypatchean _LABS_LOG_FILE directamente, scripts),
    usa _LABS_LOG_FILE tal cual: comportamiento idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "labs_log.json"
    except Exception:
        pass
    return _LABS_LOG_FILE


def _empty_labs() -> dict:
    return {"entries": [], "updated": None}


# ── Persistencia atómica (patrón cycle.py/journal.py — nunca lanza) ─────────

def load_labs() -> dict:
    """Lee data/labs_log.json → {entries: [...], updated}.
    Si no existe o está corrupto → estructura vacía (nunca lanza)."""
    empty = _empty_labs()
    try:
        path = _labs_log_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("labs_log.json no es dict; usando estructura vacía.")
            return empty
        data.setdefault("entries", [])
        data.setdefault("updated", None)
        if not isinstance(data.get("entries"), list):
            data["entries"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("labs_log.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo labs_log.json: %s", exc)
        return empty


def save_labs(d: dict) -> None:
    """Guarda labs_log.json con escritura ATÓMICA (fsutil.atomic_write_text).
    Nunca lanza excepción (loguea en error)."""
    try:
        from app.fsutil import atomic_write_text
        path = _labs_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("entries", [])
        d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando labs_log.json: %s", exc)


# ── Rangos de referencia ─────────────────────────────────────────────────────

def ref_range(marker_key: str, sex: Optional[str] = None) -> tuple:
    """Rango de referencia (low, high) para el marcador dado el sexo efectivo
    del perfil. Marcadores sin dependencia de sexo ignoran `sex`. Marcador
    desconocido -> (None, None) (nunca lanza)."""
    spec = _MARKER_BY_KEY.get(marker_key)
    if spec is None:
        return (None, None)
    ref = spec.get("ref")
    if isinstance(ref, dict):
        sex_key = "M" if str(sex or "M").upper().startswith("M") else "F"
        return ref.get(sex_key, (None, None))
    if isinstance(ref, (tuple, list)) and len(ref) == 2:
        return (ref[0], ref[1])
    return (None, None)


def marker_unit(marker_key: str) -> str:
    spec = _MARKER_BY_KEY.get(marker_key)
    return spec.get("unit", "") if spec else ""


def is_out_of_range(value: Any, ref_low: Optional[float], ref_high: Optional[float]) -> bool:
    """True si value está fuera de [ref_low, ref_high]. None-safe: límites
    ausentes no acotan por ese lado; value no numérico -> False (nunca lanza)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if ref_low is not None and v < ref_low:
        return True
    if ref_high is not None and v > ref_high:
        return True
    return False


# ── Catálogo localizado ──────────────────────────────────────────────────────

def catalog(locale: str = "es", sex: Optional[str] = None) -> list[dict]:
    """Catálogo completo [{key, label, unit, ref_low, ref_high}] con labels
    localizados (marker_<key> ×4) y rango de referencia resuelto por sexo."""
    out = []
    for m in MARKERS:
        low, high = ref_range(m["key"], sex)
        out.append({
            "key": m["key"],
            "label": tr("marker_" + m["key"], locale),
            "unit": m["unit"],
            "ref_low": low,
            "ref_high": high,
        })
    return out


# ── CRUD de entradas ──────────────────────────────────────────────────────────

def _valid_date(s: Any) -> Optional[datetime.date]:
    if not isinstance(s, str):
        return None
    try:
        return datetime.date.fromisoformat(s)
    except Exception:
        return None


def add_entry(date: str, marker: str, value: float, unit: Optional[str] = None,
              note: Optional[str] = None, sex: Optional[str] = None) -> Optional[dict]:
    """Añade una entrada manual de laboratorio. Persiste ref_low/ref_high
    RESUELTOS al momento de la captura (auditable: si el perfil cambia de sexo
    después, la entrada vieja conserva el rango con el que se evaluó).
    Devuelve la entrada creada, o None si marker/value/date son inválidos.
    Nunca lanza."""
    try:
        if marker not in MARKER_KEYS:
            return None
        if _valid_date(date) is None:
            return None
        try:
            v = float(value)
        except (TypeError, ValueError):
            return None
        low, high = ref_range(marker, sex)
        entry = {
            "id": uuid.uuid4().hex[:12],
            "date": date,
            "marker": marker,
            "value": v,
            "unit": unit or marker_unit(marker),
            "ref_low": low,
            "ref_high": high,
            "note": (note or "")[:500],
        }
        d = load_labs()
        entries = [e for e in d.get("entries") or [] if isinstance(e, dict)]
        entries.append(entry)
        d["entries"] = entries
        save_labs(d)
        return entry
    except Exception as exc:
        logger.error("add_entry falló: %s", exc)
        return None


def delete_entry(entry_id: str) -> bool:
    """Borra una entrada por id. Idempotente (True incluso si no existía —
    el estado final deseado se cumple). Nunca lanza."""
    try:
        d = load_labs()
        entries = [e for e in d.get("entries") or [] if isinstance(e, dict)]
        new_entries = [e for e in entries if e.get("id") != entry_id]
        d["entries"] = new_entries
        save_labs(d)
        return True
    except Exception as exc:
        logger.error("delete_entry falló: %s", exc)
        return False


def series_by_marker(labs: Optional[dict] = None) -> dict:
    """Agrupa entries por marker, ordenadas por fecha ascendente. Cada entrada
    incluye el flag `out_of_range`. {} si no hay datos. Nunca lanza."""
    try:
        d = labs if labs is not None else load_labs()
        entries = [e for e in d.get("entries") or [] if isinstance(e, dict)]
        by_marker: dict = {}
        for e in entries:
            marker = e.get("marker")
            if marker not in MARKER_KEYS:
                continue
            by_marker.setdefault(marker, []).append(e)
        for marker, lst in by_marker.items():
            lst.sort(key=lambda e: e.get("date") or "")
            for e in lst:
                e["out_of_range"] = is_out_of_range(e.get("value"), e.get("ref_low"), e.get("ref_high"))
        return by_marker
    except Exception as exc:
        logger.warning("series_by_marker falló (degradando a {}): %s", exc)
        return {}


def latest_by_marker(labs: Optional[dict] = None) -> dict:
    """Última toma por marcador — {marker: entry con out_of_range}. Usado por
    el contexto del coach. Nunca lanza."""
    try:
        by_marker = series_by_marker(labs)
        return {m: lst[-1] for m, lst in by_marker.items() if lst}
    except Exception as exc:
        logger.warning("latest_by_marker falló (degradando a {}): %s", exc)
        return {}


# ── Import CSV tolerante (D1) ────────────────────────────────────────────────

def import_csv(text: str, sex: Optional[str] = None) -> dict:
    """Parser tolerante de CSV con columnas date,marker,value[,unit][,note].
    Header opcional (detectado por nombre de columna 'date' en la primera
    fila). Filas rechazadas se reportan con motivo, no abortan el import.

    Devuelve {imported: [...entries creadas...], rejected: [{row, reason}]}.
    Nunca lanza."""
    result = {"imported": [], "rejected": []}
    try:
        import csv
        import io

        reader = csv.reader(io.StringIO(text))
        rows = [r for r in reader if r and any((c or "").strip() for c in r)]
        if not rows:
            return result

        start_idx = 0
        header = [c.strip().lower() for c in rows[0]]
        if header and header[0] == "date":
            start_idx = 1
            col_idx = {name: i for i, name in enumerate(header)}
        else:
            col_idx = {"date": 0, "marker": 1, "value": 2, "unit": 3, "note": 4}

        for i in range(start_idx, len(rows)):
            row = rows[i]
            row_num = i + 1
            try:
                date_val = row[col_idx.get("date", 0)].strip() if col_idx.get("date", 0) < len(row) else ""
                marker_val = row[col_idx.get("marker", 1)].strip().lower() if col_idx.get("marker", 1) < len(row) else ""
                value_raw = row[col_idx.get("value", 2)].strip() if col_idx.get("value", 2) < len(row) else ""
                unit_idx = col_idx.get("unit")
                note_idx = col_idx.get("note")
                unit_val = row[unit_idx].strip() if (unit_idx is not None and unit_idx < len(row)) else None
                note_val = row[note_idx].strip() if (note_idx is not None and note_idx < len(row)) else None
            except Exception:
                result["rejected"].append({"row": row_num, "reason": "columnas insuficientes"})
                continue

            if _valid_date(date_val) is None:
                result["rejected"].append({"row": row_num, "reason": f"fecha inválida: {date_val!r}"})
                continue
            if marker_val not in MARKER_KEYS:
                result["rejected"].append({"row": row_num, "reason": f"marcador desconocido: {marker_val!r}"})
                continue
            try:
                value_num = float(value_raw)
            except (TypeError, ValueError):
                result["rejected"].append({"row": row_num, "reason": f"valor no numérico: {value_raw!r}"})
                continue

            entry = add_entry(date_val, marker_val, value_num, unit=unit_val, note=note_val, sex=sex)
            if entry is None:
                result["rejected"].append({"row": row_num, "reason": "error interno al guardar"})
                continue
            result["imported"].append(entry)

        return result
    except Exception as exc:
        logger.warning("import_csv falló (degradando a resultado parcial): %s", exc)
        result["rejected"].append({"row": 0, "reason": f"error de parseo general: {exc}"})
        return result


# ── Contexto para el coach (D1) ──────────────────────────────────────────────

def coach_context_lines(locale: str = "es", labs: Optional[dict] = None) -> list[str]:
    """Líneas 'ÚLTIMOS LABORATORIOS' para inyectar en el prompt del coach: por
    cada marcador con al menos una toma, la más reciente + flag fuera-de-rango.
    [] si no hay laboratorios registrados. Nunca lanza."""
    try:
        latest = latest_by_marker(labs)
        if not latest:
            return []
        lines = []
        # Orden estable: el orden declarado en MARKERS (auditable/reproducible).
        for m in MARKERS:
            key = m["key"]
            e = latest.get(key)
            if not e:
                continue
            label = tr("marker_" + key, locale)
            flag = " ⚠" if e.get("out_of_range") else ""
            lines.append(f"• {label}: {e.get('value')} {e.get('unit', '')} ({e.get('date')}){flag}")
        return lines
    except Exception as exc:
        logger.warning("coach_context_lines falló (degradando a []): %s", exc)
        return []
