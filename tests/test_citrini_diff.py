"""Tests fuer den Citrini Holdings-Diff."""

import pandas as pd
import pytest

from eule.citrini.diff import diff_holdings, diff_to_dict, is_us_listing, load_holdings

COLUMNS = [
    "Ticker", "Security Ticker", "Name", "Citrindex Allocation",
    "Last Price", "MIC Primary Exchange",
]


def _write_holdings(path, rows):
    pd.DataFrame(rows, columns=COLUMNS).to_excel(path, index=False)
    return path


@pytest.fixture
def old_file(tmp_path):
    return _write_holdings(tmp_path / "old.xlsx", [
        ("Net exposure", None, None, 98.0, None, None),
        ("Dynamic AI", None, None, 20.0, None, None),
        (None, "AAPL US Equity", "APPLE INC", 1.5, 300.0, "XNGS"),
        (None, "CEVA US Equity", "CEVA INC", 0.3, 20.0, "XNGS"),
        (None, "Cash", None, 0.0, None, None),
        ("Payments", None, None, 5.0, None, None),
        (None, "V US Equity", "VISA INC", 0.8, 250.0, "XNYS"),
        ("TLT US 07/31/26 P82.5 Equity", None, "July 26 Puts on TLT US", 0.12, 0.09, None),
    ])


@pytest.fixture
def new_file(tmp_path):
    return _write_holdings(tmp_path / "new.xlsx", [
        ("Net exposure", None, None, 98.0, None, None),
        ("Dynamic AI", None, None, 22.0, None, None),
        (None, "AAPL US Equity", "APPLE INC", 2.0, 310.0, "XNGS"),
        (None, "TINY US Equity", "TINY CORP", 0.1, 5.0, "XNYS"),
        (None, "6954 JP Equity", "FANUC CORP", 0.9, 40.0, "XTKS"),
        ("Robotics", None, None, 10.0, None, None),
        (None, "BOTZ US Equity", "BOTZ", 0.2, 30.0, "ARCX"),
        ("TLT US 07/31/26 P82.5 Equity", None, "July 26 Puts on TLT US", 0.12, 0.09, None),
    ])


def test_load_holdings(old_file):
    h = load_holdings(old_file)
    # Cash- und Summary-Zeilen ignoriert, TLT-Put als direkte Portfolio-Position
    assert len(h.positions) == 4
    tlt = h.positions[h.positions["ticker"].str.startswith("TLT")]
    assert tlt.iloc[0]["basket"] == "(Portfolio)"
    assert set(h.baskets["basket"]) == {"Dynamic AI", "Payments"}


def test_diff(old_file, new_file):
    result = diff_holdings(load_holdings(old_file), load_holdings(new_file))

    assert set(result.added.index) == {"TINY US Equity", "6954 JP Equity", "BOTZ US Equity"}
    # FANUC: Gewicht >= 0.4 -> Kandidat; BOTZ: einziger Titel im Basket -> Top-3-Kandidat
    assert bool(result.added.loc["6954 JP Equity", "kandidat"])
    assert not bool(result.added.loc["6954 JP Equity", "us_listing"])
    assert bool(result.added.loc["BOTZ US Equity", "kandidat"])
    # TINY: 0.1 Gewicht, aber Top-3 im Basket Dynamic AI (nur 3 Titel) -> Kandidat
    assert bool(result.added.loc["TINY US Equity", "top3_im_basket"])

    assert set(result.closed.index) == {"CEVA US Equity", "V US Equity"}
    assert list(result.changed.index) == ["AAPL US Equity"]
    assert result.changed.loc["AAPL US Equity", "delta"] == pytest.approx(0.5)

    assert list(result.baskets_added.index) == ["Robotics"]
    assert list(result.baskets_closed.index) == ["Payments"]
    assert list(result.baskets_changed.index) == ["Dynamic AI"]


def test_diff_identical(old_file):
    h = load_holdings(old_file)
    result = diff_holdings(h, h)
    for df in (result.added, result.closed, result.changed,
               result.baskets_added, result.baskets_closed, result.baskets_changed):
        assert df.empty


def test_diff_to_dict(old_file, new_file):
    result = diff_holdings(load_holdings(old_file), load_holdings(new_file))
    d = diff_to_dict(result)
    assert {r["ticker"] for r in d["neuaufnahmen"]} == {"TINY US Equity", "6954 JP Equity", "BOTZ US Equity"}
    assert d["baskets_neu"][0]["basket"] == "Robotics"


def test_is_us_listing():
    assert is_us_listing("AAPL US Equity")
    assert is_us_listing("MTD US")
    assert not is_us_listing("6954 JP Equity")
    assert not is_us_listing("AAPL")
