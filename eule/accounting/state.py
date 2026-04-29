"""SoF-basierter State-Loader fuer die GbR-Buchhaltung.

Liest alle CSVs in ``<tradinggbr>/sof/*.csv`` (Cache + Archive),
dedupliziert ueber (date, amount, asset_class, description) und
konvertiert in:

- ``Roundtrip``-Liste (aus den Trade-Aggregaten)
- ``CashExpense``-Liste (aus den Fee-Aggregaten — paid_from=broker)

Die manuell gepflegten Cash-Posten (deposits, withdrawals, transfers,
NICHT-broker expenses) kommen weiterhin aus ``cash.yaml``. Der
IBKR-Adjustments-Block in cash.yaml (paid_from=broker) wird
herausgefiltert, weil er aus dem SoF redundant entsteht.

Damit ist der SoF die Single Source of Truth fuer alles, was IBKR
liefert; cash.yaml nur noch fuer das, was es nicht liefert.
"""

from datetime import date, datetime, time, timezone
from pathlib import Path

from eule.accounting.cash import (
    CashExpense,
    CashLedger,
    filter_out_broker_expenses,
    load_cash,
)
from eule.accounting.fetch import sof_dir
from eule.accounting.import_sof import (
    FeeAggregate,
    TradeAggregate,
    aggregate_fees,
    aggregate_trades,
    parse_sof_files,
)
from eule.models import Roundtrip


class SofStateError(Exception):
    """Fehler beim Laden des SoF-Cache."""


def list_sof_files(directory: Path | None = None) -> list[Path]:
    """Liefert sortiert alle SoF-CSVs im Cache-Verzeichnis."""
    d = directory or sof_dir()
    return sorted(d.glob("*.csv"))


def trade_to_roundtrip(t: TradeAggregate) -> Roundtrip:
    """Wandelt ein Trade-Aggregat in einen Roundtrip-Dummy um.

    Analog zu manual_trades._to_roundtrip — die Buchhaltungspipeline
    konsumiert nur ``Roundtrip.pnl`` und das Datum.
    """
    pnl = t.pnl_eur
    ts = datetime.combine(t.posting_date, time.min, tzinfo=timezone.utc)
    return Roundtrip(
        strategy_key="sof",
        symbol=f"{t.description} ({t.asset_class})",
        asset_class=t.asset_class.lower() or "manual",
        entry_ts=ts,
        entry_date=t.posting_date,
        entry_side="sell",
        entry_qty=1.0,
        entry_price=0.0,
        entry_value=max(pnl, 0.0),
        entry_fees=0.0,
        exit_ts=ts,
        exit_date=t.posting_date,
        exit_side="buy",
        exit_qty=1.0,
        exit_price=0.0,
        exit_value=max(-pnl, 0.0),
        exit_fees=0.0,
        exit_is_expiry=False,
    )


def fee_to_expense(f: FeeAggregate) -> CashExpense:
    """Wandelt ein Fee-Aggregat in CashExpense (paid_from=broker)."""
    # FeeAggregate.netto_eur ist der Cash-Effekt (negativ = Aufwand).
    # CashExpense.amount_eur ist positiv fuer Aufwand, negativ fuer Storno.
    return CashExpense(
        date=f.posting_date,
        amount_eur=round(-f.netto_eur, 2),
        note=f"IBKR-Cash-Adjustments ({f.count} Posten)",
        paid_from="broker",
    )


def load_state_from_sof(
    *,
    sof_directory: Path | None = None,
    cash_path: Path | None = None,
) -> tuple[list[Roundtrip], CashLedger]:
    """Komplette SoF-basierte Sicht: Roundtrips + bereinigtes CashLedger.

    Returns:
        (roundtrips_from_sof, cash_ledger_without_broker_expenses)

    Raises:
        SofStateError: wenn kein SoF-File im Cache liegt.
    """
    files = list_sof_files(sof_directory)
    if not files:
        raise SofStateError(
            f"Keine SoF-CSV gefunden in {sof_directory or sof_dir()}. "
            f"Erst `eule accounting fetch` aufrufen oder Archiv ablegen."
        )

    rows = parse_sof_files(files)
    trades = aggregate_trades(rows)
    fees = aggregate_fees(rows)

    roundtrips = [trade_to_roundtrip(t) for t in trades]
    roundtrips.sort(key=lambda r: r.exit_ts)

    cash = load_cash(cash_path)
    cash = filter_out_broker_expenses(cash)
    # Die gefilterten broker-expenses werden jetzt durch die SoF-Fees ersetzt.
    sof_expenses = [fee_to_expense(f) for f in fees]
    merged = CashLedger(
        deposits=cash.deposits,
        withdrawals=cash.withdrawals,
        expenses=cash.expenses + sof_expenses,
        transfers=cash.transfers,
    )

    return roundtrips, merged


def state_summary(
    roundtrips: list[Roundtrip], cash: CashLedger
) -> dict[str, float | int]:
    """Ein paar Kennzahlen zur Plausibilitaets-Pruefung."""
    return {
        "roundtrips": len(roundtrips),
        "pnl_total": round(sum(r.pnl for r in roundtrips), 2),
        "deposits": len(cash.deposits),
        "deposits_total": round(sum(d.amount_eur for d in cash.deposits), 2),
        "withdrawals": len(cash.withdrawals),
        "withdrawals_total": round(sum(w.amount_eur for w in cash.withdrawals), 2),
        "expenses": len(cash.expenses),
        "expenses_total": round(sum(e.amount_eur for e in cash.expenses), 2),
        "transfers": len(cash.transfers),
    }
