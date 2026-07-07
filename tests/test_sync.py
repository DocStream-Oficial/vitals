"""
test_sync.py — Fase 6A: app/sync.py::run_sync multi-fuente.

Cubre:
- No-regresión #7 (el más importante): con 1 sola fuente conectada (perfil viejo,
  el 100% de instalaciones hoy incluida la del usuario), run_sync produce EXACTAMENTE
  el mismo health_compact.json que producía el sync.py viejo (comparado con mock
  de datos sintéticos claros que demuestran passthrough).
- run_sync con 2-3 fuentes mockeadas: una falla con NoToken/TokenExpired/Exception,
  las demás fusionan igual (el sync no aborta).
- Caso "todas fallan": re-lanza la excepción de la PRIMERA fuente de la lista.
- El bloque bodyage y la escritura de health_compact.json siguen funcionando igual.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.auth import TokenExpired, NoToken


def _sample_source_data(steps=8423, hrv=54.6):
    """Payload normalizado de 13 claves — mismo shape que Source.fetch()."""
    return {
        "sleep": {"2026-06-28": {"asleep": 372, "inbed": 402, "deep": 54, "rem": 86,
                                  "light": 232, "eff": 92, "bedtime": "01:01", "waketime": "07:03"}},
        "rhr": {"2026-06-28": 52.0},
        "hrv": {"2026-06-28": hrv},
        "resp": {"2026-06-28": 14.1},
        "vo2": {"2026-06-28": 47.3},
        "steps": {"2026-06-28": steps},
        "azm": {},
        "spo2": {"2026-06-28": 97.0},
        "skin": {"2026-06-28": -0.3},
        "exercises": [{"date": "2026-06-28", "name": "Run", "dur_min": 40, "kcal": 380,
                        "distance_km": 6.21}],
        "distance_km": {"2026-06-28": 6.21},
        "energy_kcal": {"2026-06-28": 2480},
        "active_hours": {},
    }


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Aísla settings.DATA_DIR y app.profile hacia tmp_path; devuelve tmp_path.

    Frescura de Alertas + Coach (Paso 4): run_sync() ahora, al final, dispara
    best-effort coach_headline.maybe_regenerate() (que puede invocar el CLI
    real de claude vía subprocess). Para que los tests de ESTE archivo (que no
    ejercitan el titular) sigan siendo deterministas/rápidos y nunca toquen el
    data/coach_headline.json real del usuario:
      1. subprocess.run se mockea por defecto para devolver returncode!=0
         (simula "CLI no disponible") -> maybe_regenerate cae a su rama de
         "CLI falló, conservo cache viejo" sin tocar la red ni tardar.
      2. coach_headline._CACHE_PATH se redirige a tmp_path (aislado).
    Los tests que SÍ quieren ejercitar el hook del titular pueden sobreescribir
    este mock de subprocess.run localmente."""
    from app import config
    from app import profile as _pm
    import subprocess as _subprocess
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(_pm, "_PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(_pm, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "BIRTHDATE", "1990-01-01")
    monkeypatch.setattr(config.settings, "WAIST_CM", 85.0)
    monkeypatch.setattr(config.settings, "SEX", "M")

    class _NoCliResult:
        returncode = 1
        stdout = ""
        stderr = "claude CLI no disponible en tests"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _NoCliResult())

    # Reimportar sync y coach_headline para que DATA_OUT / _CACHE_PATH apunten
    # a tmp_path (ambos se calculan a import-time desde settings.DATA_DIR).
    import importlib
    import app.sync as sync_mod
    import app.coach_headline as headline_mod
    import app.report as report_mod
    import app.journal as journal_mod
    importlib.reload(sync_mod)
    importlib.reload(headline_mod)
    importlib.reload(report_mod)
    importlib.reload(journal_mod)
    monkeypatch.setattr(headline_mod, "_CACHE_PATH", tmp_path / "coach_headline.json")
    # Fase 8B (paso B6): mismo aislamiento para el informe narrativo — sin esto,
    # run_sync() también dispararía report.maybe_regenerate_reports() (que llama
    # al mismo subprocess.run mockeado arriba) y ensuciaría/contaría de más las
    # aserciones de CLI-calls de los tests del titular en este archivo.
    monkeypatch.setattr(report_mod, "_CACHE_PATH", tmp_path / "reports.json")
    monkeypatch.setattr(journal_mod, "_JOURNAL_LOG_FILE", tmp_path / "journal_log.json")
    # Este archivo prueba el hook del TITULAR (coach_headline), no el informe
    # narrativo (report.py, propio de test_report.py) — no-op por defecto para
    # que las aserciones de conteo de llamadas al CLI mockeado (subprocess.run)
    # sigan midiendo SOLO al titular, sin que build_report_data() (que puede
    # legítimamente encontrar "período completo" incluso con 1 día de dataset,
    # dependiendo de la fecha real del sistema) dispare llamadas extra.
    # report_mod es el MISMO objeto módulo que sync.py importa localmente
    # (from app import report as _report) -> el patch es visible ahí también.
    monkeypatch.setattr(report_mod, "maybe_regenerate_reports", lambda *a, **kw: None)
    yield tmp_path, sync_mod
    importlib.reload(sync_mod)  # deshacer el reload al salir (buena higiene entre tests)
    importlib.reload(headline_mod)
    importlib.reload(report_mod)
    importlib.reload(journal_mod)


