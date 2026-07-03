"""Tests fuer die EOD-JSON-basierte 0DTE-Auflaesung (Phase 3) und den
per-Strategie Market-Close-Resolver in monitoring/precheck.py."""

import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

import eule.monitoring.precheck as pc
from eule.monitoring.precheck import (
    _eod_json_overdue,
    _strategy_market_close_utc,
    check_eod_json,
)

UTC = ZoneInfo("UTC")
ET = ZoneInfo("US/Eastern")
BERLIN = ZoneInfo("Europe/Berlin")

PROD = {"tier": "production", "port": 8767}
STAGING = {"tier": "staging", "port": 8776}


# --- _strategy_market_close_utc ---

def test_close_resolves_from_universe_keys():
    # 2026-04-30 ist EDT (UTC-4) → 16:00 ET = 20:00 UTC
    uni = {"SPXW": {"market_close": time(16, 0), "tz": "US/Eastern"}}
    strat = {"universe_keys": ["SPXW"]}
    now = datetime(2026, 4, 30, 14, 0, tzinfo=UTC)
    close = _strategy_market_close_utc(strat, uni, now)
    assert close is not None
    assert close.tzinfo == UTC
    assert (close.hour, close.minute) == (20, 0)


def test_close_takes_latest_across_keys():
    uni = {
        "SPXW": {"market_close": time(16, 0), "tz": "US/Eastern"},
        "IWM": {"market_close": time(16, 15), "tz": "US/Eastern"},
    }
    strat = {"universe_keys": ["SPXW", "IWM"]}
    now = datetime(2026, 4, 30, 14, 0, tzinfo=UTC)
    close = _strategy_market_close_utc(strat, uni, now)
    assert (close.hour, close.minute) == (20, 15)


def test_close_none_when_unresolved():
    # Keine universe_keys bzw. keine Hours im Universe → None (Fallback: live)
    now = datetime(2026, 4, 30, 14, 0, tzinfo=UTC)
    assert _strategy_market_close_utc({}, {}, now) is None
    assert _strategy_market_close_utc({"universe_keys": ["X"]}, {}, now) is None


# --- check_eod_json ---

def _write_summary(tmp_path, monkeypatch, fsm_states):
    p = tmp_path / "daily-summary-x.json"
    p.write_text(json.dumps({"env": "x", "fsm_states": fsm_states}))
    monkeypatch.setattr(pc, "_eod_summary_path", lambda env, today: p)
    return p


def test_eod_0dte_in_position_alerts_critical_in_prod(tmp_path, monkeypatch):
    _write_summary(tmp_path, monkeypatch, {"spx-0dte-always": "IN_POSITION"})
    res = check_eod_json("real-ibkr", PROD)
    assert len(res) == 1
    sev, msg = res[0]
    assert sev == "CRITICAL"
    assert "EOD nicht aufgeloest" in msg
    assert "spx-0dte-always" in msg


def test_eod_0dte_in_position_warns_in_staging(tmp_path, monkeypatch):
    _write_summary(tmp_path, monkeypatch, {"spx-0dte-always": "IN_POSITION"})
    res = check_eod_json("staging-ibkr", STAGING)
    assert [s for s, _ in res] == ["WARNING"]


def test_eod_all_flat_no_alert(tmp_path, monkeypatch):
    _write_summary(tmp_path, monkeypatch, {
        "spx-0dte-always": "FLAT",
        "ndx-0dte-always": "FLAT",
    })
    assert check_eod_json("staging-ibkr", STAGING) == []


def test_eod_1dte_overnight_ignored(tmp_path, monkeypatch):
    # 1DTE haelt legitim ueber Nacht → kein Alert (1dte matcht _is_0dte_strategy nicht)
    _write_summary(tmp_path, monkeypatch, {"gld-1dte-tue-put": "IN_POSITION"})
    assert check_eod_json("staging-ibkr", STAGING) == []


def test_eod_multiple_0dte_each_alert(tmp_path, monkeypatch):
    _write_summary(tmp_path, monkeypatch, {
        "spx-0dte-always": "IN_POSITION",
        "ndx-0dte-always": "PENDING_FILL",
        "iwm-0dte-fri-put": "FLAT",
    })
    res = check_eod_json("staging-ibkr", STAGING)
    names = sorted(m.split("/")[1].split("]")[0] for _, m in res)
    assert names == ["ndx-0dte-always", "spx-0dte-always"]


def test_eod_json_missing_is_quiet(monkeypatch):
    # Gap: JSON noch nicht geschrieben → keine Aussage
    monkeypatch.setattr(pc, "_eod_summary_path", lambda env, today: None)
    assert check_eod_json("staging-ibkr", STAGING) == []


