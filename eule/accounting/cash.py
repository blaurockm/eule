"""Loader fuer tradingGbr/cash.yaml — Einlagen, Entnahmen, externe Kosten."""

from dataclasses import dataclass
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
    # Wo wurde gezahlt? "giro" = via Giro-Referenzkonto (Default fuer manuelle Eintraege),
    # "broker" = direkt vom Broker-Konto (z.B. IBKR-Datenfeed-Gebuehren).
    paid_from: str = "giro"


@dataclass(frozen=True)
class CashLedger:
    deposits: list[CashDeposit]
    withdrawals: list[CashWithdrawal]
    expenses: list[CashExpense]


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
        return CashLedger(deposits=[], withdrawals=[], expenses=[])

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
    except (KeyError, ValueError) as e:
        raise AccountingConfigError(f"Fehler beim Parsen von {path}: {e}") from e

    return CashLedger(deposits=deposits, withdrawals=withdrawals, expenses=expenses)
