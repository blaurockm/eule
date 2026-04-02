"""Tests fuer eule/allocation.py."""

from eule.allocation import check_allocation, AllocationCheck, ConcentrationAlert
from eule.config import AllocationConfig, AllocationTarget
from eule.models import PortfolioSnapshot, Position


def _make_snapshot(category_pcts: dict, category_totals: dict, positions=None) -> PortfolioSnapshot:
    total = sum(category_totals.values())
    return PortfolioSnapshot(
        positions=positions or [],
        total_value_eur=total,
        broker_totals={},
        category_totals=category_totals,
        category_pcts=category_pcts,
        timestamp="2025-03-28T12:00:00",
        fx_rates={},
        errors=[],
    )


class TestAllocationCheck:
    """Soll/Ist Pruefung."""

    def test_all_ok(self):
        snap = _make_snapshot(
            category_pcts={"core": 0.60, "gold": 0.10},
            category_totals={"core": 6000, "gold": 1000},
        )
        config = AllocationConfig(
            targets=[
                AllocationTarget("core", 0.55, 0.70),
                AllocationTarget("gold", 0.05, 0.15),
            ],
        )
        checks, _ = check_allocation(snap, config)
        assert all(c.status == "ok" for c in checks)

    def test_under(self):
        snap = _make_snapshot(
            category_pcts={"core": 0.40},
            category_totals={"core": 4000},
        )
        config = AllocationConfig(
            targets=[AllocationTarget("core", 0.55, 0.70)],
        )
        checks, _ = check_allocation(snap, config)
        assert checks[0].status == "under"
        assert checks[0].deviation > 0

    def test_over(self):
        snap = _make_snapshot(
            category_pcts={"gold": 0.50},
            category_totals={"gold": 5000},
        )
        config = AllocationConfig(
            targets=[AllocationTarget("gold", 0.05, 0.15)],
        )
        checks, _ = check_allocation(snap, config)
        assert checks[0].status == "over"

    def test_missing_category(self):
        """Kategorie in Targets aber nicht im Portfolio."""
        snap = _make_snapshot(
            category_pcts={},
            category_totals={},
        )
        config = AllocationConfig(
            targets=[AllocationTarget("bonds", 0.10, 0.25)],
        )
        checks, _ = check_allocation(snap, config)
        assert checks[0].status == "under"
        assert checks[0].actual_pct == 0.0


class TestConcentration:
    """Einzelposition-Konzentrations-Warnung."""

    def test_triggered(self):
        pos = Position(
            broker="test", ticker="BIG", name="Big Position", asset_type="stock",
            direction="long", size=100, entry_price=100, entry_date=None,
            current_price=100, currency="EUR", unrealized_pnl=0,
            unrealized_pnl_eur=0, category="core", market_value=10000,
            market_value_eur=10000, pct_of_portfolio=0.25,  # 25% > 15%
        )
        snap = _make_snapshot(
            category_pcts={"core": 1.0},
            category_totals={"core": 10000},
            positions=[pos],
        )
        config = AllocationConfig(max_single_position_pct=0.15)
        _, alerts = check_allocation(snap, config)
        assert len(alerts) == 1
        assert alerts[0].ticker == "BIG"
        assert alerts[0].pct == 0.25

    def test_not_triggered(self):
        pos = Position(
            broker="test", ticker="SMALL", name="Small", asset_type="stock",
            direction="long", size=10, entry_price=100, entry_date=None,
            current_price=100, currency="EUR", unrealized_pnl=0,
            unrealized_pnl_eur=0, category="core", market_value=1000,
            market_value_eur=1000, pct_of_portfolio=0.05,
        )
        snap = _make_snapshot(
            category_pcts={"core": 1.0},
            category_totals={"core": 1000},
            positions=[pos],
        )
        config = AllocationConfig(max_single_position_pct=0.15)
        _, alerts = check_allocation(snap, config)
        assert len(alerts) == 0
