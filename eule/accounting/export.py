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


def write_balances_json(
    balances: dict[str, HolderBalance],
    cfg: AccountingConfig,
    target_path: Path,
) -> None:
    """Schreibt balances.json fuer die Vercel-App. Pro Token nur der eigene Saldo."""
    tokens = load_tokens()
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
            "balance": round(b.balance, 2),
            "capital": round(b.capital, 2),
            "allocated_pnl": round(b.allocated_pnl, 2),
            "allocated_expenses": round(b.allocated_expenses, 2),
            "as_of": b.as_of.isoformat(),
            "currency": cfg.base_currency,
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
