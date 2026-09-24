"""Tests fuer TelegramPoller._poll_once — Backoff nach API-/Transportfehlern.

Hintergrund: ohne Pause nach 502 (Telegram-Wartungsfenster ~03:10 CEST) hat der
Poller 6-8 Requests/s abgesetzt und sich 429 "retry after 5" eingehandelt.
"""

import queue

import requests

from eule.monitoring import telegram_bot
from eule.monitoring.telegram_bot import POLL_ERROR_BACKOFF, TelegramPoller


def _poller_with(monkeypatch, response=None, exc=None):
    sleeps: list[float] = []
    calls: list[dict] = []

    def fake_call(method, **kwargs):
        calls.append({"method": method, **kwargs})
        if exc is not None:
            raise exc
        return response

    monkeypatch.setattr(telegram_bot, "tg_call", fake_call)
    monkeypatch.setattr(telegram_bot.time_module, "sleep", lambda s: sleeps.append(s))
    poller = TelegramPoller(queue.Queue())
    return poller, sleeps, calls


def test_updates_are_queued_and_offset_advances(monkeypatch):
    response = {
        "ok": True,
        "result": [
            {"update_id": 10, "message": {"text": "hi"}},
            {"update_id": 11},  # kein message-Feld (z.B. edited_message)
        ],
    }
    poller, sleeps, calls = _poller_with(monkeypatch, response=response)
    poller._poll_once()
    assert poller.offset == 12
    assert poller.message_queue.get_nowait() == {"text": "hi"}
    assert poller.message_queue.empty()
    assert sleeps == []
    assert calls[0]["method"] == "getUpdates" and calls[0]["timeout"] == telegram_bot.TELEGRAM_POLL_TIMEOUT


def test_502_backs_off_default(monkeypatch):
    poller, sleeps, _ = _poller_with(monkeypatch, response={"ok": False, "error_code": 502, "description": "Bad Gateway"})
    poller._poll_once()
    assert sleeps == [POLL_ERROR_BACKOFF]
    assert poller.offset == 0


def test_429_honours_retry_after(monkeypatch):
    response = {
        "ok": False,
        "error_code": 429,
        "description": "Too Many Requests: retry after 7",
        "parameters": {"retry_after": 7},
    }
    poller, sleeps, _ = _poller_with(monkeypatch, response=response)
    poller._poll_once()
    assert sleeps == [7.0]


def test_read_timeout_continues_without_sleep(monkeypatch):
    poller, sleeps, _ = _poller_with(monkeypatch, exc=requests.exceptions.ReadTimeout("Read timed out"))
    poller._poll_once()
    assert sleeps == []


def test_other_transport_error_backs_off(monkeypatch):
    poller, sleeps, _ = _poller_with(monkeypatch, exc=requests.exceptions.ConnectionError("name resolution"))
    poller._poll_once()
    assert sleeps == [POLL_ERROR_BACKOFF]


def test_tg_request_wraps_tg_call(monkeypatch):
    monkeypatch.setattr(telegram_bot, "tg_call", lambda m, **kw: {"ok": True, "result": {"id": 1}})
    assert telegram_bot.tg_request("getMe") == {"id": 1}
    monkeypatch.setattr(telegram_bot, "tg_call", lambda m, **kw: {"ok": False, "error_code": 502})
    assert telegram_bot.tg_request("getMe") is None
    monkeypatch.setattr(telegram_bot, "tg_call", lambda m, **kw: (_ for _ in ()).throw(requests.exceptions.ReadTimeout("x")))
    assert telegram_bot.tg_request("getMe") is None
