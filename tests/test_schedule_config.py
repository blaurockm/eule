"""Tests fuer eule/monitoring/schedule_config.py."""

from textwrap import dedent

import pytest

from eule.monitoring.schedule_config import (
    JobConfig,
    ScheduleConfig,
    ScheduleConfigError,
    load_schedule,
    _parse_job,
)


class TestParseJob:
    """Job-Konfiguration aus YAML-Dict parsen."""

    def test_internal_with_cron(self):
        job = _parse_job("daily_summary", {
            "action": "internal",
            "function": "daily_summary",
            "cron": "0 21 * * 0-4",
            "notify": ["telegram", "email"],
        })
        assert job.name == "daily_summary"
        assert job.action == "internal"
        assert job.function == "daily_summary"
        assert job.cron == "0 21 * * 0-4"
        assert job.notify == ("telegram", "email")
        assert job.enabled is True

    def test_internal_with_interval(self):
        job = _parse_job("precheck", {
            "action": "internal",
            "function": "precheck",
            "interval_minutes": 15,
        })
        assert job.interval_minutes == 15
        assert job.cron == ""

    def test_systemd_job(self):
        job = _parse_job("hamster_ibkr", {
            "action": "systemd",
            "unit": "hamster-ibkr.service",
            "cron": "0 23 * * *",
            "timeout_minutes": 150,
            "on_error": ["telegram", "email"],
        })
        assert job.action == "systemd"
        assert job.unit == "hamster-ibkr.service"
        assert job.timeout_minutes == 150

    def test_disabled_job(self):
        job = _parse_job("test", {
            "action": "internal",
            "function": "precheck",
            "interval_minutes": 10,
            "enabled": False,
        })
        assert job.enabled is False

    def test_defaults(self):
        job = _parse_job("test", {
            "action": "internal",
            "function": "precheck",
            "interval_minutes": 15,
        })
        assert job.notify == ()
        assert job.on_error == ("telegram",)
        assert job.timeout_minutes == 60
        assert job.enabled is True

    def test_invalid_action_raises(self):
        with pytest.raises(ScheduleConfigError, match="action muss"):
            _parse_job("bad", {"action": "invalid", "cron": "* * * * *"})

    def test_both_cron_and_interval_raises(self):
        with pytest.raises(ScheduleConfigError, match="genau eins"):
            _parse_job("bad", {
                "action": "internal",
                "function": "precheck",
                "cron": "* * * * *",
                "interval_minutes": 15,
            })

    def test_neither_cron_nor_interval_raises(self):
        with pytest.raises(ScheduleConfigError, match="genau eins"):
            _parse_job("bad", {
                "action": "internal",
                "function": "precheck",
            })

    def test_internal_without_function_raises(self):
        with pytest.raises(ScheduleConfigError, match="braucht 'function'"):
            _parse_job("bad", {
                "action": "internal",
                "interval_minutes": 15,
            })

    def test_systemd_without_unit_raises(self):
        with pytest.raises(ScheduleConfigError, match="braucht 'unit'"):
            _parse_job("bad", {
                "action": "systemd",
                "cron": "0 23 * * *",
            })

    def test_invalid_channel_raises(self):
        with pytest.raises(ScheduleConfigError, match="unbekannter Kanal"):
            _parse_job("bad", {
                "action": "internal",
                "function": "precheck",
                "interval_minutes": 15,
                "notify": ["slack"],
            })


class TestLoadSchedule:
    """Schedule-Datei laden und parsen."""

    def test_load_full(self, tmp_path):
        schedule_file = tmp_path / "schedule.yaml"
        schedule_file.write_text(dedent("""\
            timezone: Europe/Berlin
            jobs:
              precheck:
                action: internal
                function: precheck
                interval_minutes: 15
                notify: [telegram]
              hamster_ibkr:
                action: systemd
                unit: hamster-ibkr.service
                cron: "0 23 * * *"
                timeout_minutes: 150
        """))
        cfg = load_schedule(schedule_file)
        assert cfg.timezone == "Europe/Berlin"
        assert len(cfg.jobs) == 2
        assert "precheck" in cfg.jobs
        assert "hamster_ibkr" in cfg.jobs
        assert cfg.jobs["hamster_ibkr"].timeout_minutes == 150

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(ScheduleConfigError, match="nicht gefunden"):
            load_schedule(tmp_path / "nonexistent.yaml")

    def test_empty_file_returns_defaults(self, tmp_path):
        schedule_file = tmp_path / "schedule.yaml"
        schedule_file.write_text("")
        cfg = load_schedule(schedule_file)
        assert cfg.timezone == "Europe/Berlin"
        assert cfg.jobs == {}

    def test_default_timezone(self, tmp_path):
        schedule_file = tmp_path / "schedule.yaml"
        schedule_file.write_text("jobs: {}\n")
        cfg = load_schedule(schedule_file)
        assert cfg.timezone == "Europe/Berlin"

    def test_template_is_parseable(self, tmp_path):
        """Das SCHEDULE_TEMPLATE muss ohne Fehler ladbar sein."""
        from eule.monitoring.schedule_config import SCHEDULE_TEMPLATE
        schedule_file = tmp_path / "schedule.yaml"
        schedule_file.write_text(SCHEDULE_TEMPLATE)
        cfg = load_schedule(schedule_file)
        assert len(cfg.jobs) == 8
        assert cfg.jobs["precheck"].interval_minutes == 15
        assert cfg.jobs["daily_summary"].cron == "0 21 * * 0-4"
        assert cfg.jobs["hamster_ibkr"].unit == "hamster-ibkr.service"
