"""Export: balances.json fuer Vercel-App, CSV-Reports fuer Steuerberater."""

import csv
import json
from datetime import date, datetime
from pathlib import Path

import yaml

from eule.accounting.cash import CashLedger
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


def _recent_trades(roundtrips, limit: int = 3) -> list[dict]:
    """Aggregiert Roundtrips pro exit_date und gibt die letzten N Handelstage zurueck."""
    by_date: dict = {}
    for r in roundtrips:
        by_date.setdefault(r.exit_date, []).append(r)
    return [
        {
            "date": d.isoformat(),
            "count": len(by_date[d]),
            "pnl_eur": round(sum(r.pnl for r in by_date[d]), 2),
        }
        for d in sorted(by_date.keys(), reverse=True)[:limit]
    ]


def _pnl_share_pct(holder_id: str, cfg: AccountingConfig) -> float:
    """Anteil des Holders am Trading-PnL (z.B. 0.6 fuer Operator, 0.4 fuer Other)."""
    base = cfg.holder(holder_id).capital_share
    sign = 1 if holder_id == cfg.operator else -1
    return base + sign * cfg.performance_fee.pct


def _global_metrics(
    roundtrips,
    cash: CashLedger,
    balances: dict[str, HolderBalance],
) -> dict:
    """Aggregat-Sicht (gleich fuer alle Holder): Broker-Saldo, Brutto-PnL, Brutto-
    Kosten und naive CAGR auf Basis erste-Einlage->aktuelle-Equity.

    CAGR ist money-weighted-naiv (ignoriert wann spaetere Einlagen kamen). Fuer
    eine korrekte Time-Weighted Return braeuchte man Daily-Snapshots; das ist
    fuer dieses Frontend overkill.
    """
    broker_total = sum(b.balance_broker for b in balances.values())
    giro_total = sum(b.balance_giro for b in balances.values())

    total_pnl = sum(r.pnl for r in roundtrips)
    total_expenses = sum(e.amount_eur for e in cash.expenses)
    total_deposits = sum(d.amount_eur for d in cash.deposits)
    total_withdrawals = sum(w.amount_eur for w in cash.withdrawals)

    cagr: float | None = None
    if cash.deposits and total_deposits > 0:
        first_date = min(d.date for d in cash.deposits)
        years = (date.today() - first_date).days / 365.25
        equity_now = total_deposits - total_withdrawals + total_pnl - total_expenses
        if years > 0 and equity_now > 0:
            cagr = (equity_now / total_deposits) ** (1 / years) - 1

    return {
        "broker_total": round(broker_total, 2),
        "giro_total": round(giro_total, 2),
        "total_pnl": round(total_pnl, 2),
        "total_expenses": round(total_expenses, 2),
        "total_deposits": round(total_deposits, 2),
        "cagr": round(cagr, 4) if cagr is not None else None,
    }


def write_balances_json(
    balances: dict[str, HolderBalance],
    cfg: AccountingConfig,
    target_path: Path,
    roundtrips=None,
    cash: CashLedger | None = None,
) -> None:
    """Schreibt balances.json fuer die Vercel-App.

    Struktur:
      global   — fuer alle Holder gleich (Broker-Gesamt, Brutto-PnL, CAGR, ...)
      tokens   — pro Token: Holder-Anteil an PnL/Kosten (absolut + Prozent)
    """
    tokens = load_tokens()
    recent = _recent_trades(roundtrips or [])
    cash = cash if cash is not None else CashLedger()
    metrics = _global_metrics(roundtrips or [], cash, balances)

    def _r(v: float) -> float:
        # Vermeidet -0.0 in JSON-Output durch Round-trip-Mathematik
        x = round(v, 2)
        return 0.0 if x == 0.0 else x

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "global": {
            **metrics,
            "currency": cfg.base_currency,
            "recent_trades": recent,
        },
        "tokens": {},
    }
    for token, holder_id in tokens.items():
        if holder_id not in balances:
            continue
        b = balances[holder_id]
        h = cfg.holder(holder_id)
        payload["tokens"][token] = {
            "holder_id": b.holder_id,
            "name": b.name,
            "as_of": b.as_of.isoformat(),
            "pnl_share": _r(b.allocated_pnl),
            "pnl_share_pct": round(_pnl_share_pct(holder_id, cfg), 4),
            "expenses_share": _r(b.allocated_expenses),
            "expenses_share_pct": round(h.capital_share, 4),
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
                "Aufwandsanteil (Info)",
            ]
        )
        for ln in lines:
            writer.writerow(
                [
                    ln.holder_id,
                    ln.holder_name,
                    f"{ln.capital_income:.2f}",
                    f"{ln.expenses_share:.2f}",
                ]
            )
