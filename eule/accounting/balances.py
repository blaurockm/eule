"""Berechnete Sicht: Saldo pro Holder.

Geht nicht ueber die Doppik-Buchungen, sondern direkt aus Roundtrips + Cash.
Vorteil: einfacher zu verstehen und zu testen, identisches Ergebnis.

Saldo pro Holder = Einlagen - Entnahmen
                 + sum(allocate_pnl(rt) fuer alle Roundtrips)
                 - sum(allocate_expense(K) fuer alle externen Kosten)
"""

from datetime import date

from eule.accounting.allocator import allocate_expense, allocate_pnl
from eule.accounting.cash import CashLedger
from eule.accounting.config import AccountingConfig
from eule.accounting.models import HolderBalance
from eule.models import Roundtrip


def compute_balances(
    roundtrips: list[Roundtrip],
    cash: CashLedger,
    cfg: AccountingConfig,
    as_of: date | None = None,
) -> dict[str, HolderBalance]:
    """Berechnet aktuellen Saldo pro Holder."""
    if as_of is None:
        as_of = date.today()

    operator = cfg.operator
    other = next(h.id for h in cfg.holders if h.id != operator)

    # Init
    capital = {operator: 0.0, other: 0.0}
    pnl_share = {operator: 0.0, other: 0.0}
    expense_share = {operator: 0.0, other: 0.0}

    # Cash
    for d in cash.deposits:
        capital[d.holder] += d.amount_eur
    for w in cash.withdrawals:
        capital[w.holder] -= w.amount_eur

    # Trades
    for rt in roundtrips:
        alloc = allocate_pnl(rt.pnl, cfg)
        pnl_share[operator] += alloc.operator_share
        pnl_share[other] += alloc.other_share

    # Externe Kosten
    for ex in cash.expenses:
        for holder_id, share in allocate_expense(ex.amount_eur, cfg).items():
            expense_share[holder_id] += share

    balances: dict[str, HolderBalance] = {}
    for h in cfg.holders:
        balance = capital[h.id] + pnl_share[h.id] - expense_share[h.id]
        balances[h.id] = HolderBalance(
            holder_id=h.id,
            name=h.name,
            capital=capital[h.id],
            allocated_pnl=pnl_share[h.id],
            allocated_expenses=expense_share[h.id],
            balance=balance,
            as_of=as_of,
        )

    return balances
