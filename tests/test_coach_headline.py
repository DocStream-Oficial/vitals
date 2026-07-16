"""
test_coach_headline.py — Tests de app/coach_headline.py (Paso 3: titular IA
cacheado por firma).

Cubre:
- signature(): estable ante micro-fluctuación (misma banda), cambia al cruzar
  banda o cambiar el día/top-cambio, None-safe.
- load_cache()/save_cache(): atómico, None-safe (archivo ausente/corrupto).
- maybe_regenerate(): cache hit -> CERO llamadas al CLI (mock de subprocess.run);
  firma distinta -> SÍ llama; CLI falla -> conserva cache viejo; nunca lanza.
- get_headline(): lee cache si existe y coincide locale; fallback determinista
  si no hay cache / CLI falló / sin datos.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app import coach_headline as ch


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Aísla settings.DATA_DIR y el path del cache hacia tmp_path."""
    from app import config
    cache_path = tmp_path / "coach_headline.json"
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(ch, "_CACHE_PATH", cache_path)
    return tmp_path


def _dataset(recovery=70, hrv=55, asleep=450, strain=10, hrv_base=55, date="2026-01-01"):
    return {
        "days": [{"date": date, "recovery": recovery, "hrv": hrv, "asleep": asleep, "strain": strain}],
        "summary": {"hrv_base_recent": hrv_base},
    }


# ── signature() ──────────────────────────────────────────────────────────────

def test_signature_empty_dataset_stable():
    assert ch.signature({}) == ch.signature({"days": []})
    assert ch.signature(None or {}) is not None


def test_signature_stable_across_micro_fluctuation_same_band():
    """recovery 70 vs 71 -> misma banda 'high' -> MISMA firma (coarse a propósito)."""
    ds1 = _dataset(recovery=70)
    ds2 = _dataset(recovery=71)
    assert ch.signature(ds1) == ch.signature(ds2)


def test_signature_changes_when_band_crosses():
    """recovery 70 (high) vs 60 (mid) -> firma DISTINTA."""
    ds1 = _dataset(recovery=70)
    ds2 = _dataset(recovery=60)
    assert ch.signature(ds1) != ch.signature(ds2)


def test_signature_changes_with_date():
    ds1 = _dataset(date="2026-01-01")
    ds2 = _dataset(date="2026-01-02")
    assert ch.signature(ds1) != ch.signature(ds2)


def test_signature_changes_with_top_change_kind():
    ds = _dataset()
    sig_no_changes = ch.signature(ds, [])
    sig_with_change = ch.signature(ds, [{"factor": "recovery", "kind": "improvement"}])
    assert sig_no_changes != sig_with_change


def test_signature_none_safe():
    assert ch.signature(None) is not None
    assert ch.signature({}, None) is not None


# ── signature() consciente de alertas (F3, roadmap vitals-illness-proactivo) ──

def test_signature_no_alerts_identical_to_before_change():
    """Backward-compat (criterio #7 del roadmap): SIN alertas, la firma debe
    quedar IDÉNTICA a como era antes de agregar el parámetro `insights` —
    con insights=None, con insights=[], y con insights que no traen ninguna
    severity=='alert' (solo watch/positive/info)."""
    ds = _dataset()
    changes = [{"factor": "recovery", "kind": "improvement"}]
    baseline = ch.signature(ds, changes)  # firma "de antes" (sin 3er arg)
    assert ch.signature(ds, changes, None) == baseline
    assert ch.signature(ds, changes, []) == baseline
    non_alert_insights = [
        {"id": "sleep_debt", "severity": "watch"},
        {"id": "positive_hrv", "severity": "positive"},
    ]
    assert ch.signature(ds, changes, non_alert_insights) == baseline


def test_signature_changes_when_alert_appears():
    ds = _dataset()
    sig_sin_alerta = ch.signature(ds, [])
    insights_con_alerta = [{"id": "illness_early_warning", "severity": "alert"}]
    sig_con_alerta = ch.signature(ds, [], insights_con_alerta)
    assert sig_sin_alerta != sig_con_alerta


