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

        if not self._positions_file:
            raise ConfigError(f"{self.name}: positions_file nicht konfiguriert")

    def _load_yaml(self) -> list[dict]:
        path = Path(self._positions_file).expanduser()
        if not path.exists():
            raise ConfigError(f"{self.name}: Datei nicht gefunden: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return data.get("positions", [])

    def _fetch_positions_raw(self) -> list[Position]:
        raw_positions = self._load_yaml()
        positions: list[Position] = []

        for raw in raw_positions:
            asset_type = raw.get("asset_type", "stock")
            entry_date_str = raw.get("entry_date", "")
            entry_date = None
            if entry_date_str:
                try:
                    entry_date = date.fromisoformat(str(entry_date_str))
                except ValueError:
                    pass

            base_kwargs = dict(
                broker=self.name,
                ticker=raw.get("ticker", ""),
                name=raw.get("name", raw.get("ticker", "")),
                asset_type=asset_type,
                direction=raw.get("direction", "long"),
                size=float(raw.get("size", 0)),
                entry_price=float(raw.get("entry_price", 0)),
                entry_date=entry_date,
                current_price=None,  # Wird spaeter via quotes.py gesetzt
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
