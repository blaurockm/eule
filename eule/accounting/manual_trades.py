"""Loader fuer manuell gepflegte Trades aus tradingGbr/manual_trades.yaml.

Use-Case: Trades, die nicht in der Hase-DB landen (z.B. weil Hase aus war,
oder Trades vor Hase-Inbetriebnahme), trotzdem buchhalterisch erfassen.

Format ist bewusst minimal — fuer die Buchhaltung reichen Datum, Symbol
und Netto-PnL. Entry/Exit-Details werden nicht benoetigt, weil allocate_pnl()
nur den PnL-Wert konsumiert. Die erzeugten Roundtrips haben dummy-Felder
fuer entry_value/exit_value, sodass Roundtrip.pnl den gewuenschten Wert liefert.
"""

from datetime import date, datetime, time, timezone
from pathlib import Path

import yaml

from eule.accounting.config import AccountingConfigError, tradinggbr_dir
from eule.models import Roundtrip


def _parse_date(value) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _to_roundtrip(d: date, symbol: str, pnl: float, note: str) -> Roundtrip:
    """Konstruiert einen Roundtrip-Dummy mit gewuenschtem Netto-PnL.

    Roundtrip.pnl = entry_value - exit_value - total_fees (fuer entry_side='sell').
    Mit fees=0 ist also: entry - exit = pnl.
    """
    ts = datetime.combine(d, time.min, tzinfo=timezone.utc)
    entry_value = max(pnl, 0.0)
    exit_value = max(-pnl, 0.0)
    return Roundtrip(
        strategy_key="manual",
        symbol=symbol if not note else f"{symbol} ({note})",
        asset_class="manual",
        entry_ts=ts,
        entry_date=d,
        entry_side="sell",
        entry_qty=1.0,
        entry_price=0.0,
        entry_value=entry_value,
        entry_fees=0.0,
        exit_ts=ts,
        exit_date=d,
        exit_side="buy",
        exit_qty=1.0,
        exit_price=0.0,
        exit_value=exit_value,
        exit_fees=0.0,
        exit_is_expiry=False,
    )


def load_manual_trades(path: Path | None = None) -> list[Roundtrip]:
    """Liest manual_trades.yaml und gibt eine Roundtrip-Liste zurueck.
    Fehlt die Datei, wird eine leere Liste zurueckgegeben.
    """
    if path is None:
        path = tradinggbr_dir() / "manual_trades.yaml"
    path = path.expanduser()

    if not path.exists():
        return []

    with open(path) as f:
        raw = yaml.safe_load(f) or {}

    rts: list[Roundtrip] = []
    for idx, entry in enumerate(raw.get("manual_trades") or []):
        try:
            d = _parse_date(entry["date"])
            symbol = str(entry["symbol"])
            pnl = float(entry["pnl_eur"])
            note = str(entry.get("note", ""))
        except (KeyError, ValueError) as e:
            raise AccountingConfigError(
                f"Fehler in {path} bei Eintrag #{idx}: {e}"
            ) from e
        rts.append(_to_roundtrip(d, symbol, pnl, note))

    return rts
