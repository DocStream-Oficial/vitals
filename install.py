#!/usr/bin/env python3
"""install.py — instalador seamless de Vitals, un solo comando.

Uso:
    python install.py                  # wizard interactivo (Enter = default)
    python install.py --demo           # modo demo, sin credenciales, arranca
    python install.py --no-launch      # solo setup (venv + deps + .env)
    python install.py --yes            # no-interactivo (CI/scripts)
    python install.py --port 9000      # puerto custom
    python install.py --help

Requisitos de diseño (ver ROADMAP-vitals-install-seamless.md):
- SOLO stdlib. Este script corre ANTES de instalar dependencias de terceros,
  así que no puede importar nada que no venga con Python. No añadir imports
  de paquetes de requirements.txt aquí.
- Python 3.9+ (mismo mínimo que el resto del proyecto).
- Idempotente: si .venv existe se reusa; si .env existe NO se pisa.
- Cross-platform: usa sys.executable para crear el venv (no more python3 vs
  py -3 vs python), y pathlib para las rutas (Scripts\\python.exe en
  Windows, bin/python en posix).
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

MIN_PYTHON = (3, 9)
DEFAULT_PORT = 8700
ROOT = Path(__file__).resolve().parent


# ──────────────────────────────────────────────────────────────────────────
# Helpers puros (sin I/O real más allá de lo que reciben como argumento) —
# estos son los que tests/test_install.py ejercita directamente.
# ──────────────────────────────────────────────────────────────────────────


def venv_python_path(root: Path, os_name: str) -> Path:
    """Ruta al intérprete Python dentro de .venv, según el SO.

    `os_name` es el valor de `os.name` ("nt" en Windows, "posix" en
    Mac/Linux) — se recibe como parámetro (en vez de leer os.name adentro)
    para que sea puro y testeable sin monkeypatch de os.name.
    """
    venv_dir = root / ".venv"
    if os_name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def build_env_content(example_text: str, answers: dict) -> str:
    """Construye el contenido final de .env a partir de .env.example + answers.

    Reglas:
    - Recorre .env.example línea por línea; para cada línea `KEY=valor`,
      si `answers` trae un valor no-vacío para KEY, lo sustituye; si no,
      conserva la línea del .example tal cual (placeholder/default).
    - Líneas de comentario o vacías se preservan sin cambios.
    - No duplica claves: cada KEY se resuelve una sola vez, en su posición
      original dentro de .env.example.
    - INGEST_TOKEN: si answers no trae uno (o viene vacío) y la clave
      aparece en el .example, se autogenera con secrets.token_urlsafe(32).
      Esto sólo debe invocarse cuando NO existe ya un .env real (el
      caller es responsable de no pisar uno existente).
    - VITALS_DEMO: si answers incluye demo=True, agrega/activa
      `VITALS_DEMO=1` al final (si la línea ya existe comentada en el
      .example se descomenta con el valor 1; si no existe, se añade).
    """
    demo = bool(answers.get("_demo"))
    consumed_keys: set[str] = set()
    out_lines: list[str] = []

    for raw_line in example_text.splitlines():
        stripped = raw_line.strip()

        # Línea comentada que representa VITALS_DEMO=1 (ej. "# VITALS_DEMO=1")
        if demo and stripped.lstrip("#").strip().startswith("VITALS_DEMO="):
            out_lines.append("VITALS_DEMO=1")
            consumed_keys.add("VITALS_DEMO")
            continue

        # Comentarios / líneas vacías: preservar tal cual.
        if not stripped or stripped.startswith("#"):
            out_lines.append(raw_line)
            continue

        if "=" not in stripped:
            out_lines.append(raw_line)
            continue

        key, _, _default_value = stripped.partition("=")
        key = key.strip()

        if key == "INGEST_TOKEN":
            value = answers.get(key) or ""
            if not value:
                value = secrets.token_urlsafe(32)
            out_lines.append(f"{key}={value}")
            consumed_keys.add(key)
            continue

        if key in answers and answers[key] not in (None, ""):
            out_lines.append(f"{key}={answers[key]}")
        else:
            # Conserva la línea original (placeholder / default del .example).
            out_lines.append(raw_line)
        consumed_keys.add(key)

    if demo and "VITALS_DEMO" not in consumed_keys:
        out_lines.append("VITALS_DEMO=1")

    content = "\n".join(out_lines)
    if not content.endswith("\n"):
        content += "\n"
    return content


def is_already_installed(root: Path) -> bool:
    """True si tanto .venv como .env ya existen bajo root."""
    return (root / ".venv").exists() and (root / ".env").exists()


def parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="install.py",
        description=(
            "Instalador de un comando para Vitals: crea el venv, instala "
            "dependencias, genera .env y (por default) arranca la app en "
            "http://127.0.0.1:8700."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Ejemplos:\n"
            "  python install.py                 wizard interactivo, luego arranca\n"
            "  python install.py --demo          modo demo, sin credenciales\n"
            "  python install.py --no-launch     solo setup (venv+deps+.env)\n"
            "  python install.py --yes           no-interactivo (CI/scripts)\n"
            "  python install.py --port 9000     puerto custom\n"
        ),
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Modo demo: VITALS_DEMO=1, no pide credenciales, listo para probar ya.",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="Solo hace el setup (venv + deps + .env); no arranca uvicorn.",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="No-interactivo: copia .env.example tal cual (autogenerando "
        "INGEST_TOKEN), sin preguntar nada. Apto para CI/scripts.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Puerto para uvicorn (default: {DEFAULT_PORT}).",
    )
    return parser.parse_args(argv)


# ──────────────────────────────────────────────────────────────────────────
# Salida amigable
# ──────────────────────────────────────────────────────────────────────────


def _step(n: int, total: int, msg: str) -> None:
    print(f"[{n}/{total}] {msg}")


def _fail(msg: str) -> "SystemExit":
    print(f"\nERROR: {msg}\n", file=sys.stderr)
    return SystemExit(1)


# ──────────────────────────────────────────────────────────────────────────
# Fases con I/O real (no cubiertas directamente por los tests puros, pero
# escritas para ser fáciles de mockear/leer)
# ──────────────────────────────────────────────────────────────────────────


def check_python_version() -> None:
    if sys.version_info < MIN_PYTHON:
        current = ".".join(str(p) for p in sys.version_info[:3])
        minimum = ".".join(str(p) for p in MIN_PYTHON)
        raise _fail(
            f"Vitals requiere Python {minimum}+ (detectado {current}). "
            f"Instala una version mas reciente de Python y vuelve a correr "
            f"'python install.py'."
        )


def ensure_venv(root: Path) -> Path:
    venv_dir = root / ".venv"
    py = venv_python_path(root, os.name)
    if venv_dir.exists():
        print(f"    .venv ya existe, se reusa ({venv_dir}).")
        return py

    print(f"    Creando entorno virtual en {venv_dir} ...")
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # No dejar un venv a medias en silencio: si algo quedo parcialmente
        # creado y no es utilizable, lo reportamos (no lo borramos solos,
        # para no destruir nada por accidente).
        raise _fail(
            "No se pudo crear el entorno virtual (.venv).\n"
            f"Comando: {sys.executable} -m venv {venv_dir}\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )
    if not py.exists():
        raise _fail(
            f"El venv se creo pero no encuentro el interprete esperado en "
            f"{py}. Revisa manualmente el contenido de {venv_dir}."
        )
    return py


def install_dependencies(venv_python: Path, root: Path) -> None:
    req = root / "requirements.txt"
    if not req.exists():
        raise _fail(f"No encuentro {req}. ¿Corriste install.py desde la raiz del repo?")

    print(f"    Instalando dependencias con {venv_python} -m pip ...")
    result = subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-q", "-r", str(req)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise _fail(
            "pip install fallo instalando requirements.txt.\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}"
        )


def _prompt(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        raw = input(f"    {label}{suffix}: ")
    except EOFError:
        # stdin cerrado (ej. corrido en background/CI sin -y) — no colgar,
        # tratar como Enter (usar default).
        raw = ""
    raw = raw.strip()
    return raw or default


def build_env_answers(demo: bool, yes: bool) -> dict:
    """Junta las respuestas para build_env_content según el modo.

    - demo o yes: no pregunta nada (answers vacío + flag _demo si aplica).
    - interactivo: wizard mínimo, todo opcional (Enter = placeholder/default
      del .env.example, que build_env_content preserva tal cual).
    """
    answers: dict = {"_demo": demo}
    if demo or yes:
        return answers

    print("\nConfiguracion inicial (.env) — todo es opcional, Enter para dejar")
    print("el valor por defecto / configurarlo despues a mano en .env.\n")

    fields = [
        ("CLIENT_ID", "Google CLIENT_ID (Enter para configurar despues)"),
        ("CLIENT_SECRET", "Google CLIENT_SECRET (Enter para configurar despues)"),
        ("BIRTHDATE", "Tu fecha de nacimiento (YYYY-MM-DD)"),
        ("WAIST_CM", "Circunferencia de cintura en cm"),
        ("SEX", "Sexo (M/F)"),
    ]
    for key, label in fields:
        value = _prompt(label)
        if value:
            answers[key] = value

    return answers


def write_env_file(root: Path, demo: bool, yes: bool) -> Path:
    env_path = root / ".env"
    example_path = root / ".env.example"

    if env_path.exists():
        print(f"    .env ya existe, no se toca ({env_path}).")
        return env_path

    if not example_path.exists():
        raise _fail(f"No encuentro {example_path}; no puedo generar .env.")

    example_text = example_path.read_text(encoding="utf-8")
    answers = build_env_answers(demo=demo, yes=yes)
    content = build_env_content(example_text, answers)
    env_path.write_text(content, encoding="utf-8")
    # .env lleva secretos (INGEST_TOKEN, OAuth) — restringir a solo-dueño.
    # Best-effort: en Windows os.chmod es casi no-op, no debe romper el flujo.
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    print(f"    .env generado en {env_path}.")
    if demo:
        print("    VITALS_DEMO=1 activo: no se necesitan credenciales reales.")
    return env_path


def launch_app(venv_python: Path, root: Path, port: int) -> None:
    print(f"    Lanzando uvicorn en http://127.0.0.1:{port} ...")
    # Popen (no run) porque uvicorn se queda corriendo en foreground —
    # queremos poder hacer el smoke-check mientras vive y luego dejarlo
    # atendiendo en primer plano (igual que start.sh).
    proc = subprocess.Popen(
        [
            str(venv_python),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=str(root),
    )

    url = f"http://127.0.0.1:{port}/"
    ok = False
    for _ in range(20):
        if proc.poll() is not None:
            break
        time.sleep(0.5)
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    ok = True
                    break
        except (urllib.error.URLError, ConnectionError, OSError):
            continue

    if proc.poll() is not None:
        print(
            f"\nAVISO: uvicorn termino solo (codigo {proc.returncode}). "
            f"Revisa el output arriba para el error real."
        )
        return

    if ok:
        print(f"\nListo. Vitals esta corriendo en {url}")
    else:
        print(
            f"\nAVISO: uvicorn esta corriendo pero el smoke-check GET {url} "
            f"no respondio a tiempo. Puede que siga arrancando; "
            f"abre {url} en el navegador en unos segundos."
        )

    print("Primeros pasos:")
    print(f"  - Abre {url} en tu navegador.")
    print(f"  - Conecta una fuente real en /auth/login, o explora la pestaña 'Mas'.")
    print("  - Ctrl+C aqui para detener el servidor.")

    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait()


def print_manual_start_instructions(venv_python: Path, root: Path, port: int) -> None:
    if os.name == "nt":
        activate = r".venv\Scripts\activate"
    else:
        activate = "source .venv/bin/activate"

    print("\nSetup listo. Para arrancar la app:")
    print(f"  {activate}")
    print(f"  uvicorn main:app --host 127.0.0.1 --port {port}")
    print(f"  # o directo: {venv_python} -m uvicorn main:app --host 127.0.0.1 --port {port}")


# ──────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────


def main(argv: list) -> int:
    args = parse_args(argv)

    check_python_version()

    total_steps = 3 if args.no_launch else 4
    print("Vitals — instalador seamless\n")

    _step(1, total_steps, "Verificando/creando entorno virtual (.venv)")
    venv_python = ensure_venv(ROOT)

    _step(2, total_steps, "Instalando dependencias (requirements.txt)")
    install_dependencies(venv_python, ROOT)

    _step(3, total_steps, "Generando/verificando .env")
    write_env_file(ROOT, demo=args.demo, yes=args.yes)

    if args.no_launch:
        print_manual_start_instructions(venv_python, ROOT, args.port)
        return 0

    _step(4, total_steps, "Arrancando Vitals")
    launch_app(venv_python, ROOT, args.port)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - red de seguridad final
        print(f"\nERROR inesperado: {exc}", file=sys.stderr)
        print(
            "Si el problema persiste, revisa el traceback completo corriendo "
            "con 'python -X dev install.py' o reporta este mensaje.",
            file=sys.stderr,
        )
        sys.exit(1)
