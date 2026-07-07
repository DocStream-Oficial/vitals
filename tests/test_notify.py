"""
test_notify.py — Tests de app/notify.py (Fase 8C, paso C3).

Cubre:
(a) persistencia/atomicidad de notify_state.json (patrón test_cycle.py/test_journal.py).
(b) providers _send_ntfy / _send_telegram con HTTP mockeado (urllib.request.urlopen).
(c) notify_after_sync: no-op sin config, morning brief 1x/día, alertas dedupe,
    best-effort total (nunca lanza aunque los providers exploten).
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app import notify


# ── helpers ──────────────────────────────────────────────────────────────────

def _patch_notify_state_path(monkeypatch, tmp_path):
    monkeypatch.setattr(notify, "_NOTIFY_STATE_FILE", tmp_path / "notify_state.json")


def _make_dataset(dates, recovery=60):
    days = [{"date": d, "recovery": recovery} for d in dates]
    return {"days": days, "summary": {"updated": dates[-1] if dates else None}}


class _FakeResponse:
    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── (a) persistencia / atomicidad ───────────────────────────────────────────

def test_load_returns_empty_structure_when_no_file(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    state = notify.load_notify_state()
    assert state["last_brief_date"] is None
    assert state["sent_alerts"] == []


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    notify.save_notify_state({"last_brief_date": "2026-07-01", "sent_alerts": [{"date": "2026-07-01", "key": "x"}]})
    state = notify.load_notify_state()
    assert state["last_brief_date"] == "2026-07-01"
    assert state["sent_alerts"] == [{"date": "2026-07-01", "key": "x"}]


def test_save_leaves_no_tmp_residue(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    notify.save_notify_state({"last_brief_date": "2026-07-01"})
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_corrupt_json_degrades_to_empty(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    (tmp_path / "notify_state.json").write_text("{not valid json", encoding="utf-8")
    state = notify.load_notify_state()
    assert state["last_brief_date"] is None
    assert state["sent_alerts"] == []


def test_load_non_dict_json_degrades_to_empty(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    (tmp_path / "notify_state.json").write_text("[1, 2, 3]", encoding="utf-8")
    state = notify.load_notify_state()
    assert state["sent_alerts"] == []


# ── (b) providers stdlib (HTTP mockeado) ────────────────────────────────────

def test_send_ntfy_empty_url_returns_false():
    assert notify._send_ntfy("", "t", "b") is False


def test_send_ntfy_success():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        ok = notify._send_ntfy("https://ntfy.sh/mi-topic", "Titulo", "Cuerpo")
    assert ok is True
    assert mock_open.called


def test_send_ntfy_http_error_returns_false():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        ok = notify._send_ntfy("https://ntfy.sh/mi-topic", "Titulo", "Cuerpo")
    assert ok is False


def test_send_ntfy_non_2xx_status_returns_false():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(500)):
        ok = notify._send_ntfy("https://ntfy.sh/mi-topic", "Titulo", "Cuerpo")
    assert ok is False


def test_send_ntfy_unexpected_exception_never_raises():
    with patch("urllib.request.urlopen", side_effect=RuntimeError("kaboom")):
        ok = notify._send_ntfy("https://ntfy.sh/mi-topic", "Titulo", "Cuerpo")
    assert ok is False


def test_send_telegram_missing_creds_returns_false():
    assert notify._send_telegram("", "chat", "hola") is False
    assert notify._send_telegram("token", "", "hola") is False


def test_send_telegram_success():
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        ok = notify._send_telegram("123:ABC", "999", "hola mundo")
    assert ok is True
    assert mock_open.called


def test_send_telegram_failure_returns_false():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("boom")):
        ok = notify._send_telegram("123:ABC", "999", "hola mundo")
    assert ok is False


def test_send_telegram_truncates_long_text():
    captured = {}

    def _fake_urlopen(req, timeout=10):
        captured["data"] = req.data
        return _FakeResponse(200)

    with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
        notify._send_telegram("123:ABC", "999", "x" * 10000)
    # el payload no debe reventar por tamaño; el texto se recorta a 4000 chars
    assert len(captured["data"]) < 10000 + 200


# ── (c) notify_after_sync ────────────────────────────────────────────────────

def test_notify_after_sync_noop_without_config(tmp_path, monkeypatch):
    """Sin `notifications` configurado -> no-op silencioso, CERO requests HTTP."""
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync(dataset, [], {}, locale="es")
    assert not mock_open.called
    # tampoco debe haber escrito notify_state.json (nada que persistir)
    assert not (tmp_path / "notify_state.json").exists()


def test_notify_after_sync_noop_with_empty_channels(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "", "telegram_bot_token": "", "telegram_chat_id": ""}}
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync(dataset, [], profile, locale="es")
    assert not mock_open.called


def test_notify_after_sync_sends_morning_brief_once_per_day(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": True, "alerts": False}}

    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        notify.notify_after_sync(dataset, [], profile, locale="es")
    assert mock_open.call_count == 1  # 1 sola llamada (el brief)

    state = notify.load_notify_state()
    assert state["last_brief_date"] == "2026-07-01"

    # Segunda llamada MISMO día -> dedupe, cero nuevas requests.
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open2:
        notify.notify_after_sync(dataset, [], profile, locale="es")
    assert mock_open2.call_count == 0


def test_notify_after_sync_sends_brief_again_next_day(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": True, "alerts": False}}

    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        notify.notify_after_sync(_make_dataset(["2026-07-01"]), [], profile, locale="es")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        notify.notify_after_sync(_make_dataset(["2026-07-02"]), [], profile, locale="es")
    assert mock_open.call_count == 1

    state = notify.load_notify_state()
    assert state["last_brief_date"] == "2026-07-02"


def test_notify_after_sync_morning_brief_disabled_skips(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": False}}
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync(dataset, [], profile, locale="es")
    assert not mock_open.called


def test_notify_after_sync_sends_alert_insight(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": True}}
    insights = [
        {"id": "illness_early_warning", "severity": "alert", "title": "Posible enfermedad",
         "summary": "HRV y RHR fuera de rango.", "recommendation": "Descansa hoy."},
        {"id": "positive_hrv", "severity": "positive", "title": "HRV mejora", "summary": "..."},
    ]
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        notify.notify_after_sync(dataset, insights, profile, locale="es")
    assert mock_open.call_count == 1  # solo la alerta severity=='alert', no la positive

    state = notify.load_notify_state()
    assert len(state["sent_alerts"]) == 1
    assert state["sent_alerts"][0]["date"] == "2026-07-01"


def test_notify_after_sync_dedupes_same_alert_same_day(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": True}}
    insights = [{"id": "illness_early_warning", "severity": "alert", "title": "X", "summary": "Y"}]

    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        notify.notify_after_sync(dataset, insights, profile, locale="es")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open2:
        notify.notify_after_sync(dataset, insights, profile, locale="es")
    assert mock_open2.call_count == 0  # ya se envió hoy, dedupe


def test_notify_after_sync_same_alert_next_day_resends(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": True}}
    insights = [{"id": "illness_early_warning", "severity": "alert", "title": "X", "summary": "Y"}]

    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)):
        notify.notify_after_sync(_make_dataset(["2026-07-01"]), insights, profile, locale="es")
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        notify.notify_after_sync(_make_dataset(["2026-07-02"]), insights, profile, locale="es")
    assert mock_open.call_count == 1  # día nuevo -> vuelve a enviar


def test_notify_after_sync_alerts_disabled_skips(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": False}}
    insights = [{"id": "illness_early_warning", "severity": "alert", "title": "X", "summary": "Y"}]
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync(dataset, insights, profile, locale="es")
    assert not mock_open.called


def test_notify_after_sync_ignores_non_alert_severities(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": False, "alerts": True}}
    insights = [
        {"id": "a", "severity": "watch", "title": "X", "summary": "Y"},
        {"id": "b", "severity": "info", "title": "X", "summary": "Y"},
        {"id": "c", "severity": "positive", "title": "X", "summary": "Y"},
    ]
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync(dataset, insights, profile, locale="es")
    assert not mock_open.called


def test_notify_after_sync_no_days_is_noop(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t"}}
    with patch("urllib.request.urlopen") as mock_open:
        notify.notify_after_sync({"days": []}, [], profile, locale="es")
    assert not mock_open.called


def test_notify_after_sync_never_raises_when_provider_explodes(tmp_path, monkeypatch):
    """Best-effort total: aunque el provider HTTP reviente con una excepción
    rara, notify_after_sync() nunca debe propagar (contrato run_sync)."""
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {"ntfy_url": "https://ntfy.sh/t", "morning_brief": True, "alerts": True}}
    with patch("urllib.request.urlopen", side_effect=RuntimeError("network card on fire")):
        notify.notify_after_sync(dataset, [], profile, locale="es")  # no debe lanzar


def test_notify_after_sync_malformed_profile_never_raises(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    # notifications no es un dict -> debe degradar a no-op, nunca lanzar.
    notify.notify_after_sync(dataset, [], {"notifications": "garbage"}, locale="es")
    notify.notify_after_sync(dataset, [], None, locale="es")
    notify.notify_after_sync(None, [], {"notifications": {"ntfy_url": "x"}}, locale="es")


def test_notify_after_sync_both_channels_configured_sends_both(tmp_path, monkeypatch):
    _patch_notify_state_path(monkeypatch, tmp_path)
    dataset = _make_dataset(["2026-07-01"])
    profile = {"notifications": {
        "ntfy_url": "https://ntfy.sh/t",
        "telegram_bot_token": "123:ABC", "telegram_chat_id": "999",
        "morning_brief": True, "alerts": False,
    }}
    with patch("urllib.request.urlopen", return_value=_FakeResponse(200)) as mock_open:
        notify.notify_after_sync(dataset, [], profile, locale="es")
    # 1 morning brief -> 2 requests (ntfy + telegram)
    assert mock_open.call_count == 2
