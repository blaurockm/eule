"""
EP Pipeline DB-Layer — CRUD fuer ep_pipeline + Trade-Eintraege in trades.

Alle DB-Operationen fuer den EP-Workflow: Screening → Watch → Order → Fill → Close.
Fills und Exits werden zusaetzlich als normale Trades in die trades-Tabelle geschrieben.
"""

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

import psycopg


@dataclass
class EPPipeline:
    """Ein EP-Pipeline-Eintrag (Idee bis abgeschlossen)."""

    id: str
    ticker: str
    status: str  # idea, watch, ordered, open, partial, closed, invalid
    setup_type: str = ""
    catalyst: str = ""
    entry_plan: float = 0.0
    stop_plan: float = 0.0
    risk_per_share: float = 0.0
    planned_shares: int = 0
    target_r1: float = 0.0
    target_r2: float = 0.0
    target_r3: float = 0.0
    broker_account: str = ""
    notes: list[str] = field(default_factory=list)
    decision_log: list[dict] = field(default_factory=list)
    source: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ("open", "partial", "ordered")

    @property
    def is_watch(self) -> bool:
        return self.status in ("watch", "idea")

    @property
    def risk_total(self) -> float:
        return self.risk_per_share * self.planned_shares


def _row_to_pipeline(row: tuple, columns: list[str]) -> EPPipeline:
    """DB-Row in EPPipeline umwandeln."""
    d = dict(zip(columns, row))
    return EPPipeline(
        id=d["id"],
        ticker=d["ticker"],
        status=d["status"],
        setup_type=d.get("setup_type") or "",
        catalyst=d.get("catalyst") or "",
        entry_plan=d.get("entry_plan") or 0.0,
        stop_plan=d.get("stop_plan") or 0.0,
        risk_per_share=d.get("risk_per_share") or 0.0,
        planned_shares=d.get("planned_shares") or 0,
        target_r1=d.get("target_r1") or 0.0,
        target_r2=d.get("target_r2") or 0.0,
        target_r3=d.get("target_r3") or 0.0,
        broker_account=d.get("broker_account") or "",
        notes=d.get("notes") or [],
        decision_log=d.get("decision_log") or [],
        source=d.get("source"),
        created_at=d.get("created_at"),
        updated_at=d.get("updated_at"),
    )


_COLUMNS = [
    "id", "ticker", "status", "setup_type", "catalyst",
    "entry_plan", "stop_plan", "risk_per_share", "planned_shares",
    "target_r1", "target_r2", "target_r3", "broker_account",
    "notes", "decision_log", "source", "created_at", "updated_at",
]

_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM ep_pipeline"


def list_pipeline(
    conn: psycopg.Connection,
    status_filter: list[str] | None = None,
) -> list[EPPipeline]:
    """Pipeline-Eintraege laden, optional nach Status gefiltert."""
    if status_filter:
        placeholders = ", ".join(["%s"] * len(status_filter))
        sql = f"{_SELECT} WHERE status IN ({placeholders}) ORDER BY created_at DESC"
        cur = conn.execute(sql, status_filter)
    else:
        cur = conn.execute(f"{_SELECT} ORDER BY created_at DESC")
    return [_row_to_pipeline(row, _COLUMNS) for row in cur.fetchall()]


def get_pipeline(conn: psycopg.Connection, pipeline_id: str) -> EPPipeline | None:
    """Einzelnen Pipeline-Eintrag laden."""
    cur = conn.execute(f"{_SELECT} WHERE id = %s", (pipeline_id,))
    row = cur.fetchone()
    if not row:
        return None
    return _row_to_pipeline(row, _COLUMNS)


def upsert_pipeline(conn: psycopg.Connection, entry: EPPipeline) -> None:
    """Pipeline-Eintrag einfuegen oder aktualisieren."""
    conn.execute(
        """
        INSERT INTO ep_pipeline (
            id, ticker, status, setup_type, catalyst,
            entry_plan, stop_plan, risk_per_share, planned_shares,
            target_r1, target_r2, target_r3, broker_account,
            notes, decision_log, source, created_at, updated_at
        ) VALUES (
            %(id)s, %(ticker)s, %(status)s, %(setup_type)s, %(catalyst)s,
            %(entry_plan)s, %(stop_plan)s, %(risk_per_share)s, %(planned_shares)s,
            %(target_r1)s, %(target_r2)s, %(target_r3)s, %(broker_account)s,
            %(notes)s, %(decision_log)s, %(source)s, %(created_at)s, %(updated_at)s
        )
        ON CONFLICT (id) DO UPDATE SET
            ticker = EXCLUDED.ticker,
            status = EXCLUDED.status,
            setup_type = EXCLUDED.setup_type,
            catalyst = EXCLUDED.catalyst,
            entry_plan = EXCLUDED.entry_plan,
            stop_plan = EXCLUDED.stop_plan,
            risk_per_share = EXCLUDED.risk_per_share,
            planned_shares = EXCLUDED.planned_shares,
            target_r1 = EXCLUDED.target_r1,
            target_r2 = EXCLUDED.target_r2,
            target_r3 = EXCLUDED.target_r3,
            broker_account = EXCLUDED.broker_account,
            notes = EXCLUDED.notes,
            decision_log = EXCLUDED.decision_log,
            source = EXCLUDED.source,
            updated_at = EXCLUDED.updated_at
        """,
        {
            "id": entry.id,
            "ticker": entry.ticker,
            "status": entry.status,
            "setup_type": entry.setup_type or None,
            "catalyst": entry.catalyst or None,
            "entry_plan": entry.entry_plan or None,
            "stop_plan": entry.stop_plan or None,
            "risk_per_share": entry.risk_per_share or None,
            "planned_shares": entry.planned_shares or None,
            "target_r1": entry.target_r1 or None,
            "target_r2": entry.target_r2 or None,
            "target_r3": entry.target_r3 or None,
            "broker_account": entry.broker_account or None,
            "notes": json.dumps(entry.notes),
            "decision_log": json.dumps(entry.decision_log),
            "source": json.dumps(entry.source) if entry.source else None,
            "created_at": entry.created_at or datetime.now(ZoneInfo("UTC")),
            "updated_at": datetime.now(ZoneInfo("UTC")),
        },
    )


