"""Citrini Holdings-Diff.

Vergleicht zwei Holdings-Exports (Citrindex oder 26TF26, identisches Format)
und liefert Neuaufnahmen, Schliessungen, Gewichtsaenderungen und Basket-
Aenderungen. Vorfilter fuer Satelliten-Kandidaten: aggregiertes Gewicht
>= min_weight ODER Top-3 innerhalb des Baskets.
"""

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SUMMARY_ROWS = {"Net exposure", "Long exposure", "Short exposure", "Cash", "Gross exposure"}
WEIGHT_COL = "Citrindex Allocation"
US_SUFFIX = "US"


@dataclass
class Holdings:
    """Geparster Holdings-Export: Einzelpositionen + Basket-Gewichte."""

    positions: pd.DataFrame  # ticker, name, basket, weight, mic (eine Zeile pro Basket-Zugehoerigkeit)
    baskets: pd.DataFrame  # basket, weight


@dataclass
class DiffResult:
    added: pd.DataFrame = field(default_factory=pd.DataFrame)
    closed: pd.DataFrame = field(default_factory=pd.DataFrame)
    changed: pd.DataFrame = field(default_factory=pd.DataFrame)
    baskets_added: pd.DataFrame = field(default_factory=pd.DataFrame)
    baskets_closed: pd.DataFrame = field(default_factory=pd.DataFrame)
    baskets_changed: pd.DataFrame = field(default_factory=pd.DataFrame)


def load_holdings(path: str | Path) -> Holdings:
    """Liest einen Citrini-Holdings-Export (xlsx, ein Sheet)."""
    df = pd.read_excel(path, sheet_name=0)
    if WEIGHT_COL not in df.columns:
        raise ValueError(f"{path}: Spalte '{WEIGHT_COL}' fehlt — kein Citrini-Holdings-Export?")

    positions: list[dict] = []
    baskets: list[dict] = []
    current_basket = None
    for _, row in df.iterrows():
        ticker = row.get("Ticker")
        sec = row.get("Security Ticker")
        if pd.notna(ticker) and pd.isna(sec):
            if ticker in SUMMARY_ROWS:
                continue
            if pd.notna(row.get("Last Price")):
                # Direkte Portfolio-Position (z.B. Einzel-ETF, Options-Position)
                positions.append({
                    "ticker": str(ticker),
                    "name": row.get("Name"),
                    "basket": "(Portfolio)",
                    "weight": float(row[WEIGHT_COL]),
                    "mic": row.get("MIC Primary Exchange"),
                })
            else:
                current_basket = str(ticker)
                baskets.append({"basket": current_basket, "weight": float(row[WEIGHT_COL])})
        elif pd.notna(sec) and sec != "Cash":
            positions.append({
                "ticker": str(sec),
                "name": row.get("Name"),
                "basket": current_basket,
                "weight": float(row[WEIGHT_COL]),
                "mic": row.get("MIC Primary Exchange"),
            })

    return Holdings(positions=pd.DataFrame(positions), baskets=pd.DataFrame(baskets))


def is_us_listing(ticker: str) -> bool:
    """'AAPL US Equity' / 'MTD US' -> True, '6954 JP Equity' -> False."""
    parts = ticker.split()
    return len(parts) >= 2 and parts[1] == US_SUFFIX


def _aggregate(holdings: Holdings) -> pd.DataFrame:
    """Netto-Gewicht pro Ticker ueber alle Baskets."""
    if holdings.positions.empty:
        return pd.DataFrame(columns=["name", "weight", "baskets", "mic"])
    return holdings.positions.groupby("ticker").agg(
        name=("name", "first"),
        weight=("weight", "sum"),
        baskets=("basket", lambda b: ", ".join(sorted(set(b)))),
        mic=("mic", "first"),
    )


def _top3_tickers(holdings: Holdings) -> set[str]:
    """Ticker, die in mindestens einem Basket zu den Top 3 nach Gewicht gehoeren."""
    pos = holdings.positions
    if pos.empty:
        return set()
    ranked = pos.assign(rank=pos.groupby("basket")["weight"].rank(ascending=False, method="min"))
    return set(ranked[ranked["rank"] <= 3]["ticker"])


def diff_holdings(
    old: Holdings,
    new: Holdings,
    threshold: float = 0.25,
    min_weight: float = 0.4,
    basket_threshold: float = 1.0,
) -> DiffResult:
    """Diff zweier Holdings-Staende.

    threshold: Mindest-|Gewichtsaenderung| in %-Punkten fuer 'changed'.
    min_weight: Kandidaten-Kriterium fuer Neuaufnahmen (aggregiertes Gewicht).
    basket_threshold: Mindest-|Aenderung| fuer Basket-Gewichtsaenderungen.
    """
    o, n = _aggregate(old), _aggregate(new)
    top3 = _top3_tickers(new)

    added = n[~n.index.isin(o.index)].copy()
    if not added.empty:
        added["us_listing"] = [is_us_listing(t) for t in added.index]
        added["top3_im_basket"] = [t in top3 for t in added.index]
        added["kandidat"] = (added["weight"].abs() >= min_weight) | added["top3_im_basket"]
        added = added.sort_values("weight", ascending=False)

    closed = o[~o.index.isin(n.index)].sort_values("weight", ascending=False)

    common = n.join(o[["weight"]], rsuffix="_old", how="inner")
    common["delta"] = common["weight"] - common["weight_old"]
    changed = common[common["delta"].abs() >= threshold].sort_values("delta", ascending=False)

    ob = old.baskets.set_index("basket") if not old.baskets.empty else pd.DataFrame(columns=["weight"])
    nb = new.baskets.set_index("basket") if not new.baskets.empty else pd.DataFrame(columns=["weight"])
    baskets_added = nb[~nb.index.isin(ob.index)]
    baskets_closed = ob[~ob.index.isin(nb.index)]
    bcommon = nb.join(ob[["weight"]], rsuffix="_old", how="inner")
    bcommon["delta"] = bcommon["weight"] - bcommon["weight_old"]
    baskets_changed = bcommon[bcommon["delta"].abs() >= basket_threshold].sort_values("delta", ascending=False)

    return DiffResult(
        added=added,
        closed=closed,
        changed=changed,
        baskets_added=baskets_added,
        baskets_closed=baskets_closed,
        baskets_changed=baskets_changed,
    )


def diff_to_dict(result: DiffResult) -> dict:
    """JSON-serialisierbare Form fuer --format json (Wachtel / externe Agents)."""

    def records(df: pd.DataFrame, index_name: str = "ticker") -> list[dict]:
        if df.empty:
            return []
        return df.reset_index(names=index_name).to_dict(orient="records")

    return {
        "neuaufnahmen": records(result.added),
        "schliessungen": records(result.closed),
        "gewichtsaenderungen": records(result.changed),
        "baskets_neu": records(result.baskets_added, "basket"),
        "baskets_geschlossen": records(result.baskets_closed, "basket"),
        "baskets_gewichtsaenderungen": records(result.baskets_changed, "basket"),
    }
