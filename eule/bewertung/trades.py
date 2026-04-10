"""
Trade-Loading und Roundtrip-Erkennung fuer Hase-Trades.

Roundtrip-Logik:
- Entry = sell (Short Option verkauft, echte Broker-Execution mit trade_ref)
- Exit  = buy  (Rueckkauf oder Expiry)
- Expiry-Erkennung: buy mit price=0.0 und trade_ref=NULL (synthetisch von expire_options())
- Synthetische Sells (price=0, kein trade_ref) werden gefiltert (Rollover-Marker)
- Grouping: pro strategy_key + symbol, chronologisch gepaart
"""

from datetime import date

import psycopg

from eule.models import HaseTrade, Roundtrip

OPTION_MULTIPLIER = 100


OPTION_ASSET_CLASSES = {"OPT", "OptionContract"}


def fix_option_multiplier(asset_class: str, qty: float, price: float, value: float) -> float:
    """Korrigiert fehlenden Options-Multiplier in fruehen Hase-Trades.

    Fruehe Hase-Versionen speicherten value = qty * price statt qty * price * 100.
    Erkennung: Wenn Option und value ≈ qty * price, dann Multiplier anwenden.
    """
    if asset_class in OPTION_ASSET_CLASSES and price > 0 and abs(value - qty * price) < 0.01:
        return qty * price * OPTION_MULTIPLIER
    return value


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

    trades = []
    for row in rows:
        qty = row[6] or 0.0
        price = row[7] or 0.0
        value = row[8] or 0.0
        asset_class = row[4] or ""

        value = fix_option_multiplier(asset_class, qty, price, value)

        trades.append(
            HaseTrade(
                ts=row[0],
                date=row[1],
                strategy_key=row[2] or "",
                symbol=row[3] or "",
                asset_class=asset_class,
                side=row[5] or "",
                qty=qty,
                price=price,
                value=value,
                fees=row[9] or 0.0,
                trade_ref=row[10],
                order_id=row[11],
            )
        )

    return trades


def _group_trades(trades: list[HaseTrade]) -> dict[tuple[str, str], list[HaseTrade]]:
    """Gruppiert Trades nach (strategy_key, symbol)."""
    groups: dict[tuple[str, str], list[HaseTrade]] = {}
    for t in trades:
        key = (t.strategy_key, t.symbol)
        groups.setdefault(key, []).append(t)
    return groups


def _synthetic_sell_dates(trades: list[HaseTrade]) -> set[date]:
    """Sammelt Daten an denen synthetische Sells existieren (Expiry-Evidenz)."""
    return {t.date for t in trades if t.is_synthetic_sell}


def _make_roundtrip(entry: HaseTrade, exit_trade: HaseTrade) -> Roundtrip:
    return Roundtrip(
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
        exit_ts=exit_trade.ts,
        exit_date=exit_trade.date,
        exit_side=exit_trade.side,
        exit_qty=exit_trade.qty,
        exit_price=exit_trade.price,
        exit_value=exit_trade.value,
        exit_fees=exit_trade.fees,
        exit_is_expiry=exit_trade.is_expiry,
    )


def _make_inferred_expiry_roundtrip(entry: HaseTrade, syn: HaseTrade) -> Roundtrip:
    """Erzeugt Roundtrip aus Entry-Sell + synthetischem Sell (fehlender Expiry-Buy)."""
    return Roundtrip(
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
        exit_ts=syn.ts,
        exit_date=syn.date,
        exit_side="buy",
        exit_qty=entry.qty,
        exit_price=0.0,
        exit_value=0.0,
        exit_fees=0.0,
        exit_is_expiry=True,
    )


def detect_roundtrips(trades: list[HaseTrade]) -> list[Roundtrip]:
    """
    Erkennt Roundtrips aus einer Liste von Trades.

    Logik:
    1. Gruppiert nach (strategy_key, symbol), sortiert chronologisch
    2. FIFO-Matching: reale Sells mit Buys paaren (synthetische Sells ignoriert)
    3. Inferenz: ungematchte Sells mit synthetischem Sell am selben Tag
       → Expiry-Buy fehlte in DB, Roundtrip wird trotzdem geschlossen

    Returns:
        Liste von Roundtrips (abgeschlossene Paare)
    """
    roundtrips: list[Roundtrip] = []

    for (_sk, _sym), group_trades in sorted(_group_trades(trades).items()):
        sorted_trades = sorted(group_trades, key=lambda t: t.ts)
        syn_dates = _synthetic_sell_dates(sorted_trades)
        syn_by_date = {t.date: t for t in sorted_trades if t.is_synthetic_sell}
        filtered_trades = [t for t in sorted_trades if not t.is_synthetic_sell]

        # FIFO-Matching: reale Sells mit Buys paaren
        pending_entries: list[HaseTrade] = []

        for trade in filtered_trades:
            if trade.side == "sell":
                pending_entries.append(trade)
            elif trade.side == "buy" and pending_entries:
                entry = pending_entries.pop(0)
                roundtrips.append(_make_roundtrip(entry, trade))

        # Inferenz: ungematchte Sells mit synthetischem Sell am selben Tag
        still_open: list[HaseTrade] = []
        for entry in pending_entries:
            if entry.date in syn_dates:
                roundtrips.append(
                    _make_inferred_expiry_roundtrip(entry, syn_by_date[entry.date])
                )
            else:
                still_open.append(entry)

    roundtrips.sort(key=lambda r: r.entry_ts)
    return roundtrips


def get_open_trades(trades: list[HaseTrade]) -> list[HaseTrade]:
    """
    Gibt Trades zurueck die noch keinem Roundtrip zugeordnet sind (offene Positionen).

    Gleiche Logik wie detect_roundtrips: FIFO + Synthetic-Inferenz.
    """
    open_trades: list[HaseTrade] = []

    for (_sk, _sym), group_trades in sorted(_group_trades(trades).items()):
        sorted_trades = sorted(group_trades, key=lambda t: t.ts)
        syn_dates = _synthetic_sell_dates(sorted_trades)
        filtered_trades = [t for t in sorted_trades if not t.is_synthetic_sell]
        pending_entries: list[HaseTrade] = []

        for trade in filtered_trades:
            if trade.side == "sell":
                pending_entries.append(trade)
            elif trade.side == "buy" and pending_entries:
                pending_entries.pop(0)

        # Synthetische Sells am selben Tag = Expiry-Evidenz → nicht offen
        open_trades.extend(e for e in pending_entries if e.date not in syn_dates)

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
