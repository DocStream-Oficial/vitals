"""
test_demo_hermeticity.py — Fase de cierre "hermeticidad demo" (roadmap
ROADMAP-vitals-demo-hermeticity.md, paso H2).

Bug que este test existe para atrapar: ~8 módulos de app/ definían su
`_DATA_DIR` como `Path(__file__).resolve().parent.parent / "data"` con un
`try/except` que en teoría caía a `settings.DATA_DIR` pero cuyo fallback
apuntaba SIEMPRE al `data/` real del repo. En VITALS_DEMO=1,
`settings.DATA_DIR` es un tempdir efímero (`tempfile.mkdtemp()`) — si
CUALQUIER módulo bypasea eso y escribe bajo `<repo>/data`, la promesa de
hermeticidad del modo demo es falsa (filtra al `data/` real, potencialmente
con datos reales de un usuario).

Estrategia (NO toca el `data/` real del usuario):
  1. Construye un "mini-repo" en un tmp dir: symlinks al código real
     (app/, main.py, templates/, static/, assets/ si existe) + un `.env`
     vacío + un `data/` SINTÉTICO propio (fixture, no el real).
  2. Lanza un subproceso Python con cwd=mini-repo y VITALS_DEMO=1 en el env.
     Ese subproceso importa la app, ejerce escrituras en cada módulo
     sospechoso (journal, labs, cycle, coach, report, notify, profile, auth,
     ingest token) y al final imprime un JSON con settings.DATA_DIR resuelto.
  3. Verifica: (a) settings.DATA_DIR NO es el `data/` sintético del mini-repo
     (debe ser un tempdir bajo el tempdir del SO); (b) el `data/` sintético
     del mini-repo es BYTE-IDÉNTICO antes/después (MD5 recursivo); (c) no
     aparecieron archivos nuevos en la raíz del mini-repo.

Un test separado (`test_normal_mode_uses_repo_data_dir`) corre el mismo
subproceso SIN VITALS_DEMO y confirma que ahí sí `settings.DATA_DIR` resuelve
al `data/` del mini-repo (modo normal intacto — comportamiento esperado,
no una fuga).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Módulos/paquetes de CÓDIGO que se symlinkean al mini-repo (nunca se escribe
# ahí en runtime salvo __pycache__, irrelevante para el hash de data/).
_CODE_ENTRIES = ["app", "main.py", "templates", "static", "assets", "vitals_mcp.py"]


def _make_mini_repo(tmp_path: Path) -> Path:
    """Crea un mini-repo aislado con el código real + data/ sintético propio +
    .env vacío. Nunca toca el data/ real del usuario.

    IMPORTANTE: `app/config.py` calcula `_ROOT = Path(__file__).resolve().parent.parent`
    — `.resolve()` sigue symlinks, así que si `app/` fuera un symlink al repo
    real, `_ROOT` resolvería SIEMPRE al repo real (no al mini-repo), sin
    importar dónde esté el cwd/PYTHONPATH. Por eso `app/` se COPIA de verdad
    (no symlink) — el resto (templates/static/assets, que main.py referencia
    por separado y no participan en el hash de hermeticidad) sí puede ir por
    symlink para que la copia sea barata."""
    import shutil as _shutil

    mini = tmp_path / "mini_repo"
    mini.mkdir()

    for name in _CODE_ENTRIES:
        src = _REPO_ROOT / name
        if not src.exists():
            continue
        if name == "app":
            # Copia real: _ROOT en config.py debe resolver DENTRO del mini-repo.
            _shutil.copytree(src, mini / name, symlinks=False)
        else:
            (mini / name).symlink_to(src, target_is_directory=src.is_dir())

    # .env vacío: sin credenciales reales, basta para que load_dotenv no truene.
    (mini / ".env").write_text("", encoding="utf-8")

    # data/ SINTÉTICO (fixture) — nunca es el data/ real del repo.
    data_dir = mini / "data"
    data_dir.mkdir()
    (data_dir / "health_compact.json").write_text(
        json.dumps({"days": [], "synthetic_fixture": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    (data_dir / "profile.json").write_text(
        json.dumps({"name": "Fixture", "onboarded": True}, ensure_ascii=False),
        encoding="utf-8",
    )
    users_dir = data_dir / "users"
    users_dir.mkdir()

    return mini


def _md5_tree(root: Path) -> dict:
    """MD5 por archivo (ruta relativa -> hexdigest) de TODO lo que hay bajo
    `root`, recursivo. Determinista y barato; suficiente para detectar
    cualquier escritura/creación/borrado byte a byte."""
    out = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.md5(p.read_bytes()).hexdigest()
    return out


# Script que corre DENTRO del subproceso (mini-repo como cwd). Ejerce
# escrituras en cada módulo sospechoso listado en el roadmap. Todo dentro de
# un solo proceso Python para que `config.py` lea VITALS_DEMO una sola vez,
# al import, como en producción real.
#
# GUARDIA CRÍTICA (defensa en profundidad): antes de CUALQUIER escritura,
# aborta duro si settings.DATA_DIR resolvió al data/ real del repo del usuario
# (pasado vía _REAL_REPO_DATA_DIR_FORBIDDEN en el env). Esto existe porque
# una versión temprana de este test (app/ symlinkeado en vez de copiado al
# mini-repo) dejó que _ROOT en config.py seguirse el symlink y resolviera al
# repo real — el driver alcanzó a escribir datos dummy en el data/ real ANTES
# de que el assert de aislamiento fallara. Nunca más: si esto dispara, el
# subproceso muere ANTES de tocar ningún archivo.
_DRIVER_SCRIPT = r"""
import json
import os
import sys
from pathlib import Path

