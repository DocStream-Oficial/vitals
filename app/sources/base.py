"""
app/sources/base.py — Interfaz abstracta Source para todas las fuentes de datos de salud.

Define el contrato que deben implementar Google Health, Oura, WHOOP, HealthKit, etc.
Las excepciones TokenExpired/NoToken se re-exportan aquí para que los callers
(sync.py, main.py) las importen desde un solo lugar estable.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

# Re-exportar desde auth.py para no romper imports existentes que ya hacen
#   from app.auth import TokenExpired, NoToken
# Aquí los re-exportamos para que los nuevos callers puedan hacer
#   from app.sources.base import TokenExpired, NoToken
from app.auth import TokenExpired, NoToken  # noqa: F401


class Source(ABC):
    """Interfaz común para todas las fuentes de datos de salud.

    Cada fuente implementa:
    - auth OAuth lifecycle: build_auth_url / exchange_code / auth_state
    - fetch(): jala datos y devuelve el dict normalizado que build_dataset(**data) consume

    El dict de fetch() tiene estas claves (todas requeridas, pueden ser {} o []):
        sleep        : dict[date, {asleep, inbed, ...}]
        rhr          : dict[date, float]
        hrv          : dict[date, float]
        resp         : dict[date, float]
        vo2          : dict[date, float]
        steps        : dict[date, int]
        azm          : dict[date, int]
        spo2         : dict[date, float]
        skin         : dict[date, float]
        exercises    : list[dict]
        distance_km  : dict[date, float]
        energy_kcal  : dict[date, float]
        active_hours : dict  (siempre {} por ahora — diferido)
    """

    name: str  # identificador de la fuente, e.g. "google_health"

    # ---------------------------------------------------------------- auth

    @abstractmethod
    def build_auth_url(self, state: str) -> str:
        """Construye la URL de autorización OAuth para esta fuente."""
        ...

    @abstractmethod
    def exchange_code(self, code: str) -> dict:
        """Intercambia el authorization code por tokens; los persiste."""
        ...

    @abstractmethod
    def auth_state(self) -> dict:
        """Devuelve {status, days_left} del token activo.
        status: 'active' | 'expiring' | 'expired' | 'no_token'
        """
        ...

    # ---------------------------------------------------------------- datos

    @abstractmethod
    def fetch(self, days: int) -> dict:
        """Jala datos de los últimos `days` días y devuelve el dict normalizado.

        Puede lanzar:
            TokenExpired  — si el refresh token expiró (invalid_grant)
            NoToken       — si no hay token guardado
        El caller (sync.py) maneja ambas excepciones.

        Retorna un dict con exactamente estas claves (encaja directo en build_dataset(**data)):
            sleep, rhr, hrv, resp, vo2, steps, azm, spo2, skin, exercises,
            distance_km, energy_kcal, active_hours
        """
        ...
