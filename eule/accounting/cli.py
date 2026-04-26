"""CLI fuer eule accounting — refresh, balances, journal, ledger, tax."""

from datetime import date
from pathlib import Path

import typer

from eule.accounting.balances import compute_balances
from eule.accounting.cash import load_cash
from eule.accounting.config import (
    AccountingConfig,
    AccountingConfigError,
    load_accounting_config,
    tradinggbr_dir,
)
from eule.accounting.export import (
    write_balances_json,
    write_journal_csv,
    write_ledger_csv,
    write_tax_csv,
)
from eule.accounting.import_flex import aggregate, parse_flex_files, render_yaml
from eule.accounting.journal import build_journal
from eule.accounting.ledger import compute_account_balances, journal_is_balanced
from eule.accounting.manual_trades import load_manual_trades
from eule.accounting.tax import tax_report
from eule.bewertung.trades import detect_roundtrips, load_trades
from eule.db import get_env_info
from eule.models import Roundtrip
from eule.output import console, output_json

accounting_app = typer.Typer(
    name="accounting",
    help="GbR-Buchhaltung fuer Joint-Account real2-ibkr",
    no_args_is_help=True,
)


def _load_cfg() -> AccountingConfig:
    try:
        return load_accounting_config()
    except AccountingConfigError as e:
        console.print(f"[red]Config-Fehler:[/red] {e}")
        raise typer.Exit(1)


def _hase_trade_refs(cfg: AccountingConfig) -> set[str]:
    """Liest alle nicht-NULL trade_ref-Werte aus der Hase-DB fuer das Environment.
    Leeres Set wenn use_hase_db=false.
    """
    if not cfg.use_hase_db:
        return set()
    conn, runtime = get_env_info(cfg.env)
    try:
        trades = load_trades(conn, runtime)
    finally:
        conn.close()
    return {t.trade_ref for t in trades if t.trade_ref}


def _load_roundtrips(cfg: AccountingConfig) -> tuple[list[Roundtrip], int, int]:
    """Laedt Hase-Roundtrips + manuelle Trades fuer das Environment.

    Wenn cfg.use_hase_db=false, wird die Hase-DB nicht abgefragt — manual_trades.yaml
    ist dann die einzige Trade-Quelle.

    Returns:
        (combined_roundtrips, db_count, manual_count)
    """
    if cfg.use_hase_db:
        conn, runtime = get_env_info(cfg.env)
        try:
            trades = load_trades(conn, runtime)
        finally:
            conn.close()
        db_rts = detect_roundtrips(trades)
    else:
        db_rts = []
    manual_rts = load_manual_trades()
    combined = db_rts + manual_rts
    combined.sort(key=lambda r: r.exit_ts)
    return combined, len(db_rts), len(manual_rts)


def _resolve_path(value: str) -> Path:
    """Pfade werden relativ zum tradingGbr-Verzeichnis aufgeloest."""
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = (tradinggbr_dir() / p).resolve()
    return p


@accounting_app.command(name="refresh")
def refresh_cmd(
    format: str = typer.Option("markdown", "--format", help="markdown oder json"),
) -> None:
    """Laedt Trades + Cash, berechnet Salden, schreibt balances.json fuer Vercel-App."""
    cfg = _load_cfg()
    cash = load_cash()
    rts, db_count, manual_count = _load_roundtrips(cfg)
    balances = compute_balances(rts, cash, cfg)

    target_raw = cfg.balances_json_path
    if not target_raw:
        console.print("[red]config.yaml.output.balances_json ist leer[/red]")
        raise typer.Exit(1)

    target = _resolve_path(target_raw)
    write_balances_json(balances, cfg, target, roundtrips=rts)

    if format == "json":
        output_json(
            {
                "target": str(target),
                "balances": [b.to_dict() for b in balances.values()],
                "roundtrips_count": len(rts),
                "roundtrips_db": db_count,
                "roundtrips_manual": manual_count,
                "deposits_count": len(cash.deposits),
                "withdrawals_count": len(cash.withdrawals),
                "expenses_count": len(cash.expenses),
            }
        )
        return

    console.print(f"[green]Refresh OK[/green] -> {target}")
    console.print(
        f"  {len(rts)} Roundtrips ({db_count} DB + {manual_count} manuell) | "
        f"{len(cash.deposits)} Einlagen | {len(cash.withdrawals)} Entnahmen | "
        f"{len(cash.expenses)} Aufwendungen"
    )
    for b in balances.values():
        console.print(
            f"  {b.holder_id} ({b.name}): Saldo "
            f"{b.balance:>12,.2f} {cfg.base_currency}"
        )


