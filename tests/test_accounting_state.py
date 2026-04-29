"""Tests fuer eule.accounting.state — SoF-basierter Loader."""

from datetime import date

import pytest

from eule.accounting.cash import (
    CashDeposit,
    CashExpense,
    CashLedger,
    CashTransfer,
    CashWithdrawal,
    filter_out_broker_expenses,
)
from eule.accounting.import_sof import FeeAggregate, TradeAggregate
from eule.accounting.state import (
    SofStateError,
    fee_to_expense,
    list_sof_files,
    load_state_from_sof,
    state_summary,
    trade_to_roundtrip,
)


# ── filter_out_broker_expenses ──────────────────────────


def test_filter_out_broker_expenses_keeps_giro():
    cash = CashLedger(
        deposits=[CashDeposit(date(2025, 1, 1), "A", 100.0)],
        withdrawals=[],
        expenses=[
            CashExpense(date(2025, 1, 1), 8.90, "Konto", paid_from="giro"),
            CashExpense(date(2025, 1, 2), 5.00, "IBKR", paid_from="broker"),
            CashExpense(date(2025, 1, 3), 6.50, "IT", paid_from="giro"),
        ],
        transfers=[],
    )

    filtered = filter_out_broker_expenses(cash)

    assert len(filtered.expenses) == 2
    assert all(e.paid_from == "giro" for e in filtered.expenses)
    # Andere Felder unveraendert
    assert filtered.deposits == cash.deposits


# ── trade_to_roundtrip ──────────────────────────────────


def test_trade_to_roundtrip_positive_pnl():
    t = TradeAggregate(
        posting_date=date(2025, 6, 15),
        description="MES 20JUN25",
        asset_class="FUT",
        pnl_eur=123.45,
        count=3,
    )

    rt = trade_to_roundtrip(t)

    assert abs(rt.pnl - 123.45) < 1e-9
    assert rt.exit_date == date(2025, 6, 15)
    assert "MES 20JUN25" in rt.symbol
    assert "(FUT)" in rt.symbol
    assert rt.strategy_key == "sof"


def test_trade_to_roundtrip_negative_pnl():
    t = TradeAggregate(
        posting_date=date(2025, 7, 1),
        description="ESTX50",
        asset_class="OPT",
        pnl_eur=-50.00,
        count=2,
    )

    rt = trade_to_roundtrip(t)

    assert abs(rt.pnl - (-50.00)) < 1e-9


# ── fee_to_expense ──────────────────────────────────────


def test_fee_to_expense_aufwand_positive_amount():
    """SoF-netto -10 EUR (Aufwand) -> CashExpense.amount_eur = +10."""
    f = FeeAggregate(posting_date=date(2025, 5, 1), netto_eur=-10.0, count=4)

    exp = fee_to_expense(f)

    assert exp.amount_eur == 10.0
    assert exp.paid_from == "broker"
    assert "4 Posten" in exp.note


def test_fee_to_expense_storno_negative_amount():
    """SoF-netto +5 EUR (Storno) -> CashExpense.amount_eur = -5."""
    f = FeeAggregate(posting_date=date(2025, 5, 2), netto_eur=5.0, count=1)

    exp = fee_to_expense(f)

    assert exp.amount_eur == -5.0
    assert exp.paid_from == "broker"


# ── load_state_from_sof ──────────────────────────────────


SOF_CSV_MINIMAL = (
    '"AssetClass","Description","Conid","FXRateToBase","Amount",'
    '"CurrencyPrimary","SettleDate","Date","ReportDate","Balance",'
    '"TradePrice","TradeGross","TradeCommission","Expiry","TradeCode",'
    '"LevelOfDetail"\n'
    '"FUT","MES 20JUN25","123","1","100","EUR","20250620","20250615",'
    '"20250615","100","1","100","0","20250620","","BaseCurrency"\n'
    '"","","","1","-8.50","EUR","","20250630","20250630","91.50",'
    '"0","0","0","","","BaseCurrency"\n'
)


