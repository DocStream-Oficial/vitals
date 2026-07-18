"""
coach_store.py — Persistencia de CONVERSACIONES del Coach (v2, multi-chat).

Storage: data/coach_conversations.json =
  {"version": 2, "active_id": "<id>|null",
   "conversations": [{id, title, created, updated, messages: [{role, content, ts}]}]}

Migración automática (idempotente): si NO existe el archivo v2 pero SÍ el
`coach_history.json` plano (Ronda 1, lista de {role, content, ts}) -> se envuelve
en UNA conversación con TODOS sus mensajes (sin pérdida), se marca activa, y el
archivo viejo se renombra a `coach_history.json.v1.bak` (nunca se borra).

Escritura ATÓMICA: .tmp + os.replace (mismo patrón que la v1).
Caps: _MAX_MSGS_PER_CONV=200 (por conversación), _MAX_CONVERSATIONS=50 (evicta
la más vieja por `updated` asc; NUNCA evicta la conversación activa).
Todo None-safe: nunca lanza excepción (loguea y degrada con gracia).

Backward-compat (Ronda 1 API, deprecada pero soportada por main.py):
  load_history() -> mensajes de la conversación activa (lista plana).
  append_turn(question, answer) -> firma vieja detectada por posicionales;
    usar append_turn(cid, question, answer) en código nuevo.
  clear() -> alias de clear_all().
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("vitals.coach_store")

# Lock de módulo (re-entrante): el store es UN solo archivo con patrón
# read-modify-write; dos append_turn concurrentes (POST /api/coach corre
# ask_coach en threadpool, ~90s) podían pisarse y PERDER una conversación
# entera + spamear FileNotFoundError en os.replace. Serializa toda mutación,
# mismo criterio que _SYNC_LOCK en app/sync.py. RLock: append_turn -> _save_store
# anidan sin deadlock.
_STORE_LOCK = threading.RLock()

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_STORE_FILE = _DATA_DIR / "coach_conversations.json"  # legacy — ver _store_file()
_LEGACY_HISTORY_FILE = _DATA_DIR / "coach_history.json"  # legacy — ver _legacy_history_file()
_LEGACY_BACKUP_FILE = _DATA_DIR / "coach_history.json.v1.bak"  # legacy — ver _legacy_backup_file()


def _user_data_dir() -> Optional[Path]:
    """Directorio del usuario activo si hay un contexto household-aware
    (Fase 8D, paso D3), o None si no (tests preexistentes, scripts) —
    en ese caso los callers usan las constantes legacy tal cual."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir()
    except Exception:
        pass
    return None


def _store_file() -> Path:
    d = _user_data_dir()
    return (d / "coach_conversations.json") if d is not None else _STORE_FILE


def _legacy_history_file() -> Path:
    d = _user_data_dir()
    return (d / "coach_history.json") if d is not None else _LEGACY_HISTORY_FILE


def _legacy_backup_file() -> Path:
    d = _user_data_dir()
    return (d / "coach_history.json.v1.bak") if d is not None else _LEGACY_BACKUP_FILE

_MAX_MSGS_PER_CONV = 200
_MAX_CONVERSATIONS = 50

_DEFAULT_TITLE = "Conversación anterior"


# ── helpers internos ──────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return secrets.token_urlsafe(12)


def _empty_store() -> dict:
    return {"version": 2, "active_id": None, "conversations": []}


def _atomic_write(data: dict) -> None:
    store_file = _store_file()
    store_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = store_file.with_suffix(store_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, store_file)


def _title_from_first_message(messages: list[dict]) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user" and (m.get("content") or "").strip():
            text = m["content"].strip()
            return (text[:40] + "…") if len(text) > 40 else text
    return _DEFAULT_TITLE


