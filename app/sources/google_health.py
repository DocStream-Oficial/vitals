"""
app/sources/google_health.py — Adaptador Google Health que implementa Source.

Orquesta app/auth.py + app/health_api.py + app/parsers.py SIN duplicar su lógica.
fetch() = exactamente las líneas 56-115 del sync.py original, encapsuladas aquí.

Backward-compat: lee data/token.json (el token vivo del usuario — no se renombra).
"""
from __future__ import annotations

import datetime

from app.sources.base import Source, TokenExpired, NoToken  # noqa: F401 (re-export)
import app.auth as _auth
import app.health_api as health_api
from app.parsers import parse_sleep, parse_daily, parse_exercises
from app.config import settings

# ── Tipos de métricas diarias ─────────────────────────────────────────────────
DAILY_TYPES = {
    "rhr":  "daily-resting-heart-rate",
    "hrv":  "daily-heart-rate-variability",
    "resp": "daily-respiratory-rate",
    "vo2":  "daily-vo2-max",
}

_DISTANCE_CANDIDATES = ["distance", "daily-distance", "distance-delta"]
_ENERGY_CANDIDATES   = ["daily-calories-expended", "total-calories-burned", "active-calories-burned"]


def _try_rollup_candidates(candidates, token, start, end, key, subfields, unit_factor=1.0, save_prefix=None):
    """Intenta daily_rollup con varios datatypes. Retorna el primero con ≥1 dato o {} si todos fallan."""
    for dtype in candidates:
        sname = f"{save_prefix}_{dtype.replace('-','_')}" if save_prefix else None
        try:
            result = health_api.daily_rollup(dtype, token, start, end, key, subfields, save_name=sname)
        except Exception as exc:
            print(f"   (aviso) {dtype}: excepción → {exc}")
            result = {}
        if result:
            print(f"   {dtype}: {len(result)} días con datos ✓")
            if unit_factor != 1.0:
                result = {d: round(v * unit_factor, 2) for d, v in result.items() if v is not None}
            return result
        else:
            print(f"   {dtype}: sin datos (degradado)")
    return {}