# Import primero config (lee VITALS_DEMO en import, fija settings.DATA_DIR).
from app.config import settings

_forbidden = os.environ.get("_REAL_REPO_DATA_DIR_FORBIDDEN")
if _forbidden:
    _forbidden_path = Path(_forbidden).resolve()
    _resolved = Path(settings.DATA_DIR).resolve()
    if _resolved == _forbidden_path or _forbidden_path in _resolved.parents:
        print(json.dumps({
            "ABORT": True,
            "reason": f"settings.DATA_DIR ({_resolved}) resolvio al data/ real prohibido ({_forbidden_path})",
        }))
        sys.exit(97)

from app import journal, labs, cycle, coach_store, profile, notify, userctx, auth, report, plan_store, api_keys

result = {"data_dir": str(settings.DATA_DIR)}

# journal.py
j = journal.load_journal()
j = journal.set_entry("2026-01-01", {"alcohol": False, "creatina": True})
result["journal_ok"] = True

# plan_store.py (Roadmap P1, F4) — inicia y marca un check manual.
plan_store.start_plan("sleep_reset", "2026-01-01")
plan_store.manual_check("2026-01-01")
result["plan_store_ok"] = True

# labs.py
labs.add_entry("2026-01-01", "glucose", 90.0, unit="mg/dL")
result["labs_ok"] = True

# cycle.py
log = cycle.load_cycle_log()
log.setdefault("periods", []).append({"start": "2026-01-01", "end": "2026-01-04"})
cycle.save_cycle_log(log)
result["cycle_ok"] = True

# coach_store.py (mock — no dispara CLI real de claude)
cid = coach_store.append_turn(None, "hola coach", "hola, dummy answer de test")
result["coach_ok"] = bool(cid)

# report.py — cache directo, sin invocar el CLI de claude.
report.save_cache({"weekly": {"generated_at": "test", "text": "dummy"}})
result["report_ok"] = True

# notify.py
notify.save_notify_state({"last_brief_date": "2026-01-01", "sent_alerts": []})
result["notify_ok"] = True

# api_keys.py (Roadmap P2, F10) — genera y revoca una clave dummy.
_key = api_keys.generate_key("demo hermeticity test")
if _key:
    api_keys.revoke_key(_key["id"])
result["api_keys_ok"] = bool(_key)

# profile.py
profile.save_profile({"name": "Demo", "onboarded": True})
result["profile_ok"] = True

# auth.py — token dummy (nunca un token real).
auth._save_token({"access_token": "dummy", "refresh_token": "dummy", "expiry": "2000-01-01T00:00:00"})
result["auth_ok"] = True

# userctx.py — chokepoint del household: fuerza resolución + registro.
result["userctx_data_root"] = str(userctx._data_root())

