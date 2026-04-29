"""Loader fuer tradingGbr/cash.yaml — Einlagen, Entnahmen, externe Kosten."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from eule.accounting.config import AccountingConfigError, tradinggbr_dir


@dataclass(frozen=True)
class CashDeposit:
    date: date
    holder: str           # "A" oder "B"
    amount_eur: float
    note: str = ""


@dataclass(frozen=True)
class CashWithdrawal:
    date: date
    holder: str
    amount_eur: float
    note: str = ""


@dataclass(frozen=True)
class CashExpense:
    date: date
    amount_eur: float
    note: str = ""
    # Wo wurde gezahlt?
    #   "giro"   = vom Giro-Referenzkonto (Default — Geld muss vorher per Transfer dort sein)
    #   "broker" = direkt vom Broker-Konto (z.B. IBKR-Datenfeed-Gebuehren)
    paid_from: str = "giro"


@dataclass(frozen=True)
class CashTransfer:
    """Cash-Bewegung zwischen Giro und Broker (oder umgekehrt). Keine Holder-Bewegung."""
    date: date
    from_account: str    # "broker" oder "giro"
    to_account: str      # "broker" oder "giro"
    amount_eur: float
    note: str = ""


@dataclass(frozen=True)
class CashLedger:
    deposits: list[CashDeposit] = field(default_factory=list)
    withdrawals: list[CashWithdrawal] = field(default_factory=list)
    expenses: list[CashExpense] = field(default_factory=list)
    transfers: list[CashTransfer] = field(default_factory=list)


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def load_cash(path: Path | None = None) -> CashLedger:
    """Laedt cash.yaml. Fehlt die Datei, gibt leeres Ledger zurueck."""
    if path is None:
        path = tradinggbr_dir() / "cash.yaml"
    path = path.expanduser()

    if not path.exists():
        return CashLedger(deposits=[], withdrawals=[], expenses=[], transfers=[])

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    try:
        deposits = [
            CashDeposit(
                date=_parse_date(e["date"]),
                holder=str(e["holder"]),
                amount_eur=float(e["amount_eur"]),
                note=str(e.get("note", "")),
            )
            for e in raw.get("deposits") or []
        ]
        withdrawals = [
            CashWithdrawal(
                date=_parse_date(e["date"]),
                holder=str(e["holder"]),
                amount_eur=float(e["amount_eur"]),
                note=str(e.get("note", "")),
            )
            for e in raw.get("withdrawals") or []
        ]
        expenses = []
        for e in raw.get("expenses") or []:
            paid_from = str(e.get("paid_from", "giro")).lower()
            if paid_from not in ("giro", "broker"):
                raise AccountingConfigError(
                    f"expenses[{e}].paid_from muss 'giro' oder 'broker' sein, war '{paid_from}'"
                )
            expenses.append(
                CashExpense(
                    date=_parse_date(e["date"]),
                    amount_eur=float(e["amount_eur"]),
                    note=str(e.get("note", "")),
                    paid_from=paid_from,
                )
            )
        transfers = []
        for t in raw.get("transfers") or []:
            from_acc = str(t.get("from", "")).lower()
            to_acc = str(t.get("to", "")).lower()
            valid = {"broker", "giro"}
            if from_acc not in valid or to_acc not in valid:
                raise AccountingConfigError(
                    f"transfer.from/to muss broker|giro sein: {t}"
                )
            if from_acc == to_acc:
                raise AccountingConfigError(f"transfer.from == transfer.to: {t}")
            transfers.append(
                CashTransfer(
                    date=_parse_date(t["date"]),
                    from_account=from_acc,
                    to_account=to_acc,
                    amount_eur=float(t["amount_eur"]),
                    note=str(t.get("note", "")),
                )
            )
    except (KeyError, ValueError) as e:
        raise AccountingConfigError(f"Fehler beim Parsen von {path}: {e}") from e

    return CashLedger(
        deposits=deposits,
        withdrawals=withdrawals,
        expenses=expenses,
        transfers=transfers,
    )


def filter_out_broker_expenses(cash: CashLedger) -> CashLedger:
    """Entfernt alle expenses mit paid_from=broker.

    Use-Case: SoF-basierte Pipeline. Die broker-Expenses (IBKR-Cash-
    Adjustments) kommen dort direkt aus dem SoF und wuerden sonst
    doppelt gezaehlt — einmal als IBKR-Adjustment-Block in cash.yaml,
    einmal als SoF-Fee.
    """
    return CashLedger(
        deposits=cash.deposits,
        withdrawals=cash.withdrawals,
        expenses=[e for e in cash.expenses if e.paid_from != "broker"],
        transfers=cash.transfers,
    )
