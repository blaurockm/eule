"""
EP Scanner — Barchart screener emails fetchen, parsen, auto-scoren.

Pipeline: IMAP fetch → CSV parse → Pre-Filter → Auto-Score (5 von 10 Kriterien).
"""

import csv
import email
import email.policy
import imaplib
import io
import os
import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# Force IPv4 — Posteo IPv6 IMAP unreachable from some hosts
_orig_getaddrinfo = socket.getaddrinfo


def _ipv4_getaddrinfo(*args, **kwargs):
    results = _orig_getaddrinfo(*args, **kwargs)
    ipv4 = [r for r in results if r[0] == socket.AF_INET]
    return ipv4 if ipv4 else results


socket.getaddrinfo = _ipv4_getaddrinfo


@dataclass
class Candidate:
    """Ein EP-Kandidat aus dem Barchart-Screener."""

    symbol: str
    latest: float
    change: float
    pct_change: float
    open: float
    high: float
    low: float
    volume: int
    date: str
    screener_type: str = "gap-up"  # gap-up oder follow-through

    # Berechnete Werte
    close_position: float = 0.0  # (latest - low) / (high - low)
    gap_ok: bool = False          # >= 10%
    close_ok: bool = False        # >= 0.75

    # Auto-Score (5 von 10 Kriterien, Rest braucht Recherche)
    auto_score: int = 0
    score_details: dict = field(default_factory=dict)

    def compute_pre_filter(self) -> None:
        """Berechne Werte die direkt aus dem CSV kommen."""
        if self.high > self.low:
            self.close_position = (self.latest - self.low) / (self.high - self.low)
        self.gap_ok = self.pct_change >= 10.0
        self.close_ok = self.close_position >= 0.75

    def auto_score_from_csv(self) -> None:
        """Score Kriterien 1 und 7 aus CSV-Daten."""
        self.score_details = {}

        # Kriterium 1: Gap >= 10%
        if self.pct_change >= 10.0:
            self.score_details["gap_10pct"] = True
            self.auto_score += 1
        else:
            self.score_details["gap_10pct"] = False

        # Kriterium 7: Close im oberen Viertel
        if self.close_position >= 0.75:
            self.score_details["close_upper_quarter"] = True
            self.auto_score += 1
        else:
            self.score_details["close_upper_quarter"] = False


def _load_imap_env() -> dict[str, str]:
    """IMAP-Credentials laden — aus ~/.eule/.env oder ~/.openclaw/secrets/posteo.env."""
    # Primaer: eule .env (bereits via dotenv geladen)
    host = os.environ.get("IMAP_HOST")
    if host:
        return {
            "IMAP_HOST": host,
            "IMAP_PORT": os.environ.get("IMAP_PORT", "993"),
            "IMAP_USER": os.environ.get("IMAP_USER", ""),
            "IMAP_PASS": os.environ.get("IMAP_PASS", ""),
            "IMAP_ALIAS": os.environ.get("IMAP_ALIAS", ""),
        }

    # Fallback: openclaw secrets
    for path in [
        Path.home() / ".eule" / "posteo.env",
        Path.home() / ".openclaw" / "secrets" / "posteo.env",
    ]:
        if path.exists():
            env = {}
            for line in path.read_text().splitlines():
                line = line.strip()
                if line and "=" in line and not line.startswith("#"):
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
            return env

    raise FileNotFoundError(
        "IMAP-Credentials nicht gefunden. "
        "Setze IMAP_HOST/IMAP_USER/IMAP_PASS in ~/.eule/.env "
        "oder lege ~/.eule/posteo.env an."
    )


