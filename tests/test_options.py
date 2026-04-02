"""Tests fuer eule/options.py."""

from datetime import date, timedelta

from eule.models import OptionPosition, Position
from eule.options import analyze_options


def _make_option(days_to_expiry=30, sold_premium=245.0, current_value=120.0, **kw) -> OptionPosition:
    expiry = date.today() + timedelta(days=days_to_expiry)
    defaults = dict(
        broker="ibkr",
        ticker="SPX",
        name="SPX Put",
        asset_type="option",
        direction="short",
        size=1.0,
        entry_price=2.45,
        entry_date=None,
        current_price=1.20,
        currency="USD",
        unrealized_pnl=125.0,
        unrealized_pnl_eur=None,
        category="opportunistic",
        market_value=120.0,
        market_value_eur=None,
        pct_of_portfolio=None,
        underlying="SPX",
        strike=5465.0,
        expiry=expiry,
        option_type="put",
        sold_premium=sold_premium,
        current_value=current_value,
        pnl_percent=0.0,
        fifty_pct_target=0.0,
        days_to_expiry=days_to_expiry,
    )
    defaults.update(kw)
    return OptionPosition(**defaults)


class TestFiftyPctRule:
    """50%-Regel fuer Short Options."""

    def test_triggered(self):
        """Praemie 245, aktuell 100 → 59% Gewinn → Alert."""
        opt = _make_option(sold_premium=245.0, current_value=100.0)
        _, alerts = analyze_options([opt])
        fifty_alerts = [a for a in alerts if a.alert_type == "fifty_pct"]
        assert len(fifty_alerts) == 1
        assert "59%" in fifty_alerts[0].message

    def test_not_triggered(self):
        """Praemie 245, aktuell 200 → 18% Gewinn → kein Alert."""
        opt = _make_option(sold_premium=245.0, current_value=200.0)
        _, alerts = analyze_options([opt])
        fifty_alerts = [a for a in alerts if a.alert_type == "fifty_pct"]
        assert len(fifty_alerts) == 0

    def test_only_short(self):
        """Long Options loesen keine 50%-Regel aus."""
        opt = _make_option(direction="long", sold_premium=0.0)
        _, alerts = analyze_options([opt])
        fifty_alerts = [a for a in alerts if a.alert_type == "fifty_pct"]
        assert len(fifty_alerts) == 0

    def test_disabled(self):
        """50%-Regel abgeschaltet."""
        opt = _make_option(sold_premium=245.0, current_value=0.0)
        _, alerts = analyze_options([opt], fifty_pct_rule=False)
        fifty_alerts = [a for a in alerts if a.alert_type == "fifty_pct"]
        assert len(fifty_alerts) == 0


class TestDteWarnings:
    """DTE-Warnungen."""

    def test_warning_7_days(self):
        opt = _make_option(days_to_expiry=5)
        _, alerts = analyze_options([opt])
        dte_alerts = [a for a in alerts if "expiry" in a.alert_type]
        assert len(dte_alerts) == 1
        assert dte_alerts[0].alert_type == "expiry_warning"

    def test_urgent_3_days(self):
        opt = _make_option(days_to_expiry=2)
        _, alerts = analyze_options([opt])
        dte_alerts = [a for a in alerts if "expiry" in a.alert_type]
        assert len(dte_alerts) == 1
        assert dte_alerts[0].alert_type == "expiry_urgent"

    def test_critical_1_day(self):
        opt = _make_option(days_to_expiry=1)
        _, alerts = analyze_options([opt])
        dte_alerts = [a for a in alerts if "expiry" in a.alert_type]
        assert len(dte_alerts) == 1
        assert dte_alerts[0].alert_type == "expiry_critical"

    def test_no_warning_30_days(self):
        opt = _make_option(days_to_expiry=30)
        _, alerts = analyze_options([opt])
        dte_alerts = [a for a in alerts if "expiry" in a.alert_type]
        assert len(dte_alerts) == 0


class TestAnalyzeOptions:
    """Allgemeine Tests."""

    def test_filters_only_options(self):
        stock = Position(
            broker="test", ticker="AAPL", name="Apple", asset_type="stock",
            direction="long", size=10, entry_price=150, entry_date=None,
            current_price=175, currency="USD", unrealized_pnl=250,
            unrealized_pnl_eur=None, category="core", market_value=1750,
            market_value_eur=None, pct_of_portfolio=None,
        )
        opt = _make_option()
        options, _ = analyze_options([stock, opt])
        assert len(options) == 1

    def test_empty_list(self):
        options, alerts = analyze_options([])
        assert options == []
        assert alerts == []
