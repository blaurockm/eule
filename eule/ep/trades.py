"""
EP Trades — Lesen und Anzeigen von EP-Positionen und Watchlist.

Liest ep-trades.json (aktuell aus trading-collab, spaeter aus Eule-DB).
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _find_trades_file() -> Path:
    """EP-Trades-Datei finden.

    Sucht in:
    1. EULE_EP_TRADES env var
    2. ~/.eule/ep-trades.json
    3. ~/fin/trading-collab/ep-trades.json
    """
    env_path = os.environ.get("EULE_EP_TRADES")
    if env_path:
        return Path(env_path)

    for path in [
        Path.home() / ".eule" / "ep-trades.json",
        Path.home() / "fin" / "trading-collab" / "ep-trades.json",
        Path.home() / "trading-collab" / "ep-trades.json",  # auf systematic
    ]:
        if path.exists():
            return path

    raise FileNotFoundError(
        "ep-trades.json nicht gefunden. "
        "Setze EULE_EP_TRADES oder lege ~/.eule/ep-trades.json an."
    )


@dataclass
class EPTrade:
    """Ein EP-Trade aus ep-trades.json."""

    id: str
    ticker: str
    status: str  # open, partial, watch, ordered, idea, closed, invalid
    setup_type: str
    catalyst: str
    entry: float
    stop: float
    risk_per_share: float
    planned_shares: int
    filled_shares: int = 0
    filled_price: float = 0.0
    filled_date: str = ""
    target_r1: float = 0.0
    target_r2: float = 0.0
    target_r3: float = 0.0
    broker_qty: float = 0.0
    broker_avg_price: float = 0.0
    broker_market_price: float = 0.0
    stop_at_broker: bool = False
    notes: list[str] = None

    @property
    def is_active(self) -> bool:
        return self.status in ("open", "partial", "ordered")

    @property
    def is_watch(self) -> bool:
        return self.status in ("watch", "idea")

    @property
    def unrealized_pnl(self) -> float:
        if self.filled_shares > 0 and self.broker_market_price > 0:
            return (self.broker_market_price - self.filled_price) * self.filled_shares
        return 0.0

    @property
    def risk_total(self) -> float:
        return self.risk_per_share * self.planned_shares


def load_trades() -> list[EPTrade]:
    """EP-Trades aus JSON laden."""
    path = _find_trades_file()
    data = json.loads(path.read_text())

    trades = []
    for t in data.get("trades", []):
        filled = t.get("filled", {})
        targets = t.get("targets", {})
        broker = t.get("broker", {})

        trades.append(EPTrade(
            id=t.get("id", ""),
            ticker=t.get("ticker", ""),
            status=t.get("status", ""),
            setup_type=t.get("setupType", ""),
            catalyst=t.get("catalyst", ""),
            entry=t.get("entry", 0.0),
            stop=t.get("stop", 0.0),
            risk_per_share=t.get("riskPerShare", 0.0),
            planned_shares=t.get("plannedShares", 0),
            filled_shares=filled.get("shares", 0),
            filled_price=filled.get("avgPrice", 0.0),
            filled_date=filled.get("date", ""),
            target_r1=targets.get("r1", 0.0),
            target_r2=targets.get("r2", 0.0),
            target_r3=targets.get("r3", 0.0),
            broker_qty=broker.get("positionQty", 0.0),
            broker_avg_price=broker.get("avgPrice", 0.0),
            broker_market_price=broker.get("marketPrice", 0.0),
            stop_at_broker=broker.get("stopOrderPresent", False),
            notes=t.get("notes", []),
        ))

    return trades


def get_active_trades() -> list[EPTrade]:
    """Nur aktive Trades (open, partial, ordered)."""
    return [t for t in load_trades() if t.is_active]


def get_watchlist() -> list[EPTrade]:
    """Nur Watchlist-Eintraege (watch, idea)."""
    return [t for t in load_trades() if t.is_watch]


def morning_brief() -> str:
    """Pre-Market Brief: offene Positionen + Watchlist."""
    trades = load_trades()
    active = [t for t in trades if t.is_active]
    watch = [t for t in trades if t.is_watch]

    lines = ["EP Morning Brief", "=" * 40, ""]

    if active:
        lines.append(f"OFFENE POSITIONEN ({len(active)})")
        lines.append("-" * 30)
        for t in active:
            pnl = t.unrealized_pnl
            pnl_str = f"${pnl:+.2f}" if pnl != 0 else "n/a"
            lines.append(f"  {t.ticker} [{t.status}]")
            lines.append(f"    Entry: ${t.filled_price:.2f} x {t.filled_shares}")
            lines.append(f"    Stop: ${t.stop:.2f} | Risk: ${t.risk_total:.0f}")
            lines.append(f"    1R: ${t.target_r1:.2f} | 2R: ${t.target_r2:.2f}")
            if t.broker_market_price > 0:
                lines.append(f"    Markt: ${t.broker_market_price:.2f} | P&L: {pnl_str}")
            if not t.stop_at_broker:
                lines.append(f"    !! KEIN STOP AM BROKER !!")
            lines.append("")
    else:
        lines.append("Keine offenen EP-Positionen.")
        lines.append("")

    if watch:
        lines.append(f"WATCHLIST ({len(watch)})")
        lines.append("-" * 30)
        for t in watch:
            lines.append(f"  {t.ticker} [{t.setup_type}]")
            lines.append(f"    Entry: ${t.entry:.2f} | Stop: ${t.stop:.2f}")
            lines.append(f"    Shares: {t.planned_shares} | Risk: ${t.risk_total:.0f}")
            if t.notes:
                lines.append(f"    Note: {t.notes[-1]}")
            lines.append("")

    return "\n".join(lines)
