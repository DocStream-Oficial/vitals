"""
userctx.py — Household multi-perfil (Fase 8D, paso D3). EL PASO MÁS RIESGOSO
del roadmap: centraliza la resolución de "qué usuario es este request" y "en
qué carpeta viven sus datos", para que TODOS los módulos de persistencia dejen
de escribir en `data/<archivo>.json` fijo y empiecen a escribir en
`data/users/<uid>/<archivo>.json`.

Diseño (deliberadamente simple, sin ORM ni DB):
- Registro global `data/users.json` = {"users": [{"id","name","color"}, ...]}.
  Archivo GLOBAL (no migra a ninguna carpeta de usuario) — junto con
  `ingest_token.json`, son los ÚNICOS dos archivos que quedan a nivel data/
  raíz (roadmap §Arquitectura, D3).
- `user_dir(uid)` = data/users/<uid>/ — TODOS los demás archivos (profile.json,
  token*.json, health_compact.json, journal_log.json, cycle_log.json,
  labs_log.json, coach_*.json, reports.json, notify_state.json,
  healthkit_ingest.json, ecg/) viven ahí.
- `resolve_user(request)`: header `X-Vitals-User` → cookie `vitals_user` →
  único usuario registrado → "default". Nunca lanza, nunca 401/403 — un
  usuario desconocido en el header simplemente cae a "default" (fail-open,
  friendlier para single-user que fail-closed).
- `contextvar` (`_current_uid`) fijado por un middleware FastAPI en CADA
  request (ver main.py) — los módulos de persistencia leen `current_uid()` /
  `current_data_dir()` en el momento de load/save, NO al importar (import-time
  sería un solo valor congelado para todo el proceso — inservible para
  multi-usuario).
- Fuera de un request HTTP (scheduler, tests, scripts) `current_uid()` cae a
  "default" si el contextvar no fue fijado — así el scheduler puede fijarlo
  explícitamente por-usuario en cada iteración (ver sync loop en scheduler.py)
  sin que el resto del código sepa que existe household.

Migración automática (`migrate_legacy_layout_if_needed`): si existe layout
viejo (archivos sueltos en data/, ej. profile.json) Y NO existe data/users/,
mueve TODO a data/users/default/ y crea el registro. Idempotente: si
data/users/ ya existe, es no-op inmediato (nunca re-mueve, nunca pisa).
Nunca lanza — un fallo de migración deja el layout viejo intacto (peor caso:
la instancia sigue funcionando en modo legacy hasta que se resuelva a mano,
NUNCA pérdida de datos).
"""
from __future__ import annotations

import contextvars
import json
import logging
import shutil
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vitals.userctx")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

DEFAULT_UID = "default"

_USERS_REGISTRY_FILE = _DATA_DIR / "users.json"
_USERS_SUBDIR = "users"

# Archivos GLOBALES que NO migran a data/users/<uid>/ (roadmap §Arquitectura).
GLOBAL_FILES = ("users.json", "ingest_token.json")

# Nombres de colores/paleta simple para avatares nuevos (determinista, sin
# dependencias). Se asigna por índice de creación, ciclando si hay más de 8.
_AVATAR_COLORS = ["#30D158", "#5E5CE6", "#0A84FF", "#FF9F0A", "#FF375F", "#BF5AF2", "#64D2FF", "#9D8DF5"]

# Contextvar del usuario activo para ESTE request/tarea. Default None (NO el
# string "default") para poder distinguir dos casos que los módulos de
# persistencia necesitan tratar DISTINTO:
#   - Nunca se fijó (código fuera de un request/tarea household-aware: la
#     mayoría de los tests preexistentes de Fase B/C que monkeypatchean rutas
#     legacy directamente, scripts sueltos, etc.) -> is_context_active()=False
#     -> los módulos usan su ruta legacy tal cual (compat total, cero cambio
#     de comportamiento para código que no sabe que existe household).
#   - Se fijó explícitamente (middleware de main.py en cada request real, o un
#     test de household que llama set_current_uid) -> is_context_active()=True
#     -> los módulos usan current_data_dir() (data/users/<uid>/...).
_current_uid: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("vitals_current_uid", default=None)


def _data_root() -> Path:
    """Raíz de datos (soporta override en tests vía monkeypatch de settings)."""
    return _DATA_DIR


def _users_registry_path() -> Path:
    return _data_root() / "users.json"


# ── Registro de usuarios (data/users.json) ──────────────────────────────────

def _empty_registry() -> dict:
    return {"users": []}


