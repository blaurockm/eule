"""Tests fuer load_trading_hours — liest Trading-Hours aus Fuchs-Configs."""

import json
from pathlib import Path

import pytest

from eule.monitoring.precheck import load_trading_hours


@pytest.fixture
def fuchs_dir(tmp_path: Path, monkeypatch) -> Path:
    """Setze EULE_HASE_DIR auf tmp_path und schreibe Fixture-Configs."""
    monkeypatch.setenv("EULE_HASE_DIR", str(tmp_path))
    return tmp_path


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data))


def test_per_environment_trading_hours(fuchs_dir: Path):
    _write(
        fuchs_dir / "fuchs-config.staging.json",
        {
            "supervisor": {
                "trading_hours": {
                    "start": "00:00", "end": "23:59",
                    "timezone": "Europe/Berlin", "weekdays": [0, 1, 2, 3, 4, 5, 6],
                }
            },
            "environments": {
                "staging-ibkr": {
                    "trading_hours": {
                        "start": "09:00", "end": "23:30",
                        "timezone": "Europe/Berlin", "weekdays": [0, 1, 2, 3, 4],
                    }
                }
            },
        },
    )
    schedule = load_trading_hours("staging-ibkr")
    assert schedule == {
        "weekdays": [0, 1, 2, 3, 4],
        "start": "09:00",
        "end": "23:30",
        "tz": "Europe/Berlin",
    }


def test_falls_back_to_supervisor_trading_hours(fuchs_dir: Path):
    _write(
        fuchs_dir / "fuchs-config.staging.json",
        {
            "supervisor": {
                "trading_hours": {
                    "start": "00:00", "end": "23:59",
                    "timezone": "Europe/Berlin", "weekdays": [0, 1, 2, 3, 4, 5, 6],
                }
            },
            "environments": {"staging-hl": {}},  # kein per-env trading_hours
        },
    )
    schedule = load_trading_hours("staging-hl")
    assert schedule == {
        "weekdays": [0, 1, 2, 3, 4, 5, 6],
        "start": "00:00",
        "end": "23:59",
        "tz": "Europe/Berlin",
    }


def test_production_uses_production_config(fuchs_dir: Path):
    _write(
        fuchs_dir / "fuchs-config.production.json",
        {
            "supervisor": {
                "trading_hours": {
                    "start": "13:00", "end": "22:00",
                    "timezone": "Europe/Berlin", "weekdays": [0, 1, 2, 3, 4],
                }
            },
            "environments": {"real-ibkr": {}},
        },
    )
    schedule = load_trading_hours("real-ibkr")
    assert schedule["start"] == "13:00"
    assert schedule["end"] == "22:00"


def test_missing_config_returns_none(fuchs_dir: Path):
    # Keine Config-Files geschrieben — Funktion soll None liefern
    assert load_trading_hours("staging-ibkr") is None


def test_env_without_trading_hours_returns_none(fuchs_dir: Path):
    _write(
        fuchs_dir / "fuchs-config.production.json",
        {"environments": {"real-ibkr": {}}},
    )
    assert load_trading_hours("real-ibkr") is None
