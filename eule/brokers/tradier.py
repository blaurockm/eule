"""
Tradier Broker-Adapter via REST API.
"""

import re
from datetime import date

import httpx
from loguru import logger

from eule.brokers import BrokerAdapter
from eule.config import BrokerConfig, ConfigError
from eule.models import AccountSummary, OptionPosition, Position


def _parse_occ_symbol(symbol: str) -> dict | None:
    """Parsed OCC Options-Symbology.

    Format: TICKER + YYMMDD + P/C + Strike*1000 (8 Stellen)
    Beispiel: CDE260515P00015000 → CDE, 2026-05-15, put, 15.0
    """
    m = re.match(r"^([A-Z]+)(\d{6})([PC])(\d{8})$", symbol)
    if not m:
        return None
    ticker, date_str, pc, strike_str = m.groups()
    return {
        "underlying": ticker,
        "expiry": date(2000 + int(date_str[:2]), int(date_str[2:4]), int(date_str[4:6])),
        "option_type": "put" if pc == "P" else "call",
        "strike": int(strike_str) / 1000.0,
    }


class TradierAdapter(BrokerAdapter):
    """Tradier Broker-Adapter."""

    def __init__(self, config: BrokerConfig):
        self.name = config.name
        self._config = config
        self._env = config.load_env()
        self._token = self._env.get("TRADIER_TOKEN", "")
        self._account_id = self._env.get("TRADIER_ACCOUNT_ID", "")
        self._base_url = config.base_url or "https://api.tradier.com/v1"

        if not self._token:
            raise ConfigError(f"{self.name}: TRADIER_TOKEN fehlt in {config.env_file}")
        if not self._account_id:
            raise ConfigError(f"{self.name}: TRADIER_ACCOUNT_ID fehlt in {config.env_file}")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _fetch_positions_raw(self) -> list[Position]:
        url = f"{self._base_url}/accounts/{self._account_id}/positions"
        resp = httpx.get(url, headers=self._headers(), timeout=10.0)
        resp.raise_for_status()

        data = resp.json()
        raw_positions = data.get("positions", {})
        if not raw_positions or raw_positions == "null":
            return []

        pos_list = raw_positions.get("position", [])
        if isinstance(pos_list, dict):
            pos_list = [pos_list]

        # Quotes fuer aktuelle Kurse holen
        symbols = [p.get("symbol", "") for p in pos_list if p.get("symbol")]
        quotes = self._fetch_quotes(symbols) if symbols else {}

        positions: list[Position] = []
        for pos in pos_list:
            symbol = pos.get("symbol", "")
            qty = float(pos.get("quantity", 0))
            cost_basis = float(pos.get("cost_basis", 0))
            entry_price = cost_basis / qty if qty != 0 else 0
            date_acquired = pos.get("date_acquired", "")

            entry_date = None
            if date_acquired:
                try:
                    entry_date = date.fromisoformat(date_acquired[:10])
                except ValueError:
                    pass

            current_price = quotes.get(symbol)
            direction = "long" if qty > 0 else "short"
            abs_qty = abs(qty)

            market_value = abs_qty * current_price if current_price else None
            unrealized_pnl = (current_price - entry_price) * abs_qty if current_price else None

            # OCC Symbol? → Option
            occ = _parse_occ_symbol(symbol)
            if occ:
                days_to_expiry = (occ["expiry"] - date.today()).days
                positions.append(OptionPosition(
                    broker=self.name,
                    ticker=symbol,
                    name=f"{occ['underlying']} {occ['option_type'].upper()} {occ['strike']} {occ['expiry']}",
                    asset_type="option",
                    direction=direction,
                    size=abs_qty,
                    entry_price=entry_price,
                    entry_date=entry_date,
                    current_price=current_price,
                    currency="USD",
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_eur=None,
                    category="opportunistic",
                    market_value=market_value,
                    market_value_eur=None,
                    pct_of_portfolio=None,
                    underlying=occ["underlying"],
                    strike=occ["strike"],
                    expiry=occ["expiry"],
                    option_type=occ["option_type"],
                    days_to_expiry=days_to_expiry,
                ))
            else:
                positions.append(Position(
                    broker=self.name,
                    ticker=symbol,
                    name=symbol,
                    asset_type="stock",
                    direction=direction,
                    size=abs_qty,
                    entry_price=entry_price,
                    entry_date=entry_date,
                    current_price=current_price,
                    currency="USD",
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pnl_eur=None,
                    category="core",
                    market_value=market_value,
                    market_value_eur=None,
                    pct_of_portfolio=None,
                ))

        logger.debug(f"[{self.name}] {len(positions)} Positionen geladen")
        return positions

    def _fetch_quotes(self, symbols: list[str]) -> dict[str, float]:
        """Holt aktuelle Kurse via Tradier Quotes API (Batch)."""
        url = f"{self._base_url}/markets/quotes"
        resp = httpx.get(
            url,
            headers=self._headers(),
            params={"symbols": ",".join(symbols)},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        quotes_data = data.get("quotes", {})
        quote_list = quotes_data.get("quote", [])
        if isinstance(quote_list, dict):
            quote_list = [quote_list]

        result: dict[str, float] = {}
        for q in quote_list:
            sym = q.get("symbol", "")
            last = q.get("last")
            if sym and last is not None:
                result[sym] = float(last)
        return result

    def _fetch_balance_raw(self) -> AccountSummary | None:
        url = f"{self._base_url}/accounts/{self._account_id}/balances"
        resp = httpx.get(url, headers=self._headers(), timeout=10.0)
        resp.raise_for_status()

        data = resp.json().get("balances", {})
        return AccountSummary(
            broker=self.name,
            cash=float(data.get("total_cash", 0)),
            equity=float(data.get("total_equity", 0)),
            currency="USD",
            buying_power=float(data.get("option_buying_power", 0)),
        )
