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
    GIRO,
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
        # Einlagen kommen von aussen aufs Giro (nicht direkt aufs Broker-Konto).
        postings.append(
            Posting(
                date=d.date,
                debit=GIRO.code,
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
        # Privatentnahme kommt vom Giro (nicht direkt vom Broker-Konto).
        postings.append(
            Posting(
                date=w.date,
                debit=draw.code,
                credit=GIRO.code,
                amount_eur=w.amount_eur,
                description=f"Entnahme {w.holder}: {w.note}".strip(": "),
                source="withdrawal",
                ref=w.holder,
            )
        )
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

    # Cash-Transfers Broker <-> Giro (keine Holder-Bewegung)
    account_map = {"broker": BROKER, "giro": GIRO}
    for tr in cash.transfers:
        src = account_map[tr.from_account]
        dst = account_map[tr.to_account]
        postings.append(
            Posting(
                date=tr.date,
                debit=dst.code,
                credit=src.code,
                amount_eur=tr.amount_eur,
                description=f"Transfer {tr.from_account}->{tr.to_account}: {tr.note}".strip(": "),
                source="transfer",
                ref=tr.note,
            )
        )

    for ex in cash.expenses:
        # paid_from=giro:   6000 an 1100  (Geld muss vorher per Transfer dort sein)
        # paid_from=broker: 6000 an 1200  (z.B. IBKR-Datenfeed-Gebuehren direkt)
        # Negatives amount_eur (Storno einer fruheren Buchung) kehrt Soll/Haben um.
        src_account = GIRO if ex.paid_from == "giro" else BROKER
        if ex.amount_eur >= 0:
            ex_debit, ex_credit = EXTERNAL_EXPENSES.code, src_account.code
            label = "Aufwand"
        else:
            ex_debit, ex_credit = src_account.code, EXTERNAL_EXPENSES.code
            label = "Aufwand-Storno"
        postings.append(
            Posting(
                date=ex.date,
                debit=ex_debit,
                credit=ex_credit,
                amount_eur=abs(ex.amount_eur),
                description=f"{label} ({ex.paid_from}): {ex.note}".strip(": "),
                source="expense",
                ref=ex.note,
            )
        )

        # Anteilige Belastung/Gutschrift der Holder-Kapitalkonten
        for holder_id, share in allocate_expense(ex.amount_eur, cfg).items():
            if share >= 0:
                hd, hc = CAPITAL_BY_HOLDER[holder_id].code, EXTERNAL_EXPENSES.code
                hlabel = "Aufwandsanteil"
            else:
                hd, hc = EXTERNAL_EXPENSES.code, CAPITAL_BY_HOLDER[holder_id].code
                hlabel = "Storno-Anteil"
            postings.append(
                Posting(
                    date=ex.date,
                    debit=hd,
                    credit=hc,
                    amount_eur=abs(share),
                    description=f"{hlabel} {holder_id}: {ex.note}".strip(": "),
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
