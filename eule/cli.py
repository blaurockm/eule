"""
Eule CLI — Trade Journal + Portfolio Monitor.

Duenner Dispatcher: registriert Befehle aus den vier Aufgabenbereichen.
"""

from dotenv import load_dotenv

load_dotenv()  # .env im Arbeitsverzeichnis laden (EULE_DB_* etc.)

import typer

from eule.bestand import cli as bestand
from eule.bewertung import cli as bewertung
from eule.pipeline import cli as pipeline
from eule.betrieb import cli as betrieb

app = typer.Typer(
    name="eule",
    help="Eule 🦉 — Trade Journal + Portfolio Monitor CLI",
    no_args_is_help=True,
)

# ── Bestand: Was habe ich? ──────────────────────────
app.command()(bestand.positions)
app.command()(bestand.options)
app.command()(bestand.allocation)
app.command()(bestand.briefing)
app.command()(bestand.thesis)
app.command()(bestand.quote)

# ── Bewertung: Wie laeuft es? ────────────────────────
app.command()(bewertung.trades)
app.command(name="envs")(bewertung.list_envs)
app.command()(bewertung.report)
app.command()(bewertung.compare)
app.command(name="portfolio")(bewertung.portfolio_report)
app.command(name="pnl-override")(bewertung.pnl_override)

# ── Pipeline: Was kommt als naechstes? ───────────────
app.add_typer(pipeline.ep_app)

# ── Betrieb: Laeuft alles? ──────────────────────────
app.add_typer(betrieb.config_app)
app.add_typer(betrieb.schedule_app)
app.command()(betrieb.precheck)
app.command()(betrieb.bot)
app.command()(betrieb.serve)

if __name__ == "__main__":
    app()
