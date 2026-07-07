"""
app/sources/whoop.py — Adaptador WHOOP que implementa Source.

Construido "a ciegas" (Fase 5C) — sin cuenta WHOOP para probar en vivo. Correcto
por spec de la API WHOOP v2 (jun 2026) + tests con fixtures. La comunidad valida
con datos reales.

OAuth2: authorize en api.prod.whoop.com/oauth/oauth2/auth, token en
api.prod.whoop.com/oauth/oauth2/token. Scope `offline` obligatorio para recibir
refresh_token.

🔴 GOTCHA CRÍTICO — refresh tokens ROTATORIOS: WHOOP devuelve un refresh_token
NUEVO en CADA refresh y el anterior queda INVÁLIDO. access_token() persiste el
nuevo refresh_token (y access_token) INMEDIATAMENTE vía _tokenstore.save_token,
ANTES de devolver el access_token al caller. _refresh() usa un lock de threading
DE CLASE (single-flight COMPARTIDO entre instancias) para que dos refresh
concurrentes no se pisen y quemen el refresh_token vigente dos veces — de
clase porque get_source() crea una instancia NUEVA de WhoopSource en cada
llamada, así que un lock de instancia no protegería entre dos syncs/requests
concurrentes. Si el refresh falla con invalid_grant →
TokenExpired (el usuario debe re-autenticar; perdimos el refresh_token vigente).

Token storage: data/token_whoop.json (vía app.sources._tokenstore — NO toca
data/token.json de Google ni data/token_oura.json).

Normalización WHOOP → esquema interno (ver ROADMAP-vitals-fase5c-whoop.md):
    rhr[date]         ← recovery score.resting_heart_rate
    hrv[date]         ← recovery score.hrv_rmssd_milli (YA está en ms — "_milli" es la
                        unidad del valor, NO un factor pendiente: nunca dividir /1000)
    spo2[date]        ← recovery score.spo2_percentage (solo hardware 4.0; None si no viene)
    skin[date]        ← recovery score.skin_temp_celsius (desviación; solo 4.0; None si no viene)
    sleep[date]       ← activity/sleep: milli→min, eff=sleep_efficiency_percentage,
                        bedtime/waketime desde start/end ISO; día = fecha local de `end`
                        (el despertar); asleep = in_bed − awake
    resp[date]        ← activity/sleep score.respiratory_rate
    exercises[]       ← activity/workout → {date, name=sport_name, dur_min, kcal=kilojoule*0.239, distance}
    distance_km[date] ← suma de workouts (distance_meter/1000) por día
    energy_kcal[date] ← suma de workouts (kilojoule*0.239) por día
    vo2, steps, azm, active_hours ← {} (WHOOP no los expone — el motor los deriva/None)

Todos los valores de hrv/recovery/strain los RECALCULA el motor del crudo — NO
usamos el recovery_score nativo de WHOOP como score final.
"""
from __future__ import annotations

import datetime
import threading
import time
import urllib.parse
from typing import Optional

import requests

from app.sources.base import Source, TokenExpired, NoToken  # noqa: F401 (re-export)
from app.sources import _tokenstore
from app.config import settings

SOURCE_NAME = "whoop"

# kJ → kcal (1 kJ = 0.239006 kcal; el roadmap especifica ×0.239)
_KJ_TO_KCAL = 0.239
# milisegundos → minutos
_MILLI_TO_MIN = 60000.0