def test_eod_monitoring_disabled_skipped(tmp_path, monkeypatch):
    _write_summary(tmp_path, monkeypatch, {"spx-0dte-always": "IN_POSITION"})
    assert check_eod_json("staging-hl", {"tier": "staging", "monitoring": False}) == []


def test_eod_unreadable_json_reports_error(tmp_path, monkeypatch):
    p = tmp_path / "broken.json"
    p.write_text("{not valid json")
    monkeypatch.setattr(pc, "_eod_summary_path", lambda env, today: p)
    res = check_eod_json("real-ibkr", PROD)
    assert len(res) == 1
    assert res[0][0] == "CRITICAL"
    assert "nicht lesbar" in res[0][1]


# --- eod_deadline / _eod_json_overdue: abgeleitete Deadline aus Trading-Hours ---

# 2026-07-01 = Mittwoch (Werktag), 2026-07-04 = Samstag.
def _wed(h, m):
    return datetime(2026, 7, 1, h, m, tzinfo=BERLIN)


def _staging_hours(env):
    return {"weekdays": [0, 1, 2, 3, 4], "start": "09:00", "end": "23:30", "tz": "Europe/Berlin"}


def _prod_hours(env):
    return {"weekdays": [0, 1, 2, 3, 4], "start": "13:00", "end": "22:00", "tz": "Europe/Berlin"}


def test_eod_deadline_derived_from_trading_end(monkeypatch):
    # production: Ende 22:00 + 60 min Puffer = 23:00
    monkeypatch.setattr(pc, "load_trading_hours", _prod_hours)
    assert pc.eod_deadline("real-ibkr") == time(23, 0)


def test_eod_deadline_capped_before_midnight(monkeypatch):
    # staging: Ende 23:30 + 60 min laege nach Mitternacht -> Cap 23:44,
    # damit der 15-min-Precheck-Takt noch einen Lauf im Overdue-Fenster hat.
    monkeypatch.setattr(pc, "load_trading_hours", _staging_hours)
    assert pc.eod_deadline("staging-ibkr") == time(23, 44)


def test_eod_deadline_default_without_trading_hours(monkeypatch):
    monkeypatch.setattr(pc, "load_trading_hours", lambda env: None)
    assert pc.eod_deadline("x") == pc.EOD_DEADLINE_DEFAULT


def test_eod_overdue_staging_quiet_before_its_deadline(monkeypatch):
    # Regression: staging schreibt sein EOD-JSON erst ~23:30. Um 23:01 darf es
    # NICHT als "gecrasht" gelten (frueher globale 22:59-Deadline -> Falschalarm).
    monkeypatch.setattr(pc, "load_trading_hours", _staging_hours)
    cfg = {"tier": "staging", "port": 8776}
    assert _eod_json_overdue("staging-ibkr", cfg, _wed(23, 1)) is False


def test_eod_overdue_staging_flags_after_its_deadline(monkeypatch):
    monkeypatch.setattr(pc, "load_trading_hours", _staging_hours)
    cfg = {"tier": "staging", "port": 8776}
    assert _eod_json_overdue("staging-ibkr", cfg, _wed(23, 45)) is True


def test_eod_overdue_production_after_derived_deadline(monkeypatch):
    # production-Deadline = 22:00 + 60 min = 23:00
    monkeypatch.setattr(pc, "load_trading_hours", _prod_hours)
    cfg = {"tier": "production", "port": 8767}
    assert _eod_json_overdue("real-ibkr", cfg, _wed(23, 1)) is True
    assert _eod_json_overdue("real-ibkr", cfg, _wed(22, 30)) is False


def test_eod_overdue_default_deadline_without_hours(monkeypatch):
    # Ohne auffindbare Trading-Hours greift der Default (22:59).
    monkeypatch.setattr(pc, "load_trading_hours", lambda env: None)
    cfg = {"tier": "production", "port": 1}
    assert _eod_json_overdue("x", cfg, _wed(23, 0)) is True
    assert _eod_json_overdue("x", cfg, _wed(22, 0)) is False


def test_eod_overdue_never_on_weekend(monkeypatch):
    monkeypatch.setattr(pc, "load_trading_hours", _staging_hours)
    cfg = {"tier": "staging", "port": 8776}
    saturday = datetime(2026, 7, 4, 23, 50, tzinfo=BERLIN)
    assert _eod_json_overdue("staging-ibkr", cfg, saturday) is False
