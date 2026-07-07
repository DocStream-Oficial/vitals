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

        # ── Sueño ─────────────────────────────────────────────────────────────
        sleep = parse_sleep(
            health_api.list_all("sleep", token, save_name="sleep"), prefer=prefer
        )
        print(f"  sueño: {len(sleep)} noches")

        # ── Métricas diarias ───────────────────────────────────────────────────
        rhr  = parse_daily(health_api.list_all(DAILY_TYPES["rhr"],  token, save_name="daily_rhr"), "beatsPerMinute", prefer)
        hrv  = parse_daily(health_api.list_all(DAILY_TYPES["hrv"],  token, save_name="daily_hrv"), "averageHeartRateVariability", prefer)
        resp = parse_daily(health_api.list_all(DAILY_TYPES["resp"], token, save_name="daily_resp"), "breathsPerMinute", prefer)
        vo2  = parse_daily(health_api.list_all(DAILY_TYPES["vo2"],  token, save_name="daily_vo2"), "value", prefer)
        print(f"  FCR:{len(rhr)}  HRV:{len(hrv)}  Resp:{len(resp)}  VO2:{len(vo2)}")

        # ── Actividad ─────────────────────────────────────────────────────────
        steps = health_api.daily_rollup("steps", token, start, today, "steps", "countSum", save_name="steps")
        azm   = health_api.daily_rollup(
            "active-zone-minutes", token, start, today,
            "activeZoneMinutes", ["sumInCardioHeartZone", "sumInPeakHeartZone"], save_name="azm"
        )
        print(f"  pasos:{len(steps)}  AZM(vigorosos):{len(azm)}")

        # ── Distancia (degradación silenciosa) ────────────────────────────────
        print("  [nuevo] probando datatypes de distancia...")
        distance_km = _try_rollup_candidates(
            _DISTANCE_CANDIDATES, token, start, today,
            "distance", "sumInMeters", unit_factor=0.001, save_prefix="distance"
        )
        print(f"  distancia: {len(distance_km)} días con datos")

        # ── Energía (degradación silenciosa) ──────────────────────────────────
        print("  [nuevo] probando datatypes de energía...")
        energy_kcal = _try_rollup_candidates(
            _ENERGY_CANDIDATES, token, start, today,
            "calories", "sumInKilocalories", save_prefix="energy"
        )
        print(f"  energía: {len(energy_kcal)} días con datos")

        # ── Active hours (diferido — siempre {}) ──────────────────────────────
        # La API de Google Health no expone un endpoint limpio de steps-por-hora via daily_rollup.
        # Para reimplementar: explorar "steps" con windowSizeDays=0 o endpoint dataTypesDataPoints
        # con timeRange horario — requiere token con scope activity.read intraday.
        active_hours = {}  # Diferido — siempre vacío, se propaga como None en build_dataset

        # ── Métricas adicionales ──────────────────────────────────────────────
        spo2 = parse_daily(health_api.list_all("daily-oxygen-saturation", token, save_name="daily_spo2"), "average", prefer)
        skin = parse_daily(health_api.list_all("daily-sleep-temperature-derivations", token, save_name="daily_skintemp"), "elsius", prefer)

        # Google da 'daily-sleep-temperature-derivations' en Celsius ABSOLUTO (~32-35°C), NO como
        # desviación — a pesar del nombre "derivations". El motor (scoring.py::compute_wellbeing)
        # espera una DESVIACIÓN centrada en ~0 (mismo contrato que whoop.py/oura.py). Centramos
        # restando la media de la ventana. None-safe: 0-1 valores -> sin cambio/0.0 (mismo patrón WHOOP).
        if skin:
            _skin_mean = sum(skin.values()) / len(skin)
            skin = {d: round(v - _skin_mean, 2) for d, v in skin.items()}

        exercises = parse_exercises(health_api.list_all("exercise", token, save_name="exercise"))
        health_api.list_all("daily-heart-rate-zones", token, max_pages=1, save_name="daily_hrzones")
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
