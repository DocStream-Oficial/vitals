"""
app/sources/_tokenstore.py — helper de storage de token POR-FUENTE.

Reusable por cualquier adaptador (Oura, WHOOP, HealthKit...) que necesite
persistir su propio token en data/token_<source>.json SIN tocar token.json
de Google (app/auth.py sigue siendo dueño exclusivo de ese archivo).

Escritura ATÓMICA (mismo patrón que app/profile.py): .tmp + os.replace.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from app.config import settings


def token_path(source_name: str) -> Path:
    """Ruta data/token_<source>.json para la fuente dada."""
    return settings.DATA_DIR / f"token_{source_name}.json"


def load_token(source_name: str) -> Optional[dict]:
    """Lee data/token_<source>.json → dict. None si no existe o está corrupto
    (nunca lanza excepción)."""
    path = token_path(source_name)
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return None
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def save_token(source_name: str, tok: dict) -> None:
    """Guarda data/token_<source>.json con escritura atómica (.tmp + os.replace)."""
    settings.DATA_DIR.mkdir(exist_ok=True)
    path = token_path(source_name)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(tok, indent=2), encoding="utf-8")
    os.replace(tmp, path)
