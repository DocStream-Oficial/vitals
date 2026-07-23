"""
app/sources/healthkit.py — Adaptador HealthKit (Apple) que implementa Source con
semántica PUSH (Fase 5D-A).

HealthKit INVIERTE el flujo de las demás fuentes. Google/Oura/WHOOP *jalan*: el
servidor hace `fetch()` contra una API remota con OAuth. HealthKit vive on-device,
no hay API HTTP server-side que jalar. El flujo correcto es:

    app nativa iOS (Fase 5D-B, fuera de alcance aquí) lee HealthKit on-device
        → POST /api/ingest con un payload normalizado
        → HealthKitSource.ingest() lo normaliza+persiste
        → build_dataset(**data) → bodyage → health_compact.json

Por eso:
  - build_auth_url / exchange_code lanzan NotImplementedError (no hay OAuth web —
    HealthKit se autoriza on-device con HKHealthStore en la app iOS).
  - fetch() NO jala nada: reusa el ÚLTIMO payload ingerido (para que /api/sync con
    source=healthkit reprocese sin fallar), o lanza NoToken si nunca se ingirió.
  - ingest(payload) normaliza el payload al dict interno de 13 claves y guarda el
    crudo en data/healthkit_ingest.json.

────────────────────────────────────────────────────────────────────────────────
FORMATO DEL PAYLOAD (contrato con la app nativa, Fase 5D-B)
────────────────────────────────────────────────────────────────────────────────
JSON con arrays por métrica; cada entrada con `date` ISO YYYY-MM-DD, normalizado a
la fecha LOCAL del dispositivo. Todas las claves top-level son OPCIONALES (payload
parcial tolerado):

    {
      "hrv":         [{"date": "2026-06-28", "value": 54.6}],   # ms (SDNN)
      "rhr":         [{"date": "2026-06-28", "value": 52}],     # lpm
      "resp":        [{"date": "2026-06-28", "value": 14.1}],   # rpm
      "spo2":        [{"date": "2026-06-28", "value": 97.0}],   # %
      "skin_temp":   [{"date": "2026-06-28", "value": -0.3}],   # °C DESVIACIÓN (wrist temp)
      "steps":       [{"date": "2026-06-28", "value": 8423}],
      "vo2":         [{"date": "2026-06-28", "value": 47.3}],   # ml/kg/min
      "distance_km": [{"date": "2026-06-28", "value": 6.21}],
      "energy_kcal": [{"date": "2026-06-28", "value": 2480}],   # total (basal+activa)
      "sleep": [
        {"date": "2026-06-28", "asleep": 372, "deep": 54, "rem": 86, "light": 232,
         "eff": 92, "bedtime": "01:01", "waketime": "07:03", "inbed": 402}
      ],
      "workouts": [
        {"date": "2026-06-28", "name": "Run", "dur_min": 40, "kcal": 380,
         "distance_km": 6.21}   # distance_km opcional
      ],
      "menstrual_flow": [{"date": "2026-06-28", "value": "medium"}],   # Fase 7
      "basal_temp":     [{"date": "2026-06-28", "value": 36.4}],       # Fase 7, °C absoluto
      "ovulation_test":  [{"date": "2026-06-28", "value": "positive"}] # Fase 7 (opcional, no usado aún por el motor)
    }

Reglas de normalización (ver _normalize):
  - Todo array [{date, value}] → dict {date: value} (mismo shape interno que Oura/WHOOP).
  - sleep[] → dict {date: {asleep, deep, rem, light, eff, bedtime, waketime, inbed}}
    (pasa campos tal cual, default None si falta — no inventa valores).
  - skin_temp YA es desviación → mapea directo a skin[date] (igual semántica que
    Oura temperature_deviation / WHOOP skin_temp como desviación).
  - workouts[] → exercises[] {date, name, dur_min, kcal, distance_km}.
  - azm y active_hours → siempre {} (el motor los deriva/None, igual que Oura/WHOOP).
  - None-safe y tolerante a claves faltantes. Entradas sin `date` parseable se
    descartan en silencio (misma "degradación silenciosa" que Oura).

FASE 7 — salud femenina (opt-in, ver ROADMAP-vitals-fase7-salud-femenina.md):
  - `menstrual_flow`/`basal_temp`/`ovulation_test` son claves OPCIONALES adicionales
    del mismo payload. NO forman parte del dict de 13 claves que build_dataset()
    consume (_normalize las ignora para ese propósito) — build_dataset()/el motor
    de salud general NUNCA las ve, así que un payload SIN estos campos se comporta
    IDÉNTICO al actual (retrocompatibilidad estricta, criterio #8 del roadmap).
  - En vez, `merge_healthkit_cycle(payload, cycle_log)` las lee directo del payload
    crudo y las funde en cycle_log.json (días de flujo contiguos -> periodos con
    start/end; de-dupe por start+source='healthkit'). Se llama desde
    HealthKitSource.ingest(), gateado por profile.cycle_tracking (si el toggle
    está apagado, no se toca cycle_log.json en absoluto — mismo criterio de
    opt-in estricto que el resto del módulo de ciclo).
"""
from __future__ import annotations