print(json.dumps(result))
"""


def _run_driver(mini_repo: Path, *, demo: bool) -> dict:
    env = dict(os.environ)
    env.pop("VITALS_DEMO", None)
    if demo:
        env["VITALS_DEMO"] = "1"
    # PYTHONPATH explícito al mini-repo para que `import app`/`import main`
    # resuelvan ahí, no al repo real por CWD accidental.
    env["PYTHONPATH"] = str(mini_repo)
    # Guardia crítica del driver (ver comentario junto a _DRIVER_SCRIPT): el
    # data/ real del repo del usuario queda prohibido de antemano, sin importar
    # demo/normal — el mini-repo NUNCA debe resolver ahí.
    env["_REAL_REPO_DATA_DIR_FORBIDDEN"] = str(_REPO_ROOT / "data")

    proc = subprocess.run(
        [sys.executable, "-c", _DRIVER_SCRIPT],
        cwd=str(mini_repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode == 97:
        pytest.fail(
            "GUARDIA CRÍTICA: el driver abortó porque settings.DATA_DIR "
            f"resolvió al data/ real del repo (aislamiento roto). stdout:\n{proc.stdout}"
        )
    assert proc.returncode == 0, (
        f"subproceso driver falló (demo={demo}):\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )
    # La última línea de stdout es el JSON (por si algo más loguea a stdout).
    last_line = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1]
    return json.loads(last_line)


def test_demo_mode_never_touches_synthetic_data_dir(tmp_path):
    """VITALS_DEMO=1: ninguna escritura debe caer en el data/ sintético del
    mini-repo (equivalente al data/ real en producción). settings.DATA_DIR
    debe resolver a un tempdir efímero, DISTINTO del data/ del mini-repo."""
    mini_repo = _make_mini_repo(tmp_path)
    data_dir = mini_repo / "data"

    before_hash = _md5_tree(data_dir)
    before_root_listing = sorted(p.name for p in mini_repo.iterdir())

    result = _run_driver(mini_repo, demo=True)

    # Todas las escrituras del driver reportan éxito.
    for key in (
        "journal_ok", "labs_ok", "cycle_ok", "coach_ok", "report_ok",
        "notify_ok", "profile_ok", "auth_ok", "plan_store_ok", "api_keys_ok",
    ):
        assert result.get(key) is True, f"{key} no se disparó/confirmó en el driver: {result}"

    # settings.DATA_DIR resuelto en demo NO es el data/ sintético del mini-repo.
    resolved_data_dir = Path(result["data_dir"])
    assert resolved_data_dir != data_dir, (
        "settings.DATA_DIR en modo demo apunta al data/ del mini-repo "
        f"({resolved_data_dir}) — se esperaba un tempdir efímero distinto."
    )
    # Y userctx (chokepoint del household) resuelve al MISMO tempdir efímero,
    # no al data/ del mini-repo.
    assert Path(result["userctx_data_root"]) == resolved_data_dir, (
        "userctx._data_root() no coincide con settings.DATA_DIR en modo demo "
        f"(userctx={result['userctx_data_root']!r} vs settings={result['data_dir']!r})"
    )

    after_hash = _md5_tree(data_dir)
    after_root_listing = sorted(p.name for p in mini_repo.iterdir())

    assert after_hash == before_hash, (
        "El data/ sintético del mini-repo CAMBIÓ durante una corrida en modo "
        "demo — alguna escritura filtró fuera de settings.DATA_DIR (tempdir). "
        f"Antes={before_hash} Después={after_hash}"
    )
    assert after_root_listing == before_root_listing, (
        "Aparecieron/desaparecieron archivos en la raíz del mini-repo durante "
        f"una corrida demo. Antes={before_root_listing} Después={after_root_listing}"
    )

    # No debe quedar ningún users/ nuevo creado dentro del data/ sintético
    # (el household demo debe vivir enteramente bajo el tempdir efímero).
    assert not any((data_dir / "users").rglob("*")) or list((data_dir / "users").iterdir()) == [], (
        "El data/ sintético del mini-repo terminó con contenido nuevo bajo users/ "
        "— el household filtró fuera del tempdir efímero de demo."
    )


def test_normal_mode_uses_repo_data_dir(tmp_path):
    """Sin VITALS_DEMO: settings.DATA_DIR debe seguir siendo <repo>/data
    EXACTO (comportamiento actual intacto) — aquí sí se espera que el driver
    escriba en el data/ sintético del mini-repo, porque ESE es su "repo/data"."""
    mini_repo = _make_mini_repo(tmp_path)
    data_dir = mini_repo / "data"

    result = _run_driver(mini_repo, demo=False)

    resolved_data_dir = Path(result["data_dir"])
    assert resolved_data_dir == data_dir, (
        "En modo normal, settings.DATA_DIR debe ser exactamente <repo>/data. "
        f"Se obtuvo {resolved_data_dir} en vez de {data_dir}."
    )
    assert Path(result["userctx_data_root"]) == data_dir

    # Y en este caso el data/ sintético SÍ debe haber recibido las escrituras
    # (journal_log.json, labs_log.json, etc.) — confirma que el driver
    # realmente ejerce I/O real, no un no-op.
    written_names = {p.name for p in data_dir.rglob("*") if p.is_file()}
    for expected in (
        "journal_log.json", "labs_log.json", "cycle_log.json",
        "coach_conversations.json", "reports.json", "notify_state.json",
        "profile.json", "token.json", "plan_log.json", "api_keys.json",
    ):
        assert expected in written_names, (
            f"Se esperaba que el modo normal escribiera {expected} bajo "
            f"{data_dir}, pero no aparece. Archivos presentes: {written_names}"
        )
