"""Datenmodelle fuer die GbR-Buchhaltung."""

from dataclasses import dataclass, asdict
from datetime import date
from typing import Any


@dataclass(frozen=True)
class Posting:
    """Ein Buchungssatz (Soll an Haben).

    debit  = Konto, das belastet wird (Soll)
    credit = Konto, das entlastet wird (Haben)
    """

    date: date
    debit: str          # Konto-Code (z.B. "1200")
    credit: str         # Konto-Code (z.B. "4000")
    amount_eur: float
    description: str
    source: str         # "trade", "deposit", "withdrawal", "expense", "performance_fee"
    ref: str | None = None   # Referenz: roundtrip-Symbol, cash-Eintragsdatum, etc.

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["date"] = self.date.isoformat()
        d["amount_eur"] = round(self.amount_eur, 2)
        return d


@dataclass(frozen=True)
class HolderBalance:
    """Saldo eines Holders (berechnete Sicht fuer Vercel-App).

    `balance` ist der wirtschaftliche Gesamt-Anteil. Davon liegt anteilig
    `balance_broker` auf dem Broker-Konto (trading-aktiv) und `balance_giro`
    auf dem Giro-Referenzkonto (Reserve / unterwegs zu/von Aufwendungen).
    Aufteilung proportional zum Anteil der Konto-Salden am Gesamt-Aktivum.
    """

    holder_id: str
    name: str
    capital: float
    allocated_pnl: float
    allocated_expenses: float
    balance: float
    balance_broker: float
    balance_giro: float
    as_of: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "holder_id": self.holder_id,
            "name": self.name,
            "capital": round(self.capital, 2),
            "allocated_pnl": round(self.allocated_pnl, 2),
            "allocated_expenses": round(self.allocated_expenses, 2),
            "balance": round(self.balance, 2),
            "balance_broker": round(self.balance_broker, 2),
            "balance_giro": round(self.balance_giro, 2),
            "as_of": self.as_of.isoformat(),
        }


@dataclass(frozen=True)
class AccountBalance:
    """Saldo eines Kontos (Hauptbuch-Sicht)."""

    code: str
    name: str
    type: str
    debit_total: float
    credit_total: float
    balance: float            # debit_total - credit_total

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "type": self.type,
            "debit_total": round(self.debit_total, 2),
            "credit_total": round(self.credit_total, 2),
            "balance": round(self.balance, 2),
        }
