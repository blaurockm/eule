"""Tests fuer check_action_times — statischer Config-Sanity-Check, der warnt,
wenn die action_time einer Strategie nach dem Trading-Hours-Ende liegt
(Action feuert dann nie). Portiert aus Fuchs in Phase 3; die Strategie-Liste
steht seit 2026-07-30 in precheck.ENVIRONMENTS, die JSONs kommen aus
strategies_dir() (Override: EULE_STRATEGIES_DIR)."""

import json
from pathlib import Path

import pytest

from eule.monitoring import precheck as pc
from eule.monitoring.precheck import ENVIRONMENTS, check_action_times

STAGING = {"tier": "staging", "port": 8776}
PROD = {"tier": "production", "port": 8767}


@pytest.fixture
def strat_dir(tmp_path: Path, monkeypatch) -> Path:
    """Leeres Strategie-Verzeichnis + EULE_STRATEGIES_DIR darauf."""
    d = tmp_path / "strategies"
    d.mkdir()
    monkeypatch.setenv("EULE_STRATEGIES_DIR", str(d))
    return d


def _setup(
    strat_dir: Path,
    monkeypatch,
    *,
    end: str = "17:00",
    tz: str = "Europe/Berlin",
    strategies: dict[str, dict],
    trading_hours: bool = True,
) -> dict:
    """Schreibe Strategy-JSONs, setze Trading-Hours, liefere das env_config."""
    schedule = (
        {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": end, "tz": tz}
        if trading_hours
        else None
    )
    monkeypatch.setattr(pc, "load_trading_hours", lambda env_name: schedule)
    for fname, cfg in strategies.items():
        (strat_dir / fname).write_text(json.dumps(cfg))
    return {"strategy_files": list(strategies)}


def test_action_after_end_alerts(strat_dir: Path, monkeypatch):
    # 18:00 Berlin liegt nach 17:00 Ende -> Anomalie
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00",
                     strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    res = check_action_times("staging-ibkr", {**STAGING, **env_cfg})
    assert len(res) == 1
    sev, msg = res[0]
    assert sev == "WARNING"
    assert "feuert NIE" in msg
    assert "late.json" in msg


def test_action_before_end_ok(strat_dir: Path, monkeypatch):
    env_cfg = _setup(strat_dir, monkeypatch, end="23:30",
                     strategies={"early.json": {"action_time": "16:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg}) == []


def test_tz_conversion_et_to_berlin(strat_dir: Path, monkeypatch):
    # 11:00 ET ist ~16-18:00 Berlin (DST-unabhaengig deutlich nach 09:00) -> Anomalie.
    env_cfg = _setup(strat_dir, monkeypatch, end="09:00",
                     strategies={"et.json": {"action_time": "11:00", "action_time_tz": "US/Eastern"}})
    res = check_action_times("staging-ibkr", {**STAGING, **env_cfg})
    assert len(res) == 1 and "et.json" in res[0][1]


def test_force_action_time_skipped(strat_dir: Path, monkeypatch):
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00",
                     strategies={"force.json": {"action_time": "force"}})
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg}) == []


def test_missing_action_time_skipped(strat_dir: Path, monkeypatch):
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00", strategies={"none.json": {}})
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg}) == []


def test_no_trading_hours_is_noop(strat_dir: Path, monkeypatch):
    # 24/7-Environment (kein Tagesende) -> nichts zu pruefen
    env_cfg = _setup(strat_dir, monkeypatch, trading_hours=False,
                     strategies={"x.json": {"action_time": "23:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-hl", {**STAGING, **env_cfg}) == []


def test_monitoring_disabled_skipped(strat_dir: Path, monkeypatch):
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00",
                     strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg, "monitoring": False}) == []


def test_missing_strategies_dir_is_noop(tmp_path: Path, monkeypatch):
    # Dev-Rechner ohne Hase-Checkout -> [] statt Fehler
    monkeypatch.setenv("EULE_STRATEGIES_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(pc, "load_trading_hours", lambda env_name: {
        "weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "17:00", "tz": "Europe/Berlin",
    })
    env_cfg = {"strategy_files": ["late.json"]}
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg}) == []


def test_missing_strategy_file_skipped(strat_dir: Path, monkeypatch):
    # Datei in strategy_files, aber (noch) nicht im Checkout -> still ueberspringen
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00", strategies={})
    env_cfg["strategy_files"] = ["nicht-da.json"]
    assert check_action_times("staging-ibkr", {**STAGING, **env_cfg}) == []


def test_prod_severity_is_critical(strat_dir: Path, monkeypatch):
    env_cfg = _setup(strat_dir, monkeypatch, end="17:00",
                     strategies={"late.json": {"action_time": "18:00", "action_time_tz": "Europe/Berlin"}})
    res = check_action_times("real-ibkr", {**PROD, **env_cfg})
    assert len(res) == 1 and res[0][0] == "CRITICAL"


def test_every_environment_declares_strategy_files():
    for env_name, env_config in ENVIRONMENTS.items():
        files = env_config.get("strategy_files")
        assert files, env_name
        assert all(f.endswith(".json") for f in files), env_name
