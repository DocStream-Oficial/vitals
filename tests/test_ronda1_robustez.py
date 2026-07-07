"""
test_ronda1_robustez.py — Ronda 1 (auditoría 2026-07-01): atomicidad, single-flight
de sync, lock de clase de WHOOP, GZip, coach no-bloqueante.

Cubre los criterios de aceptación del roadmap ROADMAP-vitals-ronda1-robustez.md:
- atomic_write_text: contenido correcto, sin .tmp residual; ante fallo de os.replace
  el archivo original queda intacto (no truncado/corrupto).
- run_sync() single-flight: con _SYNC_LOCK tomado lanza SyncInProgress; /api/sync y
  /api/ingest responden {status:"already_running"} (HTTP 200) sin disparar fetch;
  el job del scheduler sale limpio; el lock SIEMPRE se libera (éxito y excepción,
  incluido el `raise errors[sources[0]]`).
- WhoopSource._refresh_lock es de CLASE (mismo objeto entre instancias).
- GET / y /api/data con Accept-Encoding: gzip → Content-Encoding: gzip.
"""
from __future__ import annotations

import importlib
import json
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.fsutil import atomic_write_text


# ── helpers / fixtures ──────────────────────────────────────────────────────

def _sample_source_data():
    """Payload normalizado mínimo (13 claves) — mismo shape que Source.fetch()."""
    return {
        "sleep": {"2026-06-28": {"asleep": 372, "inbed": 402, "deep": 54, "rem": 86,
                                  "light": 232, "eff": 92, "bedtime": "01:01",
                                  "waketime": "07:03"}},
        "rhr": {"2026-06-28": 52.0},
        "hrv": {"2026-06-28": 54.6},
        "resp": {"2026-06-28": 14.1},
        "vo2": {"2026-06-28": 47.3},
        "steps": {"2026-06-28": 8423},
        "azm": {},
        "spo2": {"2026-06-28": 97.0},
        "skin": {"2026-06-28": -0.3},
        "exercises": [],
        "distance_km": {},
        "energy_kcal": {},
        "active_hours": {},
    }


