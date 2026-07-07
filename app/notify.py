"""
notify.py — Notificaciones push (ntfy / Telegram), Fase 8C paso C3.

Providers stdlib: _send_ntfy() / _send_telegram() — urllib.request, timeout 10s,
BEST-EFFORT TOTAL (nunca lanzan; loguean y devuelven False en error). Cero
dependencias nuevas (nada de requests para esto — ya se usa en otras partes del
repo, pero notify.py es intencionalmente 100% stdlib para mantenerlo aislado y
trivial de auditar).

notify_after_sync(dataset, insights, profile, locale): SE LLAMA SOLO DESDE
sync.py::run_sync(), envuelta en su propio try/except (patrón EXACTO de
coach_headline/report: un fallo aquí NUNCA debe tumbar ni ralentizar run_sync()
perceptiblemente). Envía:
  (a) morning brief 1×/día (reusa mcp_tools.morning_brief — mismo texto que ya
      usa el cron del agente MCP, cero duplicación de lógica).
  (b) insights severity=='alert' nuevos (no enviados antes).

Dedupe: data/notify_state.json {last_brief_date, sent_alerts: [{date, factor_or_id}]}
— persistencia atómica (patrón cycle.py). Sin `notifications` configurado (ni
ntfy_url ni telegram) -> no-op silencioso, cero requests HTTP.
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vitals.notify")

from app.config import settings as _settings

_DATA_DIR: Path = _settings.DATA_DIR

_NOTIFY_STATE_FILE = _DATA_DIR / "notify_state.json"

_HTTP_TIMEOUT = 10  # segundos, best-effort


def _notify_state_path() -> Path:
    """Ruta a notify_state.json del usuario activo (Fase 8D, paso D3:
    household). Fuera de un request household-aware (is_context_active()=
    False — tests preexistentes que monkeypatchean _NOTIFY_STATE_FILE
    directamente, scripts), usa _NOTIFY_STATE_FILE tal cual: comportamiento
    idéntico a antes. Nunca lanza."""
    try:
        from app import userctx as _userctx
        if _userctx.should_use_household_paths():
            return _userctx.current_data_dir() / "notify_state.json"
    except Exception:
        pass
    return _NOTIFY_STATE_FILE


# ── Persistencia atómica (patrón cycle.py — nunca lanza) ────────────────────

def _empty_state() -> dict:
    return {"last_brief_date": None, "sent_alerts": [], "updated": None}


def load_notify_state() -> dict:
    """Lee data/notify_state.json. Si no existe o está corrupto -> estructura
    vacía (nunca lanza)."""
    empty = _empty_state()
    try:
        path = _notify_state_path()
        if not path.exists():
            return empty
        text = path.read_text(encoding="utf-8")
        if not text.strip():
            return empty
        data = json.loads(text)
        if not isinstance(data, dict):
            logger.warning("notify_state.json no es dict; usando estructura vacía.")
            return empty
        data.setdefault("last_brief_date", None)
        data.setdefault("sent_alerts", [])
        data.setdefault("updated", None)
        if not isinstance(data.get("sent_alerts"), list):
            data["sent_alerts"] = []
        return data
    except json.JSONDecodeError as exc:
        logger.warning("notify_state.json JSON inválido (%s); usando estructura vacía.", exc)
        return empty
    except Exception as exc:
        logger.warning("Error leyendo notify_state.json: %s", exc)
        return empty


def save_notify_state(d: dict) -> None:
    """Guarda notify_state.json con escritura ATÓMICA. Nunca lanza (loguea)."""
    try:
        from app.fsutil import atomic_write_text
        path = _notify_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        d = dict(d or {})
        d.setdefault("last_brief_date", None)
        d.setdefault("sent_alerts", [])
        d["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
        atomic_write_text(path, json.dumps(d, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.error("Error guardando notify_state.json: %s", exc)


# ── Poda de sent_alerts (evita crecimiento sin límite) ──────────────────────

_ALERT_HISTORY_DAYS = 30


def _prune_sent_alerts(sent_alerts: list, today: datetime.date) -> list:
    """Conserva solo alertas de los últimos _ALERT_HISTORY_DAYS días (dedupe
    solo necesita mirar 'hoy', pero conservamos una ventana corta para
    diagnóstico). Entradas sin fecha parseable se descartan."""
    out = []
    cutoff = today - datetime.timedelta(days=_ALERT_HISTORY_DAYS)
    for a in sent_alerts or []:
        if not isinstance(a, dict):
            continue
        try:
            d = datetime.date.fromisoformat(a.get("date", ""))
        except Exception:
            continue
        if d >= cutoff:
            out.append(a)
    return out


# ── Providers stdlib (urllib.request) — best-effort total ───────────────────

def _send_ntfy(topic_url: str, title: str, body: str, priority: str = "default") -> bool:
    """POST a un topic de ntfy (self-hosted o ntfy.sh). `topic_url` es la URL
    COMPLETA del topic (ej. https://ntfy.sh/mi-topic-secreto o
    https://mi-servidor.tld/topic). Nunca lanza — devuelve False en error."""
    if not topic_url:
        return False
    try:
        req = urllib.request.Request(
            topic_url.strip(),
            data=body.encode("utf-8"),
            method="POST",
            headers={
                "Title": title.encode("utf-8").decode("latin-1", errors="replace"),
                "Priority": priority,
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                logger.warning("ntfy respondió status %s", resp.status)
            return ok
    except urllib.error.URLError as exc:
        logger.warning("ntfy falló (URLError, best-effort): %s", exc)
        return False
    except Exception as exc:
        logger.warning("ntfy falló (best-effort): %s", exc)
        return False


def _send_telegram(bot_token: str, chat_id: str, text: str) -> bool:
    """POST a la Bot API de Telegram (sendMessage). Nunca lanza — devuelve
    False en error. Trunca el texto defensivamente (límite de Telegram
    ~4096 chars) para no fallar por un mensaje demasiado largo."""
    if not bot_token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text[:4000]}
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            ok = 200 <= resp.status < 300
            if not ok:
                logger.warning("Telegram respondió status %s", resp.status)
            return ok
    except urllib.error.URLError as exc:
        logger.warning("Telegram falló (URLError, best-effort): %s", exc)
        return False
    except Exception as exc:
        logger.warning("Telegram falló (best-effort): %s", exc)
        return False


def _dispatch(cfg: dict, title: str, body: str, priority: str = "default") -> bool:
    """Envía por TODOS los canales configurados (ntfy y/o Telegram) — no son
    mutuamente excluyentes. Devuelve True si AL MENOS uno tuvo éxito (o si no
    hay ningún canal configurado, devuelve False sin intentar nada)."""
    cfg = cfg or {}
    sent_any = False
    ntfy_url = (cfg.get("ntfy_url") or "").strip()
    if ntfy_url:
        if _send_ntfy(ntfy_url, title, body, priority):
            sent_any = True

    bot_token = (cfg.get("telegram_bot_token") or "").strip()
    chat_id = (cfg.get("telegram_chat_id") or "").strip()
    if bot_token and chat_id:
        text = f"{title}\n\n{body}" if title else body
        if _send_telegram(bot_token, chat_id, text):
            sent_any = True

    return sent_any


# ── Mapping de insight -> mensaje de alerta ──────────────────────────────────

def _alert_identity(insight: dict) -> str:
    """Identidad estable de un insight para dedupe (id si existe, si no
    category+title)."""
    iid = insight.get("id")
    if iid:
        return str(iid)
    return f"{insight.get('category', '')}:{insight.get('title', '')}"


def _format_alert_message(insight: dict, locale: str = "es") -> tuple[str, str]:
    """(title, body) del mensaje push para un insight de alerta."""
    from app.i18n import tr
    title = tr("notify_alert_title", locale, title=insight.get("title", ""))
    body_lines = [insight.get("summary", "")]
    rec = insight.get("recommendation")
    if rec:
        body_lines.append(rec)
    return title, "\n".join(l for l in body_lines if l)


# ── API pública ──────────────────────────────────────────────────────────────

def notify_after_sync(dataset: dict, insights: Optional[list], profile: Optional[dict],
                       locale: str = "es") -> None:
    """Envía morning brief (1×/día) + alertas de insights severity=='alert'
    nuevas (dedupe por fecha+identidad). Best-effort TOTAL: cualquier
    excepción se traga y se loguea, NUNCA propaga — se llama desde
    sync.py::run_sync() y no debe tumbarlo. Sin `notifications` configurado
    (ni ntfy_url ni telegram) -> no-op silencioso, CERO requests HTTP."""
    try:
        profile = profile or {}
        cfg = profile.get("notifications") or {}
        if not isinstance(cfg, dict):
            return

        has_channel = bool((cfg.get("ntfy_url") or "").strip()) or (
            bool((cfg.get("telegram_bot_token") or "").strip())
            and bool((cfg.get("telegram_chat_id") or "").strip())
        )
        if not has_channel:
            return  # no-op silencioso: ningún canal configurado

        dataset = dataset or {}
        days = dataset.get("days") or []
        if not days:
            return

        today_str = days[-1].get("date") or datetime.date.today().isoformat()
        try:
            today = datetime.date.fromisoformat(today_str)
        except Exception:
            today = datetime.date.today()

        state = load_notify_state()
        state_changed = False

        # ── (a) morning brief 1×/día ──
        if cfg.get("morning_brief", True):
            if state.get("last_brief_date") != today_str:
                try:
                    from app import mcp_tools as _mcp_tools
                    brief_text = _mcp_tools.morning_brief(dataset)
                except Exception as exc:
                    logger.warning("morning_brief falló al construir el texto: %s", exc)
                    brief_text = None
                if brief_text:
                    from app.i18n import tr
                    title = tr("notify_brief_title", locale)
                    if _dispatch(cfg, title, brief_text, priority="default"):
                        state["last_brief_date"] = today_str
                        state_changed = True

        # ── (b) insights severity=='alert' nuevos ──
        if cfg.get("alerts", True):
            insights = insights or []
            sent_alerts = _prune_sent_alerts(state.get("sent_alerts") or [], today)
            already_sent = {(a.get("date"), a.get("key")) for a in sent_alerts}

            for ins in insights:
                if not isinstance(ins, dict) or ins.get("severity") != "alert":
                    continue
                key = _alert_identity(ins)
                dedupe_key = (today_str, key)
                if dedupe_key in already_sent:
                    continue
                title, body = _format_alert_message(ins, locale)
                if not body:
                    continue
                if _dispatch(cfg, title, body, priority="high"):
                    sent_alerts.append({"date": today_str, "key": key})
                    already_sent.add(dedupe_key)
                    state_changed = True

            state["sent_alerts"] = sent_alerts

        if state_changed:
            save_notify_state(state)
    except Exception as exc:
        # Red de seguridad final: bajo NINGUNA circunstancia esto debe tumbar run_sync().
        logger.warning("notify_after_sync falló por completo (best-effort, ignorado): %s", exc)
