"""
Datenmodelle fuer Eule.
"""

from dataclasses import dataclass, asdict
from datetime import date, datetime


@dataclass(frozen=True)
class HaseTrade:
    """Ein einzelner Trade aus der Hase-DB."""

    ts: datetime
    date: date
    strategy_key: str
    symbol: str
    asset_class: str
    side: str  # 'buy' oder 'sell'
    qty: float
    price: float
    value: float
    fees: float
    trade_ref: str | None
    order_id: str | None

    @property
    def is_expiry(self) -> bool:
        """Synthetischer Trade von expire_options(): buy mit price=0, kein trade_ref."""
        return self.side == "buy" and self.price == 0.0 and self.trade_ref is None

    @property
    def is_synthetic_sell(self) -> bool:
        """Synthetischer Rollover-Sell von Hase: sell mit price=0, kein trade_ref."""
        return self.side == "sell" and self.price == 0.0 and self.trade_ref is None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat()
        d["date"] = self.date.isoformat()
        d["is_expiry"] = self.is_expiry
        d["is_synthetic_sell"] = self.is_synthetic_sell
        return d


@dataclass(frozen=True)
class Roundtrip:
    """Ein abgeschlossener Roundtrip: Entry + Exit."""

    strategy_key: str
    symbol: str
    asset_class: str

    # Entry
    entry_ts: datetime
    entry_date: date
    entry_side: str
    entry_qty: float
    entry_price: float
    entry_value: float
    entry_fees: float

    # Exit
    exit_ts: datetime
    exit_date: date
    exit_side: str
    exit_qty: float
    exit_price: float
    exit_value: float
    exit_fees: float
    exit_is_expiry: bool

    @property
    def pnl(self) -> float:
        """Realisierter P&L (Prämie kassiert - Rückkaufkosten - Fees)."""
        # Fuer short options: entry=sell (positive value), exit=buy (negative value)
        # value ist immer positiv in der DB, also: sell_value - buy_value - fees
        if self.entry_side == "sell":
            return self.entry_value - self.exit_value - self.total_fees
        else:
            return self.exit_value - self.entry_value - self.total_fees

    @property
    def total_fees(self) -> float:
        return self.entry_fees + self.exit_fees

    @property
    def holding_days(self) -> int:
        return (self.exit_date - self.entry_date).days

    @property
    def pnl_percent(self) -> float:
        """P&L in Prozent des Entry-Values."""
        if self.entry_value == 0:
            return 0.0
        return self.pnl / self.entry_value * 100

    def to_dict(self) -> dict:
        return {
            "strategy_key": self.strategy_key,
            "symbol": self.symbol,
            "asset_class": self.asset_class,
            "entry_date": self.entry_date.isoformat(),
            "entry_price": self.entry_price,
            "entry_value": self.entry_value,
            "exit_date": self.exit_date.isoformat(),
            "exit_price": self.exit_price,
            "exit_value": self.exit_value,
            "exit_is_expiry": self.exit_is_expiry,
            "holding_days": self.holding_days,
            "pnl": round(self.pnl, 2),
            "pnl_percent": round(self.pnl_percent, 2),
            "total_fees": round(self.total_fees, 2),
        }
