"""
app/routes/export.py — GET /api/data, GET /api/export (+ helpers _csv_safe,
_flatten_day_for_csv) (Fase 9, paso A2). Movidos TAL CUAL desde main.py — ver
ROADMAP-vitals-fase9-desmonolitizar.md.
"""
from __future__ import annotations

import csv
import datetime as _dt
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, Response

from app.deps import _load_dataset

router = APIRouter()


@router.get("/api/data")
async def api_data():
    dataset = _load_dataset()
    if not dataset:
        raise HTTPException(status_code=404, detail="No hay datos. Corre /api/sync primero.")
    return JSONResponse(content=dataset)


# Ronda 4: campos de `days` que son de sueño — se prefijan "sleep_" en el CSV
# aplanado para namespacing claro (el dataset real ya trae estos campos FLAT en
# cada día, no como un sub-dict "sleep" anidado — a diferencia de lo que asumía
# el roadmap original; documentado como desviación en el informe final).
_SLEEP_FIELDS = {
    "asleep", "inbed", "awake", "deep", "rem", "light", "eff",
    "bedtime", "waketime", "bed_min",
    # 'sleep_perf' YA trae el prefijo en su propio nombre -> no se re-prefija
    # (evita la columna fea 'sleep_sleep_perf').
}

# Formula-injection guard (CSV abierto en Excel/Sheets): un campo de texto que
# empiece con =, +, -, @ puede ejecutarse como fórmula. Prefijamos con ' para
# neutralizarlo. Los campos de `days`/`exercises` son todos numéricos o fechas
# ISO controladas por el propio backend, así que el único vector real es el
# campo `name` de exercises (viene de fuentes externas / HealthKit).
def _csv_safe(v: Any) -> Any:
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@"):
        return "'" + v
    return v


def _flatten_day_for_csv(day: dict) -> dict:
    """Aplana un día del dataset a un dict plano listo para csv.DictWriter.
    Campos de sueño -> prefijo 'sleep_'. None -> "" (csv no distingue None de "").

    F2 roadmap P0 (hipnograma): los valores compuestos (list/dict — hoy solo
    `segments`) se EXCLUYEN del CSV: una lista serializada como celda rompe el
    formato plano (el export JSON sí los incluye tal cual). Skip genérico por
    tipo, no por nombre — cualquier campo compuesto futuro queda cubierto.
    """
    out = {}
    for k, v in day.items():
        if isinstance(v, (list, dict)):
            continue
        key = f"sleep_{k}" if k in _SLEEP_FIELDS and k != "date" else k
        out[key] = "" if v is None else _csv_safe(v)
    return out


@router.get("/api/export")
async def api_export(fmt: str = "json"):
    """Exporta el dataset completo del usuario (Ronda 4 — producto).

    fmt=json (default): dataset completo tal cual vive en health_compact.json,
      como attachment descargable.
    fmt=csv: `days` aplanado (csv.DictWriter, columnas = UNIÓN de claves de todos
      los días — nunca solo las del primer día, que puede tener menos campos que
      el último). Los campos de sueño llevan prefijo 'sleep_'. `exercises` queda
      FUERA del CSV v1 (estructura distinta, tabla separada — documentado aquí y
      en el roadmap).

    Sin datos → 404 controlado. fmt inválido → 400 controlado. Nunca 500.
    """
    if fmt not in ("json", "csv"):
        raise HTTPException(status_code=400, detail="fmt debe ser 'json' o 'csv'.")

    dataset = _load_dataset()
    if not dataset:
        raise HTTPException(status_code=404, detail="No hay datos. Corre /api/sync primero.")

    today_str = _dt.date.today().strftime("%Y%m%d")

    if fmt == "json":
        body = json.dumps(dataset, ensure_ascii=False, indent=2).encode("utf-8")
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f"attachment; filename=vitals-export-{today_str}.json",
                "Cache-Control": "no-store",
            },
        )

    # fmt == "csv"
    days = dataset.get("days", [])
    if not days:
        raise HTTPException(status_code=404, detail="No hay datos de días para exportar.")

    flattened = [_flatten_day_for_csv(d) for d in days]
    # Fieldnames = UNIÓN de todas las claves (preserva orden de primera aparición),
    # nunca solo las del primer día (que puede tener menos campos que el último).
    fieldnames: list = []
    seen = set()
    for row in flattened:
        for k in row.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in flattened:
        writer.writerow(row)

    return Response(
        content=buf.getvalue().encode("utf-8"),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=vitals-export-{today_str}.csv",
            "Cache-Control": "no-store",
        },
    )
