"""Steuer-Report fuer den Steuerberater.

Symmetrische Verteilungs-Logik (60:40): alle Trade-Ergebnisse werden 60:40
zwischen Operator und Other aufgeteilt — Gewinne wie Verluste. Damit gibt es
KEINEN Honorar-Anteil mehr; alles ist Kapitaleinkunft (§20 EStG, Anlage KAP).

Externe Aufwendungen werden nach reinem capital_share aufgeteilt und sind
information-only — bei Kapitaleinkuenften nicht als Werbungskosten abziehbar
(§20 Abs. 9 EStG, Sparer-Pauschbetrag deckt das ab).
"""

from dataclasses import dataclass
from datetime import date

from eule.accounting.allocator import allocate_pnl
from eule.accounting.config import AccountingConfig
from eule.models import Roundtrip


@dataclass(frozen=True)
class TaxLine:
    holder_id: str
    holder_name: str
    capital_income: float       # 60:40-Anteil am Trading-Ergebnis (Anlage KAP)
    expenses_share: float       # Anteilige externe Kosten (info-only)

    def to_dict(self) -> dict:
        return {
            "holder_id": self.holder_id,
            "holder_name": self.holder_name,
            "capital_income": round(self.capital_income, 2),
            "expenses_share": round(self.expenses_share, 2),
        }


def tax_report(
    roundtrips: list[Roundtrip],
    cfg: AccountingConfig,
    expenses_total: float,
    year: int | None = None,
) -> list[TaxLine]:
    """Erzeugt eine Zeile pro Holder mit Kapitaleinkuenften."""
    if year is not None:
        roundtrips = [r for r in roundtrips if r.exit_date.year == year]

    cap_income = {h.id: 0.0 for h in cfg.holders}
    operator = cfg.operator
    other = next(h.id for h in cfg.holders if h.id != operator)

    for rt in roundtrips:
        alloc = allocate_pnl(rt.pnl, cfg)
        cap_income[operator] += alloc.operator_share
        cap_income[other] += alloc.other_share

    return [
        TaxLine(
            holder_id=h.id,
            holder_name=h.name,
            capital_income=cap_income[h.id],
            expenses_share=expenses_total * h.capital_share,
        )
        for h in cfg.holders
    ]


def fiscal_year_range(year: int, cfg: AccountingConfig) -> tuple[date, date]:
    """Gibt Start- und End-Datum eines Geschaeftsjahres zurueck.
    Aktuell nur Kalenderjahr unterstuetzt (fiscal_year_start = '01-01').
    """
    if cfg.fiscal_year_start != "01-01":
        raise NotImplementedError(
            f"Nur Kalenderjahr unterstuetzt, nicht fiscal_year_start={cfg.fiscal_year_start}"
        )
    return date(year, 1, 1), date(year, 12, 31)
