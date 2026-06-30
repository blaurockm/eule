"""Tests fuer check_action_times — statischer Config-Sanity-Check, der warnt,
wenn die action_time einer Strategie nach dem Trading-Hours-Ende liegt
(Action feuert dann nie). Portiert aus Fuchs in Phase 3."""

import json
from pathlib import Path

import pytest

from eule.monitoring.precheck import check_action_times

STAGING = {"tier": "staging", "port": 8776}
PROD = {"tier": "production", "port": 8767}


@pytest.fixture
def hase_dir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("EULE_HASE_DIR", str(tmp_path))
    return tmp_path


def _setup(
    base: Path,
    env_name: str,
    *,
    end: str = "17:00",
    tz: str = "Europe/Berlin",
    strategies: dict[str, dict],
    enabled: bool = True,
    trading_hours: bool = True,
):
    """Schreibe Fuchs-Config + strategies/ fuer ein Environment."""
    config_name = "fuchs-config.staging.json" if env_name.startswith("staging") else "fuchs-config.production.json"
    env_cfg: dict = {"enabled": enabled, "strategy_files": list(strategies)}
    if trading_hours:
        env_cfg["trading_hours"] = {
            "start": "09:00", "end": end, "timezone": tz, "weekdays": [0, 1, 2, 3, 4],
        }
    (base / config_name).write_text(json.dumps({"environments": {env_name: env_cfg}}))
    strat_dir = base / "strategies"
    strat_dir.mkdir(exist_ok=True)
    for fname, cfg in strategies.items():
        (strat_dir / fname).write_text(json.dumps(cfg))


def test_action_after_end_alerts(hase_dir: Path):
    # 18:00 Berlin liegt nach 17:00 Ende -> Anomalie
    _setup(hase_dir, "staging-ibkr", end="17:00",
           strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    res = check_action_times("staging-ibkr", STAGING)
    assert len(res) == 1
    sev, msg = res[0]
    assert sev == "WARNING"
    assert "feuert NIE" in msg
    assert "late.json" in msg


def test_action_before_end_ok(hase_dir: Path):
    _setup(hase_dir, "staging-ibkr", end="23:30",
           strategies={"early.json": {"action_time": "16:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-ibkr", STAGING) == []


def test_tz_conversion_et_to_berlin(hase_dir: Path):
    # 11:00 ET ist ~16-18:00 Berlin (DST-unabhaengig deutlich nach 09:00) -> Anomalie.
    _setup(hase_dir, "staging-ibkr", end="09:00",
           strategies={"et.json": {"action_time": "11:00", "action_time_tz": "US/Eastern"}})
    res = check_action_times("staging-ibkr", STAGING)
    assert len(res) == 1 and "et.json" in res[0][1]


def test_force_action_time_skipped(hase_dir: Path):
    _setup(hase_dir, "staging-ibkr", end="17:00",
           strategies={"force.json": {"action_time": "force"}})
    assert check_action_times("staging-ibkr", STAGING) == []


def test_missing_action_time_skipped(hase_dir: Path):
    _setup(hase_dir, "staging-ibkr", end="17:00",
           strategies={"none.json": {}})
    assert check_action_times("staging-ibkr", STAGING) == []


def test_no_trading_hours_is_noop(hase_dir: Path):
    # 24/7-Environment (kein Tagesende) -> nichts zu pruefen
    _setup(hase_dir, "staging-hl", trading_hours=False,
           strategies={"x.json": {"action_time": "23:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-hl", STAGING) == []


def test_disabled_env_skipped(hase_dir: Path):
    _setup(hase_dir, "staging-ibkr", end="17:00", enabled=False,
           strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-ibkr", STAGING) == []


def test_monitoring_disabled_skipped(hase_dir: Path):
    _setup(hase_dir, "staging-ibkr", end="17:00",
           strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-ibkr", {**STAGING, "monitoring": False}) == []


def test_missing_config_is_noop(hase_dir: Path):
    # Kein Config-File geschrieben (Dev-Rechner) -> []
    assert check_action_times("staging-ibkr", STAGING) == []


def test_prod_severity_is_critical(hase_dir: Path):
    _setup(hase_dir, "real-ibkr", end="17:00",
           strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    res = check_action_times("real-ibkr", PROD)
    assert len(res) == 1 and res[0][0] == "CRITICAL"
