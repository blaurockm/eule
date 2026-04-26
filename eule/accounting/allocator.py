"""Verteilungsregeln fuer GbR-Buchhaltung.

Asymmetrie:
- Trading-Gewinn: 50:50 Kapitaleinkunft + Performance-Fee an Operator (= 60:40 wenn Fee=10%)
- Trading-Verlust: 50:50 (keine Fee)
- Externe Kosten: 50:50 (genauer: nach capital_share)

Die Logik arbeitet pro einzelnem Roundtrip — bei einem Mix aus Gewinnen und
Verlusten loesen nur die Gewinn-Roundtrips eine Verguetung aus.
"""

from dataclasses import dataclass

from eule.accounting.config import AccountingConfig


@dataclass(frozen=True)
class PnLAllocation:
    """Aufteilung eines Roundtrip-PnLs auf die Holder."""

    operator_share: float        # was der Operator effektiv bekommt (Kapital + ggf. Fee)
    other_share: float           # was der zweite Holder bekommt
    performance_fee: float       # Anteil davon, der Honorar (kein Kapitaleinkommen) ist
    capital_share_operator: float  # Kapitaleinkunfts-Anteil Operator (fuer Steuer)
    capital_share_other: float     # Kapitaleinkunfts-Anteil andere (fuer Steuer)


def allocate_pnl(pnl: float, cfg: AccountingConfig) -> PnLAllocation:
    """Verteilt den Netto-PnL eines einzelnen Roundtrips.

    Bei pnl > 0: Performance-Fee wird zusaetzlich vom anderen Holder an Operator transferiert.
    Bei pnl <= 0: Reine Aufteilung nach capital_share, keine Fee.
    """
    operator_id = cfg.operator
    other_id = next(h.id for h in cfg.holders if h.id != operator_id)
    op_share = cfg.holder(operator_id).capital_share
    ot_share = cfg.holder(other_id).capital_share

    cap_op = pnl * op_share
    cap_ot = pnl * ot_share

    if pnl > 0:
        fee = pnl * cfg.performance_fee.pct
        return PnLAllocation(
            operator_share=cap_op + fee,
            other_share=cap_ot - fee,
            performance_fee=fee,
            capital_share_operator=cap_op,
            capital_share_other=cap_ot,
        )

    return PnLAllocation(
        operator_share=cap_op,
        other_share=cap_ot,
        performance_fee=0.0,
        capital_share_operator=cap_op,
        capital_share_other=cap_ot,
    )


def allocate_expense(amount: float, cfg: AccountingConfig) -> dict[str, float]:
    """Externe Kosten nach capital_share aufteilen."""
    return {h.id: amount * h.capital_share for h in cfg.holders}
