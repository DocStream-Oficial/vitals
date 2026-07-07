"""
app/sources/oura.py — Adaptador Oura Ring que implementa Source.

Construido "a ciegas" (Fase 5B) — sin cuenta Oura para probar en vivo. Correcto
por spec de la API Oura v2 (jun 2026) + tests con fixtures. La comunidad valida
con datos reales.

OAuth2: authorize en cloud.ouraring.com, token en api.ouraring.com/oauth/token.
Tokens de Oura son de larga vida (no expiran en ciclo corto) — aun así
implementamos refresh por si la API lo requiere en el futuro o el token entra
en estado inválido.

Token storage: data/token_oura.json (vía app.sources._tokenstore — NO toca
data/token.json de Google).

Normalización Oura → esquema interno (ver ROADMAP-vitals-fase5b-oura.md):
    sleep[date]       ← colección `sleep` (día = bedtime_end, no bedtime_start)
    hrv[date]         ← sleep.average_hrv
    rhr[date]         ← sleep.lowest_heart_rate
    resp[date]        ← sleep.average_breath
    spo2[date]        ← daily_spo2.spo2_percentage.average
    skin[date]        ← daily_readiness.temperature_deviation
    steps[date]       ← daily_activity.steps
    vo2[date]         ← vO2_max.vo2_max
    distance_km[date] ← daily_activity.equivalent_walking_distance / 1000
    energy_kcal[date] ← daily_activity.total_calories
    exercises[]       ← workout
    azm, active_hours ← {} (Oura no los expone directo — el motor los deriva/None)
"""
from __future__ import annotations

import datetime
import time
import urllib.parse
from typing import Optional

import requests

from app.sources.base import Source, TokenExpired, NoToken  # noqa: F401 (re-export)
from app.sources import _tokenstore
from app.config import settings

SOURCE_NAME = "oura"


