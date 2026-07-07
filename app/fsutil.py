"""
fsutil.py — helpers de filesystem compartidos.

atomic_write_text: escritura atómica (.tmp + os.replace) para los archivos de datos
que antes usaban write_text directo (health_compact.json, token.json de Google,
healthkit_ingest.json). profile.py / coach_store.py / _tokenstore.py ya tienen su
propio patrón atómico equivalente — no se unifican aquí (Ronda 1: solo los rotos).
"""
from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Escribe .tmp en el MISMO directorio + os.replace (atómico en POSIX y Windows)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)