def _migrate_if_needed() -> None:
    """Migración v1 (plano) -> v2 (conversaciones). Idempotente: si ya existe
    el archivo v2, no hace nada (aunque el .v1.bak o el plano sigan ahí)."""
    store_file = _store_file()
    if store_file.exists():
        return

    legacy_history_file = _legacy_history_file()
    legacy_messages: list[dict] = []
    if legacy_history_file.exists():
        try:
            text = legacy_history_file.read_text(encoding="utf-8")
            if text.strip():
                data = json.loads(text)
                if isinstance(data, list):
                    legacy_messages = [m for m in data if isinstance(m, dict)]
        except Exception as exc:
            logger.warning("Migración: coach_history.json ilegible (%s); arrancando vacío.", exc)
            legacy_messages = []

    store = _empty_store()

    if legacy_messages:
        conv_id = _new_id()
        now = _now_iso()
        conv = {
            "id": conv_id,
            "title": _title_from_first_message(legacy_messages),
            "created": legacy_messages[0].get("ts") or now,
            "updated": legacy_messages[-1].get("ts") or now,
            "messages": legacy_messages,
        }
        store["conversations"] = [conv]
        store["active_id"] = conv_id

    try:
        _atomic_write(store)
    except Exception as exc:
        logger.error("Migración: no se pudo escribir coach_conversations.json: %s", exc)
        return

    # Renombrar el viejo a .v1.bak SOLO tras escribir exitosamente el v2 (nunca se destruye).
    if legacy_history_file.exists():
        try:
            legacy_backup_file = _legacy_backup_file()
            if not legacy_backup_file.exists():
                os.replace(legacy_history_file, legacy_backup_file)
            else:
                # .v1.bak ya existe de una corrida anterior parcial: no pisar, solo
                # remover el plano para no re-disparar la migración en el futuro.
                legacy_history_file.unlink()
        except Exception as exc:
            logger.warning("Migración: no se pudo respaldar coach_history.json: %s", exc)


def _load_store() -> dict:
    with _STORE_LOCK:
        _migrate_if_needed()
    try:
        text = _store_file().read_text(encoding="utf-8")
        if not text.strip():
            return _empty_store()
        data = json.loads(text)
        if not isinstance(data, dict) or "conversations" not in data:
            logger.warning("coach_conversations.json con forma inesperada; reseteando.")
            return _empty_store()
        data.setdefault("version", 2)
        data.setdefault("active_id", None)
        if not isinstance(data.get("conversations"), list):
            data["conversations"] = []
        return data
    except FileNotFoundError:
        return _empty_store()
    except json.JSONDecodeError as exc:
        logger.warning("coach_conversations.json inválido (%s); devolviendo vacío.", exc)
        return _empty_store()
    except Exception as exc:
        logger.warning("Error leyendo coach_conversations.json: %s", exc)
        return _empty_store()


def _save_store(store: dict) -> None:
    try:
        _apply_caps(store)
        _atomic_write(store)
    except Exception as exc:
        logger.error("Error persistiendo coach_conversations.json: %s", exc)


def _apply_caps(store: dict) -> None:
    """Cap por-conversación (mensajes) + cap total de conversaciones (evicta la
    más vieja por `updated` asc, nunca la activa)."""
    convs = store.get("conversations") or []
    for conv in convs:
        msgs = conv.get("messages") or []
        if len(msgs) > _MAX_MSGS_PER_CONV:
            conv["messages"] = msgs[-_MAX_MSGS_PER_CONV:]

    if len(convs) > _MAX_CONVERSATIONS:
        active_id = store.get("active_id")
        # Orden ascendente por updated (más vieja primero); nunca evictar la activa.
        evictable = [c for c in convs if c.get("id") != active_id]
        evictable.sort(key=lambda c: c.get("updated") or "")
        n_to_evict = len(convs) - _MAX_CONVERSATIONS
        evict_ids = {c["id"] for c in evictable[:max(0, n_to_evict)]}
        if evict_ids:
            store["conversations"] = [c for c in convs if c.get("id") not in evict_ids]


