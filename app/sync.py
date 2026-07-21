"""
sync.py — orquesta: FUENTES CONECTADAS (app/sources) -> fusión (app/merge) ->
motor (build_dataset/bodyage) -> guarda JSON.

Desde Fase 6A es multi-fuente: itera TODAS las fuentes en `profile.effective_sources()`,
hace fetch() de cada una (tolerante a fallos por-fuente — una fuente rota no tumba el
sync de las demás), funde los dicts crudos vía app/merge.py::merge_sources, y alimenta
el resultado a build_dataset(). Con 1 sola fuente conectada (caso de hoy), el
comportamiento es idéntico al sync.py de Fase 5A (merge_sources con 1 sola entrada es
passthrough exacto).

Si TODAS las fuentes fallan, re-lanza la excepción de la PRIMERA fuente de la lista
(mantiene compat con el manejo existente de /api/sync en main.py, que espera
TokenExpired/NoToken re-lanzadas). Si AL MENOS UNA tiene éxito, el sync es exitoso.

Ronda 1 (robustez): run_sync() es single-flight — un `threading.Lock` a nivel de
módulo (_SYNC_LOCK) asegura que /api/sync, /api/ingest y el job del scheduler nunca
corran dos syncs en paralelo (entre otras cosas, evita quemar el refresh_token
rotatorio de WHOOP con dos refresh concurrentes). Si el lock ya está tomado, lanza
SyncInProgress de inmediato (no bloquea esperando). El lock se libera SIEMPRE
(éxito, excepción, o el `raise errors[sources[0]]`) vía try/finally.
"""
import json
import datetime
import logging
import threading
from pathlib import Path
from typing import Optional

from app.config import settings
from app.auth import TokenExpired, NoToken  # re-exportadas para callers/tests
from app.scoring import build_dataset
from app.bodyage import compute_body_age, compute_body_age_stable
from app.sources import get_source
from app.merge import merge_sources, last_merge_info
from app.fsutil import atomic_write_text
from app import profile as _profile

logger = logging.getLogger("vitals.sync")

_SYNC_LOCK = threading.Lock()


class SyncInProgress(Exception):
    """Ya hay un run_sync() en curso en este proceso (single-flight)."""


# Sentinel (deuda R2, aislamiento de tests): None en reposo -> el accessor
# resuelve SIEMPRE contra settings.DATA_DIR en runtime, así un
# importlib.reload(sync) nunca re-liga esta constante a una ruta congelada de
# import-time (que podía apuntar al data/ real). Override SOLO para tests
# (patch.object(sync, "DATA_OUT", ruta) — sigue funcionando idéntico, ver
# docstring de _data_out_path). `Optional[Path]` a propósito: el venv es
# Python 3.9.6, `Path | None` en anotación módulo-nivel revienta el import.
DATA_OUT: Optional[Path] = None  # legacy — usado si userctx no está activo