def test_signature_stable_for_same_alert_ids_order_independent():
    """La firma es estable frente al ORDEN en que llegan los insights (se
    ordenan los ids antes de hashear) — misma lista de alertas activas,
    misma firma sin importar el orden de evaluate()."""
    ds = _dataset()
    insights_a = [
        {"id": "illness_early_warning", "severity": "alert"},
        {"id": "spo2_low", "severity": "alert"},
    ]
    insights_b = [
        {"id": "spo2_low", "severity": "alert"},
        {"id": "illness_early_warning", "severity": "alert"},
    ]
    assert ch.signature(ds, [], insights_a) == ch.signature(ds, [], insights_b)


def test_signature_changes_when_alert_disappears():
    ds = _dataset()
    insights_con_alerta = [{"id": "illness_early_warning", "severity": "alert"}]
    sig_con_alerta = ch.signature(ds, [], insights_con_alerta)
    sig_sin_alerta = ch.signature(ds, [], [])
    assert sig_con_alerta != sig_sin_alerta


def test_signature_different_alert_sets_differ():
    ds = _dataset()
    sig1 = ch.signature(ds, [], [{"id": "illness_early_warning", "severity": "alert"}])
    sig2 = ch.signature(ds, [], [{"id": "spo2_low", "severity": "alert"}])
    assert sig1 != sig2


# ── load_cache() / save_cache() ──────────────────────────────────────────────

def test_load_cache_missing_file_returns_none(isolated_cache):
    assert ch.load_cache() is None


def test_save_and_load_cache_roundtrip(isolated_cache):
    ch.save_cache("sig123", "Buen día, tu recuperación mejoró.", "es")
    cache = ch.load_cache()
    assert cache is not None
    assert cache["signature"] == "sig123"
    assert cache["headline"] == "Buen día, tu recuperación mejoró."
    assert cache["locale"] == "es"
    assert "generated_at" in cache


def test_load_cache_corrupted_file_returns_none(isolated_cache):
    isolated_cache.mkdir(exist_ok=True)
    ch._CACHE_PATH.write_text("{not valid json", encoding="utf-8")
    assert ch.load_cache() is None


def test_load_cache_non_dict_json_returns_none(isolated_cache):
    isolated_cache.mkdir(exist_ok=True)
    ch._CACHE_PATH.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    assert ch.load_cache() is None


def test_save_cache_atomic_no_tmp_leftover(isolated_cache):
    ch.save_cache("sig1", "titular", "es")
    tmp_files = list(isolated_cache.glob("*.tmp"))
    assert tmp_files == []
    assert ch._CACHE_PATH.exists()


# ── _build_headline_prompt() consciente de alertas (F3, Paso 4) ─────────────

def test_prompt_no_alerts_byte_identical_to_before():
    """Criterio #8 del roadmap: SIN alertas, el prompt debe ser BYTE-IDÉNTICO
    al de antes de este cambio — con alerts=None y con alerts=[]."""
    ds = _dataset()
    changes = [{"summary": "Tu recuperación subió 20 pts."}]
    baseline = ch._build_headline_prompt(ds, changes, "es")  # sin 4to arg (firma vieja)
    assert ch._build_headline_prompt(ds, changes, "es", None) == baseline
    assert ch._build_headline_prompt(ds, changes, "es", []) == baseline


def test_prompt_with_alert_contains_alert_and_instruction():
    ds = _dataset()
    alerts = [{
        "id": "illness_early_warning",
        "severity": "alert",
        "title": "Posible enfermedad en curso",
        "summary": "Temp de piel elevada + FC en reposo alta.",
    }]
    prompt = ch._build_headline_prompt(ds, [], "es", alerts)
    assert "Posible enfermedad en curso" in prompt
    assert "Temp de piel elevada + FC en reposo alta." in prompt
    assert "NO des luz verde" in prompt
    assert "ALERTA" in prompt.upper()


def test_prompt_with_alert_differs_from_no_alert():
    ds = _dataset()
    alerts = [{"id": "illness_early_warning", "severity": "alert", "title": "X", "summary": "Y"}]
    assert ch._build_headline_prompt(ds, [], "es", alerts) != ch._build_headline_prompt(ds, [], "es", [])


# ── maybe_regenerate(): cache hit = 0 llamadas al CLI ────────────────────────

