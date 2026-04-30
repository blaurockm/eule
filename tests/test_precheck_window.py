"""Tests fuer is_in_startup_or_shutdown_window in monitoring/precheck.py."""

from datetime import datetime
from zoneinfo import ZoneInfo

from eule.monitoring.precheck import is_in_startup_or_shutdown_window

BERLIN = ZoneInfo("Europe/Berlin")
SCHEDULE = {
    "weekdays": [0, 1, 2, 3, 4],
    "start": "09:00",
    "end": "23:30",
    "tz": "Europe/Berlin",
}


def _at(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=BERLIN)


def test_none_schedule_returns_false():
    assert is_in_startup_or_shutdown_window(None, now=_at(2026, 4, 30, 9, 0, 30)) is False


def test_weekend_returns_false():
    # 2026-05-02 is a Saturday
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 5, 2, 9, 0, 30)) is False


def test_within_grace_after_start_returns_true():
    # 2026-04-30 is a Thursday
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 9, 0, 30)) is True
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 9, 1, 59)) is True


def test_at_exact_start_returns_true():
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 9, 0, 0)) is True


def test_after_grace_window_returns_false():
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 9, 2, 0)) is False
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 12, 0, 0)) is False


def test_within_grace_before_end_returns_true():
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 23, 28, 30)) is True
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 23, 29, 59)) is True


def test_at_exact_end_returns_true():
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 23, 30, 0)) is True


def test_before_shutdown_grace_returns_false():
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 23, 27, 59)) is False


def test_outside_trading_hours_returns_false():
    # Before start grace
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 8, 30, 0)) is False
    # After end grace
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 23, 31, 0)) is False


def test_real_world_failure_case():
    """Reproduziert den False-Positive vom 2026-04-30 09:00:51."""
    # staging-ibkr Prozess startete 09:00:40, Precheck lief 09:00:51.
    # Mit Grace-Window soll das jetzt KEIN Alarm mehr sein.
    assert is_in_startup_or_shutdown_window(SCHEDULE, now=_at(2026, 4, 30, 9, 0, 51)) is True