def load_registry() -> dict:
    """Lee data/users.json → {"users": [...]}. Corrupto/ausente -> vacío
    (nunca lanza)."""
    empty = _empty_registry()
    try:
        path = _users_registry_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            return empty
        data.setdefault("users", [])
        if not isinstance(data.get("users"), list):
            data["users"] = []
        return data
    except Exception as exc:
        logger.warning("Error leyendo users.json: %s", exc)
        return empty


def save_registry(d: dict) -> None:
    """Guarda data/users.json con escritura ATÓMICA. Nunca lanza."""
    try:
        from app.fsutil import atomic_write_text
        path = _users_registry_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("users", [])
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando users.json: %s", exc)


def list_users() -> list[dict]:
    """Usuarios registrados [{id,name,color}]. [] si no hay ninguno todavía
    (instalación fresh antes de la migración/primer arranque)."""
    return [u for u in load_registry().get("users", []) if isinstance(u, dict) and u.get("id")]


def get_user(uid: str) -> Optional[dict]:
    for u in list_users():
        if u.get("id") == uid:
            return u
    return None


def user_exists(uid: str) -> bool:
    return get_user(uid) is not None


def _slugify_uid(name: str) -> str:
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return s[:24] or "user"


def add_user(name: str, uid: Optional[str] = None, color: Optional[str] = None) -> Optional[dict]:
    """Registra un nuevo usuario (household, D3). id autogenerado desde el
    nombre (slug único) si no se da explícito. Crea su user_dir(uid). Devuelve
    el usuario creado, o None si el nombre es inválido. Nunca lanza."""
    try:
        name = (name or "").strip()[:60]
        if not name:
            return None
        reg = load_registry()
        users = reg.get("users", [])
        existing_ids = {u.get("id") for u in users if isinstance(u, dict)}

        if uid:
            new_uid = uid.strip()[:24]
            # Blindaje path-traversal: un uid explícito debe ser un segmento de
            # ruta seguro tal cual (sin '/', '\\' ni '..'); si _sanitize_uid lo
            # alteraría, se rechaza en vez de aceptar un id que no coincidiría
            # con su propia carpeta.
            if not new_uid or new_uid in existing_ids or _sanitize_uid(new_uid) != new_uid:
                return None
        else:
            base = _slugify_uid(name)
            new_uid = base
            i = 2
            while new_uid in existing_ids:
                new_uid = f"{base}_{i}"
                i += 1

        idx = len(users)
        assigned_color = color or _AVATAR_COLORS[idx % len(_AVATAR_COLORS)]
        user = {"id": new_uid, "name": name, "color": assigned_color}
        users.append(user)
        reg["users"] = users
        save_registry(reg)
        user_dir(new_uid).mkdir(parents=True, exist_ok=True)
        return user
    except Exception as exc:
        logger.error("add_user falló: %s", exc)
        return None


def delete_user(uid: str, delete_data: bool = False) -> bool:
    """Quita al usuario del registro. Si delete_data=True, además borra su
    carpeta de datos (destructivo — el caller de main.py debe exigir
    confirmación explícita, ver roadmap D3 "DELETE con confirmación").
    Idempotente. Nunca lanza."""
    try:
        reg = load_registry()
        users = [u for u in reg.get("users", []) if isinstance(u, dict)]
        new_users = [u for u in users if u.get("id") != uid]
        reg["users"] = new_users
        save_registry(reg)
        if delete_data:
            d = user_dir(uid)
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        return True
    except Exception as exc:
        logger.error("delete_user falló: %s", exc)
        return False


# ── Rutas por usuario ────────────────────────────────────────────────────────

def _sanitize_uid(uid: str) -> str:
    """Normaliza un uid a un ÚNICO segmento de ruta seguro dentro de
    data/users/. Blinda contra path traversal (Fase 8D, riesgo #1 / auditoría):
    un uid con separadores de ruta o componentes '..' NUNCA puede escapar de
    data/users/<uid>/ — de lo contrario `DELETE /api/users/%2e%2e?delete_data=true`
    haría `rmtree(data/)` (borrado total) y un header/registro malicioso podría
    leer/escribir fuera del árbol del usuario.

    Regla: se toma solo el basename (descarta cualquier '/' o '\\'), y si el
    resultado queda vacío o es un componente de traversal ('.', '..'), cae a
    DEFAULT_UID. Deliberadamente conservador — los uids legítimos vienen de
    `_slugify_uid` (solo [a-z0-9_]) o del literal 'default'."""
    raw = (uid or DEFAULT_UID).strip()
    # basename: descarta todo hasta el último separador POSIX o Windows.
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base or base in (".", ".."):
        return DEFAULT_UID
    return base


