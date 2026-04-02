"""Tests fuer eule/bonds.py."""

from datetime import date, timedelta

from eule.bonds import analyze_bonds, _compute_next_coupon
from eule.models import BondPosition, Position


def _make_bond(days_to_maturity=500, coupon_rate=0.05, **kw) -> BondPosition:
    maturity = date.today() + timedelta(days=days_to_maturity)
    defaults = dict(
        broker="tr",
        ticker="PBB_BOND",
        name="PBB 5% 08/2026",
        asset_type="bond",
        direction="long",
        size=1.0,
        entry_price=95.0,
        entry_date=date(2025, 6, 1),
        current_price=97.0,
        currency="EUR",
        unrealized_pnl=20.0,
        unrealized_pnl_eur=20.0,
        category="bonds",
        market_value=970.0,
        market_value_eur=970.0,
        pct_of_portfolio=0.03,
        issuer="Deutsche Pfandbriefbank",
        coupon_rate=coupon_rate,
        coupon_frequency="annual",
        maturity_date=maturity,
        face_value=1000.0,
        credit_rating="BBB-",
        annual_income=1000.0 * coupon_rate,
        days_to_maturity=days_to_maturity,
    )
    defaults.update(kw)
    return BondPosition(**defaults)


class TestMaturityWarning:
    """Faelligkeits-Warnungen."""

    def test_triggered(self):
        bond = _make_bond(days_to_maturity=60)
        _, alerts = analyze_bonds([bond], maturity_warning_days=90)
        assert len(alerts) >= 1
        maturity_alerts = [a for a in alerts if a.alert_type == "maturity_warning"]
        assert len(maturity_alerts) == 1
        assert "60 Tage" in maturity_alerts[0].message

    def test_not_triggered(self):
        bond = _make_bond(days_to_maturity=200)
        _, alerts = analyze_bonds([bond], maturity_warning_days=90)
        maturity_alerts = [a for a in alerts if a.alert_type == "maturity_warning"]
        assert len(maturity_alerts) == 0


class TestCouponDate:
    """Naechster Kupon-Termin."""

    def test_annual_next_coupon(self):
        maturity = date.today() + timedelta(days=200)
        next_coupon = _compute_next_coupon(maturity, "annual")
        assert next_coupon is not None
        assert next_coupon >= date.today()

    def test_semi_annual_next_coupon(self):
        maturity = date.today() + timedelta(days=400)
        next_coupon = _compute_next_coupon(maturity, "semi-annual")
        assert next_coupon is not None
        assert next_coupon >= date.today()
        # Bei semi-annual sollte der naechste Kupon naeher sein als bei annual
        annual = _compute_next_coupon(maturity, "annual")
        assert next_coupon <= annual


class TestAnalyzeBonds:
    """Allgemeine Bond-Analyse."""

    def test_filters_only_bonds(self):
        stock = Position(
            broker="test", ticker="AAPL", name="Apple", asset_type="stock",
            direction="long", size=10, entry_price=150, entry_date=None,
            current_price=175, currency="USD", unrealized_pnl=250,
            unrealized_pnl_eur=None, category="core", market_value=1750,
            market_value_eur=None, pct_of_portfolio=None,
        )
        bond = _make_bond()
        bonds, _ = analyze_bonds([stock, bond])
        assert len(bonds) == 1

    def test_empty(self):
        bonds, alerts = analyze_bonds([])
        assert bonds == []
        assert alerts == []

    def test_zero_coupon_no_coupon_alert(self):
        bond = _make_bond(coupon_rate=0.0, days_to_maturity=200)
        _, alerts = analyze_bonds([bond])
        coupon_alerts = [a for a in alerts if a.alert_type == "coupon_upcoming"]
        assert len(coupon_alerts) == 0
