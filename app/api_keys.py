"""
api_keys.py — API pública de lectura, claves por usuario (Roadmap P2, F10,
paso 1).

Trust boundary DELIBERADAMENTE separado de `INGEST_TOKEN` (app/config.py):
INGEST_TOKEN es un secreto único, global, de ESCRITURA (HealthKit push/ECG).
Las claves de este módulo son por-usuario, de SOLO LECTURA, revocables, y se
guardan hasheadas (nunca la clave cruda) — un límite de confianza distinto,
pensado para consumidores externos (scripts personales, Grafana futuro).

Formato de clave: `vk_<secrets.token_urlsafe(32)>`. Se persiste SOLO el hash
SHA-256 de la clave completa (prefijo incluido) — la clave cruda NUNCA se
guarda ni se puede recuperar tras su creación; solo se devuelve una vez, en el
momento de `generate_key()`.

Persistencia: data/users/<uid>/api_keys.json (patrón EXACTO de plan_store.py):
    {keys: [{id, label, hash, created, last_used, revoked_at}], updated}
load/save NUNCA lanzan; escritura atómica vía fsutil.atomic_write_text.
Household-aware vía app.userctx (mismo patrón _plan_log_path/_journal_log_path)
— demo-safe automático porque deriva de settings.DATA_DIR.

Tope _MAX_KEYS=10 por usuario (patrón _CUSTOM_MAX de journal.py): al llegar al
tope, generate_key() devuelve None (el caller de main.py responde 422).

Comparación de hash: SIEMPRE con secrets.compare_digest (nunca ==) — evita
timing attacks al resolver una clave cruda contra los hashes guardados.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import secrets
from pathlib import Path
from typing import Optional

from app.fsutil import atomic_write_text

logger = logging.getLogger("vitals.api_keys")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR
_API_KEYS_FILE = _DATA_DIR / "api_keys.json"  # legacy — usado si userctx no está activo

_KEY_PREFIX = "vk_"
_MAX_KEYS = 10
_LABEL_MAX = 60


def _api_keys_path() -> Path:
    """Ruta a api_keys.json del usuario activo (household-aware, mismo patrón
    que plan_store._plan_log_path()/journal._journal_log_path()). Fuera de un
    request household-aware, usa _API_KEYS_FILE tal cual (compat total)."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "api_keys.json"
    except Exception:
        pass
    return _API_KEYS_FILE


# ── Persistencia atómica (patrón plan_store.py — nunca lanza) ───────────────

def _empty_store() -> dict:
    return {"keys": [], "updated": None}


