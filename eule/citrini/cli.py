"""Citrini-CLI — Holdings-Exports diffen, Satelliten-Kandidaten vorfiltern."""

import typer
from rich.table import Table

from eule.output import console, output_json

citrini_app = typer.Typer(name="citrini", help="Citrini-Research: Holdings-Diff + Kandidaten-Vorfilter")


def _weight_table(title: str, df, weight_cols: list[str], extra_cols: list[str] | None = None) -> Table:
    table = Table(title=f"{title} ({len(df)})")
    table.add_column(df.index.name or "Ticker", style="bold")
    if "name" in df.columns:
        table.add_column("Name", style="dim")
    for col in weight_cols:
        table.add_column(col, justify="right")
    for col in extra_cols or []:
        table.add_column(col, justify="center")
    for idx, row in df.iterrows():
        cells = [str(idx)]
        if "name" in df.columns:
            cells.append(str(row["name"]))
        for col in weight_cols:
            val = row[col]
            style = "green" if val >= 0 else "red"
            cells.append(f"[{style}]{val:+.2f}[/{style}]")
        for col in extra_cols or []:
            cells.append("✓" if row[col] else "—")
        table.add_row(*cells)
    return table


@citrini_app.command(name="diff")
def citrini_diff(
    old_file: str = typer.Argument(..., help="Aelterer Holdings-Export (xlsx)"),
    new_file: str = typer.Argument(..., help="Neuerer Holdings-Export (xlsx)"),
    threshold: float = typer.Option(0.25, "--threshold", help="Mindest-|Gewichtsaenderung| in %-Punkten"),
    min_weight: float = typer.Option(0.4, "--min-weight", help="Kandidaten-Schwelle fuer Neuaufnahmen (%-Punkte)"),
    output_format: str = typer.Option("table", "--format", help="Output-Format: table oder json"),
) -> None:
    """Zwei Citrini-Holdings-Exports vergleichen (Citrindex oder 26TF26)."""
    from eule.citrini.diff import diff_holdings, diff_to_dict, load_holdings

    result = diff_holdings(
        load_holdings(old_file), load_holdings(new_file),
        threshold=threshold, min_weight=min_weight,
    )

    if output_format == "json":
        output_json(diff_to_dict(result))
        return

    sections = [
        ("Neuaufnahmen", result.added, ["weight"], ["kandidat", "top3_im_basket", "us_listing"]),
        ("Schliessungen", result.closed, ["weight"], None),
        ("Gewichtsaenderungen", result.changed, ["weight_old", "weight", "delta"], None),
        ("Baskets neu", result.baskets_added, ["weight"], None),
        ("Baskets geschlossen", result.baskets_closed, ["weight"], None),
        ("Basket-Gewichtsaenderungen", result.baskets_changed, ["weight_old", "weight", "delta"], None),
    ]
    any_output = False
    for title, df, weight_cols, extra in sections:
        if df.empty:
            continue
        any_output = True
        console.print(_weight_table(title, df, weight_cols, extra))
    if not any_output:
        console.print("[yellow]Keine Aenderungen zwischen den beiden Staenden.[/yellow]")
    else:
        n_cand = int(result.added["kandidat"].sum()) if not result.added.empty else 0
        console.print(f"\n[dim]Kandidaten-Vorfilter: Gewicht >= {min_weight} %-Punkte "
                      f"oder Top-3 im Basket -> {n_cand} Kandidat(en). "
                      f"Naechster Schritt: These lesen, Liquiditaet pruefen.[/dim]")
