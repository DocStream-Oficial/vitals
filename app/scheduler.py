"""
scheduler.py — APScheduler: syncs periódicos (SYNC_HOURS, default 7-22 cada 3h)
+ sync al arranque (best-effort).
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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
    """Inicia el BackgroundScheduler con los jobs de sync y dispara el sync de arranque."""
    global _scheduler
    _scheduler = BackgroundScheduler()
    # Auditoría 23-jul (H2, verificado en vivo): con UN solo sync diario (9am), una
    # noche que Google aún no consolidaba a esa hora se quedaba PARCIAL en pantalla
    # todo el día (caso real: 171 min a las 09:01 -> 574 min con un sync manual a las
    # 16h; el dato completo llevaba horas en Google y nadie lo pedía). Fix: syncs
    # cada 3 horas en horario de vigilia. Idempotente y barato (Google es pull;
    # HealthKit fetch() solo reusa el último push). Sustituye al par daily+evening
    # anterior (el de 22h del probe HRV queda cubierto por esta malla).
    # Configurable por env: SYNC_HOURS="7,10,13,16,19,22" (CSV de horas locales).
    _hours = os.getenv("SYNC_HOURS", "7,10,13,16,19,22")
    _scheduler.add_job(
        _sync_job,
        trigger=CronTrigger(hour=_hours, minute=0),
        id="daily_sync",
        name="Sync periódico (Google pull + recompute)",
        replace_existing=True,
    )
    _scheduler.start()
    logger.info("Scheduler iniciado — syncs a las horas: %s.", _hours)
    # Sync best-effort al arranque
    _startup_sync()


def stop_scheduler():
    """Detiene el scheduler limpiamente."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler detenido.")
