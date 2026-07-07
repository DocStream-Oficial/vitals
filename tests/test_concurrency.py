"""
test_concurrency.py — prueba de CONCURRENCIA REAL del fix de threadpool offload.

Contexto del bug: `main.py::api_sync`/`api_ingest` llamaban `run_sync()` (síncrono,
bloqueante — usa `requests` contra la API de Google) DIRECTO dentro del event loop
async, sin offload a thread. Eso congelaba el proceso ENTERO — incluidas otras
peticiones concurrentes como `GET /api/data` — mientras el sync corría.

Fix: `dataset = await run_in_threadpool(run_sync)`.

Por qué este test NO usa `fastapi.testclient.TestClient`:
`TestClient` (basado en httpx sync sobre un ASGITransport in-process) ejecuta las
peticiones en un event loop propio manejado internamente por petición; no ofrece una
garantía simple y documentada de que 2 llamadas `.get()/.post()` disparadas desde 2
threads de test se sirvan REALMENTE en paralelo sobre el mismo loop de la app (a
diferencia de un servidor uvicorn real, donde el loop es un objeto persistente y el
offload a threadpool es exactamente el mecanismo que se ejerce en producción). Para
probar de verdad que el event loop queda libre, este test:

1. Levanta un servidor `uvicorn` REAL (el mismo `main.app`) en un thread daemon,
   escuchando en un puerto libre de 127.0.0.1.
2. Dispara `POST /api/sync` con la fuente activa mockeada para tardar ~2s (simulando
   la llamada bloqueante real a la API de Google) desde un thread de test aparte,
   usando `httpx` como cliente HTTP real por socket TCP (no in-process).
3. Mientras ese POST está en vuelo, dispara `GET /api/data` desde el thread principal
   del test y mide cuánto tarda en responder.
4. Assert: `GET /api/data` responde en << 2s (umbral generoso 1s) — si el fix no
   estuviera aplicado (event loop bloqueado por el `run_sync()` síncrono), esta
   petición tendría que esperar a que el POST /api/sync termine (~2s), fallando el
   assert de tiempo.

Esto ejercita el mecanismo real de producción (uvicorn de 1 worker + threadpool
offload), no una aproximación.
"""
from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
import uvicorn


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _SlowFetchSource:
    """Sustituto de una Source real cuyo fetch() simula una llamada HTTP bloqueante
    lenta (como requests.get contra Google con timeout=60) durmiendo SLEEP_SECONDS."""

    SLEEP_SECONDS = 2.0

    def fetch(self, days: int = 45):
        time.sleep(self.SLEEP_SECONDS)
        return {"days": [], "exercises": []}


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Levanta main.app en un servidor uvicorn real (thread daemon) sobre un puerto
    libre, con DATA_DIR/DATA_PATH redirigidos a tmp_path (aislado del data/ real) y
    el scheduler de APScheduler deshabilitado (irrelevante para este test, ya
    confirmado que corre en su propio thread — fuera de alcance)."""
    real_compact = Path(__file__).parent.parent / "data" / "health_compact.json"
    if real_compact.exists():
        (tmp_path / "health_compact.json").write_text(real_compact.read_text())

    from app import config
    import main as main_mod
    import subprocess as _subprocess

    monkeypatch.setattr(config.settings, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config.settings, "TEMPLATES_DIR",
                         Path(__file__).parent.parent / "templates")
    monkeypatch.setattr(main_mod, "DATA_PATH", tmp_path / "health_compact.json")
    # run_sync() ESCRIBE health_compact.json vía app.sync.DATA_OUT (ruta de
    # escritura), y el token vía app.auth.TOKEN_PATH — ambos módulo-nivel a
    # import-time desde settings.DATA_DIR. monkeypatchear config.settings.DATA_DIR
    # NO los mueve, y peor: otros tests (test_sync/test_ronda1) hacen
    # importlib.reload(sync) y dejan DATA_OUT reapuntado al data/ REAL. Como este
    # test levanta un uvicorn REAL que sí ejecuta run_sync() con la fuente
    # mockeada exitosa, SIN estos dos patches sobrescribiría el
    # data/health_compact.json REAL del usuario (346 días -> dataset de prueba).
    import app.sync as _sync_mod
    import app.auth as _auth_mod
    monkeypatch.setattr(_sync_mod, "DATA_OUT", tmp_path / "health_compact.json")
    monkeypatch.setattr(_auth_mod, "TOKEN_PATH", tmp_path / "token.json")
    # Frescura de Alertas + Coach: run_sync() ahora dispara (best-effort) el hook
    # del titular del coach al final, que puede invocar el CLI real de claude vía
    # subprocess. app.coach_headline._CACHE_PATH se calcula a IMPORT-TIME desde
    # settings.DATA_DIR — monkeypatchear config.settings.DATA_DIR arriba NO lo
    # mueve. Sin esto, este test (que levanta un servidor uvicorn REAL) escribiría
    # sobre el data/coach_headline.json REAL del usuario y/o llamaría al CLI real,
    # haciendo el test lento/flaky y contaminando el filesystem real. Se redirige
    # explícitamente + se mockea subprocess.run para simular "CLI no disponible"
    # (rápido, determinista, no toca red ni el CLI real).
    import app.coach_headline as _headline_mod
    monkeypatch.setattr(_headline_mod, "_CACHE_PATH", tmp_path / "coach_headline.json")

    class _NoCliResult:
        returncode = 1
        stdout = ""
        stderr = "claude CLI no disponible en tests"

    monkeypatch.setattr(_subprocess, "run", lambda *a, **kw: _NoCliResult())
    # Ronda 1: patchear los bindings de MAIN (main.py hace `from app.scheduler
    # import start_scheduler`, así que patchear "app.scheduler.start_scheduler"
    # NO desactivaba nada — el scheduler REAL arrancaba y su startup-sync (red
    # real, token real del data/ del repo) quedaba corriendo en un thread daemon.
    # Antes era un side-effect invisible; con el single-flight de _SYNC_LOCK ese
    # sync fantasma retenía el lock y hacía fallar este test y contaminaba el
    # resto de la suite con SyncInProgress. El fixture SIEMPRE quiso deshabilitar
    # el scheduler (ver docstring) — esto solo corrige el target del patch.
    monkeypatch.setattr(main_mod, "start_scheduler", lambda: None)
    monkeypatch.setattr(main_mod, "stop_scheduler", lambda: None)
    monkeypatch.setattr("app.scheduler.start_scheduler", lambda: None)
    monkeypatch.setattr("app.scheduler.stop_scheduler", lambda: None)
    # profile.effective_sources() por default devuelve ["google_health"]; con
    # get_source mockeado más abajo, cualquier fuente conectada sirve.

    port = _free_port()
    config_ = uvicorn.Config(main_mod.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config_)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Esperar a que el server esté escuchando (uvicorn.Server expone .started).
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    assert server.started, "El servidor uvicorn de prueba no arrancó a tiempo."

    base_url = f"http://127.0.0.1:{port}"
    try:
        yield base_url
    finally:
        server.should_exit = True
        thread.join(timeout=10)


def test_get_api_data_stays_fast_while_sync_runs_concurrently(live_server):
    """CRITERIO DE ACEPTACIÓN #4 del roadmap: con un sync artificialmente lento (~2s)
    en curso vía POST /api/sync, GET /api/data concurrente debe responder en
    milisegundos — confirmando que run_in_threadpool libera el event loop y que el
    servidor de 1 worker sigue atendiendo otras peticiones mientras el sync corre."""
    base_url = live_server

    # Mockea get_source() DENTRO de app.sync (donde run_sync() lo invoca) para que
    # cualquier fuente conectada devuelva el sustituto lento — sin tocar red real.
    with patch("app.sync.get_source", return_value=_SlowFetchSource()):
        sync_times = {}

        def _fire_slow_sync():
            t0 = time.perf_counter()
            resp = httpx.post(f"{base_url}/api/sync", timeout=30)
            sync_times["status_code"] = resp.status_code
            sync_times["body"] = resp.json()
            sync_times["elapsed"] = time.perf_counter() - t0

        sync_thread = threading.Thread(target=_fire_slow_sync)
        sync_thread.start()

        # Dar tiempo a que el POST /api/sync ya esté DENTRO de run_sync() (durmiendo)
        # antes de disparar la petición concurrente que medimos.
        time.sleep(0.5)

        t0 = time.perf_counter()
        data_resp = httpx.get(f"{base_url}/api/data", timeout=10)
        data_elapsed = time.perf_counter() - t0

        sync_thread.join(timeout=15)

    # 1) GET /api/data respondió con éxito...
    assert data_resp.status_code == 200
    # 2) ...y RÁPIDO — muy por debajo de los ~2s que tarda el sync simulado. Antes del
    #    fix, esta petición hubiera tenido que esperar a que run_sync() (síncrono,
    #    bloqueando el único event loop) terminara -> ~2s. Umbral generoso 1s para
    #    absorber jitter de CI/máquina, sigue siendo << 2s.
    assert data_elapsed < 1.0, (
        f"GET /api/data tardó {data_elapsed:.3f}s mientras un sync de "
        f"{_SlowFetchSource.SLEEP_SECONDS}s estaba en curso — el event loop parece "
        f"seguir bloqueado (falta el offload a threadpool)."
    )

    # 3) El POST /api/sync original sigue funcionando igual que siempre (contrato
    #    intacto): tarda ~SLEEP_SECONDS y devuelve {status: ok, n_days: N}.
    assert sync_times["status_code"] == 200
    assert sync_times["body"]["status"] == "ok"
    assert "n_days" in sync_times["body"]
    assert sync_times["elapsed"] >= _SlowFetchSource.SLEEP_SECONDS * 0.9, (
        "El POST /api/sync terminó sospechosamente rápido — el mock lento no se "
        "habría ejercido de verdad."
    )

    print(
        f"\n[test_concurrency] GET /api/data mientras sync lento en curso: "
        f"{data_elapsed*1000:.1f} ms | POST /api/sync total: "
        f"{sync_times['elapsed']*1000:.1f} ms"
    )
