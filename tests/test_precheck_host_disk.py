"""Tests fuer check_host_disk — env-agnostischer Host-Disk-Watchdog
(WARN 85 % / CRIT 95 %). Portiert aus Fuchs in Phase 3."""

import types

import eule.monitoring.precheck as pc
from eule.monitoring.precheck import check_host_disk


def _usage(total: int, used: int, free: int):
    return types.SimpleNamespace(total=total, used=used, free=free)


def _patch(monkeypatch, total, used, free):
    monkeypatch.setattr(pc.shutil, "disk_usage", lambda _path: _usage(total, used, free))


def test_critical_at_95_percent(monkeypatch):
    _patch(monkeypatch, total=100 * 1024**3, used=96 * 1024**3, free=4 * 1024**3)
    res = check_host_disk()
    assert len(res) == 1
    sev, msg = res[0]
    assert sev == "CRITICAL"
    assert "96%" in msg


def test_warning_between_85_and_95(monkeypatch):
    _patch(monkeypatch, total=100 * 1024**3, used=90 * 1024**3, free=10 * 1024**3)
    res = check_host_disk()
    assert len(res) == 1
    assert res[0][0] == "WARNING"


def test_ok_below_85(monkeypatch):
    _patch(monkeypatch, total=100 * 1024**3, used=50 * 1024**3, free=50 * 1024**3)
    assert check_host_disk() == []


def test_exactly_85_warns(monkeypatch):
    _patch(monkeypatch, total=100 * 1024**3, used=85 * 1024**3, free=15 * 1024**3)
    assert check_host_disk()[0][0] == "WARNING"


def test_disk_usage_failure_is_noop(monkeypatch):
    def _boom(_path):
        raise OSError("no such path")

    monkeypatch.setattr(pc.shutil, "disk_usage", _boom)
    assert check_host_disk() == []