def load_keys() -> dict:
    """Lee data/users/<uid>/api_keys.json -> {keys, updated}. Si no existe o
    está corrupto -> estructura vacía (nunca lanza)."""
    empty = _empty_store()
    try:
        path = _api_keys_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("api_keys.json no es dict; usando estructura vacía.")
            return empty
        data.setdefault("keys", [])
        data.setdefault("updated", None)
        if not isinstance(data.get("keys"), list):
            data["keys"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("api_keys.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo api_keys.json: %s", exc)
        return empty


def save_keys(d: dict) -> None:
    """Guarda api_keys.json con escritura ATÓMICA. Nunca lanza."""
    try:
        path = _api_keys_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("keys", [])
        d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando api_keys.json: %s", exc)


# ── Hash (puro, sin I/O) ─────────────────────────────────────────────────────

def _hash_key(raw_key: str) -> str:
    """SHA-256 de la clave cruda completa (incluye el prefijo 'vk_'). Puro."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


# ── Mutaciones ───────────────────────────────────────────────────────────────

def generate_key(label: Optional[str] = None) -> Optional[dict]:
    """Genera una clave nueva `vk_<token_urlsafe(32)>`, persiste SOLO su hash,
    y devuelve {id, label, key, created} con la clave CRUDA — la única vez que
    se puede ver. None si ya se alcanzó el tope de _MAX_KEYS (contando también
    las revocadas: revocar no libera espacio, evita que un usuario genere
    infinitas claves basura). Nunca lanza."""
    try:
        store = load_keys()
        keys = [k for k in (store.get("keys") or []) if isinstance(k, dict)]
        if len(keys) >= _MAX_KEYS:
            return None

        label = (label or "").strip()[:_LABEL_MAX]
        raw_key = _KEY_PREFIX + secrets.token_urlsafe(32)
        key_hash = _hash_key(raw_key)
        key_id = secrets.token_hex(8)
        created = datetime.datetime.now().isoformat(timespec="seconds")

        entry = {
            "id": key_id,
            "label": label,
            "hash": key_hash,
            "created": created,
            "last_used": None,
            "revoked_at": None,
        }
        keys.append(entry)
        store["keys"] = keys
        save_keys(store)

        return {"id": key_id, "label": label, "key": raw_key, "created": created}
    except Exception as exc:
        logger.error("generate_key falló: %s", exc)
        return None


def list_keys() -> list:
    """Metadatos de TODAS las claves del usuario activo (incluidas las
    revocadas, con su revoked_at visible) — NUNCA el hash ni la clave cruda.
    [{id, label, created, last_used, revoked}]. Nunca lanza."""
    try:
        store = load_keys()
        out = []
        for k in store.get("keys") or []:
            if not isinstance(k, dict):
                continue
            out.append({
                "id": k.get("id"),
                "label": k.get("label") or "",
                "created": k.get("created"),
                "last_used": k.get("last_used"),
                "revoked": bool(k.get("revoked_at")),
            })
        return out
    except Exception as exc:
        logger.error("list_keys falló: %s", exc)
        return []


def revoke_key(key_id: str) -> bool:
    """Marca la clave `key_id` como revocada (revoked_at = ahora). True si
    existía y no estaba ya revocada; False si el id no existe (el caller de
    main.py responde 404) o ya estaba revocada. Nunca lanza."""
    try:
        store = load_keys()
        keys = store.get("keys") or []
        found = False
        for k in keys:
            if isinstance(k, dict) and k.get("id") == key_id:
                found = True
                if k.get("revoked_at"):
                    return False  # ya revocada — idempotente, no doble-cuenta
                k["revoked_at"] = datetime.datetime.now().isoformat(timespec="seconds")
                break
        if not found:
            return False
        store["keys"] = keys
        save_keys(store)
        return True
    except Exception as exc:
        logger.error("revoke_key falló: %s", exc)
        return False


def resolve_key(raw_key: str) -> bool:
    """True si `raw_key` coincide con una clave VIGENTE (no revocada) del
    usuario activo (el uid ya está resuelto por el caller vía userctx antes de
    llamar esto — ver main.py `_resolve_api_key`). Comparación de hash SIEMPRE
    con secrets.compare_digest (nunca ==), para no filtrar timing. Actualiza
    `last_used` best-effort (un fallo al persistir NUNCA bloquea la respuesta
    de éxito). Nunca lanza."""
    try:
        if not isinstance(raw_key, str) or not raw_key.startswith(_KEY_PREFIX):
            return False
        candidate_hash = _hash_key(raw_key)
        store = load_keys()
        keys = store.get("keys") or []
        for k in keys:
            if not isinstance(k, dict):
                continue
            stored_hash = k.get("hash")
            if not isinstance(stored_hash, str):
                continue
            if not secrets.compare_digest(stored_hash, candidate_hash):
                continue
            if k.get("revoked_at"):
                return False  # existe pero revocada -> inválida
            # Match vigente: actualiza last_used best-effort.
            try:
                k["last_used"] = datetime.datetime.now().isoformat(timespec="seconds")
                store["keys"] = keys
                save_keys(store)
            except Exception as exc:
                logger.warning("resolve_key: no pude actualizar last_used (%s)", exc)
            return True
        return False
    except Exception as exc:
        logger.error("resolve_key falló: %s", exc)
        return False
