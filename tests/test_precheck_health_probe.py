"""Tests fuer probe_health in monitoring/precheck.py.

Hintergrund: Vorfall 2026-07-30. Waehrend des 0DTE-Entries um 11:00 ET war die
staging-ibkr-Runtime mit Order-Ausfuehrung beschaeftigt und antwortete nicht
innerhalb von 5s. Der Precheck meldete daraufhin "API unreachable" — obwohl die
Runtime lief. Ein zaeher Response darf keinen Alarm ausloesen, ein toter
Prozess dagegen sofort.
"""

import pytest
import requests

from eule.monitoring import precheck
from eule.monitoring.precheck import probe_health


class FakeResponse:
    def __init__(self, status_code, payload=None, raises=False):
        self.status_code = status_code
        self._payload = payload
        self._raises = raises

    def json(self):
        if self._raises:
            raise ValueError("no json")
        return self._payload


def _responder(monkeypatch, outcomes):
    """Laesst requests.get nacheinander die outcomes liefern.

    Ein Eintrag ist entweder eine Exception-Instanz (wird geworfen) oder eine
    FakeResponse. Zaehlt zusaetzlich die Aufrufe mit.
    """
    calls = []

    def fake_get(url, timeout=None):
        calls.append((url, timeout))
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(precheck.requests, "get", fake_get)
    return calls


def test_no_listener_is_dead_after_one_attempt(monkeypatch):
    """Abgelehnte TCP-Verbindung = Prozess weg. Kein zweiter Versuch noetig."""
    calls = _responder(monkeypatch, [requests.exceptions.ConnectionError("refused")])
    assert probe_health(8776) == ("dead", None)
    assert len(calls) == 1


def test_connect_timeout_is_dead(monkeypatch):
    """ConnectTimeout ist Subklasse von ConnectionError — auch das ist tot."""
    _responder(monkeypatch, [requests.exceptions.ConnectTimeout("no route")])
    assert probe_health(8776) == ("dead", None)


def test_slow_first_then_answer_is_ok(monkeypatch):
    """Der Vorfall-Fall: erster Versuch laeuft ins Read-Timeout, zweiter
    antwortet. Das ist Last, kein Ausfall — also kein Alarm."""
    payload = {"status": "healthy", "runtime_health": {"health": "OK"}}
    calls = _responder(
        monkeypatch,
        [requests.exceptions.ReadTimeout("slow"), FakeResponse(200, payload)],
    )
    assert probe_health(8776) == ("ok", payload)
    assert len(calls) == 2


def test_two_read_timeouts_is_hanging(monkeypatch):
    """Verbindung steht, aber es kommt nie eine Antwort — im selben Lauf Alarm,
    nicht erst im naechsten Zyklus."""
    calls = _responder(
        monkeypatch,
        [requests.exceptions.ReadTimeout("slow"), requests.exceptions.ReadTimeout("slow")],
    )
    assert probe_health(8776) == ("hanging", None)
    assert len(calls) == 2


def test_503_is_unhealthy_not_unreachable(monkeypatch):
    """Hase liefert bei runtime_health ERROR bewusst 503. Die Runtime laeuft
    dann — das darf nicht als Erreichbarkeitsproblem gemeldet werden."""
    payload = {
        "status": "unhealthy",
        "runtime_health": {"health": "ERROR", "problems": ["broker disconnected"]},
    }
    _responder(monkeypatch, [FakeResponse(503, payload)])
    state, got = probe_health(8776)
    assert state == "unhealthy"
    assert got["runtime_health"]["problems"] == ["broker disconnected"]


def test_other_http_error_carries_status_code(monkeypatch):
    _responder(monkeypatch, [FakeResponse(500, None)])
    assert probe_health(8776) == ("error", {"status_code": 500})


def test_unparsable_body_is_error(monkeypatch):
    _responder(monkeypatch, [FakeResponse(200, raises=True)])
    assert probe_health(8776) == ("error", None)


def test_healthy_response_returns_payload(monkeypatch):
    payload = {"status": "healthy", "runtime_health": {"health": "OK"}}
    _responder(monkeypatch, [FakeResponse(200, payload)])
    assert probe_health(8776) == ("ok", payload)


def test_uses_split_connect_and_read_timeout(monkeypatch):
    """Connect kurz, Read lang — sonst greift die Unterscheidung nicht."""
    calls = _responder(monkeypatch, [FakeResponse(200, {})])
    probe_health(8776)
    _url, timeout = calls[0]
    assert timeout == (precheck.API_CONNECT_TIMEOUT, precheck.API_READ_TIMEOUT)
    assert precheck.API_CONNECT_TIMEOUT < precheck.API_READ_TIMEOUT


# --- Verdrahtung in check_environment ---------------------------------------

ENV_CONFIG = {"port": 8776, "tier": "staging"}


@pytest.fixture
def in_trading_time(monkeypatch):
    monkeypatch.setattr(precheck, "is_trading_time", lambda schedule: True)
    monkeypatch.setattr(precheck, "is_in_startup_or_shutdown_window", lambda schedule: False)


def _anomalies_for(monkeypatch, state, payload=None):
    monkeypatch.setattr(precheck, "probe_health", lambda port: (state, payload))
    return precheck.check_environment("staging-ibkr", ENV_CONFIG, {})


def test_dead_reports_unreachable(monkeypatch, in_trading_time):
    anomalies = _anomalies_for(monkeypatch, "dead")
    assert anomalies == [("WARNING", "[staging-ibkr] API unreachable")]


def test_hanging_reports_distinct_message(monkeypatch, in_trading_time):
    anomalies = _anomalies_for(monkeypatch, "hanging")
    assert len(anomalies) == 1
    sev, msg = anomalies[0]
    assert sev == "WARNING"
    assert "haengt" in msg
    assert "unreachable" not in msg


def test_unhealthy_does_not_say_unreachable(monkeypatch, in_trading_time):
    """503 muss als Runtime-Fehler durchgehen, nicht als Netzwerkproblem."""
    payload = {"runtime_health": {"health": "ERROR", "problems": ["broker disconnected"]}}
    monkeypatch.setattr(precheck, "api_get", lambda port, endpoint: None)
    anomalies = _anomalies_for(monkeypatch, "unhealthy", payload)
    assert any("Runtime meldet ERROR" in msg for _sev, msg in anomalies)
    assert not any("unreachable" in msg for _sev, msg in anomalies)