def test_load_state_from_sof_aggregates_trades_and_fees(tmp_path):
    sof_dir = tmp_path / "sof"
    sof_dir.mkdir()
    (sof_dir / "sof-test.csv").write_text(SOF_CSV_MINIMAL)
    (tmp_path / "cash.yaml").write_text(
        "deposits:\n"
        "  - { date: 2025-01-01, holder: A, amount_eur: 1000 }\n"
        "expenses:\n"
        "  - { date: 2025-02-01, amount_eur: 8.90, note: Konto }\n"
        "  - { date: 2025-03-01, amount_eur: 5.00, paid_from: broker, "
        "note: 'IBKR-Cash-Adjustments (1 Posten)' }\n"
    )

    rts, cash = load_state_from_sof(
        sof_directory=sof_dir, cash_path=tmp_path / "cash.yaml"
    )

    # 1 Trade (FUT, 100 EUR)
    assert len(rts) == 1
    assert abs(rts[0].pnl - 100.0) < 1e-9

    # giro-Expense bleibt, broker-Expense aus cash.yaml fliegt raus,
    # SoF-Fee (8.50 EUR Aufwand) kommt dazu
    assert cash.deposits == [CashDeposit(date(2025, 1, 1), "A", 1000.0)]
    paid_from_giro = [e for e in cash.expenses if e.paid_from == "giro"]
    paid_from_broker = [e for e in cash.expenses if e.paid_from == "broker"]
    assert len(paid_from_giro) == 1
    assert paid_from_giro[0].amount_eur == 8.90
    assert len(paid_from_broker) == 1
    assert paid_from_broker[0].amount_eur == 8.50
    assert paid_from_broker[0].date == date(2025, 6, 30)


def test_load_state_from_sof_raises_when_no_files(tmp_path):
    sof_dir = tmp_path / "sof-empty"
    sof_dir.mkdir()

    with pytest.raises(SofStateError, match="Keine SoF-CSV"):
        load_state_from_sof(
            sof_directory=sof_dir, cash_path=tmp_path / "cash.yaml"
        )


def test_load_state_from_sof_dedupes_across_files(tmp_path):
    """Wenn dieselbe Zeile in zwei CSVs steht, darf der Trade nicht doppelt gezaehlt werden."""
    sof_dir = tmp_path / "sof"
    sof_dir.mkdir()
    (sof_dir / "sof-a.csv").write_text(SOF_CSV_MINIMAL)
    (sof_dir / "sof-b.csv").write_text(SOF_CSV_MINIMAL)
    (tmp_path / "cash.yaml").write_text("deposits: []\n")

    rts, cash = load_state_from_sof(
        sof_directory=sof_dir, cash_path=tmp_path / "cash.yaml"
    )

    assert len(rts) == 1
    assert abs(rts[0].pnl - 100.0) < 1e-9
    sof_expenses = [e for e in cash.expenses if e.paid_from == "broker"]
    assert len(sof_expenses) == 1


def test_list_sof_files_sorted(tmp_path):
    (tmp_path / "sof-2025.csv").write_text("")
    (tmp_path / "sof-2024.csv").write_text("")
    (tmp_path / "sof-current.csv").write_text("")

    files = list_sof_files(tmp_path)

    assert [f.name for f in files] == [
        "sof-2024.csv",
        "sof-2025.csv",
        "sof-current.csv",
    ]


# ── summary helper ──────────────────────────────────────


def test_state_summary_counts(tmp_path):
    cash = CashLedger(
        deposits=[CashDeposit(date(2025, 1, 1), "A", 1000.0)],
        withdrawals=[CashWithdrawal(date(2025, 6, 1), "A", 500.0)],
        expenses=[CashExpense(date(2025, 2, 1), 8.90)],
        transfers=[CashTransfer(date(2025, 1, 2), "giro", "broker", 1000.0)],
    )
    summary = state_summary([], cash)

    assert summary["roundtrips"] == 0
    assert summary["pnl_total"] == 0.0
    assert summary["deposits"] == 1
    assert summary["deposits_total"] == 1000.0
    assert summary["expenses_total"] == 8.90
