"""Buchungs-Generator: Roundtrips + Cash → Postings (Doppik).

Buchungslogik (siehe Plan):

Gewinn-Roundtrip pnl=G > 0:
    1200 an 4000   G                      Verrechnung gewinnt
    4000 an 0?00   G * capital_share      Steuerlicher Anteil pro Holder
    0(other) an 0(operator)   G * fee_pct  Taetigkeitsverguetung intern

Verlust-Roundtrip pnl=V <= 0:
    5000 an 1200   |V|
    0?00 an 5000   |V| * capital_share

Externe Kosten K:
    6000 an 1200   K
    0?00 an 6000   K * capital_share

Einlage E von Holder H:
    1200 an 0H00   E

Entnahme W von Holder H:
    9H00 an 1200   W
    0H00 an 9H00   W      (sofort verrechnet)
"""

from eule.accounting.allocator import allocate_expense, allocate_pnl
from eule.accounting.cash import CashLedger
from eule.accounting.chart import (
    BROKER,
    CAPITAL_BY_HOLDER,
    DRAW_BY_HOLDER,
    EXTERNAL_EXPENSES,
    TRADING_GAINS,
    TRADING_LOSSES,
)
from eule.accounting.config import AccountingConfig
from eule.accounting.models import Posting
from eule.models import Roundtrip


def postings_for_roundtrip(rt: Roundtrip, cfg: AccountingConfig) -> list[Posting]:
    """Erzeugt Doppik-Buchungen fuer einen einzelnen Roundtrip."""
    pnl = rt.pnl
    ref = f"{rt.symbol}@{rt.exit_date.isoformat()}"
    operator = cfg.operator
    other = next(h.id for h in cfg.holders if h.id != operator)

    if pnl > 0:
        alloc = allocate_pnl(pnl, cfg)
        postings = [
            Posting(
                date=rt.exit_date,
                debit=BROKER.code,
                credit=TRADING_GAINS.code,
                amount_eur=pnl,
                description=f"Gewinn {rt.symbol}",
                source="trade",
                ref=ref,
            ),
            Posting(
                date=rt.exit_date,
                debit=TRADING_GAINS.code,
                credit=CAPITAL_BY_HOLDER[operator].code,
                amount_eur=alloc.capital_share_operator,
                description=f"Kapitalanteil {operator}",
                source="trade",
                ref=ref,
            ),
            Posting(
                date=rt.exit_date,
                debit=TRADING_GAINS.code,
                credit=CAPITAL_BY_HOLDER[other].code,
                amount_eur=alloc.capital_share_other,
                description=f"Kapitalanteil {other}",
                source="trade",
                ref=ref,
            ),
        ]
        if alloc.performance_fee > 0:
            postings.append(
                Posting(
                    date=rt.exit_date,
                    debit=CAPITAL_BY_HOLDER[other].code,
                    credit=CAPITAL_BY_HOLDER[operator].code,
                    amount_eur=alloc.performance_fee,
                    description=f"Taetigkeitsverguetung {other}->{operator}",
                    source="performance_fee",
                    ref=ref,
                )
            )
        return postings

    # Verlust
    abs_v = abs(pnl)
    alloc = allocate_pnl(pnl, cfg)
    postings = [
        Posting(
            date=rt.exit_date,
            debit=TRADING_LOSSES.code,
            credit=BROKER.code,
            amount_eur=abs_v,
            description=f"Verlust {rt.symbol}",
            source="trade",
            ref=ref,
        ),
        Posting(
            date=rt.exit_date,
            debit=CAPITAL_BY_HOLDER[operator].code,
            credit=TRADING_LOSSES.code,
            amount_eur=abs(alloc.capital_share_operator),
            description=f"Verlustanteil {operator}",
            source="trade",
            ref=ref,
        ),
        Posting(
            date=rt.exit_date,
            debit=CAPITAL_BY_HOLDER[other].code,
            credit=TRADING_LOSSES.code,
            amount_eur=abs(alloc.capital_share_other),
            description=f"Verlustanteil {other}",
            source="trade",
            ref=ref,
        ),
    ]
    return postings


def postings_for_cash(cash: CashLedger, cfg: AccountingConfig) -> list[Posting]:
    """Erzeugt Buchungen aus Einlagen, Entnahmen und externen Kosten."""
    postings: list[Posting] = []

    for d in cash.deposits:
        capital = CAPITAL_BY_HOLDER[d.holder]
        postings.append(
            Posting(
                date=d.date,
                debit=BROKER.code,
                credit=capital.code,
                amount_eur=d.amount_eur,
                description=f"Einlage {d.holder}: {d.note}".strip(": "),
                source="deposit",
                ref=d.holder,
            )
        )

    for w in cash.withdrawals:
        capital = CAPITAL_BY_HOLDER[w.holder]
        draw = DRAW_BY_HOLDER[w.holder]
        # Schritt 1: Auszahlung an Privat
        postings.append(
            Posting(
                date=w.date,
                debit=draw.code,
                credit=BROKER.code,
                amount_eur=w.amount_eur,
                description=f"Entnahme {w.holder}: {w.note}".strip(": "),
                source="withdrawal",
                ref=w.holder,
            )
        )
        # Schritt 2: Privat-Konto sofort gegen Kapital schliessen
        postings.append(
            Posting(
                date=w.date,
                debit=capital.code,
                credit=draw.code,
                amount_eur=w.amount_eur,
                description=f"Entnahme {w.holder} verrechnet",
                source="withdrawal",
                ref=w.holder,
            )
        )

    for ex in cash.expenses:
        # Aufwand erst aufs Kostenkonto, dann anteilig den Holdern belasten
        postings.append(
            Posting(
                date=ex.date,
                debit=EXTERNAL_EXPENSES.code,
                credit=BROKER.code,
                amount_eur=ex.amount_eur,
                description=f"Aufwand: {ex.note}".strip(": "),
                source="expense",
                ref=ex.note,
            )
        )
        for holder_id, share in allocate_expense(ex.amount_eur, cfg).items():
            postings.append(
                Posting(
                    date=ex.date,
                    debit=CAPITAL_BY_HOLDER[holder_id].code,
                    credit=EXTERNAL_EXPENSES.code,
                    amount_eur=share,
                    description=f"Aufwandsanteil {holder_id}: {ex.note}".strip(": "),
                    source="expense",
                    ref=ex.note,
                )
            )

    return postings


def build_journal(
    roundtrips: list[Roundtrip],
    cash: CashLedger,
    cfg: AccountingConfig,
) -> list[Posting]:
    """Erzeugt das vollstaendige Buchungs-Journal, chronologisch sortiert."""
    postings: list[Posting] = []
    for rt in roundtrips:
        postings.extend(postings_for_roundtrip(rt, cfg))
    postings.extend(postings_for_cash(cash, cfg))
    postings.sort(key=lambda p: (p.date, p.source))
    return postings
