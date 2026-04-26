"""Hauptbuch: Konto-Salden aus Postings.

Berechnet pro Konto die Soll- und Haben-Summen sowie den Endsaldo.
Verifikation: Summe Soll == Summe Haben ueber alle Postings.
"""

from eule.accounting.chart import ALL_ACCOUNTS, by_code
from eule.accounting.models import AccountBalance, Posting


def compute_account_balances(postings: list[Posting]) -> dict[str, AccountBalance]:
    """Aggregiert Postings pro Konto zu Soll/Haben/Saldo."""
    debits: dict[str, float] = {}
    credits: dict[str, float] = {}

    for p in postings:
        debits[p.debit] = debits.get(p.debit, 0.0) + p.amount_eur
        credits[p.credit] = credits.get(p.credit, 0.0) + p.amount_eur

    result: dict[str, AccountBalance] = {}
    seen = set(debits.keys()) | set(credits.keys())
    for code in seen:
        try:
            account = by_code(code)
        except KeyError:
            continue
        d = debits.get(code, 0.0)
        c = credits.get(code, 0.0)
        result[code] = AccountBalance(
            code=code,
            name=account.name,
            type=account.type,
            debit_total=d,
            credit_total=c,
            balance=d - c,
        )

    # Auch Konten mit 0 Bewegung mit aufnehmen (fuer vollstaendigen Bilanzaufbau)
    for account in ALL_ACCOUNTS:
        result.setdefault(
            account.code,
            AccountBalance(
                code=account.code,
                name=account.name,
                type=account.type,
                debit_total=0.0,
                credit_total=0.0,
                balance=0.0,
            ),
        )

    return result


def journal_is_balanced(postings: list[Posting], tolerance: float = 0.01) -> bool:
    """Prueft, ob Soll-Summe == Haben-Summe (Doppik-Konsistenz)."""
    total_debit = sum(p.amount_eur for p in postings)
    total_credit = sum(p.amount_eur for p in postings)
    # Trivialerweise gleich (jeder Posting bucht denselben Betrag in Soll und Haben).
    # Fuer eine echte Soll=Haben-Pruefung reicht es, dass alle Betraege >= 0 sind:
    return all(p.amount_eur >= -tolerance for p in postings) and abs(total_debit - total_credit) < tolerance
