"""Tests fuer eule/monitoring/scheduler.py — Scheduler-Logik."""

import json
import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from eule.monitoring.schedule_config import JobConfig, ScheduleConfig
from eule.monitoring.scheduler import Scheduler, _save_state, _load_state


@pytest.fixture
def tz():
    return ZoneInfo("Europe/Berlin")


@pytest.fixture
def alert_callback():
    return MagicMock()


@pytest.fixture
def email_callback():
    return MagicMock()


def _make_job(**kwargs) -> JobConfig:
    defaults = {
        "name": "test",
        "action": "internal",
        "function": "precheck",
    }
    defaults.update(kwargs)
    return JobConfig(**defaults)


class TestShouldFire:
    """Scheduler._should_fire() Logik."""

    def _make_scheduler(self, jobs, alert_cb, email_cb, job_registry=None):
        config = ScheduleConfig(timezone="Europe/Berlin", jobs=jobs)
        return Scheduler(config, alert_cb, email_cb, job_registry=job_registry or {})

    def test_cron_fires_on_match(self, alert_callback, email_callback, tz):
        job = _make_job(cron="0 21 * * *")
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 21, 0, tzinfo=tz)
        assert s._should_fire("test", job, now, time.monotonic())

    def test_cron_no_fire_on_mismatch(self, alert_callback, email_callback, tz):
        job = _make_job(cron="0 21 * * *")
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 20, 59, tzinfo=tz)
        assert not s._should_fire("test", job, now, time.monotonic())

    def test_cron_dedup_same_minute(self, alert_callback, email_callback, tz):
        job = _make_job(cron="0 21 * * *")
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 21, 0, tzinfo=tz)
        # Erster Aufruf feuert
        assert s._should_fire("test", job, now, time.monotonic())
        # Zweiter Aufruf in derselben Minute feuert nicht
        assert not s._should_fire("test", job, now, time.monotonic())

    def test_cron_fires_again_next_minute(self, alert_callback, email_callback, tz):
        job = _make_job(cron="* * * * *")  # jede Minute
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now1 = datetime(2026, 4, 10, 21, 0, tzinfo=tz)
        now2 = datetime(2026, 4, 10, 21, 1, tzinfo=tz)
        assert s._should_fire("test", job, now1, time.monotonic())
        assert s._should_fire("test", job, now2, time.monotonic())

    def test_interval_fires(self, alert_callback, email_callback, tz):
        job = _make_job(interval_minutes=15)
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 14, 0, tzinfo=tz)
        mono = time.monotonic()
        # Erster Aufruf (last=0.0, Abstand gross genug)
        assert s._should_fire("test", job, now, mono)

    def test_interval_dedup(self, alert_callback, email_callback, tz):
        job = _make_job(interval_minutes=15)
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 14, 0, tzinfo=tz)
        mono = time.monotonic()
        s._should_fire("test", job, now, mono)
        # Sofort danach: noch nicht 15 Minuten vergangen
        assert not s._should_fire("test", job, now, mono + 1)

    def test_interval_fires_after_elapsed(self, alert_callback, email_callback, tz):
        job = _make_job(interval_minutes=15)
        s = self._make_scheduler({"test": job}, alert_callback, email_callback)
        now = datetime(2026, 4, 10, 14, 0, tzinfo=tz)
        mono = time.monotonic()
        s._should_fire("test", job, now, mono)
        # Nach 15 Minuten: feuert wieder
        assert s._should_fire("test", job, now, mono + 15 * 60 + 1)


class TestExecuteJob:
    """Scheduler._execute_job() Dispatch."""

    def test_internal_job_calls_function(self, alert_callback, email_callback):
        mock_fn = MagicMock()
        job = _make_job(function="precheck", interval_minutes=15)
        config = ScheduleConfig(jobs={"precheck": job})
        s = Scheduler(config, alert_callback, email_callback, job_registry={"precheck": mock_fn})

        s._execute_job("precheck", job)

        mock_fn.assert_called_once_with(
            alert_callback=alert_callback,
            email_callback=email_callback,
            job_config=job,
        )

    def test_unknown_function_raises(self, alert_callback, email_callback):
        job = _make_job(function="nonexistent", interval_minutes=15)
        config = ScheduleConfig(jobs={"test": job})
        s = Scheduler(config, alert_callback, email_callback, job_registry={})

        # _execute_job faengt den Fehler und benachrichtigt
        s._execute_job("test", job)
        alert_callback.assert_called_once()
        assert "Unbekannte" in alert_callback.call_args[0][0]


class TestStateFile:
    """State-File lesen/schreiben."""

    def test_save_and_load(self, tmp_path, monkeypatch):
        state_path = tmp_path / ".schedule_state.json"
        monkeypatch.setattr("eule.monitoring.scheduler.STATE_PATH", state_path)

        state = {"precheck": {"last_run": "2026-04-10T14:30:00", "last_status": "ok"}}
        _save_state(state)

        loaded = _load_state()
        assert loaded["precheck"]["last_status"] == "ok"

    def test_load_missing_returns_empty(self, tmp_path, monkeypatch):
        state_path = tmp_path / "nonexistent.json"
        monkeypatch.setattr("eule.monitoring.scheduler.STATE_PATH", state_path)
        assert _load_state() == {}

    def test_load_corrupt_returns_empty(self, tmp_path, monkeypatch):
        state_path = tmp_path / "corrupt.json"
        state_path.write_text("not json {{{")
        monkeypatch.setattr("eule.monitoring.scheduler.STATE_PATH", state_path)
        assert _load_state() == {}
