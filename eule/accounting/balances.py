"""Berechnete Sicht: Saldo pro Holder.

Saldo pro Holder = Einlagen - Entnahmen
                 + sum(allocate_pnl(rt) fuer alle Roundtrips)
                 - sum(allocate_expense(K) fuer alle externen Kosten)

Zusaetzlich: Aufteilung in Broker- und Giro-Anteil pro Holder, proportional
zum Anteil der Konto-Salden am Gesamt-Aktivum (Bilanzgleichung):
  Holder.broker_share = Holder.balance * (Broker_saldo / Total_Aktiva)
  Holder.giro_share   = Holder.balance * (Giro_saldo   / Total_Aktiva)
"""

from datetime import date

from eule.accounting.allocator import allocate_expense, allocate_pnl
from eule.accounting.cash import CashLedger
from eule.accounting.config import AccountingConfig
from eule.accounting.models import HolderBalance
from eule.models import Roundtrip


def _account_balances(
    roundtrips: list[Roundtrip],
    cash: CashLedger,
    cfg: AccountingConfig,
) -> tuple[float, float]:
    """Berechnet Soll-Saldo der Aktivakonten Broker (1200) und Giro (1100)
    direkt aus den Cash-/Trade-Daten — ohne Umweg ueber das Journal."""
    broker = 0.0
    giro = 0.0

    # Einlagen → Giro
    for d in cash.deposits:
        giro += d.amount_eur
    # Entnahmen ← Giro
    for w in cash.withdrawals:
        giro -= w.amount_eur
    # Transfers
    for tr in cash.transfers:
        if tr.from_account == "broker" and tr.to_account == "giro":
            broker -= tr.amount_eur
            giro += tr.amount_eur
        elif tr.from_account == "giro" and tr.to_account == "broker":
            giro -= tr.amount_eur
            broker += tr.amount_eur
    # Trades laufen ueber Broker
    for rt in roundtrips:
        broker += rt.pnl
    # Aufwendungen
    for ex in cash.expenses:
        if ex.paid_from == "giro":
            giro -= ex.amount_eur
        else:
            broker -= ex.amount_eur

    return broker, giro


def compute_balances(
    roundtrips: list[Roundtrip],
    cash: CashLedger,
    cfg: AccountingConfig,
    as_of: date | None = None,
) -> dict[str, HolderBalance]:
    """Berechnet aktuellen Saldo pro Holder, aufgeteilt in Broker- und Giro-Anteil."""
    if as_of is None:
        as_of = date.today()

    operator = cfg.operator
    other = next(h.id for h in cfg.holders if h.id != operator)

    capital = {operator: 0.0, other: 0.0}
    pnl_share = {operator: 0.0, other: 0.0}
    expense_share = {operator: 0.0, other: 0.0}

    for d in cash.deposits:
        capital[d.holder] += d.amount_eur
    for w in cash.withdrawals:
        capital[w.holder] -= w.amount_eur

    for rt in roundtrips:
        alloc = allocate_pnl(rt.pnl, cfg)
        pnl_share[operator] += alloc.operator_share
        pnl_share[other] += alloc.other_share

    for ex in cash.expenses:
        for holder_id, share in allocate_expense(ex.amount_eur, cfg).items():
            expense_share[holder_id] += share

    broker_total, giro_total = _account_balances(roundtrips, cash, cfg)
    total_aktiva = broker_total + giro_total

    balances: dict[str, HolderBalance] = {}
    for h in cfg.holders:
        balance = capital[h.id] + pnl_share[h.id] - expense_share[h.id]
        if total_aktiva != 0:
            broker_share = balance * (broker_total / total_aktiva)
            giro_share = balance * (giro_total / total_aktiva)
        else:
            broker_share = balance
            giro_share = 0.0
        balances[h.id] = HolderBalance(
            holder_id=h.id,
            name=h.name,
            capital=capital[h.id],
            allocated_pnl=pnl_share[h.id],
            allocated_expenses=expense_share[h.id],
            balance=balance,
            balance_broker=broker_share,
            balance_giro=giro_share,
            as_of=as_of,
        )

    return balances