# ── No-regresión: 1 sola fuente conectada = passthrough exacto ──────────────

def test_run_sync_single_source_matches_legacy_shape(sync_env, monkeypatch):
    """Con profile viejo (solo 'source', sin 'sources'), run_sync usa exactamente
    los datos de esa única fuente — el criterio de no-regresión #7."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))

    fake_data = _sample_source_data(steps=8423, hrv=54.6)
    fake_source = MagicMock()
    fake_source.fetch.return_value = fake_data

    with patch("app.sync.get_source", return_value=fake_source) as mock_get_source:
        dataset = sync_mod.run_sync(45)

    mock_get_source.assert_called_once_with("google_health")
    assert dataset["summary"]["n_days"] == 1
    day = dataset["days"][0]
    assert day["steps"] == 8423
    assert day["hrv"] == 54.6

    # health_compact.json fue escrito
    out = tmp_path / "health_compact.json"
    assert out.exists()
    written = json.loads(out.read_text())
    assert written == dataset


def test_run_sync_single_source_vs_direct_build_dataset_byte_identical(sync_env):
    """Comparación EXPLÍCITA byte-a-byte: correr merge_sources({source: fetch()}) +
    build_dataset(**merged) debe dar el MISMO resultado que build_dataset(**fetch())
    directo (el comportamiento del sync.py viejo, pre-6A)."""
    tmp_path, sync_mod = sync_env
    from app.scoring import build_dataset
    from app.merge import merge_sources

    fake_data = _sample_source_data()

    # Comportamiento VIEJO (pre-6A): build_dataset directo sobre el fetch de la única fuente.
    legacy_dataset = build_dataset(**fake_data)

    # Comportamiento NUEVO (6A): pasa por merge_sources con 1 sola entrada.
    merged = merge_sources({"google_health": fake_data})
    new_dataset = build_dataset(**merged)

    assert new_dataset == legacy_dataset, "El merge de 1-sola-fuente alteró el dataset (regresión)"


# ── Multi-fuente: una falla, las demás fusionan ──────────────────────────────

def test_run_sync_multi_source_one_fails_others_merge(sync_env):
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit", "google_health"], "onboarded": True,
    }))

    hk_source = MagicMock()
    hk_source.fetch.return_value = _sample_source_data(steps=8000)
    gh_source = MagicMock()
    gh_source.fetch.side_effect = NoToken("sin token")

    def _fake_get_source(name):
        return {"healthkit": hk_source, "google_health": gh_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        dataset = sync_mod.run_sync(45)

    # healthkit sí respondió -> el sync fue exitoso pese a que google_health falló.
    assert dataset["summary"]["n_days"] == 1
    assert dataset["days"][0]["steps"] == 8000


def test_run_sync_multi_source_token_expired_one_source_still_succeeds(sync_env):
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["google_health", "oura"], "onboarded": True,
    }))

    gh_source = MagicMock()
    gh_source.fetch.side_effect = TokenExpired("expirado")
    oura_source = MagicMock()
    oura_source.fetch.return_value = _sample_source_data(hrv=60.0)

    def _fake_get_source(name):
        return {"google_health": gh_source, "oura": oura_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        dataset = sync_mod.run_sync(45)

    assert dataset["days"][0]["hrv"] == 60.0


def test_run_sync_multi_source_merges_across_two_healthy_sources(sync_env):
    """
    RONDA 3 — actualizado (antes: 'las métricas point-value se promedian de verdad',
    steps=8200/hrv=55.0). Nueva semántica end-to-end vía run_sync():
      - steps (cumulativo): gana el MAYOR valor del día (8400), no el promedio.
      - hrv (método-dependiente): CANÓNICO — con empate de 1 día de dato cada fuente,
        desempata SOURCE_PRIORITY (healthkit > google_health) -> gana healthkit (50.0).
    rhr SÍ sigue promediando (no tocado en Ronda 3): ambas fuentes traen rhr=52.0
    -> promedio=52.0, se verifica explícitamente para dejar constancia de que la
    regla vieja sigue viva donde corresponde.
    """
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit", "google_health"], "onboarded": True,
    }))

    hk_source = MagicMock()
    hk_source.fetch.return_value = _sample_source_data(steps=8000, hrv=50.0)
    gh_source = MagicMock()
    gh_source.fetch.return_value = _sample_source_data(steps=8400, hrv=60.0)

    def _fake_get_source(name):
        return {"healthkit": hk_source, "google_health": gh_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        dataset = sync_mod.run_sync(45)

    day = dataset["days"][0]
    assert day["steps"] == 8400  # MAX(8000, 8400), no promedio(8200)
    assert day["hrv"] == 50.0    # canónico (empate días -> SOURCE_PRIORITY: healthkit)
    assert day["rhr"] == 52.0    # rhr sigue promediando (ambas fuentes traen 52.0)

    # Proveniencia (Ronda 3): merge_info queda adjunto al summary con la fuente ganadora.
    merge_info = dataset["summary"].get("merge_info")
    assert merge_info is not None
    assert merge_info["hrv_source"] == "healthkit"
    assert merge_info["n_sources"] == 2


# ── Todas las fuentes fallan -> re-lanza la de la PRIMERA ────────────────────

def test_run_sync_all_sources_fail_reraises_first_source_exception(sync_env):
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["google_health", "oura"], "onboarded": True,
    }))

    gh_source = MagicMock()
    gh_source.fetch.side_effect = TokenExpired("google expirado")
    oura_source = MagicMock()
    oura_source.fetch.side_effect = NoToken("oura sin token")

    def _fake_get_source(name):
        return {"google_health": gh_source, "oura": oura_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        with pytest.raises(TokenExpired, match="google expirado"):
            sync_mod.run_sync(45)


def test_run_sync_single_source_fails_reraises(sync_env):
    """Caso pre-existente: 1 sola fuente y falla -> re-lanza (mismo comportamiento
    que el sync.py viejo, no hay nada que fusionar)."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))

    gh_source = MagicMock()
    gh_source.fetch.side_effect = NoToken("sin token")

    with patch("app.sync.get_source", return_value=gh_source):
        with pytest.raises(NoToken):
            sync_mod.run_sync(45)


