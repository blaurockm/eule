"""Regime-Erkennung: Kalendertag eines Runs ist unabhaengig von der DB-Sitzungs-Zeitzone."""

from datetime import date

import pandas as pd

from eule.elster.regimes import _run_date, detect_regimes


def test_run_date_utc_timestamp_maps_to_berlin_day():
    # staging-hl startet 00:01 Berlin = 23:01 UTC am Vortag
    assert _run_date(pd.Timestamp("2026-11-25 23:01:07", tz="UTC")) == date(2026, 11, 26)


def test_run_date_same_instant_same_day_regardless_of_session_tz():
    utc = pd.Timestamp("2026-11-25 23:01:07", tz="UTC")
    berlin = utc.tz_convert("Europe/Berlin")
    assert _run_date(utc) == _run_date(berlin) == date(2026, 11, 26)


def test_run_date_summer_time():
    # 00:01 CEST = 22:01 UTC am Vortag
    assert _run_date(pd.Timestamp("2026-07-14 22:01:00", tz="UTC")) == date(2026, 7, 15)


def test_run_date_naive_timestamp_unchanged():
    assert _run_date(pd.Timestamp("2026-11-26 00:01:07")) == date(2026, 11, 26)


def test_detect_regimes_uses_berlin_day_for_regime_start():
    runs = pd.DataFrame(
        [
            {
                "run_id": "r1",
                "started_at": pd.Timestamp("2026-11-25 23:01:07", tz="UTC"),
                "finished_at": None,
                "strategy_configs": {"s": {"a": 1}},
            }
        ]
    )
    regimes = detect_regimes(runs, "s")
    assert regimes[0].start_date == date(2026, 11, 26)
