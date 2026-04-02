"""
Eule CLI — Trade Journal + Portfolio Monitor.
"""

import json
import sys

import typer
from rich.console import Console
from rich.table import Table

from eule.config import ConfigError, init_config, load_config
from eule.db import get_env_info, list_environments
from eule.trades import (
    detect_roundtrips,
    get_open_trades,
    load_trades,
    summarize_roundtrips,
)

app = typer.Typer(
    name="eule",
    help="Eule 🦉 — Trade Journal + Portfolio Monitor CLI",
    no_args_is_help=True,
)
console = Console()


def _output_json(data: dict | list) -> None:
    """JSON-Output auf stdout."""
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


@app.command()
def trades(
    env: str = typer.Option(
        "real-ibkr",
        "--env",
        help="Hase-Environment (real-ibkr, real2-ibkr)",
    ),
    strategy: str | None = typer.Option(
        None,
        "--strategy",
        help="Nur diese Strategie (z.B. spx-0dte-mon-put)",
    ),
    days: int | None = typer.Option(
        None,
        "--days",
        help="Nur letzte N Tage",
    ),
    format: str = typer.Option(
        "markdown",
        "--format",
        help="Output-Format: markdown oder json",
    ),
    show_open: bool = typer.Option(
        False,
        "--open",
        help="Zeige offene Positionen (ohne Exit)",
    ),
) -> None:
    """Hase-Trades laden, Roundtrips erkennen und anzeigen."""
    try:
        conn, runtime_name = get_env_info(env)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    try:
        raw_trades = load_trades(
            conn,
            runtime_name,
            strategy_key=strategy,
            days=days,
        )
    finally:
        conn.close()

    if not raw_trades:
        if format == "json":
            _output_json({"trades": [], "roundtrips": [], "summary": {}, "open": []})
        else:
            console.print("[yellow]Keine Trades gefunden.[/yellow]")
        raise typer.Exit(0)

    roundtrips = detect_roundtrips(raw_trades)
    open_positions = get_open_trades(raw_trades)
    summary = summarize_roundtrips(roundtrips)

    if format == "json":
        data = {
            "env": env,
            "runtime_name": runtime_name,
            "trades_count": len(raw_trades),
            "roundtrips": [r.to_dict() for r in roundtrips],
            "summary": summary,
            "open": [t.to_dict() for t in open_positions],
        }
        _output_json(data)
        return

    # Markdown/Rich-Output
    console.print(f"\n🦉 [bold]Eule — Trades[/bold] ({env} / {runtime_name})")
    console.print(f"   {len(raw_trades)} Trades geladen\n")

    # Roundtrips-Tabelle
    if roundtrips:
        table = Table(title=f"Roundtrips ({len(roundtrips)})", show_lines=False, pad_edge=False)
        table.add_column("Strategie", style="cyan", no_wrap=True)
        table.add_column("Entry", style="green", no_wrap=True)
        table.add_column("Exit", style="red", no_wrap=True)
        table.add_column("Tage", justify="right")
        table.add_column("Entry $", justify="right", no_wrap=True)
        table.add_column("P&L $", justify="right", no_wrap=True)
        table.add_column("%", justify="right", no_wrap=True)
        table.add_column("Typ", style="dim")

        for r in roundtrips:
            pnl_style = "green" if r.pnl >= 0 else "red"
            exit_type = "EXP" if r.exit_is_expiry else "CLS"
            table.add_row(
                r.strategy_key,
                r.entry_date.strftime("%d.%m."),
                r.exit_date.strftime("%d.%m."),
                str(r.holding_days),
                f"{r.entry_value:.2f}",
                f"[{pnl_style}]{r.pnl:+.2f}[/{pnl_style}]",
                f"[{pnl_style}]{r.pnl_percent:+.1f}%[/{pnl_style}]",
                exit_type,
            )

        console.print(table)

    # Summary
    if summary["count"] > 0:
        console.print(f"\n📊 [bold]Summary[/bold]")
        console.print(f"   Roundtrips: {summary['count']} ({summary['winners']}W / {summary['losers']}L)")
        console.print(f"   Win Rate: {summary['win_rate']}%")
        console.print(f"   Total P&L: ${summary['total_pnl']:.2f}")
        console.print(f"   Avg P&L: ${summary['avg_pnl']:.2f}")
        console.print(f"   Avg Holding: {summary['avg_holding_days']} Tage")
        console.print(f"   Expired: {summary['expired_count']}")

    # Offene Positionen
    if show_open and open_positions:
        console.print(f"\n⏳ [bold]Offene Positionen ({len(open_positions)})[/bold]")
        open_table = Table(show_lines=False, pad_edge=False)
        open_table.add_column("Strategie", style="cyan", no_wrap=True)
        open_table.add_column("Entry", style="green", no_wrap=True)
        open_table.add_column("Qty", justify="right")
        open_table.add_column("Price", justify="right", no_wrap=True)
        open_table.add_column("Value", justify="right", no_wrap=True)

        for t in open_positions:
            open_table.add_row(
                t.strategy_key,
                t.date.strftime("%d.%m."),
                f"{t.qty:.0f}",
                f"{t.price:.4f}",
                f"{t.value:.2f}",
            )

        console.print(open_table)
    elif show_open:
        console.print("\n[green]Keine offenen Positionen.[/green]")

    console.print()