def test_run_sync_generic_exception_does_not_abort_other_sources(sync_env):
    """Una Exception genérica (no TokenExpired/NoToken) en una fuente tampoco debe
    abortar el sync si otra fuente sí responde."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit", "whoop"], "onboarded": True,
    }))

    hk_source = MagicMock()
    hk_source.fetch.side_effect = RuntimeError("boom")
    whoop_source = MagicMock()
    whoop_source.fetch.return_value = _sample_source_data()

    def _fake_get_source(name):
        return {"healthkit": hk_source, "whoop": whoop_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        dataset = sync_mod.run_sync(45)

    assert dataset["summary"]["n_days"] == 1


# ── bodyage sigue calculándose igual ──────────────────────────────────────────

# ── Ventana por-fuente: HealthKit override a 365, demás fuentes sin cambio ──

def test_run_sync_healthkit_gets_365_window_other_sources_keep_generic_days(sync_env):
    """Roadmap 'fix-ingest-merge-y-ventana-healthkit', Paso 2/Criterio #6 y #8:
    _SOURCE_WINDOW_OVERRIDE debe hacer que healthkit.fetch() se llame con 365
    SIEMPRE, independiente del `days` genérico que reciba run_sync(), mientras
    que google_health/oura/whoop siguen recibiendo el `days` genérico sin cambio
    (no-regresión: el override NO debe filtrarse a las demás fuentes)."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit", "google_health"], "onboarded": True,
    }))

    hk_source = MagicMock()
    hk_source.fetch.return_value = _sample_source_data(steps=8000)
    gh_source = MagicMock()
    gh_source.fetch.return_value = _sample_source_data(steps=8400)

    def _fake_get_source(name):
        return {"healthkit": hk_source, "google_health": gh_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        sync_mod.run_sync(45)  # days genérico = 45

    hk_source.fetch.assert_called_once_with(365)
    gh_source.fetch.assert_called_once_with(45)


def test_run_sync_healthkit_override_independent_of_generic_days_value(sync_env):
    """Aunque run_sync() se invoque con un `days` genérico distinto de 45,
    healthkit sigue recibiendo 365 (el override es fijo, no relativo)."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit", "oura"], "onboarded": True,
    }))

    hk_source = MagicMock()
    hk_source.fetch.return_value = _sample_source_data()
    oura_source = MagicMock()
    oura_source.fetch.return_value = _sample_source_data()

    def _fake_get_source(name):
        return {"healthkit": hk_source, "oura": oura_source}[name]

    with patch("app.sync.get_source", side_effect=_fake_get_source):
        sync_mod.run_sync(90)  # days genérico distinto del default

    hk_source.fetch.assert_called_once_with(365)
    oura_source.fetch.assert_called_once_with(90)


def test_run_sync_single_google_source_unaffected_by_healthkit_override(sync_env):
    """Criterio #8 — no-regresión explícita: con SOLO google_health conectado
    (caso más común hoy), el comportamiento de run_sync/fetch no cambia en nada
    por la existencia del override de healthkit."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    gh_source = MagicMock()
    gh_source.fetch.return_value = _sample_source_data()

    with patch("app.sync.get_source", return_value=gh_source):
        sync_mod.run_sync(45)

    gh_source.fetch.assert_called_once_with(45)


def test_run_sync_computes_bodyage_when_profile_complete(sync_env):
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "birthdate": "1985-01-01", "waist_cm": 85.0,
        "sex": "M", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    with patch("app.sync.get_source", return_value=fake_source):
        dataset = sync_mod.run_sync(45)

    assert "bodyage" in dataset["summary"]
    assert dataset["summary"]["bodyage"] is not None


# ── Paso 4: hook del titular del Coach (coach_headline) dentro de run_sync ──

def test_run_sync_generates_headline_cache_on_first_sync(sync_env, monkeypatch):
    """1er sync (sin cache previo): la firma es nueva -> el hook llama al CLI
    (mockeado) y escribe data/coach_headline.json."""
    import subprocess
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    class _OkResult:
        returncode = 0
        stdout = "Buen día, tu recuperación se ve estable.\n"
        stderr = ""

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or _OkResult())

    with patch("app.sync.get_source", return_value=fake_source):
        sync_mod.run_sync(45)

    assert len(calls) == 1, "1er sync (sin cache) debe llamar al CLI del titular exactamente 1 vez"
    cache_path = tmp_path / "coach_headline.json"
    assert cache_path.exists()
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    assert cache["headline"] == "Buen día, tu recuperación se ve estable."


