"""
Thesis-Checker — parst positions-bh.md und prueft Exit-Kriterien.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from eule.models import Position


@dataclass(frozen=True)
class ThesisEntry:
    """Eine Position mit These und Exit-Kriterien."""

    ticker: str
    thesis: str
    exit_criteria: list[str]


@dataclass(frozen=True)
class ThesisCheck:
    """Ergebnis der Pruefung eines Exit-Kriteriums."""

    ticker: str
    criterion: str
    status: str  # triggered, approaching, pending, not_checkable
    detail: str


def parse_thesis_file(path: str) -> list[ThesisEntry]:
    """Parst positions-bh.md und extrahiert Thesen + Exit-Kriterien.

    Erwartet Markdown mit Abschnitten pro Position:
    - Ueberschriften mit Ticker (## TICKER oder ### TICKER)
    - "These:" oder "Thesis:" Zeilen
    - "Exit" oder "Trigger" Tabellen oder Listen
    """
    file_path = Path(path).expanduser()
    if not file_path.exists():
        logger.warning(f"Thesis-Datei nicht gefunden: {file_path}")
        return []

    text = file_path.read_text()
    entries: list[ThesisEntry] = []

    # Abschnitte nach Headings aufteilen
    sections = re.split(r"^#{2,3}\s+", text, flags=re.MULTILINE)

    for section in sections[1:]:  # Erster Split ist vor dem ersten Heading
        lines = section.strip().split("\n")
        if not lines:
            continue

        # Ticker aus erster Zeile (Heading-Text)
        heading = lines[0].strip()
        # Ticker ist oft der erste Teil: "CDE (ex-NGD)" → "CDE"
        ticker_match = re.match(r"([A-Z0-9_.-]+)", heading)
        if not ticker_match:
            continue
        ticker = ticker_match.group(1)

        thesis = ""
        exit_criteria: list[str] = []

        for line in lines[1:]:
            stripped = line.strip()

            # These finden
            if re.match(r"^\*?\*?These\*?\*?:", stripped, re.IGNORECASE) or \
               re.match(r"^\*?\*?Thesis\*?\*?:", stripped, re.IGNORECASE):
                thesis = re.sub(r"^\*?\*?The?sis?\*?\*?:\s*", "", stripped, flags=re.IGNORECASE)

            # Exit-Kriterien aus Listen (- oder *)
            if re.match(r"^[-*]\s+", stripped):
                criterion = re.sub(r"^[-*]\s+", "", stripped)
                # Nur sinnvolle Kriterien (nicht zu kurz, nicht Headers)
                if len(criterion) > 5 and not criterion.startswith("#"):
                    exit_criteria.append(criterion)

            # Exit-Kriterien aus Tabellen (| Trigger | Aktion |)
            if "|" in stripped and not stripped.startswith("|---"):
                cells = [c.strip() for c in stripped.split("|") if c.strip()]
                if len(cells) >= 2 and cells[0].lower() not in ("trigger", "kriterium", "criterion"):
                    # Erster Teil koennte ein Trigger sein
                    if len(cells[0]) > 5:
                        exit_criteria.append(cells[0])

        if ticker:
            entries.append(ThesisEntry(
                ticker=ticker,
                thesis=thesis,
                exit_criteria=exit_criteria,
            ))

    return entries


def _check_price_criterion(criterion: str, position: Position | None) -> ThesisCheck | None:
    """Prueft Preis-basierte Kriterien wie 'Kurs unter $X'."""
    match = re.search(r"[Kk]urs\s+unter\s+[\$€]?(\d+(?:\.\d+)?)", criterion)
    if not match:
        match = re.search(r"unter\s+[\$€](\d+(?:\.\d+)?)", criterion)
    if not match:
        return None

    threshold = float(match.group(1))
    ticker = position.ticker if position else "?"

    if position and position.current_price is not None:
        if position.current_price < threshold:
            return ThesisCheck(
                ticker=ticker,
                criterion=criterion,
                status="triggered",
                detail=f"Aktuell {position.current_price:.2f} < Schwelle {threshold:.2f}",
            )
        else:
            ratio = position.current_price / threshold if threshold > 0 else 999
            if ratio < 1.1:  # Innerhalb 10%
                return ThesisCheck(
                    ticker=ticker,
                    criterion=criterion,
                    status="approaching",
                    detail=f"Aktuell {position.current_price:.2f}, Schwelle {threshold:.2f} ({ratio:.0%})",
                )
            return ThesisCheck(
                ticker=ticker,
                criterion=criterion,
                status="pending",
                detail=f"Aktuell {position.current_price:.2f}, Schwelle {threshold:.2f}",
            )

    return ThesisCheck(
        ticker=ticker,
        criterion=criterion,
        status="not_checkable",
        detail="Kein aktueller Kurs verfuegbar",
    )


def check_thesis(
    entries: list[ThesisEntry],
    positions: list[Position],
    ticker_filter: str | None = None,
) -> list[ThesisCheck]:
    """Prueft Exit-Kriterien gegen aktuelle Positionen.

    Args:
        entries: Geparste Thesis-Eintraege
        positions: Aktuelle Positionen (mit Live-Kursen)
        ticker_filter: Optional nur diesen Ticker pruefen
    """
    pos_by_ticker = {p.ticker: p for p in positions}
    checks: list[ThesisCheck] = []

    for entry in entries:
        if ticker_filter and entry.ticker != ticker_filter:
            continue

        position = pos_by_ticker.get(entry.ticker)

        for criterion in entry.exit_criteria:
            # Preis-Check
            price_check = _check_price_criterion(criterion, position)
            if price_check:
                checks.append(price_check)
                continue

            # Nicht automatisch pruefbar
            checks.append(ThesisCheck(
                ticker=entry.ticker,
                criterion=criterion,
                status="not_checkable",
                detail="Manuelle Pruefung erforderlich",
            ))

    return checks
