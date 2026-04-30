"""Tests fuer _is_0dte_strategy und _strategy_status_note."""

from datetime import datetime
from zoneinfo import ZoneInfo

from eule.monitoring.precheck import _is_0dte_strategy, _strategy_status_note

BERLIN = ZoneInfo("Europe/Berlin")


def _at(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=BERLIN)


# --- _is_0dte_strategy ---

def test_0dte_in_name_detected():
    assert _is_0dte_strategy("spx-0dte-mon-put") is True
    assert _is_0dte_strategy("ndx-0dte-tue-put") is True
    assert _is_0dte_strategy("spx-0dte-always") is True
    assert _is_0dte_strategy("iwm-0dte-fri-put") is True


def test_non_0dte_strategies():
    assert _is_0dte_strategy("carver-scalping") is False
    assert _is_0dte_strategy("mcl-rsi-opencompare") is False
    assert _is_0dte_strategy("gld-1dte-tue-put") is False  # 1dte != 0dte
    assert _is_0dte_strategy("crypto-trendconv-v7d") is False


# --- _strategy_status_note ---

def _strat(name="x", fsm="FLAT", is_active_today=True):
    return {
        "name": name,
        "is_active_today": is_active_today,
        "display": {"fsm_state": fsm},
    }


def test_inactive_today_returns_note():
    note = _strategy_status_note(_strat(is_active_today=False), _at(2026, 4, 30, 10, 0))
    assert note == "nicht aktiv heute"


def test_active_non_0dte_no_note():
    note = _strategy_status_note(_strat(name="carver-scalping", fsm="FLAT"), _at(2026, 4, 30, 23, 0))
    assert note == ""


def test_0dte_active_pre_16_et_no_note():
    # 2026-04-30 14:00 Berlin = 08:00 ET — well before 16:00 ET
    note = _strategy_status_note(
        _strat(name="spx-0dte-always", fsm="IN_POSITION"),
        _at(2026, 4, 30, 14, 0),
    )
    assert note == ""


def test_0dte_active_post_16_et_flat_no_note():
    # 2026-04-30 23:00 Berlin = 17:00 ET — after 16:00 ET
    note = _strategy_status_note(
        _strat(name="spx-0dte-always", fsm="FLAT"),
        _at(2026, 4, 30, 23, 0),
    )
    assert note == ""


def test_0dte_active_post_16_et_not_flat_warns():
    # 23:00 Berlin = 17:00 ET, IN_POSITION → must warn
    note = _strategy_status_note(
        _strat(name="spx-0dte-always", fsm="IN_POSITION"),
        _at(2026, 4, 30, 23, 0),
    )
    assert "0DTE post-16:00 ET" in note
    assert "FSM=IN_POSITION" in note


def test_0dte_inactive_today_takes_priority():
    # Even post-16:00, if not active today, just say so
    note = _strategy_status_note(
        _strat(name="spx-0dte-mon-put", fsm="IN_POSITION", is_active_today=False),
        _at(2026, 4, 30, 23, 0),
    )
    assert note == "nicht aktiv heute"