def test_run_sync_second_sync_same_signature_skips_cli(sync_env, monkeypatch):
    """2º sync con datos ~idénticos (misma firma coarse) -> el hook NO vuelve a
    llamar al CLI (criterio de costo acotado del roadmap)."""
    import subprocess
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    class _OkResult:
        returncode = 0
        stdout = "Titular estable.\n"
        stderr = ""

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: calls.append(1) or _OkResult())

    with patch("app.sync.get_source", return_value=fake_source):
        sync_mod.run_sync(45)   # 1er sync: genera
        sync_mod.run_sync(45)   # 2º sync: misma data -> misma firma -> cache hit

    assert len(calls) == 1, "el 2º sync con la misma firma NO debe volver a llamar al CLI"
    cache = json.loads((tmp_path / "coach_headline.json").read_text(encoding="utf-8"))
    generated_at_after_both = cache["generated_at"]
    assert cache["headline"] == "Titular estable."
    assert generated_at_after_both  # sigue presente, no se regeneró


def test_run_sync_headline_hook_never_breaks_sync_when_cli_raises(sync_env, monkeypatch):
    """Si el hook del titular lanza una excepción inesperada (no solo CLI con
    returncode!=0, sino una excepción real), run_sync() debe completar igual
    y devolver el dataset — el titular es best-effort, nunca crítico."""
    import subprocess
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    def _boom(*a, **kw):
        raise RuntimeError("catástrofe del CLI")

    monkeypatch.setattr(subprocess, "run", _boom)

    with patch("app.sync.get_source", return_value=fake_source):
        dataset = sync_mod.run_sync(45)  # NO debe lanzar

    assert dataset["summary"]["n_days"] == 1
    # health_compact.json sí se escribió pese al fallo del hook del titular.
    assert (tmp_path / "health_compact.json").exists()


def test_run_sync_captures_prev_vo2max_from_old_dataset(sync_env, monkeypatch):
    """El hook lee el vo2max del health_compact.json VIEJO (antes de
    sobreescribirlo) e inyecta summary['_prev_vo2max'] para que changes.py
    pueda detectar el cambio de VO2max en el sync siguiente."""
    import subprocess
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "birthdate": "1985-01-01", "waist_cm": 85.0,
        "sex": "M", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: type("R", (), {
        "returncode": 1, "stdout": "", "stderr": "sin CLI"
    })())

    with patch("app.sync.get_source", return_value=fake_source):
        sync_mod.run_sync(45)  # 1er sync: no hay dataset viejo -> sin _prev_vo2max
        dataset2 = sync_mod.run_sync(45)  # 2º sync: ya hay un dataset viejo con bodyage/vo2max

    # El 2º sync debió poder leer el vo2max del dataset del 1er sync.
    assert "_prev_vo2max" in dataset2["summary"] or dataset2["summary"].get("bodyage") is None