@app.command(name="envs")
def list_envs(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Verfuegbare Hase-Environments anzeigen."""
    envs = list_environments()
    if format == "json":
        _output_json({"environments": envs})
    else:
        console.print("\n🦉 [bold]Verfuegbare Environments[/bold]")
        for e in envs:
            console.print(f"   • {e}")
        console.print()


# ──────────────────────────────────────────────────
# Positions Command
# ──────────────────────────────────────────────────


@app.command()
def positions(
    broker: str | None = typer.Option(None, "--broker", help="Nur diesen Broker anzeigen"),
    asset_type: str | None = typer.Option(None, "--type", help="Asset-Typ filtern (stock, option, bond, etf, gold_physical)"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Alle Positionen ueber alle Broker aggregieren und anzeigen."""
    from eule.aggregator import aggregate_positions

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)

    # Filtern
    filtered = snap.positions
    if broker:
        filtered = [p for p in filtered if p.broker == broker]
    if asset_type:
        filtered = [p for p in filtered if p.asset_type == asset_type]

    if format == "json":
        data = snap.to_dict()
        if broker or asset_type:
            data["positions"] = [p.to_dict() for p in filtered]
        _output_json(data)
        return

    # Markdown/Rich-Output
    console.print(f"\n[bold]Eule — Positionen[/bold]")
    console.print(f"   Gesamt: {snap.total_value_eur:,.0f} {cfg.base_currency}")
    for b, total in sorted(snap.broker_totals.items()):
        console.print(f"   {b}: {total:,.0f} {cfg.base_currency}")

    if filtered:
        table = Table(title=f"Positionen ({len(filtered)})", show_lines=False, pad_edge=False)
        table.add_column("Broker", style="cyan", no_wrap=True)
        table.add_column("Ticker", style="bold", no_wrap=True)
        table.add_column("Typ", style="dim")
        table.add_column("Richtung")
        table.add_column("Menge", justify="right")
        table.add_column("Entry", justify="right", no_wrap=True)
        table.add_column("Aktuell", justify="right", no_wrap=True)
        table.add_column("P&L EUR", justify="right", no_wrap=True)
        table.add_column("%Port", justify="right", no_wrap=True)

        for p in filtered:
            pnl_str = ""
            pnl_style = "dim"
            if p.unrealized_pnl_eur is not None:
                pnl_style = "green" if p.unrealized_pnl_eur >= 0 else "red"
                pnl_str = f"[{pnl_style}]{p.unrealized_pnl_eur:+,.0f}[/{pnl_style}]"

            pct_str = f"{p.pct_of_portfolio:.1%}" if p.pct_of_portfolio else ""
            current_str = f"{p.current_price:,.2f}" if p.current_price else "-"

            table.add_row(
                p.broker,
                p.ticker,
                p.asset_type,
                p.direction,
                f"{p.size:,.2f}",
                f"{p.entry_price:,.2f}",
                current_str,
                pnl_str,
                pct_str,
            )

        console.print(table)
    else:
        console.print("[yellow]Keine Positionen gefunden.[/yellow]")

    # Errors anzeigen
    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")

    console.print()


# ──────────────────────────────────────────────────
# Options Command
# ──────────────────────────────────────────────────


