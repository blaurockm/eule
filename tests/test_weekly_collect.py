"""Tests fuer die Wochen-Return-Helper des Weekly-Reports.

Hintergrund: Ein-Wochentag-Strategien (z.B. spx-0dte-mon-put) haben im
7-Tage-Fenster nur einen gefilterten Return — calculate_metrics lieferte
dann 0.0 fuer alles. Der Wochen-Return kommt deshalb direkt aus den
NAV-Endpunkten (ungefiltert).
"""

from datetime import date

import pandas as pd
import pytest

from eule.elster.data import filter_active_days, portfolio_nav_returns
from eule.monitoring.telegram_bot import _nav_week_return, _portfolio_week_return


def _df(rows):
    return pd.DataFrame(rows, columns=["date", "strategy_key", "nav_end"])


def test_week_return_single_trading_day_strategy():
    # Montagsstrategie: NAV springt nur am Montag, sonst flach
    df = _df([
        (date(2026, 6, 26), "spx-0dte-mon-put", 44882.17),
        (date(2026, 6, 29), "spx-0dte-mon-put", 45184.38),
        (date(2026, 6, 30), "spx-0dte-mon-put", 45184.38),
        (date(2026, 7, 1), "spx-0dte-mon-put", 45184.38),
    ])
    ret = _nav_week_return(df, "spx-0dte-mon-put")
    assert ret == pytest.approx(45184.38 / 44882.17 - 1)


def test_week_return_unsorted_input():
    df = _df([
        (date(2026, 7, 1), "s1", 110.0),
        (date(2026, 6, 29), "s1", 100.0),
    ])
    assert _nav_week_return(df, "s1") == pytest.approx(0.10)


def test_week_return_insufficient_data():
    assert _nav_week_return(_df([]), "s1") is None
    assert _nav_week_return(_df([(date(2026, 6, 29), "s1", 100.0)]), "s1") is None


def test_portfolio_week_return_sums_navs():
    df = _df([
        (date(2026, 6, 29), "s1", 100.0),
        (date(2026, 6, 29), "s2", 200.0),
        (date(2026, 7, 1), "s1", 110.0),
        (date(2026, 7, 1), "s2", 220.0),
    ])
    assert _portfolio_week_return(df) == pytest.approx(0.10)


def test_portfolio_week_return_skips_incomplete_days():
    # 30.06. fehlt s2 — der Tag darf die Summe nicht verzerren
    df = _df([
        (date(2026, 6, 29), "s1", 100.0),
        (date(2026, 6, 29), "s2", 200.0),
        (date(2026, 6, 30), "s1", 105.0),
        (date(2026, 7, 1), "s1", 110.0),
        (date(2026, 7, 1), "s2", 220.0),
    ])
    assert _portfolio_week_return(df) == pytest.approx(0.10)


def test_portfolio_week_return_insufficient_data():
    assert _portfolio_week_return(_df([])) is None
    df = _df([(date(2026, 6, 29), "s1", 100.0), (date(2026, 6, 29), "s2", 200.0)])
    assert _portfolio_week_return(df) is None


def test_filter_active_days_keeps_settlement_losses():
    # Verlust am Folgetag (T+1) muss drinbleiben, Null-Tage fliegen raus
    r = pd.Series(
        [0.007, 0.0, -0.165, 2.2e-16, 0.006],
        index=pd.to_datetime(["2026-06-08", "2026-06-09", "2026-06-16", "2026-06-17", "2026-06-22"]),
    )
    active = filter_active_days(r)
    assert list(active) == [0.007, -0.165, 0.006]  # Float-Rauschen (2e-16) zaehlt als 0


def test_portfolio_nav_returns_weighted():
    # 100 -> 110 und 200 -> 220 am selben Tag = +10% auf 300 Basis
    df = _df([
        (date(2026, 6, 29), "s1", 100.0),
        (date(2026, 6, 29), "s2", 200.0),
        (date(2026, 6, 30), "s1", 110.0),
        (date(2026, 6, 30), "s2", 220.0),
    ])
    r = portfolio_nav_returns(df)
    assert len(r) == 1
    assert r.iloc[0] == pytest.approx(0.10)


def test_portfolio_nav_returns_no_fake_return_on_strategy_start():
    # s2 startet erst am 30.06. — Kapitalzugang darf keinen Schein-Return erzeugen
    df = _df([
        (date(2026, 6, 29), "s1", 100.0),
        (date(2026, 6, 30), "s1", 100.0),
        (date(2026, 6, 30), "s2", 50.0),
        (date(2026, 7, 1), "s1", 100.0),
        (date(2026, 7, 1), "s2", 50.0),
    ])
    r = portfolio_nav_returns(df)
    assert (r.abs() < 1e-12).all()  # alles flach, kein +50%-Sprung


def test_portfolio_nav_returns_empty():
    assert portfolio_nav_returns(_df([])).empty
