"""
IBKR Broker-Adapter via ibind (Client Portal REST API).

Liest OAuth-Credentials aus .env-Datei, erstellt IbkrClient,
holt Positionen und Balance.
"""

import os
from datetime import date
from pathlib import Path

from dotenv import dotenv_values
from loguru import logger

from eule.brokers import BrokerAdapter
from eule.config import BrokerConfig, ConfigError
from eule.models import AccountSummary, OptionPosition, Position

# WICHTIG: ibind liest Env-Vars beim Import und cached sie auf Modul-Ebene.
# Deshalb: Env-Vars ZUERST setzen, DANN ibind importieren.
# Siehe Hase CLAUDE.md: "BrokerIBKR import MUST happen AFTER loading env vars"
_ibind_imported = False


def _set_ibind_env(env: dict[str, str | None]) -> None:
    """Setzt IBIND-Env-Vars und importiert ibind (beim ersten Aufruf)."""
    global _ibind_imported
    for key, val in env.items():
        if key and key.startswith("IBIND") and val is not None:
            os.environ[key] = val

    if not _ibind_imported:
        import ibind  # noqa: F401 — Modul-Level-Init liest jetzt die gesetzten Vars
        _ibind_imported = True


class IbkrAdapter(BrokerAdapter):
    """IBKR Broker-Adapter via ibind Client Portal API."""

    def __init__(self, config: BrokerConfig):
        self.name = config.name
        self._config = config
        self._env = config.load_env()
        self._account_id = self._env.get("IBIND_ACCOUNT_ID", "")
        if not self._account_id:
            raise ConfigError(f"{self.name}: IBIND_ACCOUNT_ID fehlt in {config.env_file}")
        self._client = None

    def _get_client(self):
        # Env-Vars immer setzen (vor jedem Zugriff, falls anderer Adapter sie ueberschrieben hat)
        _set_ibind_env(self._env)
        if self._client is None:
            from ibind import IbkrClient
            use_oauth = str(self._env.get("IBIND_USE_OAUTH", "false")).lower() in ("1", "true", "yes", "on")
            self._client = IbkrClient(use_oauth=use_oauth, auto_register_shutdown=False)
        return self._client

    def _fetch_positions_raw(self) -> list[Position]:
        client = self._get_client()

        # Portfolio-Cache aufwaermen
        client.portfolio_accounts()

        # Positionen laden
        resp = client.positions2(self._account_id)
        if not resp or not resp.data:
            return []

        positions: list[Position] = []
        for pos in resp.data:
            if pos.get("position", 0) == 0:
                continue

            conid = str(pos.get("conid", ""))
            size = float(pos.get("position", 0))
            asset_class = pos.get("assetClass", "STK")
            current_price = float(pos.get("marketPrice", pos.get("mktPrice", 0)))
            entry_price = float(pos.get("avgPrice", pos.get("avgCost", 0)))
            unrealized_pnl = float(pos.get("unrealizedPnl", 0))
            currency = pos.get("currency", "USD")
            description = pos.get("description", pos.get("contractDesc", conid))

            # Ticker aus contractDesc extrahieren (erster Teil vor Leerzeichen)
            ticker = description.split()[0] if description else conid

            direction = "long" if size > 0 else "short"
            abs_size = abs(size)
            market_value = abs(float(pos.get("marketValue", pos.get("mktValue", 0))))

            if asset_class == "OPT":
                # Option — contractDesc z.B. "SPX    DEC2025 6765 P [SPXW  251216P06765000 100]"
                option_type = ""
                strike = float(pos.get("strike", 0))
                expiry_str = pos.get("expiry", "")

                # putOrCall aus contractDesc parsen wenn nicht direkt verfuegbar
                put_or_call = pos.get("putOrCall", "")
                if not put_or_call and description:
                    if " P " in description or " P[" in description:
                        option_type = "put"
                    elif " C " in description or " C[" in description:
                        option_type = "call"
                else:
                    option_type = "put" if put_or_call == "P" else "call" if put_or_call == "C" else ""

                expiry_date = None
                if expiry_str:
                    try:
                        expiry_date = date.fromisoformat(expiry_str[:10])
                    except (ValueError, IndexError):
                        pass

                days_to_expiry = (expiry_date - date.today()).days if expiry_date else 0

                positions.append(OptionPosition(
                    broker=self.name,
                    ticker=ticker,
                    name=description,
                    asset_type="option",
                    direction=direction,
                    size=abs_size,
                    entry_price=entry_price,
                    entry_date=None,
                    current_price=current_price,
                    currency=currency,
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_eur=None,  # Aggregator setzt das
                    category="opportunistic",
                    market_value=market_value,
                    market_value_eur=None,
                    pct_of_portfolio=None,
                    underlying=ticker,
                    strike=strike,
                    expiry=expiry_date,
                    option_type=option_type,
                    sold_premium=abs(entry_price * abs_size * 100) if direction == "short" else 0.0,
                    current_value=abs(current_price * abs_size * 100),
                    days_to_expiry=days_to_expiry,
                ))
            else:
                asset_type = "stock"
                if asset_class in ("FUT", "FUTURE"):
                    asset_type = "future"

                positions.append(Position(
                    broker=self.name,
                    ticker=ticker,
                    name=description,
                    asset_type=asset_type,
                    direction=direction,
                    size=abs_size,
                    entry_price=entry_price,
                    entry_date=None,
                    current_price=current_price,
                    currency=currency,
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_eur=None,
                    category="core",
                    market_value=market_value,
                    market_value_eur=None,
                    pct_of_portfolio=None,
                ))

        logger.debug(f"[{self.name}] {len(positions)} Positionen geladen")
        return positions

    def _fetch_balance_raw(self) -> AccountSummary | None:
        client = self._get_client()
        client.portfolio_accounts()
        resp = client.portfolio_summary()

        if not resp or not resp.data:
            return None

        data = resp.data
        cash = float(data.get("availablefunds", {}).get("amount", 0))
        equity = float(data.get("equitywithloanvalue", {}).get("amount", 0))
        buying_power = float(data.get("buyingpower", {}).get("amount", 0))
        currency = data.get("availablefunds", {}).get("currency", "USD")

        return AccountSummary(
            broker=self.name,
            cash=cash,
            equity=equity,
            currency=currency,
            buying_power=buying_power,
        )

    def get_client(self):
        """Gibt den ibind Client zurueck (fuer Quotes)."""
        return self._get_client()
