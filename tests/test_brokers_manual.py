"""Tests fuer eule/brokers/manual.py."""

from datetime import date
from textwrap import dedent

import pytest

from eule.brokers.manual import ManualAdapter
from eule.config import BrokerConfig, ConfigError
from eule.models import BondPosition, Position


def _make_manual_config(tmp_path, yaml_content: str) -> BrokerConfig:
    pos_file = tmp_path / "positions.yaml"
    pos_file.write_text(yaml_content)
    return BrokerConfig(
        name="test-manual",
        enabled=True,
        broker_type="manual",
        positions_file=str(pos_file),
    )


class TestManualAdapter:
    """Manuelle Positionen aus YAML."""

    def test_stock_position(self, tmp_path):
        config = _make_manual_config(tmp_path, dedent("""\
            positions:
              - ticker: GTII
                name: "Green Thumb Industries"
                asset_type: stock
                direction: long
                size: 100
                entry_price: 11.65
                currency: EUR
                category: opportunistic
                entry_date: "2025-11-20"
        """))
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()

        assert errors == []
        assert len(positions) == 1
        p = positions[0]
        assert p.ticker == "GTII"
        assert p.size == 100
        assert p.entry_price == 11.65
        assert p.entry_date == date(2025, 11, 20)
        assert p.category == "opportunistic"
        assert p.current_price is None  # Wird spaeter via quotes gesetzt

    def test_bond_position(self, tmp_path):
        config = _make_manual_config(tmp_path, dedent("""\
            positions:
              - ticker: PBB_BOND
                name: "PBB 5% 08/2026"
                asset_type: bond
                direction: long
                size: 1
                entry_price: 95.0
                currency: EUR
                category: bonds
                issuer: "Deutsche Pfandbriefbank"
                coupon_rate: 0.05
                coupon_frequency: annual
                maturity_date: "2026-08-15"
                face_value: 1000.0
                credit_rating: "BBB-"
        """))
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()

        assert len(positions) == 1
        bp = positions[0]
        assert isinstance(bp, BondPosition)
        assert bp.issuer == "Deutsche Pfandbriefbank"
        assert bp.coupon_rate == 0.05
        assert bp.face_value == 1000.0
        assert bp.annual_income == 50.0  # 1000 * 0.05
        assert bp.maturity_date == date(2026, 8, 15)
        assert bp.days_to_maturity > 0

    def test_gold_position(self, tmp_path):
        config = _make_manual_config(tmp_path, dedent("""\
            positions:
              - ticker: GOLD_PHYSICAL
                name: "Physisches Gold (Willbe)"
                asset_type: gold_physical
                direction: long
                size: 107
                entry_price: 116.52
                currency: EUR
                category: gold
        """))
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()

        assert len(positions) == 1
        p = positions[0]
        assert p.asset_type == "gold_physical"
        assert p.size == 107
        assert p.category == "gold"

    def test_multiple_positions(self, tmp_path):
        config = _make_manual_config(tmp_path, dedent("""\
            positions:
              - ticker: A
                asset_type: stock
                size: 10
                entry_price: 100
              - ticker: B
                asset_type: etf
                size: 20
                entry_price: 50
        """))
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()
        assert len(positions) == 2

    def test_empty_file(self, tmp_path):
        config = _make_manual_config(tmp_path, "")
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()
        assert positions == []

    def test_missing_file_returns_error(self, tmp_path):
        config = BrokerConfig(
            name="test",
            enabled=True,
            broker_type="manual",
            positions_file=str(tmp_path / "nonexistent.yaml"),
        )
        adapter = ManualAdapter(config)
        positions, errors = adapter.fetch_positions()
        assert positions == []
        assert len(errors) == 1

    def test_balance_returns_none(self, tmp_path):
        config = _make_manual_config(tmp_path, "positions: []")
        adapter = ManualAdapter(config)
        assert adapter.fetch_balance() is None
