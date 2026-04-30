"""Tests fuer character-Anzeige in env_data_block."""

from unittest.mock import patch

from eule.monitoring.precheck import env_data_block


def _strategies(*names):
    return [
        {
            "name": n,
            "is_active_today": True,
            "display": {"fsm_state": "FLAT"},
            "stats": {"realized_pnl": 0.0, "unrealized_pnl": 0.0},
        }
        for n in names
    ]


def _portfolio():
    return {
        "cash": {"current_cash": 100.0, "currency": "EUR"},
        "pnl": {"daily_realized_pnl": 0.0, "daily_unrealized_pnl": 0.0},
        "equity_check": {"internal_equity": 100.0},
    }


def test_character_rendered_when_baseline_has_field():
    baselines = {"some-strat": {"character": "Test-Charakter"}}
    with (
        patch("eule.monitoring.precheck.load_trading_hours", return_value=None),
        patch(
            "eule.monitoring.precheck.api_get",
            side_effect=lambda port, ep: _strategies("some-strat") if ep == "/strategies" else _portfolio(),
        ),
    ):
        out = env_data_block(baselines=baselines)
    assert "Char: Test-Charakter" in out


def test_no_character_line_when_baseline_silent():
    baselines = {}  # keine Charakter-Info
    with (
        patch("eule.monitoring.precheck.load_trading_hours", return_value=None),
        patch(
            "eule.monitoring.precheck.api_get",
            side_effect=lambda port, ep: _strategies("some-strat") if ep == "/strategies" else _portfolio(),
        ),
    ):
        out = env_data_block(baselines=baselines)
    assert "Char:" not in out