class WhoopSource(Source):
    """Adaptador WHOOP API v2.

    auth: OAuth2 propio (authorize/token de api.prod.whoop.com), token
    persistido en data/token_whoop.json (storage por-fuente, no pisa Google/Oura).
    fetch: GET paginado por colección + normalización al dict interno.
    """

    name = SOURCE_NAME

    # Lock DE CLASE para single-flight del refresh rotatorio, COMPARTIDO entre
    # instancias: get_source() crea una WhoopSource nueva por llamada, así que
    # un lock de instancia no evitaría que dos refresh concurrentes (p.ej. dos
    # syncs en vuelo) quemen el mismo refresh_token vigente (WHOOP invalida el
    # anterior en cada refresh).
    _refresh_lock = threading.Lock()

    # ---------------------------------------------------------------- auth

    def build_auth_url(self, state: str) -> str:
        """Construye la URL de autorización OAuth de WHOOP."""
        params = {
            "client_id": settings.WHOOP_CLIENT_ID,
            "redirect_uri": settings.REDIRECT_URI,
            "response_type": "code",
            "scope": " ".join(settings.WHOOP_SCOPES),
            "state": state,
        }
        return settings.WHOOP_AUTH_URL + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, code: str) -> dict:
        """Intercambia el authorization code por tokens; guarda en token_whoop.json."""
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": settings.WHOOP_CLIENT_ID,
            "client_secret": settings.WHOOP_CLIENT_SECRET,
            "redirect_uri": settings.REDIRECT_URI,
        }
        resp = requests.post(
            settings.WHOOP_TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=30,
        )
        body = resp.json()
        if resp.status_code != 200 or "access_token" not in body:
            raise RuntimeError(f"exchange_code (WHOOP) falló (status {resp.status_code}): {body}")
        tok = {
            "access_token": body.get("access_token"),
            "refresh_token": body.get("refresh_token"),
            "expires_in": body.get("expires_in"),
            "obtained_at": int(time.time()),
        }
        _tokenstore.save_token(self.name, tok)
        return tok

    def access_token(self) -> str:
        """Devuelve un access_token válido. Si el access expiró (por expires_in),
        refresca — y el refresh ROTATORIO de WHOOP exige persistir el nuevo
        refresh_token de inmediato (ver _refresh). Lanza NoToken si no hay
        token guardado; TokenExpired si el token quedó marcado expirado o el
        refresh devuelve invalid_grant."""
        tok = _tokenstore.load_token(self.name)
        if not tok or "access_token" not in tok:
            raise NoToken("No hay token de WHOOP. Visita /auth/login para autorizar.")

        if tok.get("expired"):
            raise TokenExpired("Token WHOOP expirado. Visita /auth/login.")

        if self._is_expired(tok):
            return self._refresh(tok)

        return tok["access_token"]

    @staticmethod
    def _is_expired(tok: dict) -> bool:
        """True si el access_token ya venció según expires_in/obtained_at.
        Si no tenemos expires_in (ej. token viejo guardado antes de este
        campo), asumimos que sigue vivo y dejamos que un 401 dispare el
        refresh reactivo en _get_collection."""
        expires_in = tok.get("expires_in")
        obtained_at = tok.get("obtained_at")
        if not expires_in or not obtained_at:
            return False
        # Margen de 60s para evitar usar un token que vence en el viaje de red.
        return time.time() >= (obtained_at + expires_in - 60)

    def _refresh(self, tok: dict) -> str:
        """Refresca el access_token usando el refresh_token guardado.

        🔴 Refresh ROTATORIO: WHOOP devuelve un refresh_token NUEVO en cada
        refresh y el anterior queda inválido. Por eso:
          1. single-flight vía self._refresh_lock (no dos refresh concurrentes
             quemando el mismo refresh_token vigente).
          2. Dentro del lock, releemos el token guardado por si otro hilo ya
             refrescó mientras esperábamos el lock (evita reusar un
             refresh_token ya consumido).
          3. Persistimos el nuevo access_token + refresh_token INMEDIATAMENTE
             (antes de retornar/usar el access_token), vía _tokenstore.save_token.
        """
        with self._refresh_lock:
            # Releer: si otro hilo ya refrescó mientras esperábamos el lock,
            # usamos lo que ya quedó guardado en vez de re-refrescar con un
            # refresh_token que WHOOP ya invalidó.
            latest = _tokenstore.load_token(self.name) or tok
            if latest.get("access_token") and not self._is_expired(latest) and not latest.get("expired"):
                return latest["access_token"]

            refresh_token = latest.get("refresh_token")
            if not refresh_token:
                raise NoToken("No hay refresh_token de WHOOP. Visita /auth/login.")

            data = {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": settings.WHOOP_CLIENT_ID,
                "client_secret": settings.WHOOP_CLIENT_SECRET,
                "scope": "offline",
            }
            resp = requests.post(
                settings.WHOOP_TOKEN_URL,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            try:
                body = resp.json()
            except Exception:
                body = {}

            if resp.status_code != 200 or "access_token" not in body:
                if "invalid_grant" in str(body):
                    self.mark_expired()
                    raise TokenExpired("Token WHOOP expirado (invalid_grant). Visita /auth/login.")
                raise RuntimeError(f"No pude refrescar el token WHOOP (status {resp.status_code}): {body}")

            new_refresh_token = body.get("refresh_token")
            if not new_refresh_token:
                # WHOOP siempre debería mandar uno nuevo con scope offline; si no
                # llega, NO reusamos el viejo (ya está invalidado del lado de
                # WHOOP) — preferimos fallar explícito a guardar un refresh_token
                # muerto que parezca válido.
                raise RuntimeError(
                    "Refresh WHOOP no devolvió refresh_token nuevo (requerido, rotación obligatoria)."
                )

            new_tok = {
                "access_token": body.get("access_token"),
                "refresh_token": new_refresh_token,
                "expires_in": body.get("expires_in"),
                "obtained_at": int(time.time()),
            }
            # Persistir ANTES de devolver/usar el access_token — si el proceso
            # muere justo después del refresh, el refresh_token nuevo (el único
            # válido) ya quedó en disco.
            _tokenstore.save_token(self.name, new_tok)
            return new_tok["access_token"]

    def mark_expired(self):
        """Marca el token WHOOP como expirado (mismo patrón que app.auth.mark_expired)."""
        tok = _tokenstore.load_token(self.name) or {}
        tok["expired"] = True
        _tokenstore.save_token(self.name, tok)

    def auth_state(self) -> dict:
        """{status, days_left}. WHOOP usa access_token de vida corta (~1h) con
        refresh rotatorio; mientras haya un refresh_token vigente lo tratamos
        como 'active' (el refresh es transparente en cada fetch)."""
        tok = _tokenstore.load_token(self.name)
        if not tok or "access_token" not in tok:
            return {"status": "no_token", "days_left": 0}
        if tok.get("expired"):
            return {"status": "expired", "days_left": 0}
        return {"status": "active", "days_left": 7}

    # ---------------------------------------------------------------- datos

    def fetch(self, days: int = 45) -> dict:
        """Jala datos de WHOOP de los últimos `days` días y normaliza al dict interno.

        Lanza TokenExpired o NoToken si el token no es válido.
        Tolerante a colecciones vacías / campos None / hardware sin spo2/skin_temp
        (solo WHOOP 4.0 los reporta).
        """
        token = self.access_token()  # puede lanzar TokenExpired / NoToken

        now = datetime.datetime.now(datetime.timezone.utc)
        start = now - datetime.timedelta(days=days)
        start_str = start.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_str = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        recovery_raw = self._get_collection("recovery", token, start_str, end_str)
        sleep_raw = self._get_collection("activity/sleep", token, start_str, end_str)
        workout_raw = self._get_collection("activity/workout", token, start_str, end_str)

        rhr, hrv, spo2, skin = self._parse_recovery(recovery_raw)
        sleep, resp = self._parse_sleep(sleep_raw)
        exercises, distance_km, energy_kcal = self._parse_workouts(workout_raw)

        return {
            "sleep":        sleep,
            "rhr":          rhr,
            "hrv":          hrv,
            "resp":         resp,
            "vo2":          {},
            "steps":        {},
            "azm":          {},
            "spo2":         spo2,
            "skin":         skin,
            "exercises":    exercises,
            "distance_km":  distance_km,
            "energy_kcal":  energy_kcal,
            "active_hours": {},
        }

    # ---------------------------------------------------------------- HTTP

    def _get_collection(self, path: str, token: str, start: str, end: str) -> list:
        """GET paginado (next_token) a una colección WHOOP v2. Tolerante a
        errores/colecciones vacías: devuelve [] en vez de lanzar (degradación
        silenciosa, igual que Oura/Google Health)."""
        url = f"{settings.WHOOP_API_BASE}/{path}"
        headers = {"Authorization": f"Bearer {token}"}
        out: list = []
        next_token: Optional[str] = None
        # Tope defensivo de páginas para no quedar en loop infinito si la API
        # devuelve un next_token que no avanza.
        for _ in range(50):
            params = {"start": start, "end": end, "limit": 25}
            if next_token:
                params["nextToken"] = next_token
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=30)
            except Exception as exc:
                print(f"   (aviso) WHOOP {path}: excepción de red → {exc}")
                return out
            if resp.status_code == 401:
                self.mark_expired()
                raise TokenExpired(f"Token WHOOP inválido (401) al pedir {path}.")
            if resp.status_code != 200:
                print(f"   (aviso) WHOOP {path}: status {resp.status_code} (degradado)")
                return out
            try:
                body = resp.json()
            except Exception:
                return out
            if not isinstance(body, dict):
                return out
            records = body.get("records")
            if isinstance(records, list):
                out.extend(records)
            next_token = body.get("next_token")
            if not next_token:
                break
        return out

    # ---------------------------------------------------------------- parsers

    @staticmethod
    def _day_from_iso(iso_str: Optional[str]) -> Optional[str]:
        """ISO datetime (WHOOP usa UTC con 'Z') → fecha local YYYY-MM-DD. El
        roadmap pide 'fecha local de end/created_at' — sin TZ de usuario
        explícita en el perfil, usamos la fecha del datetime tal cual viene
        (igual de determinista que Oura usando el campo `day`)."""
        if not iso_str:
            return None
        try:
            s = iso_str.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
            return dt.date().isoformat()
        except Exception:
            return None

    @staticmethod
    def _hhmm_from_iso(iso_str: Optional[str]) -> Optional[str]:
        """ISO datetime → 'HH:MM'. None-safe."""
        if not iso_str:
            return None
        try:
            s = iso_str.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s)
            return dt.strftime("%H:%M")
        except Exception:
            return None

    def _parse_recovery(self, records: list):
        """Procesa la colección `recovery`. spo2/skin_temp solo vienen en
        hardware 4.0 — si el campo no está (3.0 u otro), queda ausente
        (None-safe, no se agrega la clave)."""
        rhr: dict = {}
        hrv: dict = {}
        spo2: dict = {}
        skin: dict = {}

        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_iso(rec.get("created_at"))
            if not day:
                continue
            score = rec.get("score") or {}
            if not isinstance(score, dict):
                continue

            if score.get("resting_heart_rate") is not None:
                rhr[day] = score["resting_heart_rate"]
            hrv_milli = score.get("hrv_rmssd_milli")
            if hrv_milli is not None:
                hrv[day] = hrv_milli
            spo2_pct = score.get("spo2_percentage")
            if spo2_pct is not None:
                spo2[day] = spo2_pct
            skin_temp = score.get("skin_temp_celsius")
            if skin_temp is not None:
                skin[day] = skin_temp

        # WHOOP da skin_temp_celsius ABSOLUTO (~33°C); el motor espera una DESVIACIÓN
        # centrada en ~0 (como Google "skin temperature variation"). Centramos restando la
        # media de la ventana → mismo significado cross-source (None-safe: 1 valor → 0.0).
        if skin:
            _mean = sum(skin.values()) / len(skin)
            skin = {d: round(v - _mean, 2) for d, v in skin.items()}

        return rhr, hrv, spo2, skin

    def _parse_sleep(self, records: list):
        """Procesa la colección `activity/sleep`. Día = fecha local de `end`
        (el despertar). asleep = in_bed − awake (milli, luego /60000)."""
        sleep: dict = {}
        resp: dict = {}

        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_iso(rec.get("end"))
            if not day:
                continue
            score = rec.get("score") or {}
            if not isinstance(score, dict):
                continue
            stage = score.get("stage_summary") or {}
            if not isinstance(stage, dict):
                stage = {}

            deep_milli = stage.get("total_slow_wave_sleep_time_milli")
            rem_milli = stage.get("total_rem_sleep_time_milli")
            light_milli = stage.get("total_light_sleep_time_milli")
            inbed_milli = stage.get("total_in_bed_time_milli")
            awake_milli = stage.get("total_awake_time_milli")

            asleep_min = None
            if inbed_milli is not None and awake_milli is not None:
                asleep_min = round((inbed_milli - awake_milli) / _MILLI_TO_MIN, 1)

            sleep[day] = {
                "asleep":   asleep_min,
                "deep":     round(deep_milli / _MILLI_TO_MIN, 1) if deep_milli is not None else None,
                "rem":      round(rem_milli / _MILLI_TO_MIN, 1) if rem_milli is not None else None,
                "light":    round(light_milli / _MILLI_TO_MIN, 1) if light_milli is not None else None,
                "eff":      score.get("sleep_efficiency_percentage"),
                "inbed":    round(inbed_milli / _MILLI_TO_MIN, 1) if inbed_milli is not None else None,
                "bedtime":  self._hhmm_from_iso(rec.get("start")),
                "waketime": self._hhmm_from_iso(rec.get("end")),
            }

            if score.get("respiratory_rate") is not None:
                resp[day] = score["respiratory_rate"]

        return sleep, resp

    def _parse_workouts(self, records: list):
        """workout → exercises[] + distance_km[date]/energy_kcal[date] (suma
        por día, igual que el roadmap pide para esas dos series diarias)."""
        exercises: list = []
        distance_km: dict = {}
        energy_kcal: dict = {}

        for rec in records or []:
            if not isinstance(rec, dict):
                continue
            day = self._day_from_iso(rec.get("end")) or self._day_from_iso(rec.get("start"))
            score = rec.get("score") or {}
            if not isinstance(score, dict):
                score = {}

            start_dt = rec.get("start")
            end_dt = rec.get("end")
            dur_min = None
            if start_dt and end_dt:
                try:
                    sdt = datetime.datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
                    edt = datetime.datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
                    dur_min = round((edt - sdt).total_seconds() / 60, 1)
                except Exception:
                    dur_min = None

            kilojoule = score.get("kilojoule")
            kcal = round(kilojoule * _KJ_TO_KCAL, 1) if kilojoule is not None else None
            dist_m = score.get("distance_meter")
            dist_km = round(dist_m / 1000, 3) if dist_m is not None else None

            exercises.append({
                "date":        day,
                "name":        rec.get("sport_name"),
                "dur_min":     dur_min,
                "kcal":        kcal,
                "distance_km": dist_km,
            })

            if day:
                if dist_km is not None:
                    distance_km[day] = round(distance_km.get(day, 0.0) + dist_km, 3)
                if kcal is not None:
                    energy_kcal[day] = round(energy_kcal.get(day, 0.0) + kcal, 1)

        return exercises, distance_km, energy_kcal
