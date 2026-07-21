"""
scheduler.py — APScheduler: job diario a SYNC_HOUR:00 + sync al arranque (best-effort).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings

logger = logging.getLogger("vitals.scheduler")

_scheduler: Optional[BackgroundScheduler] = None


def _sync_one_user(uid: Optional[str]):
    """Corre run_sync() para UN usuario (o en modo legacy si uid=None — no
    fija ningún contextvar, comportamiento IDÉNTICO al de antes de Fase 8D).
    Atrapa TokenExpired/NoToken y registra estado, no 500. Si ya hay un sync
    en curso para ESTE proceso (single-flight de run_sync, Ronda 1 — el lock
    es de módulo, compartido entre usuarios: dos syncs de usuarios DISTINTOS
    tampoco corren en paralelo, ver nota en run_sync), sale limpio."""
    from app.sync import run_sync, SyncInProgress
    from app.auth import TokenExpired, NoToken
    from app import userctx as _userctx

    token = _userctx.set_current_uid(uid) if uid else None
    label = f"usuario '{uid}'" if uid else "instalación single-user"
    try:
        run_sync()
        logger.info(f"Sync automático completado ({label}).")
    except SyncInProgress:
        logger.info(f"Sync ya en curso — job saltado ({label}).")
    except TokenExpired:
        logger.warning(f"Sync automático: token expirado ({label}). Visita /auth/login para reconectar.")
    except NoToken:
        logger.warning(f"Sync automático: no hay token ({label}). Visita /auth/login.")
    except Exception as e:
        logger.error(f"Sync automático falló inesperadamente ({label}): {e}")
    finally:
        if token is not None:
            _userctx.reset_current_uid(token)


def _sync_job():
    """Job del scheduler (Fase 8D, paso D3: household-aware).

    Si la instancia YA migró a household (data/users/ existe con ≥1 usuario
    registrado), corre run_sync() por CADA usuario EN SECUENCIA (uno a la vez
    — el lock de single-flight de run_sync es de módulo/proceso, así que dos
    syncs concurrentes de usuarios distintos igual se serializan; el loop
    aquí solo garantiza el ORDEN y que un usuario no bloquea indefinidamente
    a los demás si su sync individual falla).

    Si NO hay household (instalación single-user de siempre, caso de hoy para
    el usuario y para cualquier instancia que nunca creó un segundo usuario), NO
    itera nada — llama _sync_one_user(None), que corre run_sync() SIN fijar
    ningún contextvar: comportamiento 100% idéntico al scheduler de antes de
    esta fase."""
    from app import userctx as _userctx

    try:
        if _userctx.users_root().exists():
            users = _userctx.list_users()
        else:
            users = []
    except Exception as exc:
        logger.warning(f"No pude listar usuarios para el scheduler (degradando a single-user): {exc}")
        users = []

    if not users:
        _sync_one_user(None)
        return

    for user in users:
        _sync_one_user(user.get("id"))


def _startup_sync():
    """Dispara un sync al arranque en un thread separado — no bloquea el boot."""
    t = threading.Thread(target=_sync_job, daemon=True, name="startup-sync")
    t.start()


def start_scheduler():
    """Inicia el BackgroundScheduler con el job diario y dispara el sync de arranque."""
    global _scheduler
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=settings.SYNC_HOUR, minute=0),
        id="daily_sync",
        name="Sync diario de Google Health",
        replace_existing=True,
    )
    # Probe de "HRV matutina": un segundo sync de NOCHE, para que el hrv_trail
    # capture de forma determinista el valor de la mañana (cron de SYNC_HOUR=9) Y
    # el del final del día — y así medir la deriva intradía de la HRV de Google
    # Health (pull). No afecta datos: el sync de noche solo re-jala/re-calcula
    # (idempotente). Configurable por env (default 22:00).
    _evening_hour = int(os.getenv("SYNC_EVENING_HOUR", "22"))
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=_evening_hour, minute=0),
        id="evening_sync",
        name="Sync de noche (probe HRV matutina)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler iniciado — sync diario a las %02d:00 + sync de noche a las %02d:00.",
        settings.SYNC_HOUR, _evening_hour,
    )
    # Sync best-effort al arranque
    _startup_sync()


def stop_scheduler():
    """Detiene el scheduler limpiamente."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido.")
