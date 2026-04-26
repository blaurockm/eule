"""Verteilungsregeln fuer GbR-Buchhaltung — symmetrisch.

Trade-PnL wird mit asymmetrischem Schluessel verteilt, sowohl bei Gewinn als
auch bei Verlust. Mit ``capital_share = 0.5`` und ``operator_premium_pct = 0.10``:

    Operator bekommt:  capital_share * pnl + premium * pnl  = 0.6 * pnl
    Other    bekommt:  capital_share * pnl - premium * pnl  = 0.4 * pnl

D.h. Gewinne 60:40 zugunsten Operator, Verluste 60:40 zulasten Operator. Damit
ist das ``performance_fee.pct``-Feld konzeptionell kein Honorar mehr, sondern
eine **Beteiligungs-Asymmetrie** der Trading-GbR. Steuerlich: gemeinsamer
Verteilungsschluessel der Mitunternehmerschaft, alles Kapitaleinkuenfte
(§20 EStG).

Externe Kosten werden weiterhin nach reinem ``capital_share`` aufgeteilt.
"""

from dataclasses import dataclass

from eule.accounting.config import AccountingConfig


@dataclass(frozen=True)
class PnLAllocation:
    """Aufteilung eines Roundtrip-PnLs auf die Holder.

    operator_share == capital_share_operator (sind in der symmetrischen Logik
    identisch — alles ist Kapitaleinkunft). Der Bonus (= ``performance_fee``)
    ist nur informativ, falls man ihn separat ausweisen will.
    """

    operator_share: float
    other_share: float
    performance_fee: float          # Bonus fuer Operator (kann negativ sein bei Verlust)
    capital_share_operator: float   # = operator_share
    capital_share_other: float      # = other_share


def allocate_pnl(pnl: float, cfg: AccountingConfig) -> PnLAllocation:
    """Verteilt den Netto-PnL eines Roundtrips symmetrisch.

    Bei pnl > 0: Operator bekommt mehr (capital_share + premium).
    Bei pnl < 0: Operator traegt mehr (capital_share - |premium|, also negativer Bonus).
    """
    operator_id = cfg.operator
    other_id = next(h.id for h in cfg.holders if h.id != operator_id)
    op_share = cfg.holder(operator_id).capital_share
    ot_share = cfg.holder(other_id).capital_share

    cap_op = pnl * op_share
    cap_ot = pnl * ot_share
    bonus = pnl * cfg.performance_fee.pct

    op_total = cap_op + bonus
    ot_total = cap_ot - bonus

    return PnLAllocation(
        operator_share=op_total,
        other_share=ot_total,
        performance_fee=bonus,
        capital_share_operator=op_total,
        capital_share_other=ot_total,
    )


def allocate_expense(amount: float, cfg: AccountingConfig) -> dict[str, float]:
    """Externe Kosten nach capital_share aufteilen."""
    return {h.id: amount * h.capital_share for h in cfg.holders}