@pytest.fixture
def sync_env(tmp_path, monkeypatch):
    """Aísla settings.DATA_DIR y app.profile hacia tmp_path (mismo patrón que
    tests/test_sync.py). Reload de app.sync para que DATA_OUT apunte a tmp_path
    (se calcula a import-time); el endpoint /api/sync hace `from app.sync import
    run_sync` en call-time, así que usa el módulo recargado de sys.modules.

    Frescura de Alertas + Coach: run_sync() ahora dispara (best-effort) el hook
    del titular del coach, que puede invocar el CLI real de claude. Mismo fix
    que tests/test_sync.py::sync_env — mockear subprocess.run (CLI "no
    disponible", rápido y determinista) y redirigir
    app.coach_headline._CACHE_PATH a tmp_path, para no tocar el filesystem real
    ni depender de la disponibilidad del CLI en el entorno de test."""
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

    import app.sync as sync_mod
    import app.coach_headline as headline_mod
    importlib.reload(sync_mod)
    importlib.reload(headline_mod)
    monkeypatch.setattr(headline_mod, "_CACHE_PATH", tmp_path / "coach_headline.json")
    yield tmp_path, sync_mod
    importlib.reload(sync_mod)  # deshacer al salir (higiene entre tests)
    importlib.reload(headline_mod)


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient con DATA_DIR/DATA_PATH en tmp_path y datos reales copiados si
    existen (mismo patrón que tests/test_endpoints.py::_get_client). Sin `with`
    → no corre startup (no scheduler, no startup-sync)."""
    real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
    if real_compact.exists():
        (tmp_path / "health_compact.json").write_text(real_compact.read_text())

    from app import config
    import main as main_mod
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                        Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    return TestClient(main_mod.app, raise_server_exceptions=True)


# ── 1. Atomicidad (app/fsutil.py) ───────────────────────────────────────────

def test_atomic_write_text_writes_content_without_tmp_residue(tmp_path):
    target = tmp_path / "out.json"
    atomic_write_text(target, '{"a": 1}')
    assert target.read_text(encoding="utf-8") == '{"a": 1}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_text_overwrites_existing(tmp_path):
    target = tmp_path / "out.json"
    target.write_text("viejo")
    atomic_write_text(target, "nuevo")
    assert target.read_text(encoding="utf-8") == "nuevo"
    assert list(tmp_path.glob("*.tmp")) == []


def test_atomic_write_text_failure_leaves_original_intact(tmp_path, monkeypatch):
    """Si os.replace falla (crash simulado a media escritura), el destino ORIGINAL
    queda intacto — ni truncado ni corrupto. Esa es la razón de ser del helper."""
    import app.fsutil as fsutil
    target = tmp_path / "out.json"
    target.write_text('{"original": true}')

    def _boom(src, dst):
        raise OSError("disco lleno simulado")

    monkeypatch.setattr(fsutil.os, "replace", _boom)
    with pytest.raises(OSError):
        atomic_write_text(target, '{"nuevo": true}')
    assert target.read_text(encoding="utf-8") == '{"original": true}'


# ── 2. Single-flight de run_sync ────────────────────────────────────────────

def test_run_sync_raises_syncinprogress_when_lock_held(sync_env):
    tmp_path, sync_mod = sync_env
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(sync_mod.SyncInProgress):
            sync_mod.run_sync(45)
    finally:
        sync_mod._SYNC_LOCK.release()


def test_run_sync_does_not_fetch_when_lock_held(sync_env):
    """Con el lock tomado NO debe tocarse ninguna fuente (cero fetch)."""
    tmp_path, sync_mod = sync_env
    fake_source = MagicMock()
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        with patch("app.sync.get_source", return_value=fake_source):
            with pytest.raises(sync_mod.SyncInProgress):
                sync_mod.run_sync(45)
    finally:
        sync_mod._SYNC_LOCK.release()
    fake_source.fetch.assert_not_called()


def test_run_sync_works_again_after_lock_released(sync_env):
    """Liberado el lock, el siguiente run entra normal y produce dataset."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()

    # 1er intento con lock tomado → SyncInProgress
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(sync_mod.SyncInProgress):
            sync_mod.run_sync(45)
    finally:
        sync_mod._SYNC_LOCK.release()

    # 2o intento con lock libre → sync normal
    with patch("app.sync.get_source", return_value=fake_source):
        dataset = sync_mod.run_sync(45)
    assert dataset["summary"]["n_days"] >= 1
    assert (tmp_path / "health_compact.json").exists()
    # Y sin .tmp residual (escritura atómica exitosa)
    assert list(tmp_path.glob("*.tmp")) == []


def test_run_sync_releases_lock_after_all_sources_fail(sync_env):
    """EL riesgo #1 del roadmap: el `raise errors[sources[0]]` debe pasar por el
    finally y liberar el lock. Un lock huérfano = app que no sincroniza hasta restart."""
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.side_effect = RuntimeError("todas las fuentes caídas")

    with patch("app.sync.get_source", return_value=fake_source):
        with pytest.raises(RuntimeError):
            sync_mod.run_sync(45)

    # El lock quedó LIBRE.
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    sync_mod._SYNC_LOCK.release()


def test_run_sync_releases_lock_after_success(sync_env):
    tmp_path, sync_mod = sync_env
    (tmp_path / "profile.json").write_text(json.dumps({
        "source": "google_health", "onboarded": True,
    }))
    fake_source = MagicMock()
    fake_source.fetch.return_value = _sample_source_data()
    with patch("app.sync.get_source", return_value=fake_source):
        sync_mod.run_sync(45)
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    sync_mod._SYNC_LOCK.release()


def test_api_sync_returns_already_running_when_lock_held(sync_env, client):
    """POST /api/sync con un sync en curso → {status: already_running}, HTTP 200,
    sin disparar segundo fetch."""
    tmp_path, sync_mod = sync_env
    fake_source = MagicMock()
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        with patch("app.sync.get_source", return_value=fake_source):
            resp = client.post("/api/sync")
    finally:
        sync_mod._SYNC_LOCK.release()
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_running"
    fake_source.fetch.assert_not_called()