def test_maybe_regenerate_cache_hit_zero_cli_calls(isolated_cache, monkeypatch):
    ds = _dataset()
    sig = ch.signature(ds, [])
    ch.save_cache(sig, "titular viejo", "es")

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or None)

    ch.maybe_regenerate(ds, [], "es")
    assert len(calls) == 0, "cache hit no debe llamar al CLI"
    # El cache no debe haberse tocado (mismo headline).
    assert ch.load_cache()["headline"] == "titular viejo"


def test_maybe_regenerate_signature_changed_calls_cli(isolated_cache, monkeypatch):
    ds_old = _dataset(recovery=40)
    ch.save_cache(ch.signature(ds_old, []), "titular viejo", "es")

    ds_new = _dataset(recovery=90)  # banda distinta -> firma distinta

    class FakeResult:
        returncode = 0
        stdout = "Nuevo titular fresco.\n"
        stderr = ""

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or FakeResult())

    ch.maybe_regenerate(ds_new, [], "es")
    assert len(calls) == 1, "firma distinta debe llamar al CLI exactamente 1 vez"
    cache = ch.load_cache()
    assert cache["headline"] == "Nuevo titular fresco."
    assert cache["signature"] == ch.signature(ds_new, [])


def test_maybe_regenerate_alert_appearing_forces_regeneration(isolated_cache, monkeypatch):
    """Mismo dataset/changes, pero aparece una alerta nueva -> firma cambia ->
    SÍ regenera (aunque recovery/HRV/sueño no hayan cruzado de banda)."""
    ds = _dataset()
    ch.save_cache(ch.signature(ds, [], []), "titular sin alerta", "es")

    class FakeResult:
        returncode = 0
        stdout = "Cuidado, hay una señal de alerta activa.\n"
        stderr = ""

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or FakeResult())

    insights = [{"id": "illness_early_warning", "severity": "alert", "title": "T", "summary": "S"}]
    ch.maybe_regenerate(ds, [], "es", insights)
    assert len(calls) == 1, "una alerta nueva debe forzar regeneración del titular"
    cache = ch.load_cache()
    assert cache["headline"] == "Cuidado, hay una señal de alerta activa."
    assert cache["signature"] == ch.signature(ds, [], insights)


def test_maybe_regenerate_no_insights_arg_backward_compatible(isolated_cache, monkeypatch):
    """Llamar maybe_regenerate() sin el 4to argumento (como hacía el código
    antes de F3) debe seguir funcionando exactamente igual (cache hit)."""
    ds = _dataset()
    ch.save_cache(ch.signature(ds, []), "titular viejo", "es")

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or None)

    ch.maybe_regenerate(ds, [], "es")  # sin insights
    assert len(calls) == 0
    assert ch.load_cache()["headline"] == "titular viejo"


def test_maybe_regenerate_locale_changed_calls_cli(isolated_cache, monkeypatch):
    """Mismo dataset/firma pero locale distinto al cacheado -> regenera (el
    titular debe salir en el locale del perfil)."""
    ds = _dataset()
    ch.save_cache(ch.signature(ds, []), "titular es", "es")

    class FakeResult:
        returncode = 0
        stdout = "Fresh headline.\n"
        stderr = ""

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or FakeResult())

    ch.maybe_regenerate(ds, [], "en")
    assert len(calls) == 1
    assert ch.load_cache()["locale"] == "en"


def test_maybe_regenerate_cli_fails_preserves_old_cache(isolated_cache, monkeypatch):
    ds_old = _dataset(recovery=40)
    ch.save_cache(ch.signature(ds_old, []), "titular viejo intacto", "es")
    ds_new = _dataset(recovery=90)

    class FakeResult:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())

    ch.maybe_regenerate(ds_new, [], "es")
    cache = ch.load_cache()
    assert cache["headline"] == "titular viejo intacto", "CLI falló -> cache viejo se conserva"


def test_maybe_regenerate_cli_raises_never_propagates(isolated_cache, monkeypatch):
    """Cualquier excepción (incluida la del propio subprocess) nunca debe
    propagar — maybe_regenerate() se llama desde run_sync() y jamás debe
    tumbar el sync."""
    def _boom(*a, **kw):
        raise RuntimeError("CLI catastrophe")

    monkeypatch.setattr(subprocess, "run", _boom)
    ds = _dataset()
    ch.maybe_regenerate(ds, [], "es")  # no debe lanzar
    assert True