class OuraSource(Source):
    """Adaptador Oura Ring API v2.

    auth: OAuth2 propio (authorize/token de cloud/api.ouraring.com), token
    persistido en data/token_oura.json (storage por-fuente, no pisa Google).
    fetch: GET por colección + normalización al dict interno.
    """

    name = SOURCE_NAME

    # ---------------------------------------------------------------- auth

    def build_auth_url(self, state: str) -> str:
        """Construye la URL de autorización OAuth de Oura."""
        params = {
            "client_id": settings.OURA_CLIENT_ID,
            "redirect_uri": settings.REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(settings.OURA_SCOPES),
            "state": state,
        }
        return settings.OURA_AUTH_URL + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, code: str) -> dict:
        """Intercambia el authorization code por tokens; guarda en token_oura.json."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.OURA_CLIENT_ID,
            "client_secret": settings.OURA_CLIENT_SECRET,
            "redirect_uri": settings.REDIRECT_URI,
        }
        resp = requests.post(
            settings.OURA_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        body = resp.json()
        if resp.status_code != 200 or "access_token" not in body:
            raise RuntimeError(f"exchange_code (Oura) falló (status {resp.status_code}): {body}")
        tok = {
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token"),
            "obtained_at": int(time.time()),
        }
        _tokenstore.save_token(self.name, tok)
        return tok

    def access_token(self) -> str:
        """Devuelve un access_token válido. Refresca con refresh_token si Oura lo
        requiere (tokens largos, pero implementamos el refresh por si acaso).
        Lanza NoToken si no hay token guardado; TokenExpired si invalid_grant."""
        tok = _tokenstore.load_token(self.name)
        if not tok or "access_token" not in tok:
            raise NoToken("No hay token de Oura. Visita /auth/login para autorizar.")

        if tok.get("expired"):
            raise TokenExpired("Token Oura expirado. Visita /auth/login.")

        # Tokens largos: no refrescamos proactivamente. Solo si no hay access_token
        # utilizable pero sí refresh_token, intentamos refrescar.
        if not tok.get("access_token") and tok.get("refresh_token"):
            return self._refresh(tok)

        return tok["access_token"]

    def _refresh(self, tok: dict) -> str:
        """Refresca el access_token usando el refresh_token guardado."""
        refresh_token = tok.get("refresh_token")
        if not refresh_token:
            raise NoToken("No hay refresh_token de Oura. Visita /auth/login.")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": settings.OURA_CLIENT_ID,
            "client_secret": settings.OURA_CLIENT_SECRET,
        }
        resp = requests.post(
            settings.OURA_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        body = resp.json()
        if resp.status_code != 200 or "access_token" not in body:
            if "invalid_grant" in str(body):
                self.mark_expired()
                raise TokenExpired("Token Oura expirado (invalid_grant). Visita /auth/login.")
            raise RuntimeError(f"No pude refrescar el token Oura (status {resp.status_code}): {body}")
        new_tok = {
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token", refresh_token),
            "obtained_at": int(time.time()),
        }
        _tokenstore.save_token(self.name, new_tok)
        return new_tok["access_token"]

    def mark_expired(self):
        """Marca el token Oura como expirado (mismo patrón que app.auth.mark_expired)."""
        tok = _tokenstore.load_token(self.name) or {}
        tok["expired"] = True
        _tokenstore.save_token(self.name, tok)

    def auth_state(self) -> dict:
        """{status, days_left}. Oura usa tokens de larga vida: 'active' si hay
        token (sin ciclo de expiración corto como Google); 'no_token' si no."""
        tok = _tokenstore.load_token(self.name)
        if not tok or "access_token" not in tok:
            return {"status": "no_token", "days_left": 0}
        if tok.get("expired"):
            return {"status": "expired", "days_left": 0}
        return {"status": "active", "days_left": 7}

    # ---------------------------------------------------------------- datos

    def fetch(self, days: int = 45) -> dict:
        """Jala datos de Oura de los últimos `days` días y normaliza al dict interno.

        Lanza TokenExpired o NoToken si el token no es válido.
        Tolerante a colecciones vacías / campos None (el motor ya lo soporta).
        """
        token = self.access_token()  # puede lanzar TokenExpired / NoToken

        today = datetime.date.today()
        start = today - datetime.timedelta(days=days)
        start_date = start.isoformat()
        end_date = today.isoformat()

        sleep_raw = self._get_collection("sleep", token, start_date, end_date)
        spo2_raw = self._get_collection("daily_spo2", token, start_date, end_date)
        readiness_raw = self._get_collection("daily_readiness", token, start_date, end_date)
        activity_raw = self._get_collection("daily_activity", token, start_date, end_date)
        vo2_raw = self._get_collection("vO2_max", token, start_date, end_date)
        workout_raw = self._get_collection("workout", token, start_date, end_date)

        sleep, hrv, rhr, resp = self._parse_sleep(sleep_raw)
        spo2 = self._parse_daily_spo2(spo2_raw)
        skin = self._parse_daily_readiness(readiness_raw)
        steps, distance_km, energy_kcal = self._parse_daily_activity(activity_raw)
        vo2 = self._parse_vo2(vo2_raw)
        exercises = self._parse_workouts(workout_raw)

        return {
            "sleep":        sleep,
            "rhr":          rhr,
            "hrv":          hrv,
            "resp":         resp,
            "vo2":          vo2,
            "steps":        steps,
            "azm":          {},
            "spo2":         spo2,
            "skin":         skin,
            "exercises":    exercises,
            "distance_km":  distance_km,
            "energy_kcal":  energy_kcal,
            "active_hours": {},
        }

    # ---------------------------------------------------------------- HTTP

    def _get_collection(self, collection: str, token: str, start_date: str, end_date: str) -> list:
        """GET a una colección de usercollection. Tolerante a errores/colecciones
        vacías: devuelve [] en vez de lanzar (degradación silenciosa, igual que
        Google Health en _try_rollup_candidates)."""
        url = f"{settings.OURA_API_BASE}/{collection}"
        params = {"start_date": start_date, "end_date": end_date}
        headers = {"Authorization": f"Bearer {token}"}
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as exc:
            print(f"   (aviso) Oura {collection}: excepción de red → {exc}")
            return []
        if resp.status_code == 401:
            # Token inválido/revocado a media sesión — lo marcamos para el próximo intento.
            self.mark_expired()
            raise TokenExpired(f"Token Oura inválido (401) al pedir {collection}.")
        if resp.status_code != 200:
            print(f"   (aviso) Oura {collection}: status {resp.status_code} (degradado)")
            return []
        try:
            body = resp.json()
        except Exception:
            return []
        data = body.get("data") if isinstance(body, dict) else None
        return data if isinstance(data, list) else []

    # ---------------------------------------------------------------- parsers

    @staticmethod
    def _day_from_record(rec: dict, fallback_field: str = "day") -> Optional[str]:
        """Resuelve la fecha (YYYY-MM-DD) de un registro Oura."""
        d = rec.get(fallback_field)
        if d:
            return d
        return None

    @staticmethod
    def _hhmm_from_iso(iso_str: Optional[str]) -> Optional[str]:
        """ISO datetime → 'HH:MM'. None-safe."""
        if not iso_str:
            return None
        try:
            # Oura usa formato con offset, ej. '2026-06-29T23:41:24-07:00'
            dt = datetime.datetime.fromisoformat(iso_str)
            return dt.strftime("%H:%M")
        except Exception:
            return None

    @staticmethod
    def _segments_from_phase_string(phase_str: Optional[str]) -> Optional[list]:
        """Deriva `segments` (formato canónico app.sleep_segments) desde
        `sleep_phase_5_min` de Oura v2: un string donde cada char representa
        5 minutos desde bedtime_start (1=deep, 2=light, 3=REM, 4=awake).
        Colapsa runs consecutivos del mismo char en un solo segmento.
        None si el campo no viene, está vacío, o no produce ningún segmento
        válido (nunca inventa datos — noches sin el campo quedan sin
        `segments`, byte-igual al comportamiento anterior a F2)."""
        if not phase_str or not isinstance(phase_str, str):
            return None

        _MAP = {"1": "deep", "2": "light", "3": "rem", "4": "awake"}
        segs: list = []
        cur_stage: Optional[str] = None
        cur_start = 0
        pos = 0

        for ch in phase_str:
            stage = _MAP.get(ch)
            if stage is None:
                # Carácter fuera del mapa conocido (dato corrupto/desconocido)
                # -> se descarta el intervalo de 5 min en curso pero no se
                # aborta todo el parseo (best-effort, ver criterio de riesgo #9).
                if cur_stage is not None:
                    segs.append({"s": cur_start, "e": pos, "st": cur_stage})
                    cur_stage = None
                pos += 5
                cur_start = pos
                continue
            if stage != cur_stage:
                if cur_stage is not None:
                    segs.append({"s": cur_start, "e": pos, "st": cur_stage})
                cur_stage = stage
                cur_start = pos
            pos += 5

        if cur_stage is not None:
            segs.append({"s": cur_start, "e": pos, "st": cur_stage})

        if not segs:
            return None

        from app.sleep_segments import validate_segments
        return validate_segments(segs)

    def _parse_sleep(self, records: list):
        """Procesa la colección `sleep`. La noche se asigna al `day` que Oura
        ya reporta (que corresponde al día de bedtime_end). Devuelve 4 dicts:
        sleep, hrv, rhr, resp — todos keyed por fecha."""
        sleep: dict = {}
        hrv: dict = {}
        rhr: dict = {}
        resp: dict = {}

        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            if not day:
                continue

            total_s = rec.get("total_sleep_duration")
            deep_s = rec.get("deep_sleep_duration")
            rem_s = rec.get("rem_sleep_duration")
            light_s = rec.get("light_sleep_duration")
            inbed_s = rec.get("time_in_bed")
            efficiency = rec.get("efficiency")

            day_rec = {
                "asleep":  round(total_s / 60, 1) if total_s is not None else None,
                "deep":    round(deep_s / 60, 1) if deep_s is not None else None,
                "rem":     round(rem_s / 60, 1) if rem_s is not None else None,
                "light":   round(light_s / 60, 1) if light_s is not None else None,
                "eff":     efficiency,
                "inbed":   round(inbed_s / 60, 1) if inbed_s is not None else None,
                "bedtime": self._hhmm_from_iso(rec.get("bedtime_start")),
                "waketime": self._hhmm_from_iso(rec.get("bedtime_end")),
            }

            # F2 roadmap P0: hipnograma — segments OPCIONAL, solo si el parse
            # produce >=1 segmento válido (rec sin sleep_phase_5_min queda
            # byte-igual al comportamiento anterior a este campo).
            segments = self._segments_from_phase_string(rec.get("sleep_phase_5_min"))
            if segments:
                day_rec["segments"] = segments

            sleep[day] = day_rec

            if rec.get("average_hrv") is not None:
                hrv[day] = rec["average_hrv"]
            if rec.get("lowest_heart_rate") is not None:
                rhr[day] = rec["lowest_heart_rate"]
            if rec.get("average_breath") is not None:
                resp[day] = rec["average_breath"]

        return sleep, hrv, rhr, resp

    def _parse_daily_spo2(self, records: list) -> dict:
        out: dict = {}
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            if not day:
                continue
            spo2_block = rec.get("spo2_percentage")
            avg = spo2_block.get("average") if isinstance(spo2_block, dict) else None
            if avg is not None:
                out[day] = avg
        return out

    def _parse_daily_readiness(self, records: list) -> dict:
        out: dict = {}
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            if not day:
                continue
            dev = rec.get("temperature_deviation")
            if dev is not None:
                out[day] = dev
        return out

    def _parse_daily_activity(self, records: list):
        steps: dict = {}
        distance_km: dict = {}
        energy_kcal: dict = {}
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            if not day:
                continue
            if rec.get("steps") is not None:
                steps[day] = rec["steps"]
            dist_m = rec.get("equivalent_walking_distance")
            if dist_m is not None:
                distance_km[day] = round(dist_m / 1000, 3)
            kcal = rec.get("total_calories")
            if kcal is not None:
                energy_kcal[day] = kcal
        return steps, distance_km, energy_kcal

    def _parse_vo2(self, records: list) -> dict:
        out: dict = {}
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            if not day:
                continue
            vo2 = rec.get("vo2_max")
            if vo2 is not None:
                out[day] = vo2
        return out

    def _parse_workouts(self, records: list) -> list:
        out: list = []
        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_record(rec)
            start_dt = rec.get("start_datetime")
            end_dt = rec.get("end_datetime")
            dur_min = None
            if start_dt and end_dt:
                try:
                    sdt = datetime.datetime.fromisoformat(start_dt)
                    edt = datetime.datetime.fromisoformat(end_dt)
                    dur_min = round((edt - sdt).total_seconds() / 60, 1)
                except Exception:
                    dur_min = None
            out.append({
                "date":    day,
                "name":    rec.get("activity"),
                "dur_min": dur_min,
                "kcal":    rec.get("calories"),
                "distance_km": round(rec["distance"] / 1000, 3) if rec.get("distance") is not None else None,
            })
        return out