def _find(store: dict, cid: Optional[str]) -> Optional[dict]:
    if not cid:
        return None
    for c in store.get("conversations") or []:
        if c.get("id") == cid:
            return c
    return None


# ── API pública v2 ────────────────────────────────────────────────────────────

def list_conversations() -> list[dict]:
    """Metadata ligera (sin messages), orden por `updated` desc. Nunca lanza."""
    try:
        store = _load_store()
        convs = store.get("conversations") or []
        items = [
            {
                "id": c.get("id"),
                "title": c.get("title") or _DEFAULT_TITLE,
                "updated": c.get("updated"),
                "message_count": len(c.get("messages") or []),
                "kind": c.get("kind") or "chat",
            }
            for c in convs
        ]
        items.sort(key=lambda c: c.get("updated") or "", reverse=True)
        return items
    except Exception as exc:
        logger.warning("list_conversations falló: %s", exc)
        return []


def get_conversation(cid: str) -> Optional[dict]:
    """Conversación completa (con messages) o None si no existe. Nunca lanza."""
    try:
        store = _load_store()
        conv = _find(store, cid)
        return dict(conv) if conv else None
    except Exception as exc:
        logger.warning("get_conversation falló: %s", exc)
        return None


def create_conversation(title: Optional[str] = None, kind: Optional[str] = None) -> dict:
    """Crea una conversación vacía y la persiste. Devuelve el dict completo.

    `kind` es ADITIVO (Coach Deportivo, roadmap coach-mental Paso 2): si viene
    truthy (p.ej. "mental_master") se guarda `"kind": kind` en el dict de la
    conversación; si no, NO se añade la clave — las conversaciones de chat
    normal quedan byte-idénticas a como eran antes de este campo."""
    try:
        with _STORE_LOCK:
            store = _load_store()
            now = _now_iso()
            conv = {
                "id": _new_id(),
                "title": (title or _DEFAULT_TITLE),
                "created": now,
                "updated": now,
                "messages": [],
            }
            if kind:
                conv["kind"] = kind
            convs = store.setdefault("conversations", [])
            convs.append(conv)
            _save_store(store)
            return conv
    except Exception as exc:
        logger.error("create_conversation falló: %s", exc)
        # Degradar con gracia: devolver un dict transitorio no-persistido antes que lanzar.
        now = _now_iso()
        conv = {"id": _new_id(), "title": title or _DEFAULT_TITLE, "created": now, "updated": now, "messages": []}
        if kind:
            conv["kind"] = kind
        return conv


def get_kind(cid: Optional[str]) -> str:
    """`kind` de la conversación (p.ej. "mental_master"), o "chat" por
    default — incluye conversaciones viejas sin la clave (backward-compat) y
    cid None/inexistente. Nunca lanza."""
    try:
        store = _load_store()
        conv = _find(store, cid)
        if not conv:
            return "chat"
        return conv.get("kind") or "chat"
    except Exception as exc:
        logger.warning("get_kind falló: %s", exc)
        return "chat"


def append_message(cid: Optional[str], role: str, content: str) -> None:
    """Agrega UN mensaje suelto a la conversación `cid` (para persistir la
    apertura del Coach Deportivo, que no es un par pregunta/respuesta). Con
    lock, atómica, actualiza `updated`, respeta el cap por conversación. NO
    toca el título. cid inexistente/None -> no-op (nunca lanza)."""
    try:
        with _STORE_LOCK:
            store = _load_store()
            conv = _find(store, cid)
            if conv is None:
                logger.warning("append_message: conversación %r no existe; ignorado.", cid)
                return
            ts = _now_iso()
            msgs = conv.setdefault("messages", [])
            msgs.append({"role": role, "content": content, "ts": ts})
            conv["updated"] = ts
            _save_store(store)
    except Exception as exc:
        logger.error("append_message falló: %s", exc)