def user_dir(uid: str) -> Path:
    """Carpeta de datos del usuario dado: data/users/<uid>/. No crea el
    directorio (los callers de persistencia ya hacen mkdir(parents=True) antes
    de escribir, patrón existente en todo el repo). El uid se sanitiza a un
    único segmento seguro (ver _sanitize_uid) para blindar contra path
    traversal — es el ÚNICO chokepoint por el que pasan TODAS las rutas
    por-usuario."""
    return _data_root() / _USERS_SUBDIR / _sanitize_uid(uid)


def users_root() -> Path:
    return _data_root() / _USERS_SUBDIR


# ── Resolución de usuario por request ────────────────────────────────────────

def resolve_user(header_user: Optional[str] = None, cookie_user: Optional[str] = None) -> str:
    """Resuelve el uid activo para un request:
        1. header X-Vitals-User (si el uid existe en el registro)
        2. cookie vitals_user (si el uid existe en el registro)
        3. único usuario registrado (si solo hay uno)
        4. "default"

    Fail-open deliberado (roadmap D3, riesgo #1): un uid en header/cookie que
    NO existe en el registro se ignora (no 401/403) y se sigue la cascada —
    preferible a romper el sync de un dispositivo con config vieja/typo antes
    que dejar al usuario sin datos. Nunca lanza."""
    try:
        users = list_users()
        valid_ids = {u["id"] for u in users}

        if header_user:
            h = header_user.strip()
            if h and h in valid_ids:
                return h

        if cookie_user:
            c = cookie_user.strip()
            if c and c in valid_ids:
                return c

        if len(users) == 1:
            return users[0]["id"]

        return DEFAULT_UID
    except Exception as exc:
        logger.warning("resolve_user falló (degradando a default): %s", exc)
        return DEFAULT_UID


# ── Contextvar (fijado por middleware, leído por los módulos de persistencia) ─

def set_current_uid(uid: str) -> contextvars.Token:
    """Fija el uid activo para el contexto actual (request/tarea). Devuelve el
    token para poder hacer reset() explícito (patrón estándar de contextvars,
    usado por el middleware para limpiar al final del request)."""
    return _current_uid.set(uid or DEFAULT_UID)


def reset_current_uid(token: contextvars.Token) -> None:
    try:
        _current_uid.reset(token)
    except Exception:
        pass  # best-effort: un reset fallido no debe tumbar el request


def is_context_active() -> bool:
    """True si ALGÚN código (middleware de request, scheduler, o un test de
    household) ya fijó explícitamente el uid activo para este contexto —
    distingue de "nadie llamó set_current_uid todavía", que es el estado por
    defecto de cualquier test/script que no sabe que existe household."""
    return _current_uid.get() is not None


def should_use_household_paths() -> bool:
    """Señal COMBINADA que usan los módulos de persistencia para decidir entre
    su ruta legacy (data/<archivo>.json) y la ruta por-usuario
    (data/users/<uid>/<archivo>.json):

        is_context_active() AND users_root().exists()

    El primer factor solo (is_context_active()) NO basta: el middleware de
    main.py fija el contextvar en TODO request HTTP, incluido cada uno de los
    ~1200 tests preexistentes que usan TestClient sin haber migrado ni creado
    ningún usuario — is_context_active() sería True ahí también, y rompería
    el monkeypatch directo de rutas legacy (_PROFILE_FILE, DATA_OUT, etc.) que
    esos tests dependen.

    El segundo factor (users_root().exists()) es la señal real de "esta
    instancia YA es household": data/users/ solo existe tras
    migrate_legacy_layout_if_needed() (startup real) o tras un test que
    explícitamente registra usuarios (add_user). Una instalación/test fresh
    sin ningún usuario registrado sigue usando las rutas legacy tal cual —
    comportamiento IDÉNTICO al de antes de esta fase, cero tests rotos."""
    try:
        return is_context_active() and users_root().exists()
    except Exception:
        return False


def current_uid() -> str:
    """uid activo del contexto actual. Fuera de un request/tarea que lo haya
    fijado explícitamente (tests, scripts, importación directa de módulos),
    cae a DEFAULT_UID — preserva el comportamiento single-user de siempre para
    cualquier código que no pase por el middleware/scheduler household-aware."""
    uid = _current_uid.get()
    return uid or DEFAULT_UID


def current_data_dir() -> Path:
    """Carpeta de datos del usuario activo del contexto actual."""
    return user_dir(current_uid())


