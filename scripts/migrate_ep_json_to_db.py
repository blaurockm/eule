#!/usr/bin/env python3
"""
Einmalige Migration: ep-trades.json → ep_pipeline + trades Tabellen.

Ausfuehrung:
  cd ~/eule && set -a && source .env && set +a
  poetry run python scripts/migrate_ep_json_to_db.py [--dry-run]
"""

import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from eule.db import get_db_connection
from eule.ep.db import EPPipeline, upsert_pipeline, _ensure_run


def migrate(dry_run: bool = False):
    # JSON finden
    for path in [
        Path.home() / ".eule" / "ep-trades.json",
        Path.home() / "trading-collab" / "ep-trades.json",
        Path.home() / "fin" / "trading-collab" / "ep-trades.json",
    ]:
        if path.exists():
            json_path = path
            break
    else:
        print("ep-trades.json nicht gefunden.")
        sys.exit(1)

    print(f"Lese {json_path}")
    data = json.loads(json_path.read_text())
    trades = data.get("trades", [])
    print(f"  {len(trades)} Trades gefunden")

    conn = get_db_connection("real-ibkr")

    for t in trades:
        filled = t.get("filled", {})
        targets = t.get("targets", {})
        closed = t.get("closed", {})
        source = t.get("source")
        decision_log = t.get("decisionLog", [])

        entry_price = t.get("entry", 0.0)
        stop_price = t.get("stop", 0.0)
        risk_per_share = t.get("riskPerShare", 0.0)

        ep = EPPipeline(
            id=t["id"],
            ticker=t["ticker"],
            status=t["status"],
            setup_type=t.get("setupType", ""),
            catalyst=t.get("catalyst", ""),
            entry_plan=entry_price,
            stop_plan=stop_price,
            risk_per_share=risk_per_share,
            planned_shares=t.get("plannedShares", 0),
            target_r1=targets.get("r1", 0.0),
            target_r2=targets.get("r2", 0.0),
            target_r3=targets.get("r3", 0.0),
            broker_account=t.get("broker", {}).get("account", ""),
            notes=t.get("notes", []),
            decision_log=decision_log,
            source=source,
        )

        print(f"\n  Pipeline: {ep.id} ({ep.ticker}, {ep.status})")

        if not dry_run:
            upsert_pipeline(conn, ep)

        # Entry-Trade (buy) wenn gefuellt
        if filled.get("shares") and filled.get("avgPrice"):
            fill_date = date.fromisoformat(filled["date"]) if filled.get("date") else date.today()
            fill_price = filled["avgPrice"]
            fill_shares = filled["shares"]
            run_id = f"ep-{fill_date.strftime('%Y-%m')}"
            ts = datetime.combine(fill_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))

            print(f"    Buy: {fill_shares}x ${fill_price:.2f} am {fill_date}")

            if not dry_run:
                _ensure_run(conn, run_id)
                conn.execute(
                    """
                    INSERT INTO trades (
                        run_id, runtime_name, is_live, broker, ts, date,
                        strategy_key, symbol, asset_class, side,
                        qty, price, value, fees, trade_ref
                    ) VALUES (
                        %(run_id)s, 'eule-ep', true, 'IBKR', %(ts)s, %(date)s,
                        %(strategy_key)s, %(symbol)s, 'Stock', 'buy',
                        %(qty)s, %(price)s, %(value)s, 0, %(trade_ref)s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    {
                        "run_id": run_id,
                        "ts": ts,
                        "date": fill_date,
                        "strategy_key": ep.setup_type or "ep-swing",
                        "symbol": ep.ticker,
                        "qty": fill_shares,
                        "price": fill_price,
                        "value": fill_price * fill_shares,
                        "trade_ref": ep.id,
                    },
                )

        # Exit-Trade (sell) wenn geschlossen
        if closed.get("shares") and closed.get("avgPrice"):
            close_date = date.fromisoformat(closed["date"]) if closed.get("date") else date.today()
            close_price = closed["avgPrice"]
            close_shares = closed["shares"]
            run_id = f"ep-{close_date.strftime('%Y-%m')}"
            ts = datetime.combine(close_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))

            print(f"    Sell: {close_shares}x ${close_price:.2f} am {close_date}")

            if not dry_run:
                _ensure_run(conn, run_id)
                conn.execute(
                    """
                    INSERT INTO trades (
                        run_id, runtime_name, is_live, broker, ts, date,
                        strategy_key, symbol, asset_class, side,
                        qty, price, value, fees, trade_ref
                    ) VALUES (
                        %(run_id)s, 'eule-ep', true, 'IBKR', %(ts)s, %(date)s,
                        %(strategy_key)s, %(symbol)s, 'Stock', 'sell',
                        %(qty)s, %(price)s, %(value)s, 0, %(trade_ref)s
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    {
                        "run_id": run_id,
                        "ts": ts,
                        "date": close_date,
                        "strategy_key": ep.setup_type or "ep-swing",
                        "symbol": ep.ticker,
                        "qty": close_shares,
                        "price": close_price,
                        "value": close_price * close_shares,
                        "trade_ref": ep.id,
                    },
                )

    conn.close()
    mode = "DRY RUN" if dry_run else "DONE"
    print(f"\n{mode}: {len(trades)} Pipeline-Eintraege migriert.")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    migrate(dry_run=dry_run)
