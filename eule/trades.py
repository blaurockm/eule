"""
Trade-Loading und Roundtrip-Erkennung fuer Hase-Trades.

Roundtrip-Logik:
- Entry = sell (Short Option verkauft, echte Broker-Execution mit trade_ref)
- Exit  = buy  (Rueckkauf oder Expiry)
- Expiry-Erkennung: buy mit price=0.0 und trade_ref=NULL (synthetisch von expire_options())
- Grouping: pro strategy_key + symbol, chronologisch gepaart
"""

from datetime import date

import psycopg

from eule.models import HaseTrade, Roundtrip


def load_trades(
    conn: psycopg.Connection,
    runtime_name: str,
    *,
    strategy_key: str | None = None,
    days: int | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[HaseTrade]:
    """Laedt Trades aus der Hase-DB."""
    conditions = ["runtime_name = %(rn)s"]
    params: dict = {"rn": runtime_name}

    if strategy_key:
        conditions.append("strategy_key = %(sk)s")
        params["sk"] = strategy_key

    if start_date and end_date:
        conditions.append("date >= %(start)s AND date <= %(end)s")
        params["start"] = start_date
        params["end"] = end_date
    elif days:
        conditions.append("date >= current_date - %(days)s")
        params["days"] = days

    where = " AND ".join(conditions)
    sql = f"""
        SELECT ts, date, strategy_key, symbol, asset_class, side,
               qty, price, value, fees, trade_ref, order_id
        FROM trades
        WHERE {where}
        ORDER BY ts
    """

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        HaseTrade(
            ts=row[0],
            date=row[1],
            strategy_key=row[2] or "",
            symbol=row[3] or "",
            asset_class=row[4] or "",
            side=row[5] or "",
            qty=row[6] or 0.0,
            price=row[7] or 0.0,
            value=row[8] or 0.0,
            fees=row[9] or 0.0,
            trade_ref=row[10],
            order_id=row[11],
        )
        for row in rows
    ]


def detect_roundtrips(trades: list[HaseTrade]) -> list[Roundtrip]:
    """
    Erkennt Roundtrips aus einer Liste von Trades.

    Gruppiert nach (strategy_key, symbol), sortiert chronologisch,
    paart sells mit nachfolgenden buys.

    Returns:
        Liste von Roundtrips (abgeschlossene Paare)
    """
    # Gruppiere nach strategy_key + symbol
    groups: dict[tuple[str, str], list[HaseTrade]] = {}
    for t in trades:
        key = (t.strategy_key, t.symbol)
        groups.setdefault(key, []).append(t)

    roundtrips: list[Roundtrip] = []

    for (_sk, _sym), group_trades in sorted(groups.items()):
        # Chronologisch sortieren
        sorted_trades = sorted(group_trades, key=lambda t: t.ts)

        # Sells und Buys trennen und FIFO-paaren
        pending_entries: list[HaseTrade] = []

        for trade in sorted_trades:
            if trade.side == "sell":
                pending_entries.append(trade)
            elif trade.side == "buy" and pending_entries:
                entry = pending_entries.pop(0)  # FIFO
                roundtrips.append(
                    Roundtrip(
                        strategy_key=entry.strategy_key,
                        symbol=entry.symbol,
                        asset_class=entry.asset_class,
                        entry_ts=entry.ts,
                        entry_date=entry.date,
                        entry_side=entry.side,
                        entry_qty=entry.qty,
                        entry_price=entry.price,
                        entry_value=entry.value,
                        entry_fees=entry.fees,
                        exit_ts=trade.ts,
                        exit_date=trade.date,
                        exit_side=trade.side,
                        exit_qty=trade.qty,
                        exit_price=trade.price,
                        exit_value=trade.value,
                        exit_fees=trade.fees,
                        exit_is_expiry=trade.is_expiry,
                    )
                )

    # Chronologisch nach Entry sortieren
    roundtrips.sort(key=lambda r: r.entry_ts)
    return roundtrips


def get_open_trades(trades: list[HaseTrade]) -> list[HaseTrade]:
    """
    Gibt Trades zurueck die noch keinem Roundtrip zugeordnet sind (offene Positionen).
    """
    groups: dict[tuple[str, str], list[HaseTrade]] = {}
    for t in trades:
        key = (t.strategy_key, t.symbol)
        groups.setdefault(key, []).append(t)

    open_trades: list[HaseTrade] = []

    for (_sk, _sym), group_trades in sorted(groups.items()):
        sorted_trades = sorted(group_trades, key=lambda t: t.ts)
        pending_entries: list[HaseTrade] = []

        for trade in sorted_trades:
            if trade.side == "sell":
                pending_entries.append(trade)
            elif trade.side == "buy" and pending_entries:
                pending_entries.pop(0)  # FIFO match -> Roundtrip geschlossen

        open_trades.extend(pending_entries)

    open_trades.sort(key=lambda t: t.ts)
    return open_trades


def summarize_roundtrips(roundtrips: list[Roundtrip]) -> dict:
    """Erzeugt Summary-Statistiken ueber Roundtrips."""
    if not roundtrips:
        return {
            "count": 0,
            "winners": 0,
            "losers": 0,
            "win_rate": 0.0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "avg_holding_days": 0.0,
            "expired_count": 0,
        }

    winners = [r for r in roundtrips if r.pnl > 0]
    losers = [r for r in roundtrips if r.pnl <= 0]
    expired = [r for r in roundtrips if r.exit_is_expiry]
    total_pnl = sum(r.pnl for r in roundtrips)

    return {
        "count": len(roundtrips),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / len(roundtrips) * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / len(roundtrips), 2),
        "avg_holding_days": round(
            sum(r.holding_days for r in roundtrips) / len(roundtrips), 1
        ),
        "expired_count": len(expired),
    }
