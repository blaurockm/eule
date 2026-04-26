"""Export: balances.json fuer Vercel-App, CSV-Reports fuer Steuerberater."""

import csv
import json
from datetime import datetime
from pathlib import Path

import yaml

from eule.accounting.config import AccountingConfig, AccountingConfigError, tradinggbr_dir
from eule.accounting.models import AccountBalance, HolderBalance, Posting
from eule.accounting.tax import TaxLine


def load_tokens(path: Path | None = None) -> dict[str, str]:
    """Liest tokens.yaml und gibt Dict {token: holder_id} zurueck."""
    if path is None:
        path = tradinggbr_dir() / "tokens.yaml"
    path = path.expanduser()

    if not path.exists():
        raise AccountingConfigError(
            f"tokens.yaml nicht gefunden: {path}\n"
            f"Lege sie an mit:\n"
            f"tokens:\n"
            f"  - {{ holder: A, token: '<32+ chars> '}}\n"
            f"  - {{ holder: B, token: '<32+ chars> '}}"
        )

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    out: dict[str, str] = {}
    for entry in raw.get("tokens") or []:
        out[str(entry["token"])] = str(entry["holder"])
    return out


def _display_symbol(symbol: str) -> str:
    """Schneidet Note-Anhang aus dem Symbol ab (manual_trades fuegt '(note)' an)."""
    return symbol.split(" (")[0]


def _recent_trades(roundtrips, limit: int = 3) -> list[dict]:
    """Liefert die letzten N Roundtrips (chronologisch nach exit_date) als Dict-Liste."""
    sorted_rts = sorted(roundtrips, key=lambda r: r.exit_ts, reverse=True)[:limit]
    return [
        {
            "date": r.exit_date.isoformat(),
            "symbol": _display_symbol(r.symbol),
            "pnl_eur": round(r.pnl, 2),
        }
        for r in sorted_rts
    ]


def write_balances_json(
    balances: dict[str, HolderBalance],
    cfg: AccountingConfig,
    target_path: Path,
    roundtrips=None,
) -> None:
    """Schreibt balances.json fuer die Vercel-App.

    Pro Token: nur die Broker-Sicht des Holders + die letzten 3 Trades global.
    """
    tokens = load_tokens()
    recent = _recent_trades(roundtrips or [])

    def _r(v: float) -> float:
        # Vermeidet -0.0 in JSON-Output durch Round-trip-Mathematik
        x = round(v, 2)
        return 0.0 if x == 0.0 else x

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "tokens": {},
    }
    for token, holder_id in tokens.items():
        if holder_id not in balances:
            continue
        b = balances[holder_id]
        payload["tokens"][token] = {
            "holder_id": b.holder_id,
            "name": b.name,
            "balance_broker": _r(b.balance_broker),
            "balance_giro": _r(b.balance_giro),
            "capital": _r(b.capital),
            "allocated_pnl": _r(b.allocated_pnl),
            "allocated_expenses": _r(b.allocated_expenses),
            "as_of": b.as_of.isoformat(),
            "currency": cfg.base_currency,
            "recent_trades": recent,
        }

    target_path = target_path.expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)


def write_journal_csv(postings: list[Posting], target_path: Path) -> None:
    """Schreibt das Buchungsjournal als CSV fuer den Steuerberater."""
    target_path = target_path.expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            ["Datum", "Soll", "Haben", "Betrag", "Beschreibung", "Quelle", "Referenz"]
        )
        for p in postings:
            writer.writerow(
                [
                    p.date.isoformat(),
                    p.debit,
                    p.credit,
                    f"{p.amount_eur:.2f}",
                    p.description,
                    p.source,
                    p.ref or "",
                ]
            )


def write_ledger_csv(balances: dict[str, AccountBalance], target_path: Path) -> None:
    """Schreibt das Hauptbuch als CSV (eine Zeile pro Konto)."""
    target_path = target_path.expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Konto", "Bezeichnung", "Typ", "Soll", "Haben", "Saldo"])
        for code in sorted(balances.keys()):
            b = balances[code]
            writer.writerow(
                [
                    b.code,
                    b.name,
                    b.type,
                    f"{b.debit_total:.2f}",
                    f"{b.credit_total:.2f}",
                    f"{b.balance:.2f}",
                ]
            )


def write_tax_csv(lines: list[TaxLine], target_path: Path) -> None:
    """Schreibt den Steuer-Report als CSV."""
    target_path = target_path.expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "w", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
                "Holder",
                "Name",
                "Kapitaleinkuenfte (Anlage KAP)",
                "Honorar/Selbstaendig (Anlage S)",
                "Aufwandsanteil (Info)",
            ]
        )
        for ln in lines:
            writer.writerow(
                [
                    ln.holder_id,
                    ln.holder_name,
                    f"{ln.capital_income:.2f}",
                    f"{ln.self_employment:.2f}",
                    f"{ln.expenses_share:.2f}",
                ]
            )
