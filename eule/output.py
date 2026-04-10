"""Shared output utilities for Eule CLI."""

import json

import typer
from rich.console import Console

console = Console()


def output_json(data: dict | list) -> None:
    """JSON-Output auf stdout."""
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))