def _data_out_path() -> Path:
    """Ruta a health_compact.json del usuario activo (Fase 8D, paso D3:
    household). Dentro de run_sync() esto se llama SIEMPRE con el contextvar
    ya fijado por el caller (middleware de /api/sync, o el loop por-usuario
    del scheduler — ver scheduler.py) — is_context_active() será casi siempre
    True en producción. Fuera de contexto (tests preexistentes que
    monkeypatchean DATA_OUT directamente), usa DATA_OUT tal cual si fue
    fijado explícitamente (patch.object/monkeypatch); si no, resuelve en
    RUNTIME contra settings.DATA_DIR (reload-proof — ver comentario del
    sentinel arriba). Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "health_compact.json"
    except Exception:
        pass
    if DATA_OUT is not None:   # override explícito de un test
        return DATA_OUT
    return settings.DATA_DIR / "health_compact.json"   # resolución RUNTIME

# HealthKit puede aportar más historial que el default genérico (45d) sin afectar a las
# demás fuentes (Google ya trae casi todo su histórico sin límite de fecha; Oura/WHOOP sí
# acotan sus queries HTTP por 'days' — no queremos pedirles más de lo que ya piden hoy).
_SOURCE_WINDOW_OVERRIDE = {"healthkit": 365}


def run_sync(days: int = 45):
    """Sincroniza TODAS las fuentes conectadas del perfil y actualiza data/health_compact.json.
    Fusiona los fetches exitosos vía merge_sources(). Si TODAS las fuentes fallan, re-lanza
    la excepción de la PRIMERA fuente de la lista (TokenExpired/NoToken/Exception) — el
    caller debe manejarla igual que antes.

    Single-flight: si ya hay un run_sync() en curso en este proceso, lanza
    SyncInProgress de inmediato en vez de bloquear o correr en paralelo."""
    if not _SYNC_LOCK.acquire(blocking=False):
        raise SyncInProgress("Ya hay un sync en curso.")
    try:
        sources = _profile.effective_sources()

        fetched: dict = {}
        errors: dict = {}
        for name in sources:
            try:
                source_days = _SOURCE_WINDOW_OVERRIDE.get(name, days)
                fetched[name] = get_source(name).fetch(source_days)
            except (TokenExpired, NoToken) as e:
                errors[name] = e
                logger.warning("Fuente '%s' falló en sync (token): %s", name, e)
            except Exception as e:
                errors[name] = e
                logger.warning("Fuente '%s' falló en sync: %s", name, e)

        if not fetched:
            # Todas las fuentes fallaron -> re-lanza la excepción de la PRIMERA de la lista.
            raise errors[sources[0]]

        data = merge_sources(fetched)
        # Probe de "HRV matutina" (aditivo, best-effort, NO toca el motor): registra
        # la HRV por fuente + canónica de este momento, para medir si la HRV de la
        # mañana (cron 9am, Google Health fresco) es más estable que la del final
        # del día. Envuelto para que un fallo del probe jamás tumbe el sync.
        try:
            from app import hrv_trail as _hrv_trail
            _hrv_trail.record_snapshot(fetched, data)
        except Exception as _exc:
            logger.warning("hrv_trail.record_snapshot falló (no bloqueante): %s", _exc)
        # Ronda 5: umbral de sueño único, configurable por perfil (default 480 =
        # comportamiento idéntico a antes). Cascada de effective() ya maneja
        # profile.json -> .env -> default; envuelto en try/except por si el
        # perfil está corrupto (nunca debe tumbar el sync).
        try:
            sleep_target_min = int(_profile.effective("sleep_target_min") or 480)
        except Exception:
            sleep_target_min = 480
        # Sleep-goal-vs-need: OBJETIVO personal de sueño (distinto de la NECESIDAD
        # de arriba). Mismo try/except defensivo — un perfil corrupto jamás debe
        # tumbar el sync. Se lee AQUÍ pero se inyecta DESPUÉS de build_dataset()
        # (ver abajo) para que scoring.py nunca lo vea y el golden no se mueva.
        try:
            sleep_goal_min = int(_profile.effective_sleep_goal() or 480)
        except Exception:
            sleep_goal_min = 480
        dataset = build_dataset(**data, sleep_target_min=sleep_target_min)
        # Ronda 3: proveniencia de la fusión (qué fuente ganó HRV, cuántas fuentes se
        # fundieron). Aditivo y DESPUÉS de build_dataset() -> el golden (que llama
        # build_dataset directo en tests) nunca ve esta clave.
        dataset["summary"]["merge_info"] = last_merge_info()
        # Sleep-goal-vs-need: mismo patrón que merge_info -- aditivo, DESPUÉS de
        # build_dataset(), NUNCA pasado como parámetro al motor.
        dataset["summary"]["sleep_goal_min"] = sleep_goal_min

        # ── Edad corporal — perfil con cascada: profile.json → .env → default ──
        try:
            bd = _profile.effective("birthdate")
            waist = _profile.effective("waist_cm")
            sex = _profile.effective("sex")
            age = _profile.current_age()
        except Exception:
            bd = settings.BIRTHDATE
            waist = settings.WAIST_CM
            sex = settings.SEX
            if bd:
                by = datetime.date.fromisoformat(bd)
                td = datetime.date.today()
                age = td.year - by.year - ((td.month, td.day) < (by.month, by.day))
            else:
                age = 0
        if bd and waist:
            # Ronda 5: sleep_penalty_h derivado del mismo sleep_target_min que ya
            # alimentó build_dataset — SHORT_NIGHT = target-60, y el penalty de
            # bodyage usa (target-60)/60 horas (con target=480 → 7.0h, idéntico
            # a antes). Los tres consumidores del umbral (recovery, insights,
            # bodyage) se mueven juntos si el perfil cambia su target.
            sleep_penalty_h = (sleep_target_min - 60) / 60.0
            dataset["summary"]["bodyage"] = compute_body_age(
                dataset["days"], dataset.get("exercises", []),
                age, float(waist), sex, sleep_penalty_h=sleep_penalty_h
            )
            ba = dataset["summary"]["bodyage"]
            print(f"  Edad corporal: fitness {ba['fitness_age']} · compuesta {ba['body_age']} "
                  f"(VO2máx {ba['vo2max']}, {ba['category']})")

        # ── Fase 8D (paso D2): Healthspan / Pace of Aging — aditivo, best-effort
        # TOTAL. Recomputa body age por ventanas trailing de 90d (paso mensual)
        # reusando compute_body_age SIN tocar su fórmula. <120 días de historial
        # -> None (gate duro dentro de compute_healthspan). Un fallo aquí NUNCA
        # debe tumbar el sync — mismo patrón que headline/report/notify abajo.
        try:
            from app import healthspan as _healthspan
            profile_dict_hs = _profile.effective_profile_dict()
            dataset["summary"]["healthspan"] = _healthspan.compute_healthspan(
                dataset["days"], dataset.get("exercises", []), profile_dict_hs
            )
        except Exception as exc:
            logger.warning("Healthspan falló en este sync (best-effort, ignorado): %s", exc)
            dataset["summary"]["healthspan"] = None

        # ── Edad corporal ESTABLE (modelo dos-números estilo WHOOP) ─────────
        # Roadmap edad-corporal-estable, paso 3. Aditivo, best-effort TOTAL —
        # un fallo aquí NUNCA debe tumbar el sync (mismo patrón que healthspan
        # arriba). Corre DESPUÉS del bloque de healthspan para poder leer su
        # `pace` ya escrito en summary.healthspan (orden importa: pace se
        # copia de ahí, no se recalcula).
        # Requiere que el bloque de bodyage instantáneo (arriba) haya corrido
        # (bd y waist presentes) — si no hay `bd`/`waist` o el instantáneo no
        # se pudo computar, no hay `ba` que actualizar y este bloque se salta
        # entero (deja solo el instantáneo, sin campos stable/pace).
        if bd and waist and dataset["summary"].get("bodyage") is not None:
            try:
                ba = dataset["summary"]["bodyage"]
                stable = compute_body_age_stable(
                    dataset["days"], dataset.get("exercises", []),
                    bd, float(waist), sex, sleep_penalty_h=sleep_penalty_h, window=30
                )
                ba.update(stable)  # añade body_age_stable/n_days_stable/stable_confidence
                # pace del velocímetro: copia desde summary.healthspan si existe
                # (best-effort, None-safe con <120 días de historial).
                hs = dataset["summary"].get("healthspan")
                ba["pace"] = (hs or {}).get("pace") if hs else None
            except Exception as exc:
                logger.warning("body_age_stable falló en este sync (best-effort, ignorado): %s", exc)

        # ── Frescura de Alertas + Coach (Paso 4): vo2max ANTERIOR para el evento
        # de cambio de changes.py. Se lee del health_compact.json VIEJO (antes
        # de sobrescribirlo abajo) — best-effort, None-safe: si no existe o está
        # corrupto, simplemente no habrá evento de vo2max esta vez (nunca rompe
        # el sync).
        data_out = _data_out_path()
        try:
            if data_out.exists():
                old_dataset = json.loads(data_out.read_text(encoding="utf-8"))
                prev_vo2max = ((old_dataset.get("summary") or {}).get("bodyage") or {}).get("vo2max")
                if prev_vo2max is not None:
                    dataset["summary"]["_prev_vo2max"] = prev_vo2max
        except Exception as exc:
            logger.warning("No pude leer vo2max previo para changes.py: %s", exc)

        data_out.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(data_out, json.dumps(dataset, indent=2, ensure_ascii=False))
        print(f"  datos -> {data_out.name} ({dataset['summary']['n_days']} días)")

        # ── Frescura de Alertas + Coach (Paso 4): titular IA cacheado por firma.
        # Best-effort TOTAL — envuelto en try/except propio, además de que
        # coach_headline.maybe_regenerate() ya se protege internamente. Un fallo
        # aquí (CLI ausente, timeout, excepción de detect_changes) NUNCA debe
        # tumbar ni ralentizar perceptiblemente run_sync() más allá de la
        # llamada al CLI cuando la firma cambió. Esto es lo ÚNICO que invoca al
        # CLI de Claude para el titular — la ruta GET / y el auto-reload SOLO
        # leen data/coach_headline.json (coach_headline.get_headline()), cero
        # subprocess en ese path.
        try:
            from app import changes as _changes
            from app import coach_headline as _coach_headline
            from app.insights import evaluate as _evaluate_insights_headline
            locale = _profile.effective("locale") or "es"
            change_events = _changes.detect_changes(dataset, locale)
            # F3 (roadmap vitals-illness-proactivo): el titular necesita saber si
            # hay alertas activas (insights severity=='alert') para no dar luz
            # verde encima de una. Cálculo propio, independiente del que hace el
            # bloque de notificaciones más abajo — mismo patrón de aislamiento
            # best-effort que ya usa cada bloque de este método (un fallo acá
            # no debe afectar a los demás).
            insights_for_headline = _evaluate_insights_headline(dataset, locale)
            _coach_headline.maybe_regenerate(dataset, change_events, locale, insights_for_headline)
        except Exception as exc:
            logger.warning("Titular del coach (headline) falló en este sync (best-effort, ignorado): %s", exc)

        # ── Fase 8B (paso B6): informe narrativo semanal/mensual, cacheado por
        # firma (period_key, locale). Best-effort TOTAL — igual que el titular
        # de arriba, un fallo aquí (CLI ausente, timeout, journal corrupto)
        # NUNCA debe tumbar ni ralentizar run_sync() más allá de la llamada al
        # CLI cuando la firma cambió. Único lugar que invoca al CLI para el
        # informe — GET /api/report SOLO lee data/reports.json.
        try:
            from app import report as _report
            from app import journal as _journal
            locale = _profile.effective("locale") or "es"
            journal_log = _journal.load_journal()
            _report.maybe_regenerate_reports(dataset, journal_log, locale)
        except Exception as exc:
            logger.warning("Informe narrativo (report) falló en este sync (best-effort, ignorado): %s", exc)

        # ── Fase 8C (paso C3): notificaciones push (ntfy/Telegram) — morning
        # brief 1×/día + alertas de insights nuevas. Best-effort TOTAL, igual
        # que headline/report arriba: un fallo aquí (red caída, canal no
        # configurado, insights corruptos) NUNCA debe tumbar ni ralentizar
        # run_sync() más allá de los timeouts HTTP cortos (10s) de notify.py.
        # Único lugar que dispara notificaciones — nunca desde un GET.
        try:
            from app import notify as _notify
            from app.insights import evaluate as _evaluate_insights
            locale = _profile.effective("locale") or "es"
            profile_dict = _profile.effective_profile_dict()
            insights_list = _evaluate_insights(dataset, locale)
            _notify.notify_after_sync(dataset, insights_list, profile_dict, locale)
        except Exception as exc:
            logger.warning("Notificaciones (notify) fallaron en este sync (best-effort, ignorado): %s", exc)

        return dataset
    finally:
        _SYNC_LOCK.release()