@app.command()
def options(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Options-Dashboard: 50%-Regel, DTE-Warnungen, Roll-Vorbereitung."""
    from eule.aggregator import aggregate_positions
    from eule.options import analyze_options

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    opt_list, alerts = analyze_options(
        snap.positions,
        expiry_warning_days=cfg.alerts.option_expiry_warning_days,
        fifty_pct_rule=cfg.alerts.fifty_pct_rule,
    )

    if format == "json":
        data = {
            "options": [o.to_dict() for o in opt_list],
            "alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in alerts
            ],
            "errors": snap.errors,
        }
        _output_json(data)
        return

    console.print(f"\n[bold]Eule — Options Dashboard[/bold]")
    if not opt_list:
        console.print("[yellow]Keine Option-Positionen.[/yellow]\n")
        return

    table = Table(title=f"Optionen ({len(opt_list)})", show_lines=False, pad_edge=False)
    table.add_column("Broker", style="cyan", no_wrap=True)
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Typ")
    table.add_column("Richtung")
    table.add_column("Strike", justify="right")
    table.add_column("Verfall", no_wrap=True)
    table.add_column("DTE", justify="right")
    table.add_column("Praemie", justify="right")
    table.add_column("Aktuell", justify="right")
    table.add_column("P&L%", justify="right")

    for o in opt_list:
        dte_style = "green"
        if o.days_to_expiry <= 1:
            dte_style = "red bold"
        elif o.days_to_expiry <= 3:
            dte_style = "red"
        elif o.days_to_expiry <= 7:
            dte_style = "yellow"

        pnl_pct = ""
        if o.sold_premium > 0:
            pct = (o.sold_premium - o.current_value) / o.sold_premium * 100
            pnl_style = "green" if pct >= 0 else "red"
            pnl_pct = f"[{pnl_style}]{pct:+.0f}%[/{pnl_style}]"

        table.add_row(
            o.broker,
            o.ticker,
            o.option_type,
            o.direction,
            f"{o.strike:,.0f}",
            o.expiry.strftime("%d.%m.%Y") if o.expiry else "-",
            f"[{dte_style}]{o.days_to_expiry}[/{dte_style}]",
            f"{o.sold_premium:,.0f}" if o.sold_premium else "-",
            f"{o.current_value:,.0f}" if o.current_value else "-",
            pnl_pct,
        )

    console.print(table)

    if alerts:
        console.print(f"\n[bold]Alerts ({len(alerts)})[/bold]")
        for a in alerts:
            icon = {"fifty_pct": "[green]$[/green]", "expiry_warning": "[yellow]![/yellow]",
                     "expiry_urgent": "[red]!![/red]", "expiry_critical": "[red bold]!!![/red bold]"}
            console.print(f"   {icon.get(a.alert_type, '?')} {a.message}")
            console.print(f"      → {a.action_suggested}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()


# ──────────────────────────────────────────────────
# Allocation Command
# ──────────────────────────────────────────────────


@app.command()
def allocation(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Soll vs. Ist Allokation pruefen."""
    from eule.aggregator import aggregate_positions
    from eule.allocation import check_allocation

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    checks, concentration = check_allocation(snap, cfg.allocation)

    if format == "json":
        data = {
            "total_value_eur": round(snap.total_value_eur, 2),
            "allocation": [
                {"category": c.category, "actual_pct": round(c.actual_pct, 4),
                 "actual_eur": round(c.actual_eur, 2),
                 "target_min": c.target_min, "target_max": c.target_max,
                 "status": c.status, "deviation": round(c.deviation, 4)}
                for c in checks
            ],
            "concentration_alerts": [
                {"ticker": c.ticker, "broker": c.broker,
                 "pct": round(c.pct, 4), "limit": c.limit}
                for c in concentration
            ],
            "errors": snap.errors,
        }
        _output_json(data)
        return

    console.print(f"\n[bold]Eule — Allokation[/bold]")
    console.print(f"   Gesamt: {snap.total_value_eur:,.0f} EUR\n")

    table = Table(title="Soll / Ist", show_lines=False, pad_edge=False)
    table.add_column("Kategorie", style="bold")
    table.add_column("Ist %", justify="right")
    table.add_column("Ist EUR", justify="right")
    table.add_column("Soll", justify="right")
    table.add_column("Status", justify="center")

    for c in checks:
        status_style = {"ok": "green", "under": "yellow", "over": "red"}
        status_icon = {"ok": "OK", "under": "UNTER", "over": "UEBER"}
        style = status_style.get(c.status, "dim")
        icon = status_icon.get(c.status, "?")

        table.add_row(
            c.category,
            f"{c.actual_pct:.1%}",
            f"{c.actual_eur:,.0f}",
            f"{c.target_min:.0%} – {c.target_max:.0%}",
            f"[{style}]{icon}[/{style}]",
        )

    console.print(table)

    if concentration:
        console.print(f"\n[red]Konzentrations-Warnungen:[/red]")
        for c in concentration:
            console.print(f"   {c.ticker} ({c.broker}): {c.pct:.1%} > Limit {c.limit:.0%}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()


# ──────────────────────────────────────────────────
# Briefing Command
# ──────────────────────────────────────────────────


@app.command()
def briefing(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Gesamt-Briefing: Portfolio, Alerts, Allokation."""
    from datetime import date as date_cls

    from eule.aggregator import aggregate_positions
    from eule.briefing import create_briefing

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    snap = aggregate_positions(cfg)
    brief = create_briefing(cfg, snap)

    if format == "json":
        data = {
            "date": date_cls.today().isoformat(),
            "portfolio": {
                "total_value_eur": round(snap.total_value_eur, 2),
                "broker_totals": {k: round(v, 2) for k, v in snap.broker_totals.items()},
            },
            "positions": [p.to_dict() for p in snap.positions],
            "option_alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in brief.option_alerts
            ],
            "bond_alerts": [
                {"ticker": a.position.ticker, "type": a.alert_type,
                 "message": a.message, "action": a.action_suggested}
                for a in brief.bond_alerts
            ],
            "allocation": [
                {"category": c.category, "actual_pct": round(c.actual_pct, 4),
                 "status": c.status}
                for c in brief.allocation_checks
            ],
            "concentration_alerts": [
                {"ticker": c.ticker, "pct": round(c.pct, 4)}
                for c in brief.concentration_alerts
            ],
            "errors": brief.errors,
        }
        _output_json(data)
        return

    # Markdown-Output
    from datetime import date as date_cls
    console.print(f"\n[bold]Eule — Briefing ({date_cls.today().strftime('%d.%m.%Y')})[/bold]")
    console.print(f"\n   Gesamt: ~{snap.total_value_eur:,.0f} EUR")
    for b, total in sorted(snap.broker_totals.items()):
        console.print(f"   {b}: {total:,.0f} EUR")

    # Gewinner/Verlierer
    with_pnl = [(p, p.unrealized_pnl_eur or 0) for p in snap.positions if p.unrealized_pnl_eur is not None]
    if with_pnl:
        winners = sorted(with_pnl, key=lambda x: x[1], reverse=True)[:3]
        losers = sorted(with_pnl, key=lambda x: x[1])[:3]

        if winners and winners[0][1] > 0:
            w_str = ", ".join(f"{p.ticker} {pnl:+,.0f}" for p, pnl in winners if pnl > 0)
            if w_str:
                console.print(f"\n   [green]Gewinner:[/green] {w_str}")
        if losers and losers[0][1] < 0:
            l_str = ", ".join(f"{p.ticker} {pnl:+,.0f}" for p, pnl in losers if pnl < 0)
            if l_str:
                console.print(f"   [red]Verlierer:[/red] {l_str}")

    # Alerts
    all_alerts = brief.option_alerts + brief.bond_alerts
    if all_alerts:
        console.print(f"\n   [bold]Aktionen:[/bold]")
        for a in all_alerts:
            console.print(f"   - {a.message}")
            console.print(f"     → {a.action_suggested}")

    # Allokation
    problems = [c for c in brief.allocation_checks if c.status != "ok"]
    if problems:
        console.print(f"\n   [bold]Allokation:[/bold]")
        for c in problems:
            console.print(f"   - {c.category}: {c.actual_pct:.1%} (Ziel {c.target_min:.0%}–{c.target_max:.0%}) [{c.status.upper()}]")

    if brief.concentration_alerts:
        console.print(f"\n   [red]Konzentration:[/red]")
        for c in brief.concentration_alerts:
            console.print(f"   - {c.ticker}: {c.pct:.1%} > {c.limit:.0%}")

    if brief.errors:
        console.print(f"\n   [yellow]Hinweise:[/yellow]")
        for err in brief.errors:
            console.print(f"   {err}")

    console.print()


# ──────────────────────────────────────────────────
# Thesis Command
# ──────────────────────────────────────────────────


@app.command()
def thesis(
    ticker: str | None = typer.Argument(None, help="Einzelnen Ticker pruefen"),
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Exit-Kriterien aus positions-bh.md pruefen."""
    from eule.aggregator import aggregate_positions
    from eule.thesis import check_thesis, parse_thesis_file

    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    if not cfg.thesis_file:
        console.print("[red]Fehler:[/red] thesis_file nicht in config.yaml konfiguriert")
        raise typer.Exit(1)

    entries = parse_thesis_file(cfg.thesis_file)
    if not entries:
        console.print("[yellow]Keine Thesis-Eintraege gefunden.[/yellow]")
        raise typer.Exit(0)

    snap = aggregate_positions(cfg)
    checks = check_thesis(entries, snap.positions, ticker_filter=ticker)

    if format == "json":
        data = {
            "entries": [
                {"ticker": e.ticker, "thesis": e.thesis,
                 "exit_criteria": e.exit_criteria}
                for e in entries
                if not ticker or e.ticker == ticker
            ],
            "checks": [
                {"ticker": c.ticker, "criterion": c.criterion,
                 "status": c.status, "detail": c.detail}
                for c in checks
            ],
            "errors": snap.errors,
        }
        _output_json(data)
        return

    console.print(f"\n[bold]Eule — Thesis Check[/bold]")

    for entry in entries:
        if ticker and entry.ticker != ticker:
            continue
        console.print(f"\n   [bold]{entry.ticker}[/bold]")
        if entry.thesis:
            console.print(f"   These: {entry.thesis}")

        entry_checks = [c for c in checks if c.ticker == entry.ticker]
        for c in entry_checks:
            status_style = {
                "triggered": "red bold",
                "approaching": "yellow",
                "pending": "dim",
                "not_checkable": "dim italic",
            }
            style = status_style.get(c.status, "dim")
            console.print(f"   [{style}][{c.status.upper()}][/{style}] {c.criterion}")
            if c.detail and c.status in ("triggered", "approaching"):
                console.print(f"            {c.detail}")

    if snap.errors:
        console.print(f"\n[yellow]Hinweise:[/yellow]")
        for err in snap.errors:
            console.print(f"   {err}")
    console.print()


# ──────────────────────────────────────────────────
# Config Commands
# ──────────────────────────────────────────────────

config_app = typer.Typer(name="config", help="Eule-Konfiguration verwalten", no_args_is_help=True)
app.add_typer(config_app)


@config_app.command(name="init")
def config_init() -> None:
    """Config-Templates unter ~/.eule/ erstellen."""
    created = init_config()
    if created:
        console.print("[green]Erstellt:[/green]")
        for path in created:
            console.print(f"   {path}")
    else:
        console.print("[yellow]Alle Dateien existieren bereits.[/yellow]")


@config_app.command(name="show")
def config_show(
    format: str = typer.Option("markdown", "--format", help="Output-Format: markdown oder json"),
) -> None:
    """Aktuelle Konfiguration anzeigen (Credentials maskiert)."""
    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    if format == "json":
        data = {
            "base_currency": cfg.base_currency,
            "brokers": {
                name: {
                    "enabled": b.enabled,
                    "type": b.broker_type,
                    "env_file": b.env_file or None,
                    "positions_file": b.positions_file or None,
                }
                for name, b in cfg.brokers.items()
            },
            "allocation": {
                "targets": {t.category: {"min": t.min_pct, "max": t.max_pct}
                            for t in cfg.allocation.targets},
                "max_single_position_pct": cfg.allocation.max_single_position_pct,
            },
            "thesis_file": cfg.thesis_file,
        }
        _output_json(data)
        return

    console.print("\n[bold]Eule Config[/bold]")
    console.print(f"   Waehrung: {cfg.base_currency}")
    console.print(f"\n[bold]Broker[/bold]")
    for name, b in cfg.brokers.items():
        status = "[green]aktiv[/green]" if b.enabled else "[dim]inaktiv[/dim]"
        console.print(f"   {name} ({b.broker_type}): {status}")
    console.print(f"\n[bold]Allokation[/bold]")
    for t in cfg.allocation.targets:
        console.print(f"   {t.category}: {t.min_pct:.0%} – {t.max_pct:.0%}")
    console.print()


@config_app.command(name="check")
def config_check() -> None:
    """Konfiguration validieren und Broker-Erreichbarkeit pruefen."""
    try:
        cfg = load_config()
    except ConfigError as e:
        console.print(f"[red]Fehler:[/red] {e}")
        raise typer.Exit(1)

    console.print("[bold]Config-Check[/bold]\n")
    ok = True

    for name, b in cfg.brokers.items():
        if not b.enabled:
            console.print(f"   {name}: [dim]uebersprungen (disabled)[/dim]")
            continue

        # .env-Datei pruefen
        if b.env_file:
            from pathlib import Path
            env_path = Path(b.env_file).expanduser()
            if env_path.exists():
                console.print(f"   {name}: [green]env_file OK[/green] ({env_path})")
            else:
                console.print(f"   {name}: [red]env_file FEHLT[/red] ({env_path})")
                ok = False

        # Positions-Datei pruefen
        if b.positions_file:
            from pathlib import Path
            pos_path = Path(b.positions_file).expanduser()
            if pos_path.exists():
                console.print(f"   {name}: [green]positions_file OK[/green] ({pos_path})")
            else:
                console.print(f"   {name}: [red]positions_file FEHLT[/red] ({pos_path})")
                ok = False

    if ok:
        console.print("\n[green]Alle Checks bestanden.[/green]")
    else:
        console.print("\n[red]Einige Checks fehlgeschlagen.[/red]")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
