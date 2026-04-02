"""Tests fuer eule/brokers/tradier.py."""

import json
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from eule.brokers.tradier import TradierAdapter, _parse_occ_symbol
from eule.config import BrokerConfig, ConfigError
from eule.models import OptionPosition, Position

FIXTURES = Path(__file__).parent / "fixtures"


def _make_tradier_config(tmp_path) -> BrokerConfig:
    env_file = tmp_path / "tradier.env"
    env_file.write_text("TRADIER_TOKEN=test123\nTRADIER_ACCOUNT_ID=acc456\n")
    return BrokerConfig(
        name="tradier",
        enabled=True,
        broker_type="tradier",
        env_file=str(env_file),
        base_url="https://api.tradier.com/v1",
    )


class TestOccSymbol:
    """OCC Options-Symbology Parser."""

    def test_put(self):
        result = _parse_occ_symbol("CDE260515P00015000")
        assert result is not None
        assert result["underlying"] == "CDE"
        assert result["expiry"] == date(2026, 5, 15)
        assert result["option_type"] == "put"
        assert result["strike"] == 15.0

    def test_call(self):
        result = _parse_occ_symbol("AAPL250117C00200000")
        assert result is not None
        assert result["underlying"] == "AAPL"
        assert result["option_type"] == "call"
        assert result["strike"] == 200.0

    def test_not_option(self):
        assert _parse_occ_symbol("AAPL") is None
        assert _parse_occ_symbol("") is None


class TestTradierAdapter:
    """Tradier Adapter mit gemockten HTTP-Responses."""

    def test_positions_parsing(self, tmp_path):
        config = _make_tradier_config(tmp_path)
        adapter = TradierAdapter(config)

        with open(FIXTURES / "tradier_positions.json") as f:
            positions_resp = json.load(f)

        quotes_resp = {
            "quotes": {
                "quote": [
                    {"symbol": "CDE", "last": 17.20},
                    {"symbol": "CDE260515P00015000", "last": 2.50},
                ]
            }
        }

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if "positions" in url:
                resp.json.return_value = positions_resp
            elif "quotes" in url:
                resp.json.return_value = quotes_resp
            return resp

        with patch("eule.brokers.tradier.httpx.get", side_effect=mock_get):
            positions, errors = adapter.fetch_positions()

        assert errors == []
        assert len(positions) == 2

        # Stock
        stock = next(p for p in positions if p.ticker == "CDE")
        assert isinstance(stock, Position)
        assert stock.size == 50.0
        assert stock.current_price == 17.20
        assert stock.direction == "long"

        # Option
        opt = next(p for p in positions if isinstance(p, OptionPosition))
        assert opt.underlying == "CDE"
        assert opt.strike == 15.0
        assert opt.option_type == "put"
        assert opt.direction == "short"

    def test_empty_positions(self, tmp_path):
        config = _make_tradier_config(tmp_path)
        adapter = TradierAdapter(config)

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {"positions": "null"}
            return resp

        with patch("eule.brokers.tradier.httpx.get", side_effect=mock_get):
            positions, errors = adapter.fetch_positions()

        assert positions == []

    def test_missing_token_raises(self, tmp_path):
        env_file = tmp_path / "empty.env"
        env_file.write_text("TRADIER_ACCOUNT_ID=acc\n")
        config = BrokerConfig(
            name="tradier",
            enabled=True,
            broker_type="tradier",
            env_file=str(env_file),
        )
        with pytest.raises(ConfigError, match="TRADIER_TOKEN"):
            TradierAdapter(config)
