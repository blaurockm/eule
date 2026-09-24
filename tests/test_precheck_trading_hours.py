"""Tests fuer load_trading_hours — Trading-Hours stehen statisch in
precheck.ENVIRONMENTS (bis 2026-07-30 kamen sie aus den fuchs-config.*.json
der Hase-Alt-Checkouts; Fuchs ist mit Docker-Phase 6 aufgeloest)."""

from eule.monitoring.precheck import ENVIRONMENTS, eod_deadline, load_trading_hours

_KEYS = {"weekdays", "start", "end", "tz"}


def test_every_environment_has_trading_hours_in_expected_format():
    for env_name in ENVIRONMENTS:
        schedule = load_trading_hours(env_name)
        assert schedule is not None, env_name
        assert set(schedule) == _KEYS, env_name
        assert isinstance(schedule["weekdays"], list) and schedule["weekdays"]
        assert schedule["tz"] == "Europe/Berlin"


def test_staging_ibkr_hours():
    assert load_trading_hours("staging-ibkr") == {
        "weekdays": [0, 1, 2, 3, 4],
        "start": "09:00",
        "end": "23:30",
        "tz": "Europe/Berlin",
    }


def test_production_hours_are_identical_for_both_accounts():
    real = load_trading_hours("real-ibkr")
    assert real["start"] == "13:00" and real["end"] == "22:00"
    assert real["weekdays"] == [0, 1, 2, 3, 4]
    assert load_trading_hours("real2-ibkr") == real


def test_staging_hl_runs_all_weekdays():
    schedule = load_trading_hours("staging-hl")
    assert schedule["weekdays"] == [0, 1, 2, 3, 4, 5, 6]
    assert schedule["start"] == "00:00" and schedule["end"] == "23:59"


def test_unknown_environment_returns_none():
    assert load_trading_hours("gibt-es-nicht") is None


def test_eod_deadline_derived_from_trading_hours():
    # real-*: 22:00 + 60min Puffer = 23:00
    assert eod_deadline("real-ibkr").isoformat(timespec="minutes") == "23:00"
    # staging-ibkr: 23:30 + 60min waere 00:30 -> Cap 23:44
    assert eod_deadline("staging-ibkr").isoformat(timespec="minutes") == "23:44"
