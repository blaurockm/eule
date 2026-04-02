"""
Manueller Broker-Adapter — liest Positionen aus YAML-Dateien.

Fuer Trade Republic, Willbe und andere Broker ohne API.
"""

from datetime import date
from pathlib import Path

import yaml
from loguru import logger

from eule.brokers import BrokerAdapter
from eule.config import BrokerConfig, ConfigError
from eule.models import AccountSummary, BondPosition, Position


class ManualAdapter(BrokerAdapter):
    """Liest Positionen aus YAML-Datei."""

    def __init__(self, config: BrokerConfig):
        self.name = config.name
        self._config = config
        self._positions_file = config.positions_file
        # Mapping: ticker → quote_ticker (fuer yfinance-Abfrage)
        self.quote_ticker_map: dict[str, str] = {}
        # Mapping: ticker → price_unit (z.B. "oz_to_gram" fuer Gold)
        self.price_transform: dict[str, str] = {}
        # Mapping: ticker → ISIN (fuer Bond-Abfrage via IBKR)
        self.isin_map: dict[str, str] = {}

        if not self._positions_file:
            raise ConfigError(f"{self.name}: positions_file nicht konfiguriert")

    def _load_yaml(self) -> list[dict]:
        path = Path(self._positions_file).expanduser()
        if not path.exists():
            raise ConfigError(f"{self.name}: Datei nicht gefunden: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return data.get("positions", [])

    def _load_cash(self) -> list[dict]:
        path = Path(self._positions_file).expanduser()
        if not path.exists():
            return []
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("cash", [])

    def _fetch_positions_raw(self) -> list[Position]:
        raw_positions = self._load_yaml()
        positions: list[Position] = []

        # Cash-Positionen aus YAML
        for raw_cash in self._load_cash():
            currency = raw_cash.get("currency", "EUR")
            amount = float(raw_cash.get("amount", 0))
            if abs(amount) < 0.01:
                continue
            positions.append(Position(
                broker=self.name,
                ticker=f"CASH_{currency}",
                name=raw_cash.get("name", f"Cash {currency}"),
                asset_type="cash",
                direction="long",
                size=amount,
                entry_price=1.0,
                entry_date=None,
                current_price=1.0,
                currency=currency,
                unrealized_pnl=0.0,
                unrealized_pnl_eur=None,
                category="cash",
                market_value=abs(amount),
                market_value_eur=None,
                pct_of_portfolio=None,
            ))

        for raw in raw_positions:
            asset_type = raw.get("asset_type", "stock")
            entry_date_str = raw.get("entry_date", "")
            entry_date = None
            if entry_date_str:
                try:
                    entry_date = date.fromisoformat(str(entry_date_str))
                except ValueError:
                    pass

            # current_price direkt aus YAML (fuer Bonds, Gold ohne Markt-Ticker)
            current_price_raw = raw.get("current_price")
            current_price = float(current_price_raw) if current_price_raw is not None else None

            # quote_ticker: alternativer Ticker fuer Live-Kurs-Abfrage
            quote_ticker = raw.get("quote_ticker", "")
            if quote_ticker:
                self.quote_ticker_map[raw.get("ticker", "")] = quote_ticker
            # price_transform: Umrechnung (z.B. oz_to_gram fuer Gold)
            price_transform = raw.get("price_transform", "")
            if price_transform:
                self.price_transform[raw.get("ticker", "")] = price_transform
            # ISIN: fuer Bond-Kurs-Abfrage via IBKR
            isin = raw.get("isin", "")
            if isin:
                self.isin_map[raw.get("ticker", "")] = isin

            base_kwargs = dict(
                broker=self.name,
                ticker=raw.get("ticker", ""),
                name=raw.get("name", raw.get("ticker", "")),
                asset_type=asset_type,
                direction=raw.get("direction", "long"),
                size=float(raw.get("size", 0)),
                entry_price=float(raw.get("entry_price", 0)),
                entry_date=entry_date,
                current_price=current_price,
                currency=raw.get("currency", "EUR"),
                unrealized_pnl=None,
                unrealized_pnl_eur=None,
                category=raw.get("category", "core"),
                market_value=None,
                market_value_eur=None,
                pct_of_portfolio=None,
            )

            if asset_type == "bond":
                maturity_str = raw.get("maturity_date", "")
                maturity_date = None
                if maturity_str:
                    try:
                        maturity_date = date.fromisoformat(str(maturity_str))
                    except ValueError:
                        pass

                coupon_rate = float(raw.get("coupon_rate", 0))
                face_value = float(raw.get("face_value", 0))
                days_to_maturity = (maturity_date - date.today()).days if maturity_date else 0

                positions.append(BondPosition(
                    **base_kwargs,
                    issuer=raw.get("issuer", ""),
                    coupon_rate=coupon_rate,
                    coupon_frequency=raw.get("coupon_frequency", "annual"),
                    maturity_date=maturity_date,
                    face_value=face_value,
                    credit_rating=raw.get("credit_rating", ""),
                    annual_income=face_value * coupon_rate,
                    days_to_maturity=days_to_maturity,
                ))
            else:
                positions.append(Position(**base_kwargs))

        logger.debug(f"[{self.name}] {len(positions)} Positionen aus YAML geladen")
        return positions

    def _fetch_balance_raw(self) -> AccountSummary | None:
        # Manuelle Broker haben keine Balance-API
        return None
