"""Tests fuer eule/config.py."""

from pathlib import Path
from textwrap import dedent

import pytest
import yaml

from eule.config import (
    ConfigError,
    EuleConfig,
    load_config,
    init_config,
    _parse_broker,
    _parse_allocation,
    _parse_alerts,
)


class TestLoadConfig:
    """Config-Datei laden und parsen."""

    def test_load_minimal(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("base_currency: USD\n")
        cfg = load_config(config_file)
        assert cfg.base_currency == "USD"
        assert cfg.brokers == {}

    def test_load_full(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(dedent("""\
            base_currency: EUR
            brokers:
              ibkr-one:
                enabled: true
                type: ibkr
                env_file: "~/.eule/ibkr-one.env"
              tradier:
                enabled: true
                env_file: "~/.eule/tradier.env"
                base_url: "https://api.tradier.com/v1"
              trade_republic:
                enabled: true
                type: manual
                positions_file: "~/.eule/tr-positions.yaml"
            allocation:
              targets:
                core: { min: 0.55, max: 0.70 }
                gold: { min: 0.05, max: 0.15 }
              max_single_position_pct: 0.10
            alerts:
              option_expiry_warning_days: [7, 3]
              fifty_pct_rule: false
              earnings_warning_days: 7
            thesis_file: "~/fin/trading-collab/positions-bh.md"
        """))
        cfg = load_config(config_file)

        assert cfg.base_currency == "EUR"
        assert len(cfg.brokers) == 3

        ibkr = cfg.brokers["ibkr-one"]
        assert ibkr.broker_type == "ibkr"
        assert ibkr.enabled is True
        assert ibkr.env_file == "~/.eule/ibkr-one.env"

        tradier = cfg.brokers["tradier"]
        assert tradier.broker_type == "tradier"
        assert tradier.base_url == "https://api.tradier.com/v1"

        tr = cfg.brokers["trade_republic"]
        assert tr.broker_type == "manual"
        assert tr.positions_file == "~/.eule/tr-positions.yaml"

        assert len(cfg.allocation.targets) == 2
        core_target = next(t for t in cfg.allocation.targets if t.category == "core")
        assert core_target.min_pct == 0.55
        assert core_target.max_pct == 0.70
        assert cfg.allocation.max_single_position_pct == 0.10

        assert cfg.alerts.option_expiry_warning_days == [7, 3]
        assert cfg.alerts.fifty_pct_rule is False
        assert cfg.alerts.earnings_warning_days == 7
        assert cfg.thesis_file == "~/fin/trading-collab/positions-bh.md"

    def test_missing_config_raises(self, tmp_path):
        with pytest.raises(ConfigError, match="Config nicht gefunden"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_config_returns_defaults(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("")
        cfg = load_config(config_file)
        assert cfg.base_currency == "EUR"
        assert cfg.brokers == {}


class TestParseBroker:
    """Broker-Typ-Ableitung und Parsing."""

    def test_explicit_type(self):
        b = _parse_broker("mein-broker", {"type": "ibkr", "env_file": "/tmp/test.env"})
        assert b.broker_type == "ibkr"
        assert b.name == "mein-broker"

    def test_type_from_name_ibkr(self):
        b = _parse_broker("ibkr-one", {"env_file": "/tmp/test.env"})
        assert b.broker_type == "ibkr"

    def test_type_from_name_tradier(self):
        b = _parse_broker("tradier", {"env_file": "/tmp/test.env"})
        assert b.broker_type == "tradier"

    def test_type_from_name_ig(self):
        b = _parse_broker("ig", {"env_file": "/tmp/test.env"})
        assert b.broker_type == "ig"

    def test_type_default_manual(self):
        b = _parse_broker("willbe", {"positions_file": "/tmp/test.yaml"})
        assert b.broker_type == "manual"

    def test_disabled(self):
        b = _parse_broker("ig", {"enabled": False})
        assert b.enabled is False

    def test_extra_fields_preserved(self):
        b = _parse_broker("custom", {"type": "manual", "custom_key": "value"})
        assert b.extra["custom_key"] == "value"


class TestBrokerConfigEnv:
    """Broker .env-Datei laden."""

    def test_load_env_success(self, tmp_path):
        env_file = tmp_path / "test.env"
        env_file.write_text("API_KEY=secret123\nACCOUNT=acc456\n")
        b = _parse_broker("test", {"env_file": str(env_file)})
        env = b.load_env()
        assert env["API_KEY"] == "secret123"
        assert env["ACCOUNT"] == "acc456"

    def test_load_env_missing_raises(self):
        b = _parse_broker("test", {"env_file": "/tmp/nonexistent.env"})
        with pytest.raises(ConfigError, match="env_file nicht gefunden"):
            b.load_env()

    def test_load_env_empty_returns_empty(self):
        b = _parse_broker("test", {})
        assert b.load_env() == {}


class TestParseAllocation:
    """Allokations-Config parsen."""

    def test_with_targets(self):
        alloc = _parse_allocation({
            "targets": {
                "core": {"min": 0.6, "max": 0.7},
                "gold": {"min": 0.05, "max": 0.15},
            },
            "max_single_position_pct": 0.20,
        })
        assert len(alloc.targets) == 2
        assert alloc.max_single_position_pct == 0.20

    def test_empty_defaults(self):
        alloc = _parse_allocation({})
        assert alloc.targets == []
        assert alloc.max_single_position_pct == 0.15


class TestParseAlerts:
    """Alert-Config parsen."""

    def test_custom_values(self):
        alerts = _parse_alerts({
            "option_expiry_warning_days": [5, 2],
            "fifty_pct_rule": False,
            "earnings_warning_days": 10,
        })
        assert alerts.option_expiry_warning_days == [5, 2]
        assert alerts.fifty_pct_rule is False
        assert alerts.earnings_warning_days == 10

    def test_defaults(self):
        alerts = _parse_alerts({})
        assert alerts.option_expiry_warning_days == [7, 3, 1]
        assert alerts.fifty_pct_rule is True
        assert alerts.earnings_warning_days == 14


class TestInitConfig:
    """Config-Templates erstellen."""

    def test_creates_files(self, tmp_path, monkeypatch):
        monkeypatch.setattr("eule.config.EULE_DIR", tmp_path)
        monkeypatch.setattr("eule.config.CONFIG_PATH", tmp_path / "config.yaml")
        created = init_config()
        assert len(created) > 0
        assert (tmp_path / "config.yaml").exists()
        assert (tmp_path / "ibkr-one.env").exists()
        assert (tmp_path / "tradier.env").exists()
        assert (tmp_path / "tr-positions.yaml").exists()

        # Config muss parsebar sein
        cfg = load_config(tmp_path / "config.yaml")
        assert cfg.base_currency == "EUR"

    def test_no_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("eule.config.EULE_DIR", tmp_path)
        monkeypatch.setattr("eule.config.CONFIG_PATH", tmp_path / "config.yaml")
        init_config()
        # Zweiter Aufruf ueberschreibt nichts
        created = init_config()
        assert created == []