def fetch_screener_emails(days: int = 1, mark_read: bool = False) -> list[dict]:
    """Barchart-Screener-Emails von Posteo IMAP holen.

    Returns:
        Liste von dicts mit keys: subject, date, filename, csv_content
    """
    env = _load_imap_env()

    ctx = ssl.create_default_context()
    imap = imaplib.IMAP4_SSL(env["IMAP_HOST"], int(env["IMAP_PORT"]), ssl_context=ctx)
    imap.login(env["IMAP_USER"], env["IMAP_PASS"])

    try:
        imap.select("INBOX")
        since_date = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")

        # Erst ungelesene, dann alle
        for criteria in [
            f'(FROM "noreply@barchart.com" SINCE {since_date} UNSEEN)',
            f'(FROM "noreply@barchart.com" SINCE {since_date})',
        ]:
            status, message_ids = imap.search(None, criteria)
            if status == "OK" and message_ids[0]:
                break
        else:
            return []

        results = []
        for msg_id in message_ids[0].split():
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK":
                continue

            msg = email.message_from_bytes(msg_data[0][1], policy=email.policy.default)

            for part in msg.walk():
                content_type = part.get_content_type()
                filename = part.get_filename() or ""

                if content_type == "text/csv" or filename.lower().endswith(".csv"):
                    csv_content = part.get_content()
                    if isinstance(csv_content, bytes):
                        csv_content = csv_content.decode("utf-8", errors="replace")

                    results.append({
                        "subject": str(msg.get("subject", "")),
                        "date": str(msg.get("date", "")),
                        "filename": filename,
                        "csv_content": csv_content,
                    })

            if mark_read:
                imap.store(msg_id, "+FLAGS", "\\Seen")

        return results
    finally:
        imap.logout()


def parse_csv(csv_content: str) -> list[Candidate]:
    """Barchart CSV parsen und Candidates erzeugen."""
    candidates = []
    seen = set()

    reader = csv.DictReader(io.StringIO(csv_content))
    for row in reader:
        symbol = row.get("Symbol", "").strip().strip('"')
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)

        def parse_num(val: str) -> float:
            val = val.strip().strip('"').replace(",", "").replace("+", "")
            if val.endswith("%"):
                val = val[:-1]
            try:
                return float(val)
            except (ValueError, TypeError):
                return 0.0

        def parse_int(val: str) -> int:
            return int(parse_num(val))

        # Screener-Typ aus Dateiname erkennen (falls vorhanden)
        screener_type = "gap-up"

        c = Candidate(
            symbol=symbol,
            latest=parse_num(row.get("Latest", "0")),
            change=parse_num(row.get("Change", "0")),
            pct_change=parse_num(row.get("%Change", row.get("Pct_Change", "0"))),
            open=parse_num(row.get("Open", "0")),
            high=parse_num(row.get("High", "0")),
            low=parse_num(row.get("Low", "0")),
            volume=parse_int(row.get("Volume", "0")),
            date=row.get("Time", ""),
            screener_type=screener_type,
        )
        c.compute_pre_filter()
        c.auto_score_from_csv()
        candidates.append(c)

    return candidates


def scan(days: int = 1, mark_read: bool = False, min_gap: float = 8.0) -> list[Candidate]:
    """Kompletter Scan: Emails fetchen, parsen, filtern.

    Args:
        days: Emails der letzten N Tage
        mark_read: Emails als gelesen markieren
        min_gap: Minimaler Gap in % (Pre-Filter)

    Returns:
        Liste von Candidates, sortiert nach pct_change absteigend
    """
    emails = fetch_screener_emails(days=days, mark_read=mark_read)
    if not emails:
        return []

    all_candidates = []
    for em in emails:
        # Screener-Typ aus Filename erkennen
        screener_type = "gap-up"
        if "followthrough" in em.get("filename", "").lower():
            screener_type = "follow-through"

        candidates = parse_csv(em["csv_content"])
        for c in candidates:
            c.screener_type = screener_type
        all_candidates.extend(candidates)

    # Pre-Filter
    filtered = [c for c in all_candidates if c.pct_change >= min_gap]

    # Sortieren nach Gap absteigend
    filtered.sort(key=lambda c: c.pct_change, reverse=True)

    return filtered
