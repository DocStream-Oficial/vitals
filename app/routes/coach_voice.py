"""
app/routes/coach_voice.py — Notas de voz asíncronas del Coach (roadmap
coach-voz, Paso 3). Patrón de app/routes/coach.py: APIRouter, JSONResponse,
logger "vitals.main", nunca 500 en flujos degradables.

POST /api/coach/voice?conversation_id=...     — sube audio, transcribe,
    responde (reusa la bifurcación normal/master de api_coach), sintetiza.
GET  /api/coach/voice/audio/{audio_id}        — sirve el WAV cacheado.

IMPORTANTE (compat de tests, mismo motivo que app/routes/coach.py): las
llamadas a ask_coach/ask_master se resuelven vía `import main as _main`
DIFERIDO (dentro del handler) para que los tests puedan parchear
`main.ask_coach`/`main.ask_master` por nombre.

Upload como RAW BODY (no multipart): FastAPI exige python-multipart para
forms y NO está en requirements.txt — el endpoint lee `await request.body()`
con el Content-Type que mande el navegador (Safari graba audio/mp4, Chrome
audio/webm). Cap de tamaño: 15 MB -> 413.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app import coach_store as _coach_store
from app import voice as _voice
from app.deps import _load_dataset
from app.i18n import tr as _tr

logger = logging.getLogger("vitals.main")

router = APIRouter()

_MAX_BODY_BYTES = 15 * 1024 * 1024  # 15 MB


def _resolve_locale() -> str:
    try:
        from app.profile import effective as _peff
        return _peff("locale") or "es"
    except Exception:
        return "es"


@router.post("/api/coach/voice")
async def api_coach_voice_post(request: Request, conversation_id: Optional[str] = None):
    """Nota de voz -> transcribe -> responde (kind normal/master, MISMO
    camino que POST /api/coach) -> sintetiza. Nunca 500:
    - STT caído: {error_key: "voice_stt_down", message: ...} — SIN persistir turno.
    - TTS caído: respuesta normal, audio_id=None, voice=False — el turno SÍ
      se persiste (el coach respondió, solo falta el audio).
    """
    import main as _main  # deferred: tests parchean main.ask_coach/ask_master por nombre

    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="Body de audio vacío.")
    if len(body) > _MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Audio demasiado grande (máx 15 MB).")

    content_type = request.headers.get("content-type") or "application/octet-stream"
    locale = _resolve_locale()

    transcript = await run_in_threadpool(_voice.transcribe, body, content_type)
    if not transcript:
        return JSONResponse(content={
            "error_key": "voice_stt_down",
            "message": _tr("voice_stt_down", locale),
        })

    dataset = _load_dataset() or {}
    cid = conversation_id or _coach_store.get_active_id()
    context_history = _coach_store.get_context(cid, 10)
    kind = _coach_store.get_kind(cid)
    if kind == "mental_master":
        answer = await run_in_threadpool(_main.ask_master, transcript, dataset, context_history)
    else:
        answer = await run_in_threadpool(_main.ask_coach, transcript, dataset, context_history)

    used_cid = _coach_store.append_turn(cid, transcript, answer)
    _coach_store.set_active(used_cid)

    wav = await run_in_threadpool(_voice.synthesize, answer, locale)
    audio_id = _voice.save_audio(wav) if wav else None

    return JSONResponse(content={
        "transcript": transcript,
        "answer": answer,
        "conversation_id": used_cid,
        "audio_id": audio_id,
        "voice": bool(audio_id),
    })


@router.get("/api/coach/voice/audio/{audio_id}")
async def api_coach_voice_audio_get(audio_id: str):
    """Sirve el WAV cacheado. audio_id fuera del formato token (regex
    estricta en voice.audio_path, validada ANTES de tocar filesystem) o
    inexistente -> 404 controlado (nunca toca disco con un id malicioso)."""
    path = _voice.audio_path(audio_id)
    if path is None:
        raise HTTPException(status_code=404, detail="Audio no encontrado.")
    return FileResponse(path, media_type="audio/wav")