import datetime
import json
import logging
import time
from typing import Any, Optional

from app.sources.base import Source, TokenExpired, NoToken  # noqa: F401 (re-export)
from app.config import settings
from app.fsutil import atomic_write_text

logger = logging.getLogger("vitals.healthkit")

SOURCE_NAME = "healthkit"

# Métricas que comparten el shape [{date, value}] → dict {date: value}
_SCALAR_METRICS = (
    "hrv", "rhr", "resp", "spo2", "steps", "vo2", "distance_km", "energy_kcal",
)


def _ingest_path():
    """Ruta a healthkit_ingest.json del usuario activo (Fase 8D, paso D3:
    household). Se resuelve al llamar (no a import-time). Fuera de un
    request household-aware (is_context_active()=False — tests preexistentes
    que hacen patch.object(settings, "DATA_DIR", tmp_path)), usa
    settings.DATA_DIR tal cual: comportamiento idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "healthkit_ingest.json"
    except Exception:
        pass
    return settings.DATA_DIR / "healthkit_ingest.json"


class HealthKitSource(Source):
    """Adaptador HealthKit (Apple) con semántica PUSH.

    auth: no usa OAuth web (se autoriza on-device en la app iOS).
    ingest(payload): normaliza+persiste el payload empujado por la app nativa.
    fetch(): reusa el último payload ingerido (no jala red).
    """

    name = SOURCE_NAME

    # ---------------------------------------------------------------- auth

    def build_auth_url(self, state: str) -> str:
        raise NotImplementedError(
            "HealthKit no usa OAuth web — la autorización ocurre en la app iOS, "
            "on-device. No hay /auth/login para esta fuente."
        )

    def exchange_code(self, code: str) -> dict:
        raise NotImplementedError(
            "HealthKit no usa OAuth web — la autorización ocurre en la app iOS, "
            "on-device. No hay /auth/callback para esta fuente."
        )

    def auth_state(self) -> dict:
        """{status, days_left}. 'active' si existe un ingest previo válido,
        'no_token' si nunca se ingirió nada. days_left=0 por convención de shape
        (el frontend solo lee AUTH.status)."""
        payload = self._load_raw_payload()
        if payload is None:
            return {"status": "no_token", "days_left": 0}
        return {
            "status": "active",
            "days_left": 0,
            "last_ingest": payload.get("_ingested_at"),
        }

    # ---------------------------------------------------------------- datos

    def fetch(self, days: int = 365) -> dict:
        """Reusa el ÚLTIMO payload ingerido (no jala red). Lo re-normaliza y
        opcionalmente recorta entradas anteriores a hoy-`days`.

        Lanza NoToken si nunca se ingirió nada (así /api/sync con source=healthkit
        reusa el manejo existente de NoToken en main.py sin tocar api_sync)."""
        payload = self._load_raw_payload()
        if payload is None:
            raise NoToken(
                "No se ha ingerido ningún dato de HealthKit todavía. "
                "Usa la app iOS para sincronizar."
            )
        data = self._normalize(payload)
        if days and days > 0:
            data = self._trim_to_window(data, days)
        return data

    def ingest(self, payload: dict) -> dict:
        """Normaliza el payload de la app nativa al dict interno de 13 claves y
        guarda el payload CRUDO en data/healthkit_ingest.json. None-safe.

        Fase 7: además intenta fundir menstrual_flow/basal_temp en cycle_log.json
        vía merge_healthkit_cycle(), envuelto en try/except — un fallo del módulo
        de ciclo NUNCA debe tumbar el ingest general de salud (criterio: nunca
        crashea). Gateado por profile.cycle_tracking dentro de merge_healthkit_cycle."""
        if not isinstance(payload, dict):
            payload = {}
        data = self._normalize(payload)
        self._save_raw_payload(payload)
        try:
            merge_healthkit_cycle(payload)
        except Exception as exc:
            logger.warning(
                "merge_healthkit_cycle falló durante HealthKitSource.ingest (no bloqueante): %s", exc
            )
        return data

    # ---------------------------------------------------------------- storage

    def _load_raw_payload(self) -> Optional[dict]:
        """Lee data/healthkit_ingest.json → dict, o None si no existe/corrupto."""
        path = _ingest_path()
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8")
            if not text.strip():
                return None
            data = json.loads(text)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def _save_raw_payload(self, payload: dict) -> None:
        """Guarda el payload crudo (+ marca de timestamp) en healthkit_ingest.json.
        Escritura ATÓMICA (.tmp + os.replace) vía app.fsutil.atomic_write_text —
        mismo helper que sync.py usa para health_compact.json."""
        settings.DATA_DIR.mkdir(exist_ok=True)
        raw = dict(payload)
        raw["_ingested_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        atomic_write_text(
            _ingest_path(),
            json.dumps(raw, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ---------------------------------------------------------------- normalización

    def _normalize(self, payload: dict) -> dict:
        """Payload crudo → dict de 13 claves que build_dataset(**data) consume.
        Tolerante a claves faltantes/None/arrays vacíos."""
        if not isinstance(payload, dict):
            payload = {}

        scalars = {
            metric: self._array_to_dict(payload.get(metric))
            for metric in _SCALAR_METRICS
        }

        return {
            "sleep":        self._parse_sleep(payload.get("sleep")),
            "rhr":          scalars["rhr"],
            "hrv":          scalars["hrv"],
            "resp":         scalars["resp"],
            "vo2":          scalars["vo2"],
            "steps":        scalars["steps"],
            "azm":          {},
            "spo2":         scalars["spo2"],
            "skin":         self._array_to_dict(payload.get("skin_temp")),
            "exercises":    self._parse_workouts(payload.get("workouts")),
            "distance_km":  scalars["distance_km"],
            "energy_kcal":  scalars["energy_kcal"],
            "active_hours": {},
        }

    @staticmethod
    def _valid_date(d) -> Optional[str]:
        """Devuelve la fecha YYYY-MM-DD si es parseable, si no None."""
        if not isinstance(d, str):
            return None
        try:
            datetime.date.fromisoformat(d)
            return d
        except Exception:
            return None

    def _array_to_dict(self, arr, key: str = "value") -> dict:
        """[{date, value}] → {date: value}. None-safe; descarta entradas sin
        fecha válida. Pasa valores None tal cual (build_dataset los tolera)."""
        out: dict = {}
        if not isinstance(arr, list):
            return out
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            day = self._valid_date(entry.get("date"))
            if day is None:
                continue
            out[day] = entry.get(key)
        return out

    def _parse_sleep(self, arr) -> dict:
        """sleep[] → {date: {asleep, deep, rem, light, eff, bedtime, waketime,
        inbed}}. Pasa campos tal cual (default None si falta).

        F2 roadmap P0 (hipnograma): acepta ADEMÁS un campo opcional `segments`
        por entrada ([{s, e, st}], minutos desde bedtime) — la app iOS aún no
        lo manda, pero el contrato queda LISTO para cuando empuje las fases de
        HealthKit. Se sanea con validate_segments: si es inválido se descarta
        SOLO ese campo (warning) y la noche entra igual — un payload malformado
        de segments jamás debe costar la noche entera de sueño."""
        out: dict = {}
        if not isinstance(arr, list):
            return out
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            day = self._valid_date(entry.get("date"))
            if day is None:
                continue
            rec = {
                "asleep":   entry.get("asleep"),
                "deep":     entry.get("deep"),
                "rem":      entry.get("rem"),
                "light":    entry.get("light"),
                "eff":      entry.get("eff"),
                "bedtime":  entry.get("bedtime"),
                "waketime": entry.get("waketime"),
                "inbed":    entry.get("inbed"),
            }
            raw_segments = entry.get("segments")
            if raw_segments is not None:
                from app.sleep_segments import validate_segments
                segments = validate_segments(raw_segments)
                if segments:
                    rec["segments"] = segments
                else:
                    logger.warning(
                        "HealthKit ingest: campo 'segments' inválido para %s — "
                        "se descarta solo ese campo, la noche entra igual.", day,
                    )
            # Auditoría 23-jul (H4): el iOS puede mandar DOS entradas con el mismo
            # día de despertar (noche interrumpida >3h partida en fragmentos, o
            # noche + siesta que termina el mismo día). Antes la última SOBRESCRIBÍA
            # a la anterior sin criterio (caso real medido: una noche de 8.2h quedó
            # mostrada como la siesta de 1.2h — se perdieron 7 horas). Regla ahora:
            # gana el registro con MAYOR `asleep` — el mismo criterio "gana el más
            # completo" que ya usan _merge_sleep (merge.py) y parse_sleep
            # (parsers.py) para dedup de noches. NO se suman fragmentos a propósito:
            # sumar inflaría cuando el duplicado es una siesta, y distinguir
            # fragmento-de-noche vs siesta con solo "HH:MM" es ambiguo.
            prev = out.get(day)
            if prev is not None and (rec.get("asleep") or 0) <= (prev.get("asleep") or 0):
                logger.info(
                    "HealthKit ingest: entrada duplicada para %s (asleep=%s) — se "
                    "conserva la más completa ya registrada (asleep=%s).",
                    day, rec.get("asleep"), prev.get("asleep"),
                )
                continue
            out[day] = rec
        return out

    def _parse_workouts(self, arr) -> list:
        """workouts[] → exercises[] {date, name, dur_min, kcal, distance_km}.
        distance_km es opcional (None si no viene)."""
        out: list = []
        if not isinstance(arr, list):
            return out
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            day = self._valid_date(entry.get("date"))
            if day is None:
                continue
            out.append({
                "date":        day,
                "name":        entry.get("name"),
                "dur_min":     entry.get("dur_min"),
                "kcal":        entry.get("kcal"),
                "distance_km": entry.get("distance_km"),
            })
        return out

    @staticmethod
    def _trim_to_window(data: dict, days: int) -> dict:
        """Recorta entradas con fecha anterior a hoy-`days`. Simple, None-safe."""
        cutoff = datetime.date.today() - datetime.timedelta(days=days)

        def _keep(day_str: str) -> bool:
            try:
                return datetime.date.fromisoformat(day_str) >= cutoff
            except Exception:
                return True  # si la fecha no parsea, no recortar (no debería pasar)

        trimmed = dict(data)
        for metric, val in data.items():
            if isinstance(val, dict):
                trimmed[metric] = {d: v for d, v in val.items() if _keep(d)}
            elif isinstance(val, list):
                trimmed[metric] = [
                    e for e in val
                    if not (isinstance(e, dict) and e.get("date"))
                    or _keep(e["date"])
                ]
        return trimmed


# ── Fase 7: fusión de menstrual_flow/basal_temp del payload HealthKit → cycle_log ──

def _valid_date_str(d) -> Optional[str]:
    if not isinstance(d, str):
        return None
    try:
        datetime.date.fromisoformat(d)
        return d
    except Exception:
        return None


def merge_healthkit_cycle(payload: dict) -> None:
    """Funde `menstrual_flow`/`basal_temp` del payload crudo de HealthKit en
    data/cycle_log.json. Gateado por profile.cycle_tracking (opt-in estricto,
    criterio #1 del roadmap de Fase 7): con el toggle apagado, esta función es
    un no-op — no toca cycle_log.json en absoluto.

    menstrual_flow: días con flujo reportado se agrupan en periodos contiguos
    (gap de 1 día entre entradas = mismo periodo) → {start, end, flow, source}.
    Idempotente: de-dupe por (start, source='healthkit') — un periodo healthkit
    con el mismo start se REEMPLAZA, no se duplica; convive con periodos 'manual'
    en el mismo start (fuentes distintas, no se pisan entre sí).

    basal_temp: no persiste directamente en cycle_log (el motor de cycle.py usa
    skin_temp del dataset general, ya fusionado por build_dataset/merge_sources).
    Se deja como punto de extensión documentado — basal_temp llega en el payload
    pero el v1 del motor no lo consume aparte de skin_temp.

    Nunca lanza (los callers ya lo envuelven en try/except, pero esta función
    también se protege internamente para poder llamarse standalone/en tests)."""
    try:
        if not isinstance(payload, dict):
            return

        try:
            from app import profile as _profile
            if not _profile.effective("cycle_tracking"):
                return
        except Exception:
            return

        flow_arr = payload.get("menstrual_flow")
        if not isinstance(flow_arr, list) or not flow_arr:
            return

        # {date: flow_value}, solo entradas con fecha válida.
        flow_by_date: dict[str, Any] = {}
        for entry in flow_arr:
            if not isinstance(entry, dict):
                continue
            day = _valid_date_str(entry.get("date"))
            if day is None:
                continue
            flow_by_date[day] = entry.get("value")

        if not flow_by_date:
            return

        # Agrupar días contiguos (gap <= 1 día) en periodos.
        sorted_days = sorted(flow_by_date.keys())
        groups: list[list[str]] = []
        current: list[str] = []
        prev_date: Optional[datetime.date] = None
        for day_str in sorted_days:
            d = datetime.date.fromisoformat(day_str)
            if prev_date is not None and (d - prev_date).days > 1:
                if current:
                    groups.append(current)
                current = []
            current.append(day_str)
            prev_date = d
        if current:
            groups.append(current)

        new_periods = []
        for g in groups:
            start, end = g[0], g[-1]
            # 'flow' del periodo: el valor del primer día del grupo (best-effort,
            # sin inventar un agregado que el payload no provee).
            new_periods.append({
                "start": start,
                "end": end,
                "flow": flow_by_date.get(start),
                "source": "healthkit",
            })

        from app import cycle as _cycle_mod
        log = _cycle_mod.load_cycle_log()
        existing = log.get("periods") or []
        new_starts = {p["start"] for p in new_periods}
        # De-dupe: quita periodos healthkit viejos que compartan 'start' con los
        # nuevos (se reemplazan); deja intactos los periodos manuales/otros starts.
        kept = [
            p for p in existing
            if not (isinstance(p, dict) and p.get("source") == "healthkit" and p.get("start") in new_starts)
        ]
        log["periods"] = kept + new_periods
        _cycle_mod.save_cycle_log(log)
    except Exception as exc:
        logger.warning("merge_healthkit_cycle falló (no bloqueante): %s", exc)