def test_api_ingest_returns_already_running_and_keeps_payload(sync_env, client, monkeypatch):
    """POST /api/ingest con un sync en curso → already_running, PERO el payload
    quedó guardado (hk.ingest corre ANTES de run_sync) — el mensaje lo dice y
    healthkit_ingest.json existe en disco."""
    tmp_path, sync_mod = sync_env
    from app import profile as _pm
    (tmp_path / "profile.json").write_text(json.dumps({
        "sources": ["healthkit"], "onboarded": True,
    }))
    # Fase 8C (paso C6): INGEST_TOKEN es SIEMPRE obligatorio ahora — este test
    # no está probando auth, así que fijamos un token y lo mandamos en el
    # header para llegar a la lógica de 'already_running' que sí interesa aquí.
    from app.config import settings
    monkeypatch.setattr(settings, "INGEST_TOKEN", "s3cret-already-running")

    payload = {"schema": 1, "days": 30, "data": _sample_source_data()}
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        resp = client.post(
            "/api/ingest", json=payload,
            headers={"X-Vitals-Token": "s3cret-already-running"},
        )
    finally:
        sync_mod._SYNC_LOCK.release()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "already_running"
    assert "guardado" in body["message"]
    # El crudo quedó persistido para el próximo sync.
    assert (tmp_path / "healthkit_ingest.json").exists()


def test_scheduler_job_skips_cleanly_when_sync_in_progress(sync_env, caplog):
    """El job del scheduler no explota ni loguea error si ya hay sync en curso."""
    import logging
    tmp_path, sync_mod = sync_env
    from app.scheduler import _sync_job
    assert sync_mod._SYNC_LOCK.acquire(blocking=False)
    try:
        with caplog.at_level(logging.INFO, logger="vitals.scheduler"):
            _sync_job()  # no debe lanzar
    finally:
        sync_mod._SYNC_LOCK.release()
    assert any("en curso" in rec.message for rec in caplog.records)
    assert not any(rec.levelno >= logging.ERROR for rec in caplog.records)


# ── 3. WHOOP: lock de clase compartido entre instancias ─────────────────────

def test_whoop_refresh_lock_is_shared_across_instances():
    """get_source() crea una instancia nueva por llamada → el single-flight del
    refresh rotatorio solo sirve si el lock es de CLASE (mismo objeto siempre)."""
    from app.sources.whoop import WhoopSource
    a, b = WhoopSource(), WhoopSource()
    assert a._refresh_lock is b._refresh_lock
    assert a._refresh_lock is WhoopSource._refresh_lock
    # Y es un lock de verdad (adquirible/liberable).
    assert a._refresh_lock.acquire(blocking=False)
    a._refresh_lock.release()


def test_whoop_instances_share_lock_via_get_source():
    from app.sources import get_source
    s1, s2 = get_source("whoop"), get_source("whoop")
    assert s1 is not s2  # instancias distintas...
    assert s1._refresh_lock is s2._refresh_lock  # ...MISMO lock


# ── 4. GZip ──────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not (Path(__file__).parent.parent / "data" / "health_compact.json").exists(),
    reason="requiere data/health_compact.json real para que GET / supere minimum_size=1024",
)
def test_root_serves_gzip_when_accepted(client):
    """Criterio del roadmap: GET / con Accept-Encoding: gzip → Content-Encoding: gzip.
    (El dashboard con datos reales pesa ~250 KB, muy por encima del minimum_size.)"""
    resp = client.get("/", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


def test_api_data_serves_gzip_when_accepted(tmp_path, monkeypatch):
    """Variante determinista sin depender del data/ real: /api/data con un dataset
    sintético > 1024 bytes debe salir comprimido."""
    from app import config
    import main as main_mod
    dataset = {"summary": {"n_days": 1}, "days": [{"date": "2026-06-28",
               "filler": "x" * 3000}], "exercises": []}
    (tmp_path / "health_compact.json").write_text(json.dumps(dataset))
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    c = TestClient(main_mod.app)
    resp = c.get("/api/data", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"
    assert resp.json()["summary"]["n_days"] == 1  # httpx descomprime transparente


def test_small_responses_not_gzipped(tmp_path, monkeypatch):
    """Respuestas < minimum_size (1024) NO se comprimen (sin overhead inútil)."""
    from app import config
    import main as main_mod
    dataset = {"summary": {"n_days": 1}, "days": [], "exercises": []}  # JSON diminuto
    (tmp_path / "health_compact.json").write_text(json.dumps(dataset))
    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    c = TestClient(main_mod.app)
    resp = c.get("/api/data", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert len(resp.content) < 1024
    assert resp.headers.get("content-encoding") != "gzip"