@accounting_app.command(name="balances")
def balances_cmd(
    holder: str = typer.Option("", "--holder", help="A oder B (leer = alle)"),
    format: str = typer.Option("markdown", "--format", help="markdown oder json"),
) -> None:
    """Aktueller Saldo pro Holder (berechnete Sicht)."""
    cfg = _load_cfg()
    cash = load_cash()
    rts, _, _ = _load_roundtrips(cfg)
    balances = compute_balances(rts, cash, cfg)

    if holder:
        if holder not in balances:
            console.print(f"[red]Holder '{holder}' nicht in config[/red]")
            raise typer.Exit(1)
        balances = {holder: balances[holder]}

    if format == "json":
        output_json([b.to_dict() for b in balances.values()])
        return

    console.print("[bold]Salden[/bold]\n")
    for b in balances.values():
        console.print(f"[bold]{b.holder_id} — {b.name}[/bold]")
        console.print(f"  Kapital (Einlagen-Entnahmen):    {b.capital:>12,.2f}")
        console.print(f"  Anteil Trading-PnL:              {b.allocated_pnl:>12,.2f}")
        console.print(f"  Anteil externe Aufwendungen:     {b.allocated_expenses:>12,.2f}")
        console.print(f"  [bold]Saldo:[/bold]                          {b.balance:>12,.2f} {cfg.base_currency}")
        console.print(f"  Stand: {b.as_of.isoformat()}\n")


@accounting_app.command(name="journal")
def journal_cmd(
    year: int = typer.Option(0, "--year", help="Geschaeftsjahr (0 = alle)"),
    format: str = typer.Option("markdown", "--format", help="markdown, json oder csv"),
    out: str = typer.Option("", "--out", help="Pfad fuer CSV-Export (nur bei --format csv)"),
) -> None:
    """Buchungs-Journal (chronologisch, Doppik)."""
    cfg = _load_cfg()
    cash = load_cash()
    rts, _, _ = _load_roundtrips(cfg)
    postings = build_journal(rts, cash, cfg)

    if year:
        postings = [p for p in postings if p.date.year == year]

    if format == "json":
        output_json([p.to_dict() for p in postings])
        return

    if format == "csv":
        target = _resolve_path(out) if out else _resolve_path(f"journal_{year or 'all'}.csv")
        write_journal_csv(postings, target)
        console.print(f"[green]CSV geschrieben:[/green] {target} ({len(postings)} Buchungen)")
        return

    console.print(f"[bold]Journal[/bold] — {len(postings)} Buchungen\n")
    for p in postings:
        console.print(
            f"  {p.date.isoformat()}  {p.debit} an {p.credit}  "
            f"{p.amount_eur:>10,.2f}  {p.description}"
        )

    if not journal_is_balanced(postings):
        console.print("\n[red]WARNUNG: Buchungen nicht ausgeglichen[/red]")


@accounting_app.command(name="ledger")
def ledger_cmd(
    year: int = typer.Option(0, "--year", help="Geschaeftsjahr (0 = alle)"),
    format: str = typer.Option("markdown", "--format", help="markdown, json oder csv"),
    out: str = typer.Option("", "--out", help="Pfad fuer CSV-Export"),
) -> None:
    """Hauptbuch (Konten-Salden)."""
    cfg = _load_cfg()
    cash = load_cash()
    rts, _, _ = _load_roundtrips(cfg)
    postings = build_journal(rts, cash, cfg)
    if year:
        postings = [p for p in postings if p.date.year == year]
    balances = compute_account_balances(postings)

    if format == "json":
        output_json([balances[c].to_dict() for c in sorted(balances.keys())])
        return

    if format == "csv":
        target = _resolve_path(out) if out else _resolve_path(f"ledger_{year or 'all'}.csv")
        write_ledger_csv(balances, target)
        console.print(f"[green]CSV geschrieben:[/green] {target}")
        return

    console.print("[bold]Hauptbuch[/bold]\n")
    console.print(f"  {'Konto':6} {'Bezeichnung':40} {'Soll':>14} {'Haben':>14} {'Saldo':>14}")
    for code in sorted(balances.keys()):
        b = balances[code]
        console.print(
            f"  {b.code:6} {b.name[:40]:40} {b.debit_total:>14,.2f} "
            f"{b.credit_total:>14,.2f} {b.balance:>14,.2f}"
        )


