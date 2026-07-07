"""
app/sources/__init__.py — Registro de fuentes de datos de salud.

get_source(name) → instancia de Source lista para usar.
Todas las fuentes (Google/Oura/WHOOP/HealthKit) ya están implementadas (5A-5D).
_NotImplementedSource queda como molde por si se agrega una fuente futura.
"""
from __future__ import annotations

from app.sources.base import Source, TokenExpired, NoToken  # noqa: F401


def get_source(name: str) -> Source:
    """Devuelve el adaptador Source correspondiente al nombre dado.

    Fuentes disponibles:
        "google_health"  → GoogleHealthSource (implementado, Fase 5A)
        "oura"           → OuraSource (implementado, Fase 5B)
        "whoop"          → WhoopSource (implementado, Fase 5C)
        "healthkit"      → HealthKitSource (implementado PUSH, Fase 5D-A)

    Args:
        name: identificador de la fuente (case-sensitive).

    Raises:
        NotImplementedError: si la fuente existe pero aún no está implementada (stubs).
        ValueError: si el nombre de fuente no existe en el registro.
    """
    if name == "google_health":
        from app.sources.google_health import GoogleHealthSource
        return GoogleHealthSource()

    if name == "oura":
        from app.sources.oura import OuraSource
        return OuraSource()

    if name == "whoop":
        from app.sources.whoop import WhoopSource
        return WhoopSource()

    if name == "healthkit":
        from app.sources.healthkit import HealthKitSource
        return HealthKitSource()

    raise ValueError(
        f"Fuente desconocida: '{name}'. "
        f"Fuentes disponibles: google_health, oura (5B), whoop (5C), healthkit (5D)."
    )


class _NotImplementedSource(Source):
    """Stub para fuentes pendientes. Lanza NotImplementedError en cualquier operación."""

    def __init__(self, source_name: str):
        self.name = source_name

    def build_auth_url(self, state: str) -> str:
        raise NotImplementedError(
            f"Fuente '{self.name}' aún no implementada (Fase 5B/C/D). "
            f"Por ahora solo está disponible 'google_health'."
        )

    def exchange_code(self, code: str) -> dict:
        raise NotImplementedError(
            f"Fuente '{self.name}' aún no implementada (Fase 5B/C/D)."
        )

    def auth_state(self) -> dict:
        raise NotImplementedError(
            f"Fuente '{self.name}' aún no implementada (Fase 5B/C/D)."
        )

    def fetch(self, days: int) -> dict:
        raise NotImplementedError(
            f"Fuente '{self.name}' aún no implementada (Fase 5B/C/D). "
            f"Por ahora solo está disponible 'google_health'."
        )