def _ensure_run(conn: psycopg.Connection, run_id: str) -> None:
    """Run-Eintrag anlegen falls er nicht existiert (FK-Constraint auf trades)."""
    conn.execute(
        """
        INSERT INTO runs (run_id, runtime_name, is_live, broker, started_at)
        VALUES (%s, 'eule-ep', true, 'manual', now())
        ON CONFLICT (run_id) DO NOTHING
        """,
        (run_id,),
    )


def update_status(conn: psycopg.Connection, pipeline_id: str, new_status: str) -> None:
    """Status eines Pipeline-Eintrags aendern."""
    conn.execute(
        "UPDATE ep_pipeline SET status = %s, updated_at = now() WHERE id = %s",
        (new_status, pipeline_id),
    )


def record_fill(
    conn: psycopg.Connection,
    pipeline_id: str,
    fill_date: date,
    fill_price: float,
    fill_shares: int,
    broker: str = "IBKR",
    env: str = "real-ibkr",
) -> None:
    """Fill erfassen: Trade in trades-Tabelle schreiben + Pipeline-Status aktualisieren."""
    entry = get_pipeline(conn, pipeline_id)
    if not entry:
        raise ValueError(f"Pipeline-Eintrag '{pipeline_id}' nicht gefunden")

    run_id = f"ep-{fill_date.strftime('%Y-%m')}"
    _ensure_run(conn, run_id)
    ts = datetime.combine(fill_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
    value = fill_price * fill_shares

    conn.execute(
        """
        INSERT INTO trades (
            run_id, runtime_name, is_live, broker, ts, date,
            strategy_key, symbol, asset_class, side,
            qty, price, value, fees, trade_ref
        ) VALUES (
            %(run_id)s, 'eule-ep', true, %(broker)s, %(ts)s, %(date)s,
            %(strategy_key)s, %(symbol)s, 'Stock', 'buy',
            %(qty)s, %(price)s, %(value)s, 0, %(trade_ref)s
        )
        """,
        {
            "run_id": run_id,
            "broker": broker,
            "ts": ts,
            "date": fill_date,
            "strategy_key": entry.setup_type or "ep-swing",
            "symbol": entry.ticker,
            "qty": fill_shares,
            "price": fill_price,
            "value": value,
            "trade_ref": f"{pipeline_id}:buy",
        },
    )

    # Pipeline-Status: open wenn erster Fill, partial wenn es schon Fills gibt
    existing_fills = conn.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM trades "
        "WHERE runtime_name = 'eule-ep' AND trade_ref = %s",
        (f"{pipeline_id}:buy",),
    ).fetchone()[0]

    new_status = "partial" if existing_fills < entry.planned_shares else "open"
    update_status(conn, pipeline_id, new_status)


def close_pipeline(
    conn: psycopg.Connection,
    pipeline_id: str,
    exit_date: date,
    exit_price: float,
    exit_shares: int,
    reason: str = "",
    broker: str = "IBKR",
) -> None:
    """Exit erfassen: Sell-Trade in trades-Tabelle + Pipeline auf closed."""
    entry = get_pipeline(conn, pipeline_id)
    if not entry:
        raise ValueError(f"Pipeline-Eintrag '{pipeline_id}' nicht gefunden")

    run_id = f"ep-{exit_date.strftime('%Y-%m')}"
    _ensure_run(conn, run_id)
    ts = datetime.combine(exit_date, datetime.min.time(), tzinfo=ZoneInfo("America/New_York"))
    value = exit_price * exit_shares

    conn.execute(
        """
        INSERT INTO trades (
            run_id, runtime_name, is_live, broker, ts, date,
            strategy_key, symbol, asset_class, side,
            qty, price, value, fees, trade_ref
        ) VALUES (
            %(run_id)s, 'eule-ep', true, %(broker)s, %(ts)s, %(date)s,
            %(strategy_key)s, %(symbol)s, 'Stock', 'sell',
            %(qty)s, %(price)s, %(value)s, 0, %(trade_ref)s
        )
        """,
        {
            "run_id": run_id,
            "broker": broker,
            "ts": ts,
            "date": exit_date,
            "strategy_key": entry.setup_type or "ep-swing",
            "symbol": entry.ticker,
            "qty": exit_shares,
            "price": exit_price,
            "value": value,
            "trade_ref": f"{pipeline_id}:sell",
        },
    )

    # Pruefen ob alle Shares verkauft
    total_bought = conn.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM trades "
        "WHERE runtime_name = 'eule-ep' AND trade_ref = %s",
        (f"{pipeline_id}:buy",),
    ).fetchone()[0]
    total_sold = conn.execute(
        "SELECT COALESCE(SUM(qty), 0) FROM trades "
        "WHERE runtime_name = 'eule-ep' AND trade_ref = %s",
        (f"{pipeline_id}:sell",),
    ).fetchone()[0]

    if total_sold >= total_bought:
        update_status(conn, pipeline_id, "closed")
    else:
        update_status(conn, pipeline_id, "partial")
