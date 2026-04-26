"""Steuer-Report fuer den Steuerberater.

Trennt zwischen:
- Kapitaleinkuenfte pro Holder (50:50, fuer Steuererklaerung)
- Honorar an Operator (Taetigkeitsverguetung, Einkuenfte aus selbstaendiger Arbeit nach §18 EStG)

Hinweis: B kann das gezahlte Honorar NICHT als Werbungskosten abziehen
(§20 Abs. 9 EStG, Werbungskosten-Pauschale via Sparer-Pauschbetrag).
Diese Doppelbesteuerung ist im Modell akzeptiert.
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
    capital_income: float       # Kapitaleinkuenfte (50% des Trade-PnLs, fuer Anlage KAP)
    self_employment: float      # Honorar (nur Operator, fuer Anlage S)
    expenses_share: float       # Anteilige externe Kosten (Info-only, nicht abzugsfaehig)

    def to_dict(self) -> dict:
        return {
            "holder_id": self.holder_id,
            "holder_name": self.holder_name,
            "capital_income": round(self.capital_income, 2),
            "self_employment": round(self.self_employment, 2),
            "expenses_share": round(self.expenses_share, 2),
        }


def tax_report(
    roundtrips: list[Roundtrip],
    cfg: AccountingConfig,
    expenses_total: float,
    year: int | None = None,
) -> list[TaxLine]:
    """Erzeugt eine Zeile pro Holder mit Kapitaleinkuenften und ggf. Honorar."""
    if year is not None:
        roundtrips = [r for r in roundtrips if r.exit_date.year == year]

    cap_income = {h.id: 0.0 for h in cfg.holders}
    honorar = {h.id: 0.0 for h in cfg.holders}

    operator = cfg.operator
    other = next(h.id for h in cfg.holders if h.id != operator)

    for rt in roundtrips:
        alloc = allocate_pnl(rt.pnl, cfg)
        cap_income[operator] += alloc.capital_share_operator
        cap_income[other] += alloc.capital_share_other
        honorar[operator] += alloc.performance_fee  # nur Operator bekommt das

    return [
        TaxLine(
            holder_id=h.id,
            holder_name=h.name,
            capital_income=cap_income[h.id],
            self_employment=honorar[h.id],
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