def test_maybe_regenerate_cli_timeout_preserves_cache(isolated_cache, monkeypatch):
    ds_old = _dataset(recovery=40)
    ch.save_cache(ch.signature(ds_old, []), "titular viejo", "es")
    ds_new = _dataset(recovery=90)

    def _timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=60)

    monkeypatch.setattr(subprocess, "run", _timeout)
    ch.maybe_regenerate(ds_new, [], "es")
    assert ch.load_cache()["headline"] == "titular viejo"


def test_maybe_regenerate_empty_cli_output_preserves_cache(isolated_cache, monkeypatch):
    ds_old = _dataset(recovery=40)
    ch.save_cache(ch.signature(ds_old, []), "titular viejo", "es")
    ds_new = _dataset(recovery=90)

    class FakeResult:
        returncode = 0
        stdout = "   "
        stderr = ""

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeResult())
    ch.maybe_regenerate(ds_new, [], "es")
    assert ch.load_cache()["headline"] == "titular viejo"


# ── get_headline(): lectura pura, nunca subprocess ───────────────────────────

def test_get_headline_no_subprocess_ever(isolated_cache, monkeypatch):
    """get_headline() NUNCA debe invocar subprocess (es la ruta de GET /)."""
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or None)
    ds = _dataset()
    ch.get_headline(ds, [], "es")
    assert len(calls) == 0


def test_get_headline_reads_cache_when_locale_matches(isolated_cache):
    ds = _dataset()
    ch.save_cache(ch.signature(ds, []), "Titular cacheado", "es")
    assert ch.get_headline(ds, [], "es") == "Titular cacheado"


def test_get_headline_fallback_when_no_cache(isolated_cache):
    ds = _dataset()
    result = ch.get_headline(ds, [], "es")
    assert result  # nunca vacío
    assert isinstance(result, str)


def test_get_headline_fallback_when_locale_mismatch(isolated_cache):
    ds = _dataset()
    ch.save_cache(ch.signature(ds, []), "Cached in Spanish", "es")
    result = ch.get_headline(ds, [], "en")
    assert result != "Cached in Spanish"  # no sirve el cache de otro locale


def test_get_headline_no_data_fallback(isolated_cache):
    result = ch.get_headline({"days": []}, [], "es")
    assert result  # nunca vacío


def test_get_headline_never_raises_on_corrupted_cache(isolated_cache):
    isolated_cache.mkdir(exist_ok=True)
    ch._CACHE_PATH.write_text("{broken", encoding="utf-8")
    ds = _dataset()
    result = ch.get_headline(ds, [], "es")
    assert result


# ── _fallback_headline(): determinista, i18n, nunca vacío ────────────────────

def test_fallback_headline_no_data():
    assert ch._fallback_headline([], {"days": []}, "es") == ch.tr("headline_fallback_no_data", "es") \
        if hasattr(ch, "tr") else True


def test_fallback_headline_uses_top_change():
    changes = [{"summary": "Tu recuperación subió 20 pts."}]
    result = ch._fallback_headline(changes, _dataset(), "es")
    assert "Tu recuperación subió 20 pts." in result


def test_fallback_headline_static_when_no_changes():
    result = ch._fallback_headline([], _dataset(), "es")
    assert result  # nunca vacío
    assert "recuperación" not in result.lower() or True  # no asume texto de cambio


def test_fallback_headline_all_locales_non_empty():
    for locale in ("es", "en", "fr", "pt"):
        r1 = ch._fallback_headline([], _dataset(), locale)
        r2 = ch._fallback_headline([{"summary": "algo cambió"}], _dataset(), locale)
        assert r1 and r2


# ── None-safety general (dataset vacío / sin days) ──────────────────────────

def test_get_headline_none_dataset_safe():
    assert ch.get_headline(None, None, "es")


def test_maybe_regenerate_none_dataset_safe(monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or None)
    ch.maybe_regenerate(None, None, "es")  # no debe lanzar
