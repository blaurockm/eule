"""Kontenrahmen fuer GbR-Buchhaltung."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    code: str
    type: str  # "asset", "equity", "revenue", "expense", "draw"
    name: str


CAPITAL_A = Account("0100", "equity", "Kapital A")
CAPITAL_B = Account("0110", "equity", "Kapital B")
BROKER = Account("1200", "asset", "Verrechnung Broker (real2-ibkr)")
TRADING_GAINS = Account("4000", "revenue", "Trading-Gewinne (netto, realisiert)")
TRADING_LOSSES = Account("5000", "expense", "Trading-Verluste (netto, realisiert)")
EXTERNAL_EXPENSES = Account("6000", "expense", "Externe Aufwendungen")
DRAW_A = Account("9000", "draw", "Privatentnahme A")
DRAW_B = Account("9010", "draw", "Privatentnahme B")


ALL_ACCOUNTS: list[Account] = [
    CAPITAL_A,
    CAPITAL_B,
    BROKER,
    TRADING_GAINS,
    TRADING_LOSSES,
    EXTERNAL_EXPENSES,
    DRAW_A,
    DRAW_B,
]


CAPITAL_BY_HOLDER: dict[str, Account] = {"A": CAPITAL_A, "B": CAPITAL_B}
DRAW_BY_HOLDER: dict[str, Account] = {"A": DRAW_A, "B": DRAW_B}


def by_code(code: str) -> Account:
    for a in ALL_ACCOUNTS:
        if a.code == code:
            return a
    raise KeyError(f"Unbekanntes Konto: {code}")
