"""
voice.py — Cliente de voz (STT + TTS) del Coach, roadmap coach-voz Paso 2.
PURO stdlib (urllib.request) — mismo patrón EXACTO de
app/llm.py::_generate_openai_compat y app/notify.py. CERO dependencia nueva.

transcribe(audio_bytes, content_type) -> Optional[str]
  POST raw a {VITALS_STT_URL}/transcribe. Nunca lanza -> None + log (el
  caller lo trata como "STT caído").

synthesize(text, locale="es") -> Optional[bytes]
  Recorta el texto a 1200 chars (fin de oración) y hace POST JSON a
  {VITALS_TTS_URL}/tts. Nunca lanza -> None + log ("TTS caído").

Caché de audio (household-aware, mismo patrón `_user_data_dir()` de
coach_store.py/mental_store.py):
  save_audio(wav_bytes) -> Optional[str]  — audio_id (token urlsafe)
  audio_path(audio_id) -> Optional[Path]  — regex ANTES de tocar filesystem
  Cap 50 archivos por usuario (evicta el más viejo por mtime asc).

Timeouts: STT 60s, TTS 60s (nunca cuelgan el request más allá de eso).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from app.config import settings as _settings

logger = logging.getLogger("vitals.voice")

_DATA_DIR: Path = _settings.DATA_DIR

_VOICE_AUDIO_DIRNAME = "voice_audio"
_MAX_AUDIO_FILES = 50
_MAX_SYNTH_CHARS = 1200
_TIMEOUT_S = 60

# Mismo formato de token que coach_store._new_id()/mental_store._new_id()
# (secrets.token_urlsafe), acotado a un rango seguro para regex/filesystem.
_AUDIO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,32}$")

_SENTENCE_END_RE = re.compile(r"[.!?…]")


def _user_data_dir() -> Optional[Path]:
    """Directorio del usuario activo si hay contexto household-aware, o None
    si no (tests preexistentes, scripts) — mismo patrón EXACTO que
    app/coach_store.py::_user_data_dir() y app/mental_store.py::_user_data_dir()."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir()
    except Exception:
        pass
    return None


def _voice_audio_dir() -> Path:
    d = _user_data_dir()
    base = d if d is not None else _DATA_DIR
    return base / _VOICE_AUDIO_DIRNAME


# ── STT ──────────────────────────────────────────────────────────────────

