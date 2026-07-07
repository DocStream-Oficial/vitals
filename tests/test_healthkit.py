"""
test_healthkit.py — Fase 5D-A: HealthKitSource (PUSH) + POST /api/ingest.

HealthKit no tiene API remota que mockear vía HTTP — se prueba con payload JSON
directo. Cubre:
  - get_source('healthkit') ya no lanza (5D stub → 5D-A implementado)
  - auth_state: no_token sin ingest, active tras ingest
  - normalización del payload de muestra → dict de 13 claves
  - persistencia del payload CRUDO en healthkit_ingest.json
  - tolerancia a campos faltantes / None / entradas malformadas
  - fetch() reusa el último ingest; lanza NoToken si nunca hubo ingest
  - build_auth_url / exchange_code lanzan NotImplementedError
  - POST /api/ingest: guard source==healthkit, payload malformado, wrong_source
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.sources import get_source
from app.sources.healthkit import HealthKitSource
from app.sources.base import Source, NoToken


# ── Payload de muestra (el del roadmap / contrato con la app nativa) ──

SAMPLE_PAYLOAD = {
    "hrv":         [{"date": "2026-06-28", "value": 54.6}],
    "rhr":         [{"date": "2026-06-28", "value": 52}],
    "resp":        [{"date": "2026-06-28", "value": 14.1}],
    "spo2":        [{"date": "2026-06-28", "value": 97.0}],
    "skin_temp":   [{"date": "2026-06-28", "value": -0.3}],
    "steps":       [{"date": "2026-06-28", "value": 8423}],
    "vo2":         [{"date": "2026-06-28", "value": 47.3}],
    "distance_km": [{"date": "2026-06-28", "value": 6.21}],
    "energy_kcal": [{"date": "2026-06-28", "value": 2480}],
    "sleep": [
        {"date": "2026-06-28", "asleep": 372, "deep": 54, "rem": 86, "light": 232,
         "eff": 92, "bedtime": "01:01", "waketime": "07:03", "inbed": 402}
    ],
    "workouts": [
        {"date": "2026-06-28", "name": "Run", "dur_min": 40, "kcal": 380, "distance_km": 6.21}
    ],
}


@pytest.fixture
def hk_datadir(tmp_path):
    """Apunta settings.DATA_DIR a tmp_path para que el ingest NO toque data/ real."""
    from app import config
    with patch.object(config.settings, "DATA_DIR", tmp_path):
        yield tmp_path


# ── get_source ──

def test_get_source_healthkit_is_implemented():
    src = get_source("healthkit")
    assert isinstance(src, Source)
    assert src.name == "healthkit"
    for m in ("build_auth_url", "exchange_code", "auth_state", "fetch"):
        assert callable(getattr(src, m))


# ── auth_state ──

def test_auth_state_no_token_when_no_ingest(hk_datadir):
    src = HealthKitSource()
    st = src.auth_state()
    assert st["status"] == "no_token"
    assert st["days_left"] == 0


def test_auth_state_active_after_ingest(hk_datadir):
    src = HealthKitSource()
    src.ingest(SAMPLE_PAYLOAD)
    st = src.auth_state()
    assert st["status"] == "active"


# ── normalización ──

def test_ingest_normalizes_sample_payload(hk_datadir):
    src = HealthKitSource()
    data = src.ingest(SAMPLE_PAYLOAD)

    expected_keys = {
        "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
        "exercises", "distance_km", "energy_kcal", "active_hours",
    }
    assert set(data.keys()) == expected_keys

    # scalars [{date,value}] → {date: value}
    assert data["hrv"]["2026-06-28"] == 54.6
    assert data["rhr"]["2026-06-28"] == 52
    assert data["resp"]["2026-06-28"] == 14.1
    assert data["spo2"]["2026-06-28"] == 97.0
    assert data["steps"]["2026-06-28"] == 8423
    assert data["vo2"]["2026-06-28"] == 47.3
    assert data["distance_km"]["2026-06-28"] == 6.21
    assert data["energy_kcal"]["2026-06-28"] == 2480

    # skin_temp ya es desviación → skin directo
    assert data["skin"]["2026-06-28"] == -0.3

    # sleep → dict {date: {...}}
    s = data["sleep"]["2026-06-28"]
    assert s["asleep"] == 372
    assert s["deep"] == 54
    assert s["rem"] == 86
    assert s["light"] == 232
    assert s["eff"] == 92
    assert s["bedtime"] == "01:01"
    assert s["waketime"] == "07:03"
    assert s["inbed"] == 402

    # workouts → exercises[]
    assert len(data["exercises"]) == 1
    ex = data["exercises"][0]
    assert ex["date"] == "2026-06-28"
    assert ex["name"] == "Run"
    assert ex["dur_min"] == 40
    assert ex["kcal"] == 380
    assert ex["distance_km"] == 6.21

    # azm / active_hours siempre {}
    assert data["azm"] == {}
    assert data["active_hours"] == {}


def test_ingest_persists_raw_payload(hk_datadir):
    src = HealthKitSource()
    src.ingest(SAMPLE_PAYLOAD)
    path = hk_datadir / "healthkit_ingest.json"
    assert path.exists()
    raw = json.loads(path.read_text())
    # Es el payload CRUDO (con arrays {date,value}), no el normalizado (dicts).
    assert raw["hrv"] == [{"date": "2026-06-28", "value": 54.6}]
    assert "_ingested_at" in raw  # marca de timestamp añadida


def test_ingest_tolerant_to_missing_fields(hk_datadir):
    src = HealthKitSource()
    data = src.ingest({"hrv": [{"date": "2026-06-28", "value": 50}]})
    assert data["hrv"]["2026-06-28"] == 50
    # las demás claves vacías, no KeyError
    assert data["rhr"] == {}
    assert data["sleep"] == {}
    assert data["exercises"] == []
    assert data["skin"] == {}


def test_ingest_tolerant_to_none_and_empty(hk_datadir):
    src = HealthKitSource()
    payload = {
        "hrv": None,
        "rhr": [],
        "sleep": [{"date": "2026-06-28"}],          # campos de sleep faltantes → None
        "steps": [{"date": "2026-06-28", "value": None}],  # value None se pasa tal cual
        "workouts": None,
    }
    data = src.ingest(payload)
    assert data["hrv"] == {}
    assert data["rhr"] == {}
    assert data["steps"]["2026-06-28"] is None
    assert data["sleep"]["2026-06-28"]["asleep"] is None
    assert data["exercises"] == []


def test_ingest_discards_malformed_entries(hk_datadir):
    src = HealthKitSource()
    payload = {
        "hrv": [
            {"value": 50},                      # sin date → descartada
            {"date": "not-a-date", "value": 51},  # date inválida → descartada
            {"date": "2026-06-28", "value": 52},  # válida
            "garbage",                           # no-dict → ignorada
        ],
        "sleep": [
            {"asleep": 300},                     # sin date → descartada
            {"date": "2026-06-28", "asleep": 372},
        ],
        "workouts": [
            {"name": "Run"},                     # sin date → descartada
            {"date": "2026-06-28", "name": "Bike"},
        ],
    }
    data = src.ingest(payload)
    assert data["hrv"] == {"2026-06-28": 52}
    assert list(data["sleep"].keys()) == ["2026-06-28"]
    assert len(data["exercises"]) == 1
    assert data["exercises"][0]["name"] == "Bike"


# ── F2 roadmap P0: hipnograma — segments opcional en el ingest ──────────────

def test_ingest_sleep_with_valid_segments_attaches_them(hk_datadir):
    src = HealthKitSource()
    payload = {
        "sleep": [
            {
                "date": "2026-06-28",
                "asleep": 372,
                "segments": [
                    {"s": 0, "e": 60, "st": "light"},
                    {"s": 60, "e": 120, "st": "deep"},
                ],
            },
        ],
    }
    data = src.ingest(payload)
    night = data["sleep"]["2026-06-28"]
    assert night["asleep"] == 372
    assert night["segments"] == [
        {"s": 0, "e": 60, "st": "light"},
        {"s": 60, "e": 120, "st": "deep"},
    ]


def test_ingest_sleep_with_invalid_segments_discards_only_that_field(hk_datadir, caplog):
    """Segments inválidos (traslape, etapa desconocida, etc.) -> se descarta
    SOLO el campo 'segments'; la noche entra igual con el resto de sus datos."""
    src = HealthKitSource()
    payload = {
        "sleep": [
            {
                "date": "2026-06-28",
                "asleep": 372,
                "segments": [
                    {"s": 0, "e": 60, "st": "napping"},  # etapa inválida
                ],
            },
        ],
    }
    data = src.ingest(payload)
    night = data["sleep"]["2026-06-28"]
    assert night["asleep"] == 372  # la noche entra igual
    assert "segments" not in night


def test_ingest_sleep_without_segments_field_has_no_segments_key(hk_datadir):
    """Entrada sin 'segments' (app iOS actual, aún no lo manda) -> sin la
    clave, byte-igual al comportamiento anterior a F2."""
    src = HealthKitSource()
    payload = {"sleep": [{"date": "2026-06-28", "asleep": 372}]}
    data = src.ingest(payload)
    assert "segments" not in data["sleep"]["2026-06-28"]


# ── fetch ──

def test_fetch_raises_no_token_when_never_ingested(hk_datadir):
    src = HealthKitSource()
    with pytest.raises(NoToken):
        src.fetch(45)


def test_fetch_reuses_last_ingest(hk_datadir):
    src = HealthKitSource()
    src.ingest(SAMPLE_PAYLOAD)
    data = src.fetch(3650)  # ventana amplia para no recortar la fecha de muestra
    assert data["hrv"]["2026-06-28"] == 54.6
    assert data["sleep"]["2026-06-28"]["asleep"] == 372


def test_fetch_trims_old_entries(hk_datadir):
    """fetch(days) recorta entradas anteriores a hoy-days."""
    src = HealthKitSource()
    src.ingest({"hrv": [{"date": "2000-01-01", "value": 40}]})
    data = src.fetch(45)  # 2000 queda muy fuera de la ventana de 45 días
    assert data["hrv"] == {}


def test_fetch_default_window_is_365_not_45(hk_datadir):
    """Roadmap 'fix-ingest-merge-y-ventana-healthkit', Paso 3/Criterio #6:
    fetch() SIN argumento explícito usa days=365 (antes 45). Verificación
    indirecta vía _trim_to_window: una entrada de hace ~200 días NO debe
    descartarse con el default nuevo, aunque SÍ se habría descartado con el
    default viejo de 45."""
    import datetime as _dt

    src = HealthKitSource()
    old_date = (_dt.date.today() - _dt.timedelta(days=200)).isoformat()
    src.ingest({"hrv": [{"date": old_date, "value": 41.0}]})

    # Con el default viejo (45 días) esta entrada habría sido descartada.
    data_default = src.fetch()  # sin argumento -> debe usar 365, no 45
    assert data_default["hrv"].get(old_date) == 41.0

    # Control: con days=45 explícito SÍ se descarta (confirma que el trim
    # funciona y que el resultado anterior no es un falso positivo).
    data_45 = src.fetch(45)
    assert data_45["hrv"] == {}


# ── auth OAuth no aplica ──

def test_build_auth_url_raises_not_implemented():
    src = HealthKitSource()
    with pytest.raises(NotImplementedError):
        src.build_auth_url("state")


def test_exchange_code_raises_not_implemented():
    src = HealthKitSource()
    with pytest.raises(NotImplementedError):
        src.exchange_code("code")


# ── POST /api/ingest (TestClient) ──

def _ingest_client(tmp_path: Path, source: str = "healthkit", ingest_token: str = "s3cret",
                    sources=None, client_sends_header: bool = True):
    """TestClient con DATA_DIR en tmp y profile.effective_sources() == `sources`
    (default [source] si `sources` no se pasa explícito — compat con el uso viejo
    de este helper, que solo conocía una fuente 'activa').

    Fase 6A: el guard de /api/ingest usa effective_sources(), no effective('source'),
    así que este helper patchea AMBOS para que los tests viejos (que solo pasaban
    `source`) sigan funcionando sin cambios.

    `ingest_token`: patchea config.settings.INGEST_TOKEN (el token que el
    SERVER exige). Fase 8C (paso C6): INGEST_TOKEN es SIEMPRE obligatorio
    ahora (401 sin token válido) — el default ya NO es "" (backward-compat
    viejo), sino un token fijo de prueba ("s3cret").

    `client_sends_header`: si True (default), el TestClient manda
    automáticamente el header X-Vitals-Token == `ingest_token` en cada
    request — así los tests que NO están probando específicamente el flujo
    de auth (la mayoría de este archivo) siguen pasando sin tocar cada
    client.post(...) uno por uno. Los tests que SÍ prueban auth (header
    ausente/incorrecto) pasan client_sends_header=False y agregan el header
    que quieran a mano en su propio client.post(...).

    Roadmap 'fix-ingest-merge-y-ventana-healthkit' Paso 1: api_ingest ahora llama
    a app.sync.run_sync() internamente, que escribe a app.sync.DATA_OUT — una
    constante calculada a IMPORT-TIME sobre settings.DATA_DIR (mismo patrón que
    main_mod.DATA_PATH). Hay que parchearla también, si no run_sync() sigue
    escribiendo al DATA_DIR real en vez de tmp_path (ver test_sync.py::sync_env,
    que resuelve esto con importlib.reload — aquí usamos patch.object directo
    porque es más simple para un solo valor).
    """
    from app import config
    import main as main_mod
    import app.sync as sync_mod
    import app.coach_headline as headline_mod
    import subprocess as _subprocess

    effective_sources_list = sources if sources is not None else [source]

    def _fake_effective(field):
        if field == "source":
            return source
        if field == "birthdate":
            return "1990-01-01"
        if field == "waist_cm":
            return 85.0
        if field == "sex":
            return "M"
        if field == "locale":
            return "es"
        return None

    class _NoCliResult:
        returncode = 1
        stdout = ""
        stderr = "claude CLI no disponible en tests"

    # Frescura de Alertas + Coach: run_sync() (llamado internamente por
    # /api/ingest) dispara el hook best-effort del titular del coach al final,
    # que puede invocar el CLI real de claude vía subprocess y escribir sobre
    # data/coach_headline.json. Igual que test_sync.py::sync_env: mockear
    # subprocess.run + redirigir _CACHE_PATH a tmp_path para no tocar el
    # filesystem real ni depender del CLI en el entorno de test.
    with patch.object(config.settings, "DATA_DIR", tmp_path), \
         patch.object(config.settings, "INGEST_TOKEN", ingest_token), \
         patch.object(main_mod, "DATA_PATH", tmp_path / "health_compact.json"), \
         patch.object(sync_mod, "DATA_OUT", tmp_path / "health_compact.json"), \
         patch.object(headline_mod, "_CACHE_PATH", tmp_path / "coach_headline.json"), \
         patch.object(_subprocess, "run", lambda *a, **kw: _NoCliResult()), \
         patch("main._profile.effective", side_effect=_fake_effective), \
         patch("main._profile.effective_sources", return_value=effective_sources_list), \
         patch("main._profile.current_age", return_value=36), \
         patch("app.scheduler.start_scheduler"), \
         patch("app.scheduler.stop_scheduler"):
        headers = {"X-Vitals-Token": ingest_token} if (client_sends_header and ingest_token) else {}
        client = TestClient(main_mod.app, raise_server_exceptions=True, headers=headers)
        yield client, tmp_path


def test_api_ingest_ok_with_healthkit_source(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit")
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["n_days"] >= 1
    # health_compact.json poblado
    out = tmp / "health_compact.json"
    assert out.exists()
    dataset = json.loads(out.read_text())
    assert dataset["summary"]["n_days"] >= 1


def test_api_ingest_wrong_source_does_not_overwrite(tmp_path):
    gen = _ingest_client(tmp_path, source="google_health")
    client, tmp = next(gen)
    # Sembrar un health_compact.json existente para verificar que NO se sobrescribe
    seed = tmp / "health_compact.json"
    seed.write_text(json.dumps({"sentinel": True}))
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "wrong_source"
    assert body["active"] == "google_health"
    # No tocó el archivo
    assert json.loads(seed.read_text()) == {"sentinel": True}
    # Tampoco escribió healthkit_ingest.json
    assert not (tmp / "healthkit_ingest.json").exists()


def test_api_ingest_wrong_source_multi_source_without_healthkit(tmp_path):
    """Fase 6A: el guard ahora checa effective_sources() (lista). Con 2 fuentes
    conectadas pero SIN healthkit, sigue siendo wrong_source (no basta con
    tener >1 fuente, healthkit debe estar entre ellas)."""
    gen = _ingest_client(tmp_path, sources=["google_health", "oura"])
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "wrong_source"
    assert body["active"] == "google_health"  # primera de la lista


def test_api_ingest_ok_when_healthkit_connected_alongside_others(tmp_path):
    """Fase 6A — criterio #6: el guard cambia de 'active != healthkit' a
    'healthkit not in effective_sources()'. HealthKit conectada JUNTO a otra
    fuente (no exclusiva) debe seguir permitiendo el ingest."""
    gen = _ingest_client(tmp_path, sources=["google_health", "healthkit"])
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    out = tmp / "health_compact.json"
    assert out.exists()


def test_api_ingest_merges_with_other_connected_source_not_healthkit_only(tmp_path):
    """Roadmap 'fix-ingest-merge-y-ventana-healthkit', Bug A / Criterios #1-#2:
    /api/ingest ya NO bypassea merge_sources() construyendo su propio dataset
    aislado de HealthKit. Con google_health también conectado y con datos propios
    (mockeados vía app.sync.get_source), el resultado de /api/ingest debe ser el
    dataset FUSIONADO (mismo motor que /api/sync), no solo el aporte de HealthKit.
    """
    import app.sync as sync_mod

    fake_gh_data = {
        "sleep": {"2026-05-01": {"asleep": 400, "inbed": 420, "deep": 60, "rem": 90,
                                  "light": 250, "eff": 95, "bedtime": "00:30", "waketime": "07:10"}},
        "rhr": {"2026-05-01": 48.0},
        "hrv": {"2026-05-01": 70.0},
        "resp": {"2026-05-01": 13.0},
        "vo2": {"2026-05-01": 50.0},
        "steps": {"2026-05-01": 10000},
        "azm": {},
        "spo2": {"2026-05-01": 98.0},
        "skin": {"2026-05-01": 0.1},
        "exercises": [],
        "distance_km": {"2026-05-01": 7.0},
        "energy_kcal": {"2026-05-01": 2600},
        "active_hours": {},
    }
    fake_gh_source = MagicMock()
    fake_gh_source.fetch.return_value = fake_gh_data

    gen = _ingest_client(tmp_path, sources=["google_health", "healthkit"])
    client, tmp = next(gen)

    # Solo mockeamos google_health (la otra fuente conectada); healthkit debe
    # seguir usando la implementación REAL (reusa el ingest recién guardado).
    real_get_source = sync_mod.get_source

    def _partial_fake_get_source(name):
        if name == "google_health":
            return fake_gh_source
        return real_get_source(name)

    with patch.object(sync_mod, "get_source", side_effect=_partial_fake_get_source):
        resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    # 2 días distintos: 2026-05-01 (google_health, mockeado) + 2026-06-28 (healthkit,
    # del SAMPLE_PAYLOAD real) -> el dataset fusionado tiene AMBOS, no solo el de HK.
    assert body["n_days"] == 2

    out = tmp / "health_compact.json"
    dataset = json.loads(out.read_text())
    assert dataset["summary"]["n_days"] == 2
    dates = {d["date"] for d in dataset["days"]}
    assert dates == {"2026-05-01", "2026-06-28"}


def test_api_ingest_malformed_json_no_500(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit")
    client, tmp = next(gen)
    resp = client.post(
        "/api/ingest",
        content=b"{not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


def test_api_ingest_non_object_payload_no_500(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit")
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=[1, 2, 3])
    assert resp.status_code == 200
    assert resp.json()["status"] == "error"


# ── Auth de secreto compartido (Fase 5D-B / obligatorio desde Fase 8C C6) ──

def test_api_ingest_unauthorized_when_token_set_and_header_missing(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit", ingest_token="s3cret", client_sends_header=False)
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 401
    assert resp.json()["status"] == "unauthorized"
    assert not (tmp / "health_compact.json").exists()


def test_api_ingest_unauthorized_when_token_wrong(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit", ingest_token="s3cret", client_sends_header=False)
    client, tmp = next(gen)
    resp = client.post(
        "/api/ingest", json=SAMPLE_PAYLOAD, headers={"X-Vitals-Token": "nope"}
    )
    assert resp.status_code == 401
    assert resp.json()["status"] == "unauthorized"
    assert not (tmp / "health_compact.json").exists()


def test_api_ingest_ok_when_token_matches(tmp_path):
    gen = _ingest_client(tmp_path, source="healthkit", ingest_token="s3cret", client_sends_header=False)
    client, tmp = next(gen)
    resp = client.post(
        "/api/ingest", json=SAMPLE_PAYLOAD, headers={"X-Vitals-Token": "s3cret"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    out = tmp / "health_compact.json"
    assert out.exists()
    dataset = json.loads(out.read_text())
    assert dataset["summary"]["n_days"] >= 1


def test_api_ingest_401_when_ingest_token_empty(tmp_path):
    """Fase 8C (paso C6): INGEST_TOKEN vacío en el server (caso ya imposible en
    producción real, donde config.py autogenera uno — pero cubre el guard en
    main.py directamente) -> 401 SIEMPRE, ya NO existe el modo permisivo
    'sin auth' de fases anteriores."""
    gen = _ingest_client(tmp_path, source="healthkit", ingest_token="", client_sends_header=False)
    client, tmp = next(gen)
    resp = client.post("/api/ingest", json=SAMPLE_PAYLOAD)
    assert resp.status_code == 401
    assert resp.json()["status"] == "unauthorized"
    assert not (tmp / "health_compact.json").exists()


def test_api_ingest_auth_precedes_source_guard(tmp_path):
    gen = _ingest_client(tmp_path, source="google_health", ingest_token="s3cret", client_sends_header=False)
    client, tmp = next(gen)
    resp = client.post(
        "/api/ingest", json=SAMPLE_PAYLOAD, headers={"X-Vitals-Token": "wrong"}
    )
    assert resp.status_code == 401
    assert resp.json()["status"] == "unauthorized"
    assert not (tmp / "health_compact.json").exists()


# ── Fase 7: menstrual_flow/basal_temp/ovulation_test en el payload ──────────

SAMPLE_PAYLOAD_WITH_CYCLE = dict(SAMPLE_PAYLOAD, **{
    "menstrual_flow": [
        {"date": "2026-06-01", "value": "medium"},
        {"date": "2026-06-02", "value": "heavy"},
        {"date": "2026-06-03", "value": "light"},
    ],
    "basal_temp": [{"date": "2026-06-01", "value": 36.3}],
    "ovulation_test": [{"date": "2026-06-15", "value": "positive"}],
})


def test_ingest_accepts_cycle_fields_without_error(hk_datadir):
    """menstrual_flow/basal_temp/ovulation_test en el payload no rompen la
    normalización general (no forman parte de las 13 claves de build_dataset)."""
    src = HealthKitSource()
    data = src.ingest(SAMPLE_PAYLOAD_WITH_CYCLE)
    expected_keys = {
        "sleep", "rhr", "hrv", "resp", "vo2", "steps", "azm", "spo2", "skin",
        "exercises", "distance_km", "energy_kcal", "active_hours",
    }
    assert set(data.keys()) == expected_keys  # sin claves de ciclo mezcladas


def test_ingest_payload_without_cycle_fields_behaves_identical(hk_datadir):
    """Criterio #8: payload SIN campos de ciclo se comporta IDÉNTICO al payload
    con ellos, en cuanto al dict de 13 claves normalizado."""
    src = HealthKitSource()
    data_without = src.ingest(SAMPLE_PAYLOAD)
    data_with = src.ingest(SAMPLE_PAYLOAD_WITH_CYCLE)
    # Las 13 claves normales son iguales entre ambos payloads (SAMPLE_PAYLOAD es
    # subconjunto de SAMPLE_PAYLOAD_WITH_CYCLE salvo por los campos de ciclo).
    for key in data_without:
        assert data_without[key] == data_with[key], f"Divergencia en '{key}'"


def test_merge_healthkit_cycle_noop_when_toggle_off(hk_datadir):
    """Con cycle_tracking=False (default), merge_healthkit_cycle no toca cycle_log.json."""
    from app.sources.healthkit import merge_healthkit_cycle
    with patch("app.profile.effective", return_value=False):
        merge_healthkit_cycle(SAMPLE_PAYLOAD_WITH_CYCLE)
    assert not (hk_datadir / "cycle_log.json").exists()


def test_merge_healthkit_cycle_groups_contiguous_days_into_period(hk_datadir):
    """Días contiguos de menstrual_flow -> UN periodo con start/end correctos."""
    from app.sources.healthkit import merge_healthkit_cycle
    from app import cycle as cycle_mod
    with patch.object(cycle_mod, "_CYCLE_LOG_FILE", hk_datadir / "cycle_log.json"), \
         patch("app.profile.effective", return_value=True):
        merge_healthkit_cycle(SAMPLE_PAYLOAD_WITH_CYCLE)
        log = cycle_mod.load_cycle_log()

    periods = log["periods"]
    assert len(periods) == 1
    assert periods[0]["start"] == "2026-06-01"
    assert periods[0]["end"] == "2026-06-03"
    assert periods[0]["source"] == "healthkit"


def test_merge_healthkit_cycle_dedupes_same_start_healthkit_source(hk_datadir):
    """Ingestar dos veces el mismo rango de flujo no duplica el periodo (idempotente)."""
    from app.sources.healthkit import merge_healthkit_cycle
    from app import cycle as cycle_mod
    with patch.object(cycle_mod, "_CYCLE_LOG_FILE", hk_datadir / "cycle_log.json"), \
         patch("app.profile.effective", return_value=True):
        merge_healthkit_cycle(SAMPLE_PAYLOAD_WITH_CYCLE)
        merge_healthkit_cycle(SAMPLE_PAYLOAD_WITH_CYCLE)
        log = cycle_mod.load_cycle_log()
    assert len(log["periods"]) == 1


def test_merge_healthkit_cycle_does_not_touch_manual_periods_same_start(hk_datadir):
    """Un periodo 'manual' con el mismo start que uno healthkit no se pisa —
    ambas fuentes conviven (de-dupe solo entre entradas de LA MISMA fuente)."""
    from app.sources.healthkit import merge_healthkit_cycle
    from app import cycle as cycle_mod
    with patch.object(cycle_mod, "_CYCLE_LOG_FILE", hk_datadir / "cycle_log.json"), \
         patch("app.profile.effective", return_value=True):
        cycle_mod.save_cycle_log({"periods": [{"start": "2026-06-01", "end": "2026-06-04", "source": "manual"}]})
        merge_healthkit_cycle(SAMPLE_PAYLOAD_WITH_CYCLE)
        log = cycle_mod.load_cycle_log()

    sources = {p["source"] for p in log["periods"]}
    assert sources == {"manual", "healthkit"}


def test_merge_healthkit_cycle_no_flow_data_is_noop(hk_datadir):
    from app.sources.healthkit import merge_healthkit_cycle
    with patch("app.profile.effective", return_value=True):
        merge_healthkit_cycle({"hrv": [{"date": "2026-06-01", "value": 50}]})
    assert not (hk_datadir / "cycle_log.json").exists()


def test_merge_healthkit_cycle_never_raises_on_garbage_payload(hk_datadir):
    """Robustez: payload con basura no lanza (nunca-crash)."""
    from app.sources.healthkit import merge_healthkit_cycle
    with patch("app.profile.effective", return_value=True):
        merge_healthkit_cycle({"menstrual_flow": "not-a-list"})
        merge_healthkit_cycle({"menstrual_flow": [{"date": "bad-date", "value": "x"}, "garbage"]})
        merge_healthkit_cycle(None)
        merge_healthkit_cycle({})


def test_ingest_calls_merge_healthkit_cycle_when_toggle_on(hk_datadir):
    """HealthKitSource.ingest() invoca merge_healthkit_cycle internamente cuando
    el toggle está prendido -> cycle_log.json queda poblado tras un ingest normal."""
    from app import cycle as cycle_mod
    with patch.object(cycle_mod, "_CYCLE_LOG_FILE", hk_datadir / "cycle_log.json"), \
         patch("app.profile.effective", return_value=True):
        src = HealthKitSource()
        src.ingest(SAMPLE_PAYLOAD_WITH_CYCLE)
        log = cycle_mod.load_cycle_log()
    assert len(log["periods"]) == 1


def test_ingest_does_not_crash_when_merge_healthkit_cycle_raises(hk_datadir):
    """Si merge_healthkit_cycle lanza inesperadamente, ingest() sigue devolviendo
    los datos normalizados (nunca-crash del ingest general por un fallo de ciclo)."""
    with patch("app.sources.healthkit.merge_healthkit_cycle", side_effect=RuntimeError("boom")):
        src = HealthKitSource()
        data = src.ingest(SAMPLE_PAYLOAD_WITH_CYCLE)
    assert data["hrv"]["2026-06-28"] == 54.6


def test_api_ingest_non_ascii_header_no_500(tmp_path):
    """Un X-Vitals-Token con bytes no-ASCII (header latin-1 malformado) NO debe
    reventar en TypeError de secrets.compare_digest (que daría 500). Debe dar 401.
    Se llama a api_ingest directo porque el cliente HTTP rechaza enviar headers
    no-ASCII, pero un servidor ASGI real sí entrega ese str al endpoint.
    """
    import asyncio
    from app import config
    import main as main_mod

    class _Headers(dict):
        def get(self, k, default=None):
            return "t\xf6ken" if k == "X-Vitals-Token" else default

    class _Req:
        headers = _Headers()

    with patch.object(config.settings, "INGEST_TOKEN", "s3cret"):
        resp = asyncio.run(main_mod.api_ingest(_Req()))
    assert resp.status_code == 401
    assert json.loads(bytes(resp.body))["status"] == "unauthorized"
