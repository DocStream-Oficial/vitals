"""
app/routes/coach.py — GET /api/coach/suggestions, GET /api/sleep-coach,
GET/POST/DELETE /api/coach/conversations*, POST /api/coach,
GET/DELETE /api/coach/history (Fase 9, paso A2). Movidos TAL CUAL desde
main.py — ver ROADMAP-vitals-fase9-desmonolitizar.md.

IMPORTANTE (compat de tests): decenas de tests en tests/test_endpoints.py
parchean `main.ask_coach`, `main.load_history` y `main.clear_history` por
nombre (`patch("main.ask_coach", ...)`) — un import directo de
`app.coach_chat.ask_coach` aquí NUNCA vería ese patch (bindearía el nombre al
módulo original, no al atributo parcheado de `main`). Por eso estas 3
llamadas se resuelven vía `import main as _main` DIFERIDO (dentro de cada
handler, no al tope del módulo — evita import circular main<->coach) y se
invocan como `_main.ask_coach(...)` / `_main.load_history(...)` /
`_main.clear_history(...)`, igual que el patrón ya usado en app/deps.py para
DATA_PATH.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app import coach_store as _coach_store
from app import profile as _profile
from app.coach_suggest import suggested_questions as _suggested_questions
from app.deps import _load_dataset
from app.profile import effective_profile_dict
from app.routes._models import ConversationCreate, CoachRequest

logger = logging.getLogger("vitals.main")

router = APIRouter()


@router.get("/api/coach/suggestions")
async def api_coach_suggestions(locale: Optional[str] = None):
    """Preguntas sugeridas (chips) del tab Coach — F1 del roadmap P0.

    Devuelve {questions: [{id, text}]}, derivadas de los insights activos del
    dataset actual (mismo dataset/household que /api/insights — resuelto por
    el middleware de userctx vía X-Vitals-User) con fallback al pool genérico.
    Nunca 500: sin datos -> lista de genéricas (coach_suggest ya es None-safe).
    """
    dataset = _load_dataset()
    try:
        resolved_locale = locale or _profile.effective("locale") or "es"
        questions = _suggested_questions(dataset or {}, locale=resolved_locale, limit=4)
        return JSONResponse(content={"questions": questions})
    except Exception as e:
        logger.error(f"suggested_questions falló: {e}")
        return JSONResponse(content={"questions": []})


@router.get("/api/sleep-coach")
async def api_sleep_coach_get():
    """Recomendación de hora de dormir para esta noche (Fase 8C, paso C4).
    Sin datos suficientes (poco historial de wake time) -> {available: false}
    (nunca 500).

    Roadmap P1 F5 (paso 2): campos ADITIVOS `need_min/sleep_score/consistency`
    — el shape previo (bedtime/wake_assumed/extra_min/need_min/drivers) se
    conserva IDÉNTICO; sleep_score/consistency son None-safe y se calculan
    on-read desde app/sleep_scores.py (nunca tocan build_dataset)."""
    dataset = _load_dataset()
    if not dataset:
        return JSONResponse(content={"available": False})
    try:
        from app import sleep_coach as _sleep_coach
        from app import sleep_scores as _sleep_scores
        days = dataset.get("days", [])
        summary = dataset.get("summary", {})
        profile = effective_profile_dict()
        rec = _sleep_coach.recommend_bedtime(days, summary, profile)
        if rec is None:
            return JSONResponse(content={"available": False})
        rec["available"] = True

        # Aditivo (F5): need_min ya viene de recommend_bedtime (misma
        # fórmula) — sleep_score/consistency se derivan aparte, None-safe.
        today = days[-1] if days and isinstance(days[-1], dict) else {}
        rec["sleep_score"] = _sleep_scores.sleep_score(today.get("asleep"), rec.get("need_min"))
        rec["consistency"] = _sleep_scores.consistency_score(days)
        return JSONResponse(content=rec)
    except Exception as e:
        logger.error(f"GET /api/sleep-coach falló: {e}")
        return JSONResponse(content={"available": False})


@router.get("/api/coach/conversations")
async def api_coach_conversations_list():
    """Lista LIGERA de conversaciones [{id, title, updated, message_count}],
    orden por `updated` desc. Sin conversaciones -> []. Nunca 500."""
    return JSONResponse(content=_coach_store.list_conversations())


@router.post("/api/coach/conversations")
async def api_coach_conversations_create(body: ConversationCreate = None):
    """Crea una conversación vacía. Devuelve {id}."""
    title = body.title if body else None
    conv = _coach_store.create_conversation(title=title)
    return JSONResponse({"id": conv["id"]})


@router.get("/api/coach/conversations/{cid}")
async def api_coach_conversation_get(cid: str):
    """Conversación completa (con messages). id inexistente -> 404 controlado."""
    conv = _coach_store.get_conversation(cid)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    return JSONResponse(content=conv)


@router.delete("/api/coach/conversations/{cid}")
async def api_coach_conversation_delete(cid: str):
    """Borra SOLO esa conversación. Nunca 500."""
    _coach_store.delete_conversation(cid)
    return JSONResponse({"status": "ok"})


@router.post("/api/coach")
async def api_coach(body: CoachRequest):
    """Coach IA conversacional: recibe pregunta + conversation_id opcional,
    responde vía claude CLI. El contexto que se le pasa a ask_coach es SOLO
    el de ESA conversación (aislamiento — nunca se mezcla con otras).
    Sin conversation_id -> usa la activa, o crea una nueva. Si no hay datos de
    salud, responde igualmente con contexto mínimo. Si el CLI falla, devuelve
    fallback amable — nunca 500.

    NOTA (deprecación): `body.history` ya NO se usa como contexto — el contexto
    sale exclusivamente de coach_store.get_context(cid). Se ignora si viene.
    """
    import main as _main  # deferred: tests parchean main.ask_coach por nombre
    dataset = _load_dataset() or {}
    cid = body.conversation_id or _coach_store.get_active_id()
    # Contexto AISLADO: solo los últimos N mensajes de ESTA conversación.
    context_history = _coach_store.get_context(cid, 10)
    # Ronda 1: offload a threadpool — ask_coach lanza `claude` CLI vía subprocess.run
    # síncrono (hasta ~90 s); en el event loop congelaba TODA la app mientras tanto.
    answer = await run_in_threadpool(_main.ask_coach, body.question, dataset, context_history)
    # Persistir el turno (crea la conversación si cid era None/inexistente).
    used_cid = _coach_store.append_turn(cid, body.question, answer)
    _coach_store.set_active(used_cid)
    return JSONResponse({"answer": answer, "conversation_id": used_cid})


@router.get("/api/coach/history")
async def api_coach_history():
    """DEPRECADO: usa GET /api/coach/conversations/{id}. Devuelve los mensajes
    de la conversación ACTIVA (últimos 100). Sin activa -> [] (nunca 500)."""
    import main as _main  # deferred: tests parchean main.load_history por nombre
    history = _main.load_history()
    return JSONResponse(content=history[-100:])


@router.delete("/api/coach/history")
async def api_coach_history_clear():
    """DEPRECADO: usa DELETE /api/coach/conversations/{id}. Borra TODAS las
    conversaciones (clear_all). Escritura atómica."""
    import main as _main  # deferred: tests parchean main.clear_history por nombre
    _main.clear_history()
    return JSONResponse({"status": "ok", "message": "Historial borrado."})
