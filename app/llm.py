"""
llm.py — Backend de LLM intercambiable para TODOS los consumidores del coach
(chat, headline, reportes). Roadmap P0-launch-gaps, F3.

generate(prompt, *, timeout=90, purpose="coach") -> str | None
  Despacha según settings.COACH_BACKEND:
  - "claude_cli" (default): comportamiento EXACTO al que vivía duplicado en
    coach_chat.ask_coach / coach_headline._call_cli / report._call_cli
    (subprocess, stdin, shell=False, cmd /c en Windows, encoding utf-8
    errors=replace, timeout).
  - "openai_compat": POST a {COACH_API_BASE}/chat/completions vía
    urllib.request (SOLO stdlib, patrón de app/notify.py — cero dependencia
    nueva), header Authorization: Bearer {COACH_API_KEY} solo si hay key.
  Backend no reconocido -> warning + fallback a claude_cli.

  Nunca lanza: cualquier fallo (CLI ausente, timeout, HTTP caído, JSON
  malformado) devuelve None. El caller decide su propio fallback amable
  (coach_chat -> _FALLBACK; headline/report -> None, cache viejo se conserva).
"""
from __future__ import annotations

import json
import logging
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Optional

from app.config import settings

logger = logging.getLogger("vitals.llm")


# ── claude_cli (movido tal cual desde coach_chat.ask_coach) ─────────────────

def _generate_claude_cli(prompt: str, timeout: int, purpose: str) -> Optional[str]:
    """Llama al claude CLI pasando el prompt por STDIN. Nunca lanza.

    SEGURIDAD: el prompt va por input= (STDIN), NUNCA interpolado en la línea
    de comando del shell y NUNCA con shell=True.
    """
    cli = settings.CLAUDE_CLI

    try:
        if sys.platform == "win32":
            # En Windows, el .cmd necesita cmd /c para ejecutarse
            cmd = ["cmd", "/c", cli, "-p"]
        else:
            cmd = [cli, "-p"]

        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",      # forzar UTF-8: Windows usa cp1252 por defecto y revienta con ₂/acentos
            errors="replace",
            timeout=timeout,
            # NUNCA shell=True con la pregunta del usuario
        )

        if result.returncode != 0:
            logger.error(
                "claude CLI (%s) exit %d · stderr: %s",
                purpose, result.returncode,
                result.stderr[:500] if result.stderr else "(vacío)",
            )
            return None

        answer = result.stdout.strip()
        if not answer:
            logger.warning("claude CLI (%s) devolvió salida vacía", purpose)
            return None

        return answer

    except FileNotFoundError:
        logger.error(
            "claude CLI no encontrado en '%s' (%s). "
            "Configura CLAUDE_CLI en .env o instala el CLI de Claude.",
            cli, purpose,
        )
        return None
    except subprocess.TimeoutExpired:
        logger.error("claude CLI (%s) excedió el timeout de %ss.", purpose, timeout)
        return None
    except Exception as exc:
        logger.error("Error inesperado invocando claude CLI (%s): %s", purpose, exc)
        return None


# ── openai_compat (Ollama / LM Studio / llama.cpp) — SOLO stdlib ────────────

def _generate_openai_compat(prompt: str, timeout: int, purpose: str) -> Optional[str]:
    """POST {COACH_API_BASE}/chat/completions con un único mensaje de usuario.
    urllib.request puro (sin requests/openai SDK — el repo es stdlib-first,
    patrón de app/notify.py). Nunca lanza: HTTP caído/lento, timeout, JSON
    malformado o shape inesperada -> None + log."""
    base = (settings.COACH_API_BASE or "").rstrip("/")
    if not base:
        logger.warning("openai_compat (%s): COACH_API_BASE vacío, no puedo llamar.", purpose)
        return None

    url = f"{base}/chat/completions"
    payload = {
        "model": settings.COACH_MODEL,
        "messages": [{"role": "user", "content": prompt}],
    }

    headers = {"Content-Type": "application/json"}
    api_key = (settings.COACH_API_KEY or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not (200 <= resp.status < 300):
                logger.error("openai_compat (%s) respondió status %s", purpose, resp.status)
                return None
            body = resp.read().decode("utf-8", errors="replace")

        parsed = json.loads(body)
        choices = parsed.get("choices") if isinstance(parsed, dict) else None
        if not choices or not isinstance(choices, list):
            logger.warning("openai_compat (%s): respuesta sin 'choices' válido.", purpose)
            return None

        message = (choices[0] or {}).get("message") or {}
        content = message.get("content")
        if not content or not isinstance(content, str):
            logger.warning("openai_compat (%s): 'choices[0].message.content' ausente o vacío.", purpose)
            return None

        return content.strip() or None

    except urllib.error.URLError as exc:
        logger.error("openai_compat (%s) falló (URLError): %s", purpose, exc)
        return None
    except json.JSONDecodeError as exc:
        logger.error("openai_compat (%s): JSON malformado en la respuesta: %s", purpose, exc)
        return None
    except Exception as exc:
        logger.error("Error inesperado invocando openai_compat (%s): %s", purpose, exc)
        return None


# ── API pública ──────────────────────────────────────────────────────────────

_BACKENDS = ("claude_cli", "openai_compat")


def generate(prompt: str, *, timeout: int = 90, purpose: str = "coach") -> Optional[str]:
    """Punto único de despacho hacia el backend de LLM configurado
    (settings.COACH_BACKEND). `purpose` se loguea para distinguir el origen
    de la llamada ("coach", "headline", "report") en los logs — mismo texto
    que antes tenían las 3 copias de subprocess.

    Backend no reconocido -> warning + fallback a claude_cli (criterio del
    roadmap). Nunca lanza — el caller siempre recibe str o None."""
    backend = settings.COACH_BACKEND
    if backend not in _BACKENDS:
        logger.warning(
            "COACH_BACKEND=%r no reconocido; usando claude_cli por defecto.", backend,
        )
        backend = "claude_cli"

    if backend == "openai_compat":
        return _generate_openai_compat(prompt, timeout, purpose)
    return _generate_claude_cli(prompt, timeout, purpose)
