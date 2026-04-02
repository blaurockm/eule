"""Tests fuer eule/brokers/ibkr.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from eule.brokers.ibkr import IbkrAdapter
from eule.config import BrokerConfig
from eule.models import OptionPosition, Position

FIXTURES = Path(__file__).parent / "fixtures"


def _make_ibkr_config(tmp_path) -> BrokerConfig:
    env_file = tmp_path / "ibkr.env"
    env_file.write_text("IBIND_ACCOUNT_ID=U20724384\nIBIND_USE_OAUTH=True\n")
    return BrokerConfig(
        name="ibkr-test",
        enabled=True,
        broker_type="ibkr",
        env_file=str(env_file),
    )


class TestIbkrAdapter:
    """IBKR Adapter mit gemocktem ibind Client."""

    def test_positions_parsing(self, tmp_path):
        config = _make_ibkr_config(tmp_path)
        adapter = IbkrAdapter(config)

        # ibind Client mocken
        mock_client = MagicMock()
        adapter._client = mock_client

        with open(FIXTURES / "ibkr_positions.json") as f:
            positions_data = json.load(f)

        mock_client.portfolio_accounts.return_value = None
        mock_resp = MagicMock()
        mock_resp.data = positions_data
        mock_client.positions2.return_value = mock_resp

        positions, errors = adapter.fetch_positions()

        assert errors == []
        assert len(positions) == 2  # Position mit size=0 wird gefiltert

        # Stock
        stock = next(p for p in positions if p.asset_type == "stock")
        assert stock.ticker == "AAPL"
        assert stock.size == 100.0
        assert stock.entry_price == 150.25
        assert stock.current_price == 175.50
        assert stock.direction == "long"
        assert stock.unrealized_pnl == 2525.0

        # Option
        opt = next(p for p in positions if p.asset_type == "option")
        assert isinstance(opt, OptionPosition)
        assert opt.ticker == "SPX"
        assert opt.direction == "short"
        assert opt.size == 1.0
        assert opt.strike == 5465.0
        assert opt.option_type == "put"

    def test_empty_positions(self, tmp_path):
        config = _make_ibkr_config(tmp_path)
        adapter = IbkrAdapter(config)
        mock_client = MagicMock()
        adapter._client = mock_client

        mock_resp = MagicMock()
        mock_resp.data = []
        mock_client.portfolio_accounts.return_value = None
        mock_client.positions2.return_value = mock_resp

        positions, errors = adapter.fetch_positions()
        assert positions == []
        assert errors == []

    def test_connection_error_returns_empty_with_error(self, tmp_path):
        config = _make_ibkr_config(tmp_path)
        adapter = IbkrAdapter(config)
        mock_client = MagicMock()
        adapter._client = mock_client

        mock_client.portfolio_accounts.side_effect = ConnectionError("Gateway offline")

        positions, errors = adapter.fetch_positions()
        assert positions == []
        assert len(errors) == 1
        assert "Gateway offline" in errors[0]

    def test_missing_account_id_raises(self, tmp_path):
        env_file = tmp_path / "empty.env"
        env_file.write_text("")
        config = BrokerConfig(
            name="ibkr-bad",
            enabled=True,
            broker_type="ibkr",
            env_file=str(env_file),
        )
        with pytest.raises(Exception, match="IBIND_ACCOUNT_ID"):
            IbkrAdapter(config)

    def test_balance(self, tmp_path):
        config = _make_ibkr_config(tmp_path)
        adapter = IbkrAdapter(config)
        mock_client = MagicMock()
        adapter._client = mock_client

        mock_client.portfolio_accounts.return_value = None
        mock_resp = MagicMock()
        mock_resp.data = {
            "availablefunds": {"amount": 5000.0, "currency": "USD"},
            "equitywithloanvalue": {"amount": 14000.0},
            "buyingpower": {"amount": 10000.0},
        }
        mock_client.portfolio_summary.return_value = mock_resp

        balance = adapter.fetch_balance()
        assert balance is not None
        assert balance.cash == 5000.0
        assert balance.equity == 14000.0
        assert balance.buying_power == 10000.0