def transcribe(audio_bytes: bytes, content_type: str) -> Optional[str]:
    """POST raw del audio a {VITALS_STT_URL}/transcribe. Nunca lanza: server
    caído/timeout/HTTP no-2xx/JSON malformado/sin 'text' -> None + log."""
    if not audio_bytes:
        return None
    base = (_settings.VITALS_STT_URL or "").rstrip("/")
    if not base:
        logger.warning("transcribe: VITALS_STT_URL vacío, no puedo llamar.")
        return None

    url = f"{base}/transcribe"
    headers = {"Content-Type": content_type or "application/octet-stream"}
    try:
        req = urllib.request.Request(url, data=audio_bytes, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                logger.error("transcribe: STT respondió status %s", resp.status)
                return None
            body = resp.read().decode("utf-8", errors="replace")

        parsed = json.loads(body)
        text = parsed.get("text") if isinstance(parsed, dict) else None
        if not text or not isinstance(text, str):
            logger.warning("transcribe: STT respondió sin 'text' válido.")
            return None
        return text.strip() or None

    except urllib.error.URLError as exc:
        logger.error("transcribe falló (URLError): %s", exc)
        return None
    except json.JSONDecodeError as exc:
        logger.error("transcribe: JSON malformado en la respuesta: %s", exc)
        return None
    except Exception as exc:
        logger.error("transcribe: error inesperado: %s", exc)
        return None


# ── TTS ──────────────────────────────────────────────────────────────────

def _truncate_at_sentence_end(text: str, limit: int = _MAX_SYNTH_CHARS) -> str:
    """Recorta `text` a `limit` chars, retrocediendo hasta el último fin de
    oración (. ! ? …) dentro de la ventana, para no cortar a media frase.
    Sin ningún fin de oración en el rango -> corte duro en `limit`. Texto ya
    corto -> tal cual. Nunca lanza."""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    window = text[:limit]
    last_end = -1
    for m in _SENTENCE_END_RE.finditer(window):
        last_end = m.end()
    if last_end > 0:
        return window[:last_end].strip()
    return window.strip()


def synthesize(text: str, locale: str = "es") -> Optional[bytes]:
    """Recorta el texto y hace POST JSON a {VITALS_TTS_URL}/tts. Nunca lanza:
    server caído/timeout/HTTP no-2xx/cuerpo vacío -> None + log (el caller lo
    trata como 'TTS caído': la respuesta de texto SÍ se persiste igual)."""
    if not text:
        return None
    base = (_settings.VITALS_TTS_URL or "").rstrip("/")
    if not base:
        logger.warning("synthesize: VITALS_TTS_URL vacío, no puedo llamar.")
        return None

    trimmed = _truncate_at_sentence_end(text)
    payload = {
        "text": trimmed,
        "speaker": _settings.VITALS_TTS_SPEAKER,
        "language": locale or "es",
    }
    url = f"{base}/tts"
    headers = {"Content-Type": "application/json"}
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            if not (200 <= resp.status < 300):
                logger.error("synthesize: TTS respondió status %s", resp.status)
                return None
            wav_bytes = resp.read()
        if not wav_bytes:
            logger.warning("synthesize: TTS respondió cuerpo vacío.")
            return None
        return wav_bytes

    except urllib.error.URLError as exc:
        logger.error("synthesize falló (URLError): %s", exc)
        return None
    except Exception as exc:
        logger.error("synthesize: error inesperado: %s", exc)
        return None


# ── Caché de audio (household-aware) ────────────────────────────────────────

def _new_audio_id() -> str:
    return secrets.token_urlsafe(12)


def _evict_oldest_if_over_cap(d: Path) -> None:
    """Evicta los archivos más viejos (por mtime asc) si hay más de
    _MAX_AUDIO_FILES en `d`. Nunca lanza."""
    try:
        files = sorted(d.glob("*.wav"), key=lambda p: p.stat().st_mtime)
        excess = len(files) - _MAX_AUDIO_FILES
        for p in files[:max(0, excess)]:
            try:
                p.unlink()
            except Exception:
                pass
    except Exception as exc:
        logger.warning("_evict_oldest_if_over_cap falló: %s", exc)


def save_audio(wav_bytes: bytes) -> Optional[str]:
    """Guarda `wav_bytes` en data/users/<uid>/voice_audio/<id>.wav (o
    data/voice_audio/<id>.wav sin household activo) y devuelve el audio_id.
    Cap _MAX_AUDIO_FILES por usuario (evicta el más viejo). Nunca lanza ->
    None en error."""
    if not wav_bytes:
        return None
    try:
        d = _voice_audio_dir()
        d.mkdir(parents=True, exist_ok=True)
        audio_id = _new_audio_id()
        path = d / f"{audio_id}.wav"
        path.write_bytes(wav_bytes)
        _evict_oldest_if_over_cap(d)
        return audio_id
    except Exception as exc:
        logger.error("save_audio falló: %s", exc)
        return None


def audio_path(audio_id: str) -> Optional[Path]:
    """Ruta del audio cacheado del usuario ACTIVO, o None si `audio_id` no
    matchea el formato token (regex validada ANTES de tocar filesystem —
    blindaje path traversal, ver riesgo #2 del roadmap) o el archivo no
    existe (incluye el caso de un audio_id válido pero de OTRO usuario: como
    la ruta se resuelve sobre `_voice_audio_dir()` del contexto activo, un
    id ajeno simplemente no existe ahí -> None, aislamiento household
    automático). Nunca lanza."""
    if not audio_id or not _AUDIO_ID_RE.match(audio_id):
        return None
    try:
        path = _voice_audio_dir() / f"{audio_id}.wav"
        return path if path.exists() else None
    except Exception as exc:
        logger.warning("audio_path falló: %s", exc)
        return None
