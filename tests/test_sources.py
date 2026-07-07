"""Tests de la abstracción de fuentes (Fase 5A)."""
import pytest

from app.sources import get_source
from app.sources.base import Source


def test_google_health_source():
    src = get_source("google_health")
    assert isinstance(src, Source)
    assert src.name == "google_health"
    # tiene los métodos de la interfaz
    for m in ("build_auth_url", "exchange_code", "auth_state", "fetch"):
        assert callable(getattr(src, m))


# Fase 5D-A: ya no quedan fuentes en estado "stub" — Google/Oura/WHOOP/HealthKit
# están todas implementadas. (test_pending_sources_are_stubs eliminado: lista vacía.)


def test_oura_source_is_implemented():
    """Fase 5B: Oura ya no es stub — get_source('oura') no lanza."""
    src = get_source("oura")
    assert isinstance(src, Source)
    assert src.name == "oura"
    for m in ("build_auth_url", "exchange_code", "auth_state", "fetch"):
        assert callable(getattr(src, m))


def test_whoop_source_is_implemented():
    """Fase 5C: WHOOP ya no es stub — get_source('whoop') no lanza."""
    src = get_source("whoop")
    assert isinstance(src, Source)
    assert src.name == "whoop"
    for m in ("build_auth_url", "exchange_code", "auth_state", "fetch"):
        assert callable(getattr(src, m))


def test_healthkit_source_is_implemented():
    """Fase 5D-A: HealthKit ya no es stub — get_source('healthkit') no lanza."""
    src = get_source("healthkit")
    assert isinstance(src, Source)
    assert src.name == "healthkit"
    for m in ("build_auth_url", "exchange_code", "auth_state", "fetch"):
        assert callable(getattr(src, m))


def test_unknown_source_raises():
    with pytest.raises(ValueError):
        get_source("garmin")


def test_fetch_keys_match_build_dataset_signature():
    """El dict que devuelve fetch() debe tener las claves que build_dataset consume."""
    import inspect
    from app.scoring import build_dataset
    params = set(inspect.signature(build_dataset).parameters.keys())
    expected = {
        "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
        "exercises", "distance_km", "energy_kcal", "active_hours",
    }
    # build_dataset debe aceptar todas esas claves como kwargs
    missing = expected - params
    assert not missing, f"build_dataset no acepta: {missing}"
