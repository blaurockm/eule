"""Tests fuer eule/aggregator.py."""

from dataclasses import replace
from datetime import date
from unittest.mock import MagicMock, patch

from eule.aggregator import aggregate_positions, create_adapter
from eule.config import (
    AllocationConfig,
    AlertsConfig,
    BrokerConfig,
    EuleConfig,
)
from eule.models import Position


def _make_position(broker="test", ticker="AAPL", currency="USD", market_value=1000.0, **kw) -> Position:
    defaults = dict(
        broker=broker,
        ticker=ticker,
        name=ticker,
        asset_type="stock",
        direction="long",
        size=10.0,
        entry_price=100.0,
        entry_date=date(2025, 1, 1),
        current_price=100.0,
        currency=currency,
        unrealized_pnl=0.0,
        unrealized_pnl_eur=None,
        category="core",
        market_value=market_value,
        market_value_eur=None,
        pct_of_portfolio=None,
    )
    defaults.update(kw)
    return Position(**defaults)


class TestCreateAdapter:
    """Adapter-Erstellung aus Config."""

    def test_manual_adapter(self, tmp_path):
        pos_file = tmp_path / "test.yaml"
        pos_file.write_text("positions: []")
        config = BrokerConfig(
            name="test",
            broker_type="manual",
            positions_file=str(pos_file),
        )
        adapter = create_adapter(config)
        assert adapter.name == "test"


class TestAggregatePositions:
    """Aggregation ueber mehrere Broker."""

    def test_single_manual_broker(self, tmp_path, monkeypatch):
        """Ein manueller Broker, EUR, keine FX-Konvertierung."""
        pos_file = tmp_path / "positions.yaml"
        pos_file.write_text("""\
positions:
  - ticker: GOLD
    name: Physisches Gold
    asset_type: gold_physical
    size: 100
    entry_price: 100.0
    currency: EUR
    category: gold
""")
        cfg = EuleConfig(
            base_currency="EUR",
            brokers={
                "willbe": BrokerConfig(
                    name="willbe",
                    enabled=True,
                    broker_type="manual",
                    positions_file=str(pos_file),
                ),
            },
        )

        # Quotes mocken (kein current_price in YAML)
        monkeypatch.setattr(
            "eule.aggregator.fetch_quotes",
            lambda tickers, ibkr_client=None: ({"GOLD": 120.0}, []),
        )

        snap = aggregate_positions(cfg)

        assert len(snap.positions) == 1
        p = snap.positions[0]
        assert p.ticker == "GOLD"
        assert p.current_price == 120.0
        assert p.market_value == 12000.0
        assert p.market_value_eur == 12000.0  # EUR, keine Konvertierung
        assert p.pct_of_portfolio == 1.0
        assert snap.total_value_eur == 12000.0

    def test_fx_conversion(self, tmp_path, monkeypatch):
        """USD-Position wird nach EUR konvertiert."""
        pos_file = tmp_path / "positions.yaml"
        pos_file.write_text("""\
positions:
  - ticker: AAPL
    asset_type: stock
    size: 10
    entry_price: 150.0
    currency: USD
    category: core
""")
        cfg = EuleConfig(
            base_currency="EUR",
            brokers={
                "manual": BrokerConfig(
                    name="manual",
                    enabled=True,
                    broker_type="manual",
                    positions_file=str(pos_file),
                ),
            },
        )

        monkeypatch.setattr(
            "eule.aggregator.fetch_quotes",
            lambda tickers, ibkr_client=None: ({"AAPL": 175.0}, []),
        )
        # FX: 1 USD = 0.90 EUR
        import eule.fx
        eule.fx.reset_cache()
        monkeypatch.setattr(eule.fx, "_fetch_ecb_rates", lambda: {"EUR": 1.0, "USD": 0.90})

        snap = aggregate_positions(cfg)
        p = snap.positions[0]
        assert abs(p.market_value - 1750.0) < 0.01  # 10 * 175
        assert abs(p.market_value_eur - 1575.0) < 0.01  # 1750 * 0.90
        assert "USD/EUR" in snap.fx_rates

        eule.fx.reset_cache()

    def test_disabled_broker_skipped(self, tmp_path):
        cfg = EuleConfig(
            brokers={
                "disabled": BrokerConfig(
                    name="disabled",
                    enabled=False,
                    broker_type="manual",
                    positions_file=str(tmp_path / "none.yaml"),
                ),
            },
        )
        snap = aggregate_positions(cfg)
        assert snap.positions == []
        assert snap.errors == []

    def test_broker_error_collected(self, tmp_path):
        """Fehlender Broker-File fuehrt zu Error in snap.errors."""
        cfg = EuleConfig(
            brokers={
                "broken": BrokerConfig(
                    name="broken",
                    enabled=True,
                    broker_type="manual",
                    positions_file=str(tmp_path / "nonexistent.yaml"),
                ),
            },
        )
        snap = aggregate_positions(cfg)
        assert snap.positions == []
        assert len(snap.errors) > 0

    def test_multiple_brokers_aggregated(self, tmp_path, monkeypatch):
        """Zwei Broker werden zusammengefuehrt."""
        f1 = tmp_path / "b1.yaml"
        f1.write_text("positions:\n  - ticker: A\n    asset_type: stock\n    size: 10\n    entry_price: 100\n    currency: EUR\n    category: core\n")
        f2 = tmp_path / "b2.yaml"
        f2.write_text("positions:\n  - ticker: B\n    asset_type: etf\n    size: 5\n    entry_price: 200\n    currency: EUR\n    category: core\n")

        cfg = EuleConfig(
            brokers={
                "broker1": BrokerConfig(name="broker1", enabled=True, broker_type="manual", positions_file=str(f1)),
                "broker2": BrokerConfig(name="broker2", enabled=True, broker_type="manual", positions_file=str(f2)),
            },
        )

        monkeypatch.setattr(
            "eule.aggregator.fetch_quotes",
            lambda tickers, ibkr_client=None: ({t: 150.0 for t in tickers}, []),
        )

        snap = aggregate_positions(cfg)
        assert len(snap.positions) == 2
        assert "broker1" in snap.broker_totals
        assert "broker2" in snap.broker_totals
        assert snap.total_value_eur > 0
