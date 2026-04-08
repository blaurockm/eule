"""
EP Trades — Lesen und Anzeigen von EP-Positionen und Watchlist.

Liest aus der ep_pipeline-Tabelle in PostgreSQL.
"""

from eule.db import get_db_connection
from eule.ep.db import EPPipeline, list_pipeline


def _default_conn():
    return get_db_connection("real-ibkr")


def load_trades() -> list[EPPipeline]:
    """Alle EP-Pipeline-Eintraege laden (nicht invalid)."""
    conn = _default_conn()
    try:
        return list_pipeline(conn, status_filter=["idea", "watch", "ordered", "open", "partial", "closed"])
    finally:
        conn.close()


def get_active_trades() -> list[EPPipeline]:
    """Nur aktive Trades (open, partial, ordered)."""
    conn = _default_conn()
    try:
        return list_pipeline(conn, status_filter=["open", "partial", "ordered"])
    finally:
        conn.close()


def get_watchlist() -> list[EPPipeline]:
    """Nur Watchlist-Eintraege (watch, idea)."""
    conn = _default_conn()
    try:
        return list_pipeline(conn, status_filter=["watch", "idea"])
    finally:
        conn.close()


def _get_filled_data(pipeline_id: str) -> tuple[int, float]:
    """Filled shares + avg price aus trades-Tabelle holen."""
    conn = _default_conn()
    try:
        row = conn.execute(
            "SELECT COALESCE(SUM(qty), 0), "
            "CASE WHEN SUM(qty) > 0 THEN SUM(value) / SUM(qty) ELSE 0 END "
            "FROM trades WHERE runtime_name = 'eule-ep' AND trade_ref = %s AND side = 'buy'",
            (pipeline_id,),
        ).fetchone()
        return int(row[0]), float(row[1])
    finally:
        conn.close()


def morning_brief() -> str:
    """Pre-Market Brief: offene Positionen + Watchlist."""
    active = get_active_trades()
    watch = get_watchlist()

    lines = ["EP Morning Brief", "=" * 40, ""]

    if active:
        lines.append(f"OFFENE POSITIONEN ({len(active)})")
        lines.append("-" * 30)
        for t in active:
            filled_shares, filled_price = _get_filled_data(t.id)
            lines.append(f"  {t.ticker} [{t.status}]")
            lines.append(f"    Entry: ${filled_price:.2f} x {filled_shares}")
            lines.append(f"    Stop: ${t.stop_plan:.2f} | Risk: ${t.risk_total:.0f}")
            lines.append(f"    1R: ${t.target_r1:.2f} | 2R: ${t.target_r2:.2f}")
            lines.append("")
    else:
        lines.append("Keine offenen EP-Positionen.")
        lines.append("")

    if watch:
        lines.append(f"WATCHLIST ({len(watch)})")
        lines.append("-" * 30)
        for t in watch:
            lines.append(f"  {t.ticker} [{t.setup_type}]")
            lines.append(f"    Entry: ${t.entry_plan:.2f} | Stop: ${t.stop_plan:.2f}")
            lines.append(f"    Shares: {t.planned_shares} | Risk: ${t.risk_total:.0f}")
            if t.notes:
                lines.append(f"    Note: {t.notes[-1]}")
            lines.append("")

    return "\n".join(lines)