@accounting_app.command(name="tax")
def tax_cmd(
    year: int = typer.Option(date.today().year, "--year", help="Geschaeftsjahr"),
    format: str = typer.Option("markdown", "--format", help="markdown, json oder csv"),
    out: str = typer.Option("", "--out", help="Pfad fuer CSV-Export"),
) -> None:
    """Steuer-Report: Kapitaleinkuenfte + Honorar pro Holder."""
    cfg = _load_cfg()
    cash = load_cash()
    rts, _, _ = _load_roundtrips(cfg)
    expenses_year = sum(e.amount_eur for e in cash.expenses if e.date.year == year)
    lines = tax_report(rts, cfg, expenses_total=expenses_year, year=year)

    if format == "json":
        output_json([ln.to_dict() for ln in lines])
        return

    if format == "csv":
        target = _resolve_path(out) if out else _resolve_path(f"tax_{year}.csv")
        write_tax_csv(lines, target)
        console.print(f"[green]CSV geschrieben:[/green] {target}")
        return

    console.print(f"[bold]Steuer-Report {year}[/bold]\n")
    console.print(
        f"  {'Holder':8} {'Name':25} {'Kapitaleinkuenfte':>20} {'Honorar (§18)':>16} {'Aufwand-Anteil':>16}"
    )
    for ln in lines:
        console.print(
            f"  {ln.holder_id:8} {ln.holder_name[:25]:25} "
            f"{ln.capital_income:>20,.2f} {ln.self_employment:>16,.2f} {ln.expenses_share:>16,.2f}"
        )
    console.print(
        "\n[dim]Hinweis: Kapitaleinkuenfte → Anlage KAP. Honorar → Anlage S. "
        "B kann Honorar nicht als Werbungskosten abziehen (§20 Abs. 9 EStG).[/dim]"
    )


@accounting_app.command(name="import-flex")
def import_flex_cmd(
    files: list[Path] = typer.Argument(..., help="IBKR Flex-CSV-Dateien (eine oder mehrere)"),
    out: str = typer.Option(
        "", "--out", help="Ziel-YAML (Default: stdout). z.B. ~/Dokumente/obsidian/tradingGbr/manual_trades.yaml"
    ),
    skip_db: bool = typer.Option(
        True, "--skip-db/--no-skip-db",
        help="Trades, deren TradeID in Hase-DB existiert, ueberspringen (default: ja)",
    ),
) -> None:
    """Importiert IBKR Flex-CSV(s) als manual_trades.yaml-Block.

    Mehrere Dateien werden ueber TradeID dedupliziert. Aggregiert pro
    (Symbol, Datum). USD/Fremdwaehrung wird per FX-Tabelle aus dem CSV
    in EUR konvertiert. Trades, die in der Hase-DB als trade_ref bekannt
    sind, werden ausgeschlossen.

    Default-Output ist stdout — zum Speichern: --out <pfad> oder shell-redirect.
    """
    cfg = _load_cfg()
    files = [Path(p).expanduser() for p in files]
    for p in files:
        if not p.exists():
            console.print(f"[red]Datei nicht gefunden:[/red] {p}", err=True)
            raise typer.Exit(1)

    db_refs = _hase_trade_refs(cfg) if skip_db else set()
    flex_trades, fx_lookup = parse_flex_files(files)
    aggregated, skipped, fx_missing = aggregate(flex_trades, fx_lookup, db_refs)

    summary_lines = [
        f"Aus {len(files)} Datei(en): {len(flex_trades)} Trade-Legs, "
        f"{len(fx_lookup)} FX-Eintraege",
        f"Skipped: {len(skipped)} (in Hase-DB), {len(fx_missing)} (FX-Rate fehlt)",
        f"Importiert: {len(aggregated)} aggregierte Eintraege "
        f"(pro Symbol+Datum eine Buchung)",
    ]

    if fx_missing:
        summary_lines.append("FX-Lecks:")
        for t in fx_missing[:5]:
            summary_lines.append(
                f"  {t.trade_date} {t.symbol} {t.net_cash} {t.currency}"
            )
        if len(fx_missing) > 5:
            summary_lines.append(f"  ... und {len(fx_missing)-5} weitere")

    yaml_text = render_yaml(aggregated, header_comment="\n".join(summary_lines))

    if out:
        target = Path(out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(yaml_text)
        console.print(f"[green]Geschrieben:[/green] {target}")
        for ln in summary_lines:
            console.print(f"  {ln}")
    else:
        # YAML auf stdout, Summary auf stderr (damit Redirect sauber bleibt)
        import sys
        for ln in summary_lines:
            print(f"# {ln}", file=sys.stderr)
        typer.echo(yaml_text)
