"""Tests fuer Position-Models (Phase 1)."""

from datetime import date

from eule.models import (
    AccountSummary,
    BondPosition,
    OptionPosition,
    PortfolioSnapshot,
    Position,
)


def _make_position(**overrides) -> Position:
    defaults = dict(
        broker="test",
        ticker="AAPL",
        name="Apple Inc",
        asset_type="stock",
        direction="long",
        size=10.0,
        entry_price=150.0,
        entry_date=date(2025, 1, 15),
        current_price=175.0,
        currency="USD",
        unrealized_pnl=250.0,
        unrealized_pnl_eur=230.0,
        category="core",
        market_value=1750.0,
        market_value_eur=1610.0,
        pct_of_portfolio=0.15,
    )
    defaults.update(overrides)
    return Position(**defaults)


class TestPosition:
    """Basis-Position Modell."""

    def test_creation(self):
        p = _make_position()
        assert p.ticker == "AAPL"
        assert p.size == 10.0
        assert p.direction == "long"

    def test_frozen(self):
        import pytest
        p = _make_position()
        with pytest.raises(AttributeError):
            p.ticker = "MSFT"  # type: ignore

    def test_to_dict(self):
        p = _make_position()
        d = p.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["entry_date"] == "2025-01-15"
        assert isinstance(d["market_value"], float)

    def test_to_dict_none_values(self):
        p = _make_position(current_price=None, unrealized_pnl=None, pct_of_portfolio=None)
        d = p.to_dict()
        assert d["current_price"] is None
        assert d["pct_of_portfolio"] is None


class TestOptionPosition:
    """Option-Position Modell."""

    def test_creation(self):
        op = OptionPosition(
            broker="ibkr-one",
            ticker="SPX 250307P05465",
            name="SPX Put",
            asset_type="option",
            direction="short",
            size=1.0,
            entry_price=2.45,
            entry_date=date(2025, 3, 2),
            current_price=1.20,
            currency="USD",
            unrealized_pnl=125.0,
            unrealized_pnl_eur=115.0,
            category="opportunistic",
            market_value=120.0,
            market_value_eur=110.0,
            pct_of_portfolio=0.01,
            underlying="SPX",
            strike=5465.0,
            expiry=date(2025, 3, 7),
            option_type="put",
            sold_premium=245.0,
            current_value=120.0,
            pnl_percent=51.0,
            fifty_pct_target=122.5,
            days_to_expiry=5,
        )
        assert op.underlying == "SPX"
        assert op.strike == 5465.0
        assert op.days_to_expiry == 5
        assert isinstance(op, Position)

    def test_to_dict_includes_option_fields(self):
        op = OptionPosition(
            broker="ibkr",
            ticker="SPX Put",
            name="SPX Put",
            asset_type="option",
            direction="short",
            size=1.0,
            entry_price=2.45,
            entry_date=None,
            current_price=1.20,
            currency="USD",
            unrealized_pnl=125.0,
            unrealized_pnl_eur=115.0,
            category="opportunistic",
            market_value=120.0,
            market_value_eur=110.0,
            pct_of_portfolio=None,
            underlying="SPX",
            strike=5465.0,
            expiry=date(2025, 3, 7),
            option_type="put",
        )
        d = op.to_dict()
        assert d["underlying"] == "SPX"
        assert d["expiry"] == "2025-03-07"
        assert d["option_type"] == "put"


class TestBondPosition:
    """Bond-Position Modell."""

    def test_creation(self):
        bp = BondPosition(
            broker="trade_republic",
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
            coupon_rate=0.05,
            coupon_frequency="annual",
            maturity_date=date(2026, 8, 15),
            face_value=1000.0,
            credit_rating="BBB-",
            next_coupon_date=date(2026, 2, 15),
            annual_income=50.0,
            days_to_maturity=500,
        )
        assert bp.issuer == "Deutsche Pfandbriefbank"
        assert bp.annual_income == 50.0
        assert isinstance(bp, Position)

    def test_to_dict_includes_bond_fields(self):
        bp = BondPosition(
            broker="tr",
            ticker="PBB",
            name="PBB Bond",
            asset_type="bond",
            direction="long",
            size=1.0,
            entry_price=95.0,
            entry_date=None,
            current_price=None,
            currency="EUR",
            unrealized_pnl=None,
            unrealized_pnl_eur=None,
            category="bonds",
            market_value=None,
            market_value_eur=None,
            pct_of_portfolio=None,
            issuer="PBB",
            maturity_date=date(2026, 8, 15),
        )
        d = bp.to_dict()
        assert d["issuer"] == "PBB"
        assert d["maturity_date"] == "2026-08-15"


class TestAccountSummary:
    """Account-Summary Modell."""

    def test_creation(self):
        s = AccountSummary(broker="ibkr-one", cash=5000.0, equity=14000.0, currency="USD")
        assert s.cash == 5000.0
        assert s.buying_power is None

    def test_to_dict_omits_none(self):
        s = AccountSummary(broker="ibkr", cash=1000.0, equity=5000.0, currency="USD")
        d = s.to_dict()
        assert "buying_power" not in d


class TestPortfolioSnapshot:
    """Portfolio-Snapshot Modell."""

    def test_to_dict(self):
        p = _make_position()
        snap = PortfolioSnapshot(
            positions=[p],
            total_value_eur=1610.0,
            broker_totals={"test": 1610.0},
            category_totals={"core": 1610.0},
            category_pcts={"core": 1.0},
            timestamp="2025-03-28T12:00:00",
            fx_rates={"USD/EUR": 0.92},
            errors=[],
        )
        d = snap.to_dict()
        assert d["total_value_eur"] == 1610.0
        assert len(d["positions"]) == 1
        assert d["positions"][0]["ticker"] == "AAPL"
        assert d["errors"] == []