class GoogleHealthSource(Source):
    """Adaptador Google Health API.

    auth: delega a app.auth (build_auth_url / exchange_code / access_token / auth_state / mark_expired).
    fetch: orquesta health_api + parsers para producir el dict normalizado.
    """

    name = "google_health"

    # ---------------------------------------------------------------- auth

    def build_auth_url(self, state: str) -> str:
        """Delega a app.auth.build_auth_url — sin duplicar la lógica OAuth."""
        return _auth.build_auth_url(state)

    def exchange_code(self, code: str) -> dict:
        """Delega a app.auth.exchange_code — guarda token en data/token.json."""
        return _auth.exchange_code(code)

    def auth_state(self) -> dict:
        """Delega a app.auth.auth_state."""
        return _auth.auth_state()

    # ---------------------------------------------------------------- datos

    def fetch(self, days: int = 45) -> dict:
        """Jala datos de Google Health de los últimos `days` días.

        Lanza TokenExpired o NoToken si el token no es válido.
        Retorna dict con las claves que build_dataset(**data) espera.
        """
        # Obtener token (puede lanzar TokenExpired / NoToken)
        try:
            token = _auth.access_token()
        except TokenExpired:
            _auth.mark_expired()
            raise
        # NoToken se propaga sin modificación

        prefer = settings.PREFER_PLATFORM
        today = datetime.date.today()
        start = today - datetime.timedelta(days=days)
        print(f"=== SYNC (google_health): últimos {days} días · fuente preferida: {prefer} ===")

        # Perf 23-jul: las ~12 llamadas HTTP de abajo eran SECUENCIALES y el sync
        # tardaba ~57s medidos (cada list_all pagina contra la API de Google). Son
        # independientes entre sí: token read-only, requests sin Session compartida,
        # y cada save_name escribe a un archivo DISTINTO -> se lanzan en paralelo
        # (6 workers) y el muro de espera se reduce a la llamada más lenta.
        # _try_rollup_candidates conserva su prueba SECUENCIAL interna (prueba
        # candidatos hasta que uno funcione), solo corre en paralelo CON las demás.
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=6) as _pool:
            _f_sleep = _pool.submit(health_api.list_all, "sleep", token, save_name="sleep")
            _f_rhr = _pool.submit(health_api.list_all, DAILY_TYPES["rhr"], token, save_name="daily_rhr")
            _f_hrv = _pool.submit(health_api.list_all, DAILY_TYPES["hrv"], token, save_name="daily_hrv")
            _f_resp = _pool.submit(health_api.list_all, DAILY_TYPES["resp"], token, save_name="daily_resp")
            _f_vo2 = _pool.submit(health_api.list_all, DAILY_TYPES["vo2"], token, save_name="daily_vo2")
            _f_steps = _pool.submit(health_api.daily_rollup, "steps", token, start, today,
                                    "steps", "countSum", save_name="steps")
            _f_azm = _pool.submit(health_api.daily_rollup, "active-zone-minutes", token, start, today,
                                  "activeZoneMinutes", ["sumInCardioHeartZone", "sumInPeakHeartZone"],
                                  save_name="azm")
            _f_dist = _pool.submit(_try_rollup_candidates, _DISTANCE_CANDIDATES, token, start, today,
                                   "distance", "sumInMeters", unit_factor=0.001, save_prefix="distance")
            _f_ener = _pool.submit(_try_rollup_candidates, _ENERGY_CANDIDATES, token, start, today,
                                   "calories", "sumInKilocalories", save_prefix="energy")
            _f_spo2 = _pool.submit(health_api.list_all, "daily-oxygen-saturation", token, save_name="daily_spo2")
            _f_skin = _pool.submit(health_api.list_all, "daily-sleep-temperature-derivations", token,
                                   save_name="daily_skintemp")
            _f_exer = _pool.submit(health_api.list_all, "exercise", token, save_name="exercise")
            _f_zones = _pool.submit(health_api.list_all, "daily-heart-rate-zones", token,
                                    max_pages=1, save_name="daily_hrzones")

            sleep = parse_sleep(_f_sleep.result(), prefer=prefer)
            rhr = parse_daily(_f_rhr.result(), "beatsPerMinute", prefer)
            hrv = parse_daily(_f_hrv.result(), "averageHeartRateVariability", prefer)
            resp = parse_daily(_f_resp.result(), "breathsPerMinute", prefer)
            vo2 = parse_daily(_f_vo2.result(), "value", prefer)
            steps = _f_steps.result()
            azm = _f_azm.result()
            distance_km = _f_dist.result()
            energy_kcal = _f_ener.result()

        print(f"  sueño: {len(sleep)} noches")
        print(f"  FCR:{len(rhr)}  HRV:{len(hrv)}  Resp:{len(resp)}  VO2:{len(vo2)}")
        print(f"  pasos:{len(steps)}  AZM(vigorosos):{len(azm)}")
        print(f"  distancia: {len(distance_km)} días con datos")
        print(f"  energía: {len(energy_kcal)} días con datos")

        # ── Active hours (diferido — siempre {}) ──────────────────────────────
        # La API de Google Health no expone un endpoint limpio de steps-por-hora via daily_rollup.
        # Para reimplementar: explorar "steps" con windowSizeDays=0 o endpoint dataTypesDataPoints
        # con timeRange horario — requiere token con scope activity.read intraday.
        active_hours = {}  # Diferido — siempre vacío, se propaga como None en build_dataset

        # ── Métricas adicionales (ya descargadas en paralelo arriba) ─────────
        spo2 = parse_daily(_f_spo2.result(), "average", prefer)
        skin = parse_daily(_f_skin.result(), "elsius", prefer)

        # Google da 'daily-sleep-temperature-derivations' en Celsius ABSOLUTO (~32-35°C), NO como
        # desviación — a pesar del nombre "derivations". El motor (scoring.py::compute_wellbeing)
        # espera una DESVIACIÓN centrada en ~0 (mismo contrato que whoop.py/oura.py). Centramos
        # restando la media de la ventana. None-safe: 0-1 valores -> sin cambio/0.0 (mismo patrón WHOOP).
        if skin:
            _skin_mean = sum(skin.values()) / len(skin)
            skin = {d: round(v - _skin_mean, 2) for d, v in skin.items()}

        exercises = parse_exercises(_f_exer.result())
        _f_zones.result()  # dump crudo de zonas (solo se persiste el archivo, sin parse)
        print(f"  SpO2:{len(spo2)}  TempPiel:{len(skin)}  Entrenamientos:{len(exercises)}")

        return {
            "sleep":        sleep,
            "rhr":          rhr,
            "hrv":          hrv,
            "resp":         resp,
            "vo2":          vo2,
            "steps":        steps,
            "azm":          azm,
            "spo2":         spo2,
            "skin":         skin,
            "exercises":    exercises,
            "distance_km":  distance_km,
            "energy_kcal":  energy_kcal,
            "active_hours": active_hours,
        }
