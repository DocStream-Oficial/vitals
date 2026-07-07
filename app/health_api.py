"""
health_api.py — cliente HTTP para el Google Health API.
Porta http/api_get/api_post/list_all/daily_rollup de vitals_sync.py a `requests`.
La semántica (paths, params, bodies del rollup) se preserva idéntica al original.
"""
import json
import datetime
import requests
from pathlib import Path
from app.config import settings

API = settings.API_BASE


def _raw_dir() -> Path:
    """Carpeta vitals_raw/ (dumps crudos de debug de Google Health) del usuario
    activo (Fase 8D, paso D3: household). Fuera de un request household-aware
    (is_context_active()=False — scripts/tests), usa settings.DATA_DIR/
    vitals_raw tal cual: comportamiento idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "vitals_raw"
    except Exception:
        pass
    return settings.DATA_DIR / "vitals_raw"


def http(method: str, url: str, headers: dict = None, data=None):
    """Wrapper HTTP genérico — misma firma/contrato que el original urllib."""
    headers = headers or {}
    kwargs = {"headers": headers, "timeout": 60}
    if data is not None:
        if isinstance(data, (dict, list)):
            kwargs["json"] = data
            headers.setdefault("Content-Type", "application/json")
        else:
            # form-encoded string o bytes
            kwargs["data"] = data
    resp = requests.request(method, url, **kwargs)
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def api_get(path: str, token: str, params: dict = None):
    url = f"{API}/{path}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    resp = requests.get(url, headers=headers, params=params, timeout=60)
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {"_raw": resp.text}
    return resp.status_code, body


def api_post(path: str, token: str, body: dict):
    url = f"{API}/{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    resp = requests.post(url, headers=headers, json=body, timeout=60)
    try:
        rb = resp.json() if resp.content else {}
    except Exception:
        rb = {"_raw": resp.text}
    return resp.status_code, rb


def list_all(datatype: str, token: str, max_pages: int = 12, save_name: str = None):
    """GET .../dataTypes/<type>/dataPoints con paginación. Devuelve lista de dataPoints.
    Idéntico al original — mismos paths y params."""
    out, page = [], None
    raw_dir = _raw_dir()
    for _ in range(max_pages):
        params = {}
        if page:
            params["pageToken"] = page
        status, resp = api_get(
            f"dataTypes/{datatype}/dataPoints", token, params or None
        )
        if status != 200:
            print(f"   (aviso) {datatype}: status {status} -> {str(resp)[:200]}")
            break
        if save_name and not out:
            raw_dir.mkdir(exist_ok=True)
            (raw_dir / f"{save_name}.json").write_text(
                json.dumps(resp, indent=2)[:200000]
            )
        out.extend(resp.get("dataPoints", []))
        page = resp.get("nextPageToken")
        if not page:
            break
    return out


def daily_rollup(
    datatype: str,
    token: str,
    start_date: datetime.date,
    end_date: datetime.date,
    key: str,
    subfields,
    save_name: str = None,
):
    """POST :dailyRollUp para sumas diarias.
    Body IDÉNTICO al original — no cambiar.
    'key' = nombre del objeto de medida, 'subfields' = campo (str) o lista de campos a sumar."""
    # importamos find_date aquí para evitar circular; también podría ir al top
    from app.parsers import find_date

    body = {
        "range": {
            "start": {
                "date": {
                    "year": start_date.year,
                    "month": start_date.month,
                    "day": start_date.day,
                },
                "time": {"hours": 0, "minutes": 0, "seconds": 0},
            },
            "end": {
                "date": {
                    "year": end_date.year,
                    "month": end_date.month,
                    "day": end_date.day,
                },
                "time": {"hours": 23, "minutes": 59, "seconds": 59},
            },
        },
        "windowSizeDays": 1,
    }
    status, resp = api_post(f"dataTypes/{datatype}/dataPoints:dailyRollUp", token, body)
    raw_dir = _raw_dir()
    if save_name:
        raw_dir.mkdir(exist_ok=True)
        (raw_dir / f"{save_name}.json").write_text(json.dumps(resp, indent=2)[:200000])
    res = {}
    if status != 200:
        print(f"   (aviso) {datatype} rollup: status {status} -> {str(resp)[:160]}")
        return res
    for rp in resp.get("rollupDataPoints", []):
        d = find_date(rp.get("civilStartTime", {}))
        m = rp.get(key, {})
        if not d or not isinstance(m, dict):
            continue
        if isinstance(subfields, (list, tuple)):
            val = sum(float(m.get(s, 0) or 0) for s in subfields)
        else:
            v = m.get(subfields)
            val = float(v) if v not in (None, "") else None
        if val is not None:
            res[d] = round(val)
    return res