def get_context(cid: Optional[str], n: int = 10) -> list[dict]:
    """Últimos n mensajes de ESA conversación (para ask_coach). AISLADO: nunca
    incluye mensajes de otra conversación. cid inexistente/None -> []."""
    try:
        store = _load_store()
        conv = _find(store, cid)
        if not conv:
            return []
        msgs = conv.get("messages") or []
        return [dict(m) for m in msgs[-n:]] if n else []
    except Exception as exc:
        logger.warning("get_context falló: %s", exc)
        return []


def append_turn(cid: Optional[str], question: str, answer: str) -> str:
    """Agrega el turno (user + assistant) a la conversación `cid`. Si `cid` es
    None o no existe, crea una nueva conversación. Setea el título en el primer
    turno si estaba vacío/default. Devuelve el id de la conversación usada.
    Escritura atómica, cap aplicado. Nunca lanza."""
    try:
        with _STORE_LOCK:
            store = _load_store()
            conv = _find(store, cid)
            if conv is None:
                now = _now_iso()
                conv = {"id": _new_id(), "title": _DEFAULT_TITLE, "created": now, "updated": now, "messages": []}
                store.setdefault("conversations", []).append(conv)

            ts = _now_iso()
            msgs = conv.setdefault("messages", [])
            was_empty = len(msgs) == 0
            msgs.append({"role": "user", "content": question, "ts": ts})
            msgs.append({"role": "assistant", "content": answer, "ts": ts})
            conv["updated"] = ts
            if was_empty or not (conv.get("title") or "").strip():
                conv["title"] = _title_from_first_message(msgs)

            store["active_id"] = conv["id"]
            _save_store(store)
            return conv["id"]
    except Exception as exc:
        logger.error("append_turn falló: %s", exc)
        return cid or ""


def delete_conversation(cid: str) -> None:
    """Borra SOLO esa conversación. Si era la activa, la activa pasa a None
    (el front/endpoint decide la siguiente). Nunca lanza."""
    try:
        with _STORE_LOCK:
            store = _load_store()
            convs = store.get("conversations") or []
            store["conversations"] = [c for c in convs if c.get("id") != cid]
            if store.get("active_id") == cid:
                store["active_id"] = None
            _save_store(store)
    except Exception as exc:
        logger.error("delete_conversation falló: %s", exc)


def set_active(cid: Optional[str]) -> None:
    try:
        with _STORE_LOCK:
            store = _load_store()
            store["active_id"] = cid
            _save_store(store)
    except Exception as exc:
        logger.error("set_active falló: %s", exc)


def get_active_id() -> Optional[str]:
    try:
        return _load_store().get("active_id")
    except Exception as exc:
        logger.warning("get_active_id falló: %s", exc)
        return None


def clear_all() -> None:
    """Borra TODAS las conversaciones (backward-compat de DELETE /api/coach/history)."""
    try:
        with _STORE_LOCK:
            _save_store(_empty_store())
    except Exception as exc:
        logger.error("clear_all falló: %s", exc)


# ── Backward-compat (API v1, deprecada) ──────────────────────────────────────

def load_history() -> list[dict]:
    """DEPRECADO: mensajes de la conversación ACTIVA (lista plana). [] si no
    hay activa o no existe. Nunca lanza."""
    try:
        store = _load_store()
        active_id = store.get("active_id")
        conv = _find(store, active_id)
        if not conv:
            # Sin activa explícita: caer a la más reciente si existe alguna.
            convs = store.get("conversations") or []
            if not convs:
                return []
            convs = sorted(convs, key=lambda c: c.get("updated") or "", reverse=True)
            conv = convs[0]
        return list(conv.get("messages") or [])
    except Exception as exc:
        logger.warning("load_history (compat) falló: %s", exc)
        return []


def clear() -> None:
    """DEPRECADO: alias de clear_all()."""
    clear_all()
