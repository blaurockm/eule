"""IBKR Flex Web Service Client fuer Statement of Funds.

Holt SoF-CSV via 2-stufigem Protokoll:
1. SendRequest mit Token + QueryID -> ReferenceCode
2. GetStatement mit Token + ReferenceCode -> CSV (mit Polling, da
   IBKR das Statement asynchron generiert)

Endpoints (v=3):
  SendRequest:  https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.SendRequest
  GetStatement: https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService.GetStatement

Token + Query-ID kommen aus den Umgebungsvariablen
EULE_IBKR_FLEX_TOKEN und EULE_IBKR_FLEX_QUERY_ID.

Setup im IBKR Account Management:
- Reporting -> Settings -> Flex Web Service -> Token generieren (1 Jahr gueltig)
- Reporting -> Flex Queries -> Activity Flex Query anlegen mit Section
  "Statement of Funds", LevelOfDetail=BaseCurrency, Format=CSV,
  Period=Year to Date
"""

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

FLEX_BASE = (
    "https://gdcdyn.interactivebrokers.com/Universal/servlet/FlexStatementService"
)
FLEX_VERSION = 3

# Polling: IBKR braucht meist 5-30s, in Spitzen bis 2min
POLL_INTERVAL_SEC = 5.0
POLL_TIMEOUT_SEC = 180.0

# Fehlercode "Statement generation in progress" — heisst weiter pollen
ERROR_NOT_READY = "1019"


class FlexError(Exception):
    """Fehler beim Abruf vom IBKR Flex Web Service."""


def _env_required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise FlexError(
            f"Umgebungsvariable {name} nicht gesetzt. "
            f"Trage Token + Query-ID in .env ein."
        )
    return val


def request_statement(
    token: str, query_id: str, *, client: httpx.Client | None = None
) -> str:
    """Stoesst die Statement-Generierung an. Liefert ReferenceCode."""
    url = f"{FLEX_BASE}.SendRequest"
    params = {"t": token, "q": query_id, "v": FLEX_VERSION}

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=30.0)

    try:
        resp = client.get(url, params=params)
        resp.raise_for_status()
    finally:
        if owns_client:
            client.close()

    root = _parse_xml(resp.text)
    status = (root.findtext("Status") or "").strip()
    if status != "Success":
        code = root.findtext("ErrorCode") or "?"
        msg = root.findtext("ErrorMessage") or resp.text
        raise FlexError(f"SendRequest fehlgeschlagen ({code}): {msg}")

    ref = root.findtext("ReferenceCode")
    if not ref or not ref.strip():
        raise FlexError(f"Kein ReferenceCode in Response: {resp.text[:200]}")
    return ref.strip()


def fetch_statement(
    token: str,
    reference_code: str,
    *,
    client: httpx.Client | None = None,
    poll_interval: float = POLL_INTERVAL_SEC,
    poll_timeout: float = POLL_TIMEOUT_SEC,
    sleep: callable = time.sleep,
) -> str:
    """Pollt GetStatement bis CSV verfuegbar. Liefert CSV-Text.

    `sleep` ist injizierbar fuer Tests.
    """
    url = f"{FLEX_BASE}.GetStatement"
    params = {"t": token, "q": reference_code, "v": FLEX_VERSION}

    owns_client = client is None
    if owns_client:
        client = httpx.Client(timeout=60.0)

    deadline = time.monotonic() + poll_timeout
    try:
        while True:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            text = resp.text

            # CSV beginnt nicht mit '<' — wenn doch, ist es eine XML-Antwort
            # vom Flex-Service (Status/Warn/Fail), keine Daten.
            if not text.lstrip().startswith("<"):
                return text

            try:
                root = ET.fromstring(text)
            except ET.ParseError:
                # Kein gueltiges XML — vermutlich CSV mit Leerzeichen am Anfang
                return text

            status = (root.findtext("Status") or "").strip()
            code = (root.findtext("ErrorCode") or "?").strip()
            msg = root.findtext("ErrorMessage") or "(keine Nachricht)"

            if status == "Warn" and code == ERROR_NOT_READY:
                if time.monotonic() >= deadline:
                    raise FlexError(
                        f"Timeout nach {poll_timeout}s — "
                        f"Statement nicht fertig ({msg})"
                    )
                sleep(poll_interval)
                continue

            raise FlexError(f"GetStatement fehlgeschlagen ({code}): {msg}")
    finally:
        if owns_client:
            client.close()


def fetch_sof_csv(
    *,
    token: str | None = None,
    query_id: str | None = None,
) -> str:
    """End-to-end: SendRequest + Polling. Liefert CSV-Text.

    Liest Token/QueryID aus EULE_IBKR_FLEX_TOKEN / EULE_IBKR_FLEX_QUERY_ID
    wenn nicht direkt uebergeben.
    """
    if token is None:
        token = _env_required("EULE_IBKR_FLEX_TOKEN")
    if query_id is None:
        query_id = _env_required("EULE_IBKR_FLEX_QUERY_ID")

    with httpx.Client(timeout=60.0) as client:
        ref = request_statement(token, query_id, client=client)
        return fetch_statement(token, ref, client=client)


def sof_dir() -> Path:
    """Verzeichnis fuer SoF-CSV-Cache: <tradinggbr_dir>/sof/"""
    from eule.accounting.config import tradinggbr_dir

    d = tradinggbr_dir() / "sof"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sof_current_path() -> Path:
    """Default-Pfad fuer die laufend aktualisierte Cache-CSV: <sof_dir>/sof-current.csv

    Eine Datei, wird bei jedem fetch ueberschrieben. Inhalt = was die
    konfigurierte Flex-Query aktuell liefert (YTD, 365d, ...).

    Archiv-CSVs (`sof-2024.csv`, `sof-2025.csv`, ...) liegen daneben und
    werden via glob `sof/*.csv` gemeinsam gelesen + dedupliziert.
    """
    return sof_dir() / "sof-current.csv"


def _parse_xml(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        raise FlexError(
            f"Antwort ist kein gueltiges XML: {e}\nResponse: {text[:200]}"
        ) from e