# ── Migración automática desde layout viejo (D3, el paso más riesgoso) ──────

# Archivos/carpetas del layout viejo que se mueven a data/users/default/ en la
# migración. Deliberadamente explícito (whitelist) en vez de "todo lo que no
# esté en GLOBAL_FILES" — más auditable y menos sorpresas si algún día se
# agrega un archivo global nuevo sin actualizar esta lista a la vez.
_LEGACY_ENTRIES = (
    "profile.json", "token.json", "healthkit_ingest.json",
    "health_compact.json", "journal_log.json", "cycle_log.json",
    "labs_log.json", "coach_store.json", "coach_conversations.json",
    "coach_history.json", "coach_history.json.v1.bak",
    "coach_headline.json", "reports.json", "notify_state.json",
    "ecg", "vitals_raw",
)


def _legacy_layout_present(root: Path) -> bool:
    """True si CUALQUIER archivo/carpeta del layout viejo existe en la raíz de
    data/ — señal de que esta instancia nunca pasó por household."""
    return any((root / entry).exists() for entry in _LEGACY_ENTRIES)


def migrate_legacy_layout_if_needed() -> Optional[str]:
    """Migración automática en startup (roadmap D3): si existe layout viejo Y
    NO existe data/users/, mueve TODO a data/users/default/ y registra ese
    usuario. Idempotente — si data/users/ YA existe (instalación ya migrada,
    o instalación fresh de household desde cero), es no-op inmediato.

    Devuelve un mensaje de log human-readable si migró algo, o None si no hizo
    nada (log claro para quien opere el servidor — roadmap D3 "log claro").

    Nunca lanza: cualquier error a mitad de la migración se loguea y se
    detiene ahí (mejor un estado parcial identificable + backup de data/ que
    el roadmap YA exige antes de desplegar D, que perder datos silenciosamente).
    """
    root = _data_root()
    users_dir = users_root()

    if users_dir.exists():
        return None  # ya migrado (o household fresh) — no-op

    if not _legacy_layout_present(root):
        # Instalación fresh sin NINGÚN dato viejo: NO crear data/users/ ni
        # registrar "default" todavía. Crítico (bug real encontrado durante el
        # desarrollo de este mismo paso D3): crear data/users/ aquí activaría
        # should_use_household_paths()=True para TODO request futuro —
        # incluyendo el primer /api/sync real de una instalación nueva, que
        # entonces escribiría en data/users/default/ en vez de data/ (rompiendo
        # la expectativa de "single-user por default" para instalaciones que
        # nunca configuraron household). El sistema permanece en modo legacy-
        # compat total (single-user, data/<archivo>.json) hasta que:
        #   (a) se detecte legacy real en un arranque futuro (rama de abajo), o
        #   (b) el usuario cree explícitamente un usuario vía POST /api/users
        #       (add_user ya crea users_root() al primer alta).
        # Nunca lanza — esta rama es deliberadamente un no-op silencioso.
        return None

    try:
        default_dir = user_dir(DEFAULT_UID)
        default_dir.mkdir(parents=True, exist_ok=True)

        moved = []
        for entry in _LEGACY_ENTRIES:
            src = root / entry
            if not src.exists():
                continue
            dst = default_dir / entry
            if dst.exists():
                # Ya hay algo en el destino (migración parcial previa
                # interrumpida) — no pisar, no perder datos. Se loguea y se
                # sigue con las demás entradas.
                logger.warning(
                    "userctx: migración omite '%s' — el destino %s ya existe "
                    "(posible migración parcial previa; revisar a mano).",
                    entry, dst,
                )
                continue
            shutil.move(str(src), str(dst))
            moved.append(entry)

        if not user_exists(DEFAULT_UID):
            reg = load_registry()
            users = reg.get("users", [])
            users.append({"id": DEFAULT_UID, "name": "Default", "color": _AVATAR_COLORS[0]})
            reg["users"] = users
            save_registry(reg)

        msg = (
            f"userctx: migración de layout viejo -> data/users/{DEFAULT_UID}/ completada. "
            f"Movido: {moved}"
        )
        logger.info(msg)
        return msg
    except Exception as exc:
        logger.error(
            "userctx: migración de layout viejo FALLÓ a medio camino (%s). "
            "El layout viejo puede haber quedado parcialmente movido — revisar "
            "data/ y data/users/default/ a mano. Los datos NO se borran en "
            "ningún paso de esta función (solo shutil.move dentro del mismo "
            "filesystem data/), así que no debería haber pérdida, pero sí "
            "puede requerir completar la migración manualmente.",
            exc,
        )
        return None
