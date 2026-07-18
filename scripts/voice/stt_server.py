#!/usr/bin/env python3
"""
scripts/voice/stt_server.py — Server STT standalone del Coach (roadmap
coach-voz, Paso 1). FastAPI que carga whisper UNA vez y transcribe audio.
Vive en el WSL del box (venv `~/CosyVoice/venv`, torch CUDA ya probado),
NO en el proceso de Vitals — NADA en app/ importa este archivo.

Contrato:
  GET  /health              -> {"ok": true, "model_loaded": bool, "model": "<nombre>"}
  POST /transcribe?lang=es  -> raw body (audio) + header Content-Type -> {"text", "duration_s"}
    Errores: body vacío -> 400; body >15MB -> 413; modelo no disponible o
    whisper/ffmpeg lanzan -> 503 {"error": ...}.

Import de whisper DIFERIDO (dentro de load_model(), no al tope del módulo)
— testeable sin GPU ni modelo real: tests/test_stt_server.py inyecta un
whisper falso en sys.modules antes de llamar load_model(). NUNCA se
descarga ningún modelo real en la suite de tests.

WHISPER_MODEL env var, default "medium" (se carga UNA vez al boot y vive en
memoria). Bind 0.0.0.0:8102 — wslrelay lo expone a Windows en
127.0.0.1:8102, mismo patrón que el server XTTS en :8100 (ver README.md de
este directorio).
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vitals-stt")

app = FastAPI(title="Vitals STT")

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "medium")

# Mismo cap que POST /api/coach/voice en Vitals (app/routes/coach_voice.py) —
# consistencia de límites entre el cliente (Vitals) y este server.
MAX_BODY_BYTES = 15 * 1024 * 1024  # 15 MB

# Modelo cargado al boot (load_model(), llamado desde el evento startup).
# None hasta que cargue con éxito — /health lo reporta, /transcribe 503 si
# todavía no está listo (nunca lanza, nunca cuelga el request esperando).
_model = None

_EXT_BY_CONTENT_TYPE = {
    "audio/mp4": ".mp4",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/ogg": ".ogg",
}


def load_model() -> None:
    """Carga el modelo whisper UNA vez. Import DIFERIDO — permite importar
    este módulo en tests sin whisper instalado (sys.modules mockeado).
    Nunca lanza: si falla (whisper ausente, modelo corrupto, sin GPU/RAM),
    `_model` queda None — /health lo reporta y /transcribe responde 503 en
    vez de tumbar el proceso."""
    global _model
    try:
        import whisper  # deferred — ver docstring del módulo
        logger.info("Cargando modelo whisper '%s'…", WHISPER_MODEL)
        _model = whisper.load_model(WHISPER_MODEL)
        logger.info("Modelo whisper '%s' cargado.", WHISPER_MODEL)
    except Exception as exc:
        logger.error("No se pudo cargar el modelo whisper '%s': %s", WHISPER_MODEL, exc)
        _model = None


@app.on_event("startup")
def _on_startup() -> None:
    load_model()


def _ext_for_content_type(content_type: Optional[str]) -> str:
    """Extensión de archivo temporal según Content-Type del blob grabado
    (Safari manda audio/mp4, Chrome audio/webm). Desconocido -> .bin
    (ffmpeg/whisper huelen el formato real por contenido, no por extensión)."""
    if not content_type:
        return ".bin"
    base = content_type.split(";")[0].strip().lower()
    return _EXT_BY_CONTENT_TYPE.get(base, ".bin")


def _duration_from_result(result: dict) -> float:
    """Duración aproximada en segundos: fin del último segmento transcrito
    por whisper. Sin segmentos (o forma inesperada) -> 0.0. Nunca lanza."""
    try:
        segments = result.get("segments") or []
        if segments:
            return float(segments[-1].get("end") or 0.0)
    except Exception:
        pass
    return 0.0


@app.get("/health")
async def health():
    return JSONResponse(content={
        "ok": True,
        "model_loaded": _model is not None,
        "model": WHISPER_MODEL,
    })


@app.post("/transcribe")
async def transcribe(request: Request, lang: str = "es"):
    body = await request.body()
    if not body:
        return JSONResponse(status_code=400, content={"error": "Body vacío."})
    if len(body) > MAX_BODY_BYTES:
        return JSONResponse(status_code=413, content={"error": "Audio demasiado grande."})

    if _model is None:
        return JSONResponse(status_code=503, content={"error": "Modelo whisper no disponible."})

    ext = _ext_for_content_type(request.headers.get("content-type"))
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(body)
            tmp_path = Path(tmp.name)

        result = _model.transcribe(str(tmp_path), language=lang or "es")
        text = (result.get("text") or "").strip() if isinstance(result, dict) else ""
        duration_s = _duration_from_result(result) if isinstance(result, dict) else 0.0
        return JSONResponse(content={"text": text, "duration_s": duration_s})
    except Exception as exc:
        logger.error("transcribe falló: %s", exc)
        return JSONResponse(status_code=503, content={"error": "No se pudo transcribir el audio."})
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8102)
