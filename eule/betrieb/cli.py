"""Betrieb-CLI — Laeuft alles? Config, Precheck, Bot."""

import typer

from eule.config import ConfigError, init_config, load_config
from eule.output import console, output_json

config_app = typer.Typer(name="config", help="Eule-Konfiguration verwalten", no_args_is_help=True)


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
        output_json(data)
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

        if b.env_file:
            from pathlib import Path
            env_path = Path(b.env_file).expanduser()
            if env_path.exists():
                console.print(f"   {name}: [green]env_file OK[/green] ({env_path})")
            else:
                console.print(f"   {name}: [red]env_file FEHLT[/red] ({env_path})")
                ok = False

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


def precheck(
    summary: bool = typer.Option(False, "--summary", help="Daily-Summary erzwingen"),
    format: str = typer.Option("text", "--format", help="Output-Format: text oder json"),
) -> None:
    """Health-Check aller Hase-Environments."""
    from eule.monitoring.precheck import run_precheck

    exit_code, output = run_precheck(force_summary=summary)

    if format == "json":
        output_json({"exit_code": exit_code, "output": output})
    else:
        console.print(output)
    raise typer.Exit(exit_code)


def bot() -> None:
    """Wachtel Telegram Bot starten."""
    from eule.monitoring.telegram_bot import main as bot_main

    bot_main()
