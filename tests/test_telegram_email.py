"""Tests fuer telegram_bot.send_email — SMTP-Credentials kommen aus den
Env-Vars (.env), nicht mehr aus der Fuchs-Config. Vertrag: bool-Return,
niemals raise (Scheduler-Jobs werten nur den bool aus)."""

import pytest

from eule.monitoring.telegram_bot import send_email


@pytest.fixture
def smtp_env(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "posteo.de")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "wachtel@example.org")
    monkeypatch.setenv("SMTP_PASS", "secret")
    monkeypatch.setenv("EMAIL_FROM", "wachtel@example.org")
    monkeypatch.setenv("EMAIL_TO", "a@example.org, b@example.org")


def test_returns_false_when_not_configured(monkeypatch):
    for var in ("SMTP_USER", "SMTP_PASS", "EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    assert send_email("Subject", "Body") is False


@pytest.mark.parametrize("missing", ["SMTP_USER", "SMTP_PASS", "EMAIL_TO"])
def test_returns_false_when_single_var_missing(smtp_env, monkeypatch, missing):
    monkeypatch.delenv(missing)
    assert send_email("Subject", "Body") is False


def test_delegates_to_pipeline_email(smtp_env, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "eule.pipeline.email.send_email",
        lambda subject, body, html=False: calls.append((subject, body, html)),
    )
    assert send_email("Subject", "<b>Body</b>", html=True) is True
    assert calls == [("Subject", "<b>Body</b>", True)]


def test_smtp_failure_returns_false_without_raising(smtp_env, monkeypatch):
    def _boom(subject, body, html=False):
        raise OSError("SMTP down")

    monkeypatch.setattr("eule.pipeline.email.send_email", _boom)
    assert send_email("Subject", "Body") is False


def test_multiple_recipients_end_up_in_to_header(smtp_env, monkeypatch):
    """EMAIL_TO darf kommasepariert mehrere Adressen enthalten (frueher
    to_addresses in der Fuchs-Config) — smtplib leitet die Empfaenger aus
    dem To-Header ab."""
    sent = {}

    class _FakeSMTP:
        def __init__(self, host, port):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            sent["user"] = user

        def send_message(self, msg):
            sent["to"] = msg["To"]

    monkeypatch.setattr("smtplib.SMTP", _FakeSMTP)
    assert send_email("Subject", "Body") is True
    assert sent["to"] == "a@example.org, b@example.org"
    assert (sent["host"], sent["port"]) == ("posteo.de", 587)
