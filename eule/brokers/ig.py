"""
IG Markets Broker-Adapter via trading_ig.

Credentials aus .env-Datei: IG_USERNAME, IG_PASSWORD, IG_API_KEY, IG_ACC_NUMBER.
"""

from loguru import logger
from trading_ig.rest import IGService

from eule.brokers import BrokerAdapter
from eule.config import BrokerConfig, ConfigError
from eule.models import AccountSummary, Position


class IgAdapter(BrokerAdapter):
    """IG Markets Broker-Adapter."""

    def __init__(self, config: BrokerConfig):
        self.name = config.name
        self._config = config
        self._env = config.load_env()

        self._username = self._env.get("IG_USERNAME", "")
        self._password = self._env.get("IG_PASSWORD", "")
        self._api_key = self._env.get("IG_API_KEY", "")
        self._acc_number = self._env.get("IG_ACC_NUMBER", "")
        self._acc_type = self._env.get("IG_ACC_TYPE", "LIVE")

        if not all([self._username, self._password, self._api_key]):
            raise ConfigError(
                f"{self.name}: IG_USERNAME, IG_PASSWORD und IG_API_KEY muessen in {config.env_file} gesetzt sein"
            )

        self._service: IGService | None = None

    def _get_service(self) -> IGService:
        if self._service is None:
            self._service = IGService(
                self._username,
                self._password,
                self._api_key,
                self._acc_type,
                acc_number=self._acc_number or None,
            )
            self._service.create_session()
        return self._service

    def _fetch_positions_raw(self) -> list[Position]:
        ig = self._get_service()
        positions_df = ig.fetch_open_positions()

        if positions_df is None or len(positions_df) == 0:
            return []

        positions: list[Position] = []
        for _, pos in positions_df.iterrows():
            epic = str(pos.get("epic", ""))
            market_name = str(pos.get("market", epic))
            direction_raw = str(pos.get("direction", "BUY"))
            size = float(pos.get("size", 0))
            level = float(pos.get("level", 0))
            profit = float(pos.get("profit", 0))
            currency = str(pos.get("currency", "EUR"))

            direction = "long" if direction_raw == "BUY" else "short"

            # Ticker aus epic ableiten (z.B. "IX.D.DAX.DAILY.IP" → "DAX")
            parts = epic.split(".")
            ticker = parts[2] if len(parts) > 2 else epic

            positions.append(Position(
                broker=self.name,
                ticker=ticker,
                name=market_name,
                asset_type="cfd",
                direction=direction,
                size=abs(size),
                entry_price=level,
                entry_date=None,
                current_price=None,  # IG gibt keinen current_price in positions
                currency=currency,
                unrealized_pnl=profit,
                unrealized_pnl_eur=None,
                category="opportunistic",
                market_value=abs(size * level),
                market_value_eur=None,
                pct_of_portfolio=None,
            ))

        logger.debug(f"[{self.name}] {len(positions)} Positionen geladen")
        return positions

    def _fetch_balance_raw(self) -> AccountSummary | None:
        ig = self._get_service()
        accounts = ig.fetch_accounts()

        if accounts is None or len(accounts) == 0:
            return None

        # Erstes Konto nehmen (oder acc_number filtern)
        for _, acc in accounts.iterrows():
            if self._acc_number and str(acc.get("accountId", "")) != self._acc_number:
                continue
            return AccountSummary(
                broker=self.name,
                cash=float(acc.get("available", 0)),
                equity=float(acc.get("balance", 0)),
                currency=str(acc.get("currency", "EUR")),
            )

        return None
