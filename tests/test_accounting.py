"""Tests fuer eule.accounting — GbR-Buchhaltung."""

from datetime import date, datetime, timezone

import pytest

from eule.accounting.allocator import allocate_expense, allocate_pnl
from eule.accounting.balances import compute_balances
from eule.accounting.cash import (
    CashDeposit,
    CashExpense,
    CashLedger,
    CashTransfer,
    CashWithdrawal,
)
from eule.accounting.config import (
    AccountingConfig,
    HolderDef,
    PerformanceFee,
)
from eule.accounting.journal import build_journal
from eule.accounting.ledger import compute_account_balances
from eule.accounting.manual_trades import _to_roundtrip, load_manual_trades
from eule.accounting.tax import tax_report
from eule.models import Roundtrip


def _cfg(fee_pct: float = 0.10, use_hase_db: bool = True) -> AccountingConfig:
    return AccountingConfig(
        env="real2-ibkr",
        base_currency="EUR",
        holders=[
            HolderDef(id="A", name="Markus", capital_share=0.5),
            HolderDef(id="B", name="Partner", capital_share=0.5),
        ],
        operator="A",
        performance_fee=PerformanceFee(
            pct=fee_pct,
            base="per_winning_roundtrip",
            recipient="A",
        ),
        fiscal_year_start="01-01",
        balances_json_path="balances.json",
        use_hase_db=use_hase_db,
    )


def _rt(pnl: float, exit_date: date | None = None) -> Roundtrip:
    """Konstruiert einen Roundtrip mit dem gewuenschten netto-PnL.
    Setzt entry_value = pnl + 1.05 (Fee), exit_value = 0 -> pnl = entry - exit - fee = pnl.
    """
    fee = 1.05
    entry_value = pnl + fee  # so dass pnl genau passt
    exit_date = exit_date or date(2025, 6, 1)
    entry_date = date(exit_date.year, exit_date.month, max(1, exit_date.day - 5))
    return Roundtrip(
        strategy_key="test",
        symbol="TEST 250601P00100",
        asset_class="OPT",
        entry_ts=datetime.combine(entry_date, datetime.min.time(), tzinfo=timezone.utc),
        entry_date=entry_date,
        entry_side="sell",
        entry_qty=1.0,
        entry_price=entry_value / 100,
        entry_value=entry_value,
        entry_fees=fee,
        exit_ts=datetime.combine(exit_date, datetime.min.time(), tzinfo=timezone.utc),
        exit_date=exit_date,
        exit_side="buy",
        exit_qty=1.0,
        exit_price=0.0,
        exit_value=0.0,
        exit_fees=0.0,
        exit_is_expiry=True,
    )


# ─────────────────────────────────────────
# Allocator
# ─────────────────────────────────────────


class TestAllocator:
    def test_winning_trade_60_40_split(self):
        """Gewinn 100€ -> A=60, B=40 mit 10% Fee."""
        alloc = allocate_pnl(100.0, _cfg())
        assert alloc.operator_share == pytest.approx(60.0)
        assert alloc.other_share == pytest.approx(40.0)
        assert alloc.performance_fee == pytest.approx(10.0)
        assert alloc.capital_share_operator == pytest.approx(50.0)
        assert alloc.capital_share_other == pytest.approx(50.0)

    def test_losing_trade_50_50_split(self):
        """Verlust -100€ -> A=-50, B=-50, KEINE Fee."""
        alloc = allocate_pnl(-100.0, _cfg())
        assert alloc.operator_share == pytest.approx(-50.0)
        assert alloc.other_share == pytest.approx(-50.0)
        assert alloc.performance_fee == 0.0

    def test_zero_pnl_no_fee(self):
        alloc = allocate_pnl(0.0, _cfg())
        assert alloc.performance_fee == 0.0
        assert alloc.operator_share == 0.0
        assert alloc.other_share == 0.0

    def test_mix_of_trades_per_trade_logic(self):
        """Mix +100, -50: Fee NUR auf +100, nicht auf Saldo +50."""
        cfg = _cfg()
        win = allocate_pnl(100.0, cfg)
        loss = allocate_pnl(-50.0, cfg)
        a_total = win.operator_share + loss.operator_share
        b_total = win.other_share + loss.other_share
        # A: 60 + (-25) = 35
        # B: 40 + (-25) = 15
        # Sum: 50 (= net P&L)
        assert a_total == pytest.approx(35.0)
        assert b_total == pytest.approx(15.0)
        assert (a_total + b_total) == pytest.approx(50.0)

    def test_expense_split(self):
        out = allocate_expense(100.0, _cfg())
        assert out["A"] == pytest.approx(50.0)
        assert out["B"] == pytest.approx(50.0)


# ─────────────────────────────────────────
# Balances (berechnete Sicht)
# ─────────────────────────────────────────


class TestBalances:
    def test_only_deposits(self):
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 50000, ""),
                CashDeposit(date(2024, 1, 15), "B", 50000, ""),
            ],
            withdrawals=[],
            expenses=[],
        )
        b = compute_balances([], cash, _cfg())
        assert b["A"].balance == pytest.approx(50000.0)
        assert b["B"].balance == pytest.approx(50000.0)

    def test_with_winning_and_losing_trades(self):
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 50000, ""),
                CashDeposit(date(2024, 1, 15), "B", 50000, ""),
            ],
            withdrawals=[],
            expenses=[],
        )
        rts = [_rt(100.0), _rt(-50.0)]
        b = compute_balances(rts, cash, _cfg())
        # A: 50000 + 35 = 50035
        # B: 50000 + 15 = 50015
        assert b["A"].balance == pytest.approx(50035.0)
        assert b["B"].balance == pytest.approx(50015.0)

    def test_with_withdrawal_and_expense(self):
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 1000, ""),
                CashDeposit(date(2024, 1, 15), "B", 1000, ""),
            ],
            withdrawals=[CashWithdrawal(date(2025, 1, 1), "A", 200, "")],
            expenses=[CashExpense(date(2024, 6, 1), 100.0, "Tradingview")],
        )
        b = compute_balances([], cash, _cfg())
        # A: 1000 - 200 - 50 = 750
        # B: 1000 - 50 = 950
        assert b["A"].balance == pytest.approx(750.0)
        assert b["B"].balance == pytest.approx(950.0)

    def test_balance_split_into_broker_and_giro(self):
        """Wenn Geld auf Giro liegt, wird der Holder-Saldo proportional aufgeteilt."""
        cfg = _cfg()
        # Einlage 1000 ans Giro, Transfer 800 aufs Broker → Giro=200, Broker=800
        cash = CashLedger(
            deposits=[CashDeposit(date(2024, 1, 1), "A", 1000, "")],
            transfers=[CashTransfer(date(2024, 1, 2), "giro", "broker", 800.0, "")],
        )
        b = compute_balances([], cash, cfg)
        # A.balance = 1000, davon Broker 80% (800/1000), Giro 20% (200/1000)
        assert b["A"].balance == pytest.approx(1000.0)
        assert b["A"].balance_broker == pytest.approx(800.0)
        assert b["A"].balance_giro == pytest.approx(200.0)

    def test_balance_split_zero_giro(self):
        """Wenn Giro = 0 ist, liegt der gesamte Holder-Saldo auf Broker."""
        cfg = _cfg()
        cash = CashLedger(
            deposits=[CashDeposit(date(2024, 1, 1), "A", 1000, "")],
            transfers=[CashTransfer(date(2024, 1, 2), "giro", "broker", 1000.0, "")],
        )
        b = compute_balances([], cash, cfg)
        assert b["A"].balance_broker == pytest.approx(1000.0)
        assert b["A"].balance_giro == pytest.approx(0.0)

    def test_balance_conservation(self):
        """Summe aller Holder-Salden == Summe Deposits + PnL - Expenses - Withdrawals."""
        cfg = _cfg()
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 50000, ""),
                CashDeposit(date(2024, 1, 15), "B", 50000, ""),
            ],
            withdrawals=[CashWithdrawal(date(2025, 1, 1), "A", 5000, "")],
            expenses=[CashExpense(date(2024, 6, 1), 240.0, "")],
        )
        rts = [_rt(500.0), _rt(-200.0), _rt(300.0)]
        b = compute_balances(rts, cash, cfg)

        total_deposits = 100000.0
        total_pnl = 600.0
        total_withdrawals = 5000.0
        total_expenses = 240.0
        expected = total_deposits + total_pnl - total_withdrawals - total_expenses
        actual = b["A"].balance + b["B"].balance
        assert actual == pytest.approx(expected, abs=0.01)


# ─────────────────────────────────────────
# Journal (Doppik) + Ledger-Konsistenz
# ─────────────────────────────────────────


class TestJournal:
    def test_winning_trade_creates_four_postings(self):
        """Gewinn-Trade: 3 Buchungen (Verrechnung, Anteil A, Anteil B) + Fee = 4."""
        cfg = _cfg()
        cash = CashLedger([], [], [])
        rts = [_rt(100.0)]
        postings = build_journal(rts, cash, cfg)
        assert len(postings) == 4
        # Gesamtsumme der Buchungen: Brutto + 2*Anteil + Fee = 100 + 50 + 50 + 10 = 210
        assert sum(p.amount_eur for p in postings) == pytest.approx(210.0)

    def test_losing_trade_creates_three_postings(self):
        """Verlust: nur 3 Buchungen, keine Fee."""
        cfg = _cfg()
        rts = [_rt(-50.0)]
        postings = build_journal(rts, CashLedger([], [], []), cfg)
        assert len(postings) == 3
        sources = {p.source for p in postings}
        assert "performance_fee" not in sources

    def test_journal_balance_via_account_totals(self):
        """Sum(Debits) je Konto - Sum(Credits) je Konto: globale Summe muss 0 sein."""
        cfg = _cfg()
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 50000, ""),
                CashDeposit(date(2024, 1, 15), "B", 50000, ""),
            ],
            withdrawals=[CashWithdrawal(date(2025, 1, 1), "A", 5000, "")],
            expenses=[CashExpense(date(2024, 6, 1), 240.0, "")],
        )
        rts = [_rt(500.0), _rt(-200.0)]
        postings = build_journal(rts, cash, cfg)
        balances = compute_account_balances(postings)
        sum_balance = sum(b.balance for b in balances.values())
        assert sum_balance == pytest.approx(0.0, abs=0.01)

    def test_giro_path_only_books_expense(self):
        """expense paid_from=giro erzeugt 6000 an 1100 + 2x Holderanteil — KEIN Auto-Withdraw."""
        cfg = _cfg()
        cash = CashLedger(
            expenses=[CashExpense(date(2024, 6, 1), 100.0, "Test", paid_from="giro")],
        )
        postings = build_journal([], cash, cfg)
        assert len(postings) == 3  # expense + 2 holder shares
        balances = compute_account_balances(postings)
        # Giro hat -100 Soll (weil Expense vom Giro abging, ohne dass es vorher Geld bekam)
        # → in der Realitaet braucht es einen Transfer Broker→Giro davor
        assert balances["1100"].balance == pytest.approx(-100.0)
        # Broker bleibt unberuehrt (kein Auto-Withdraw mehr!)
        assert balances["1200"].balance == pytest.approx(0.0)

    def test_broker_path_skips_giro(self):
        """expense paid_from=broker erzeugt nur 6000 an 1200 + Holderanteile."""
        cfg = _cfg()
        cash = CashLedger(
            expenses=[CashExpense(date(2024, 6, 1), 50.0, "IBKR-Feed", paid_from="broker")],
        )
        postings = build_journal([], cash, cfg)
        assert len(postings) == 3
        balances = compute_account_balances(postings)
        # Giro unberuehrt, Broker hat -50
        assert balances["1100"].balance == pytest.approx(0.0)
        assert balances["1200"].balance == pytest.approx(-50.0)

    def test_transfer_broker_to_giro(self):
        """Cash-Transfer ohne Aufwand: 1100 an 1200, keine Holder-Bewegung."""
        cfg = _cfg()
        cash = CashLedger(
            transfers=[CashTransfer(date(2024, 6, 1), "broker", "giro", 500.0, "Reserve")],
        )
        postings = build_journal([], cash, cfg)
        assert len(postings) == 1
        balances = compute_account_balances(postings)
        assert balances["1100"].balance == pytest.approx(500.0)   # Giro +500
        assert balances["1200"].balance == pytest.approx(-500.0)  # Broker -500
        assert balances["0100"].balance == pytest.approx(0.0)     # Holder unberuehrt
        assert balances["0110"].balance == pytest.approx(0.0)

    def test_giro_funded_expense_with_transfer(self):
        """expense paid_from=giro + vorgaengiger Transfer: Giro endet bei 0."""
        cfg = _cfg()
        cash = CashLedger(
            transfers=[CashTransfer(date(2024, 6, 1), "broker", "giro", 100.0, "")],
            expenses=[CashExpense(date(2024, 6, 2), 100.0, "Test", paid_from="giro")],
        )
        postings = build_journal([], cash, cfg)
        balances = compute_account_balances(postings)
        assert balances["1100"].balance == pytest.approx(0.0)     # Transfer-rein, Aufwand-raus
        assert balances["1200"].balance == pytest.approx(-100.0)  # nur einmal abgebucht

    def test_balances_unchanged_by_paid_from_choice(self):
        """Holder-Salden duerfen nicht davon abhaengen, ob via Giro oder direkt."""
        cfg = _cfg()
        cash_giro = CashLedger(
            deposits=[CashDeposit(date(2024, 1, 1), "A", 1000, "")],
            expenses=[CashExpense(date(2024, 6, 1), 100.0, "", paid_from="giro")],
        )
        cash_broker = CashLedger(
            deposits=[CashDeposit(date(2024, 1, 1), "A", 1000, "")],
            expenses=[CashExpense(date(2024, 6, 1), 100.0, "", paid_from="broker")],
        )
        from eule.accounting.balances import compute_balances
        b_giro = compute_balances([], cash_giro, cfg)
        b_broker = compute_balances([], cash_broker, cfg)
        assert b_giro["A"].balance == pytest.approx(b_broker["A"].balance)
        assert b_giro["B"].balance == pytest.approx(b_broker["B"].balance)

    def test_balances_match_capital_account_balances(self):
        """Berechnete Sicht == Saldo der Kapital-Konten aus Doppik."""
        cfg = _cfg()
        cash = CashLedger(
            deposits=[
                CashDeposit(date(2024, 1, 15), "A", 50000, ""),
                CashDeposit(date(2024, 1, 15), "B", 50000, ""),
            ],
            withdrawals=[],
            expenses=[CashExpense(date(2024, 6, 1), 240.0, "")],
        )
        rts = [_rt(500.0), _rt(-200.0)]

        balances = compute_balances(rts, cash, cfg)
        postings = build_journal(rts, cash, cfg)
        accounts = compute_account_balances(postings)

        # Kapitalkonto = Eigenkapital (Credit-Saldo) -> -balance bei type=equity
        # debit - credit fuer 0100 = was am Konto steht.
        # Bei einem Eigenkapitalkonto: Habensaldo = abgegebene Mittel.
        # Convention im Code: balance = debit_total - credit_total.
        # Fuer Kapitalkonto erwarten wir balance == -holder.balance
        cap_a = accounts["0100"].balance
        cap_b = accounts["0110"].balance
        assert -cap_a == pytest.approx(balances["A"].balance, abs=0.01)
        assert -cap_b == pytest.approx(balances["B"].balance, abs=0.01)


# ─────────────────────────────────────────
# Steuer-Report
# ─────────────────────────────────────────


class TestTaxReport:
    def test_capital_income_50_50(self):
        """Kapitaleinkuenfte werden 50:50 aufgeteilt, unabhaengig von Fee."""
        cfg = _cfg()
        rts = [_rt(100.0), _rt(-30.0)]
        lines = tax_report(rts, cfg, expenses_total=0.0, year=2025)
        a = next(ln for ln in lines if ln.holder_id == "A")
        b = next(ln for ln in lines if ln.holder_id == "B")
        # Net pnl = 70, Kapitaleinkommen 50:50: 35 / 35
        assert a.capital_income == pytest.approx(35.0)
        assert b.capital_income == pytest.approx(35.0)

    def test_self_employment_only_for_operator(self):
        """Honorar steht nur beim Operator."""
        cfg = _cfg()
        rts = [_rt(100.0), _rt(-50.0)]
        lines = tax_report(rts, cfg, expenses_total=0.0, year=2025)
        a = next(ln for ln in lines if ln.holder_id == "A")
        b = next(ln for ln in lines if ln.holder_id == "B")
        # Honorar nur fuer +100, nicht fuer -50: 10€
        assert a.self_employment == pytest.approx(10.0)
        assert b.self_employment == 0.0

    def test_year_filter(self):
        cfg = _cfg()
        rts = [
            _rt(100.0, exit_date=date(2024, 6, 1)),
            _rt(200.0, exit_date=date(2025, 6, 1)),
        ]
        lines_2025 = tax_report(rts, cfg, expenses_total=0.0, year=2025)
        a = next(ln for ln in lines_2025 if ln.holder_id == "A")
        # Nur 2025: Kapitaleinkunft 100 (50% von 200), Honorar 20
        assert a.capital_income == pytest.approx(100.0)
        assert a.self_employment == pytest.approx(20.0)


# ─────────────────────────────────────────
# Manuelle Trades
# ─────────────────────────────────────────


class TestManualTrades:
    def test_to_roundtrip_positive_pnl(self):
        rt = _to_roundtrip(date(2025, 3, 15), "TLT", 250.0, "")
        assert rt.pnl == pytest.approx(250.0)
        assert rt.exit_date == date(2025, 3, 15)
        assert rt.strategy_key == "manual"

    def test_to_roundtrip_negative_pnl(self):
        rt = _to_roundtrip(date(2025, 3, 15), "TLT", -50.0, "")
        assert rt.pnl == pytest.approx(-50.0)

    def test_to_roundtrip_zero_pnl(self):
        rt = _to_roundtrip(date(2025, 3, 15), "TLT", 0.0, "")
        assert rt.pnl == pytest.approx(0.0)

    def test_loader_missing_file_returns_empty(self, tmp_path):
        rts = load_manual_trades(tmp_path / "does_not_exist.yaml")
        assert rts == []

    def test_loader_parses_yaml(self, tmp_path):
        path = tmp_path / "manual_trades.yaml"
        path.write_text(
            "manual_trades:\n"
            "  - { date: 2025-03-15, symbol: TLT, pnl_eur: 250.0, note: 'Stop' }\n"
            "  - { date: 2025-04-01, symbol: SPX, pnl_eur: -50.0 }\n"
        )
        rts = load_manual_trades(path)
        assert len(rts) == 2
        assert rts[0].pnl == pytest.approx(250.0)
        assert rts[1].pnl == pytest.approx(-50.0)
        assert "Stop" in rts[0].symbol  # note wird in Symbol angehaengt

    def test_manual_trade_flows_through_allocator(self):
        """Ein manueller Trade muss die Verteilungslogik genauso durchlaufen."""
        cfg = _cfg()
        rt = _to_roundtrip(date(2025, 3, 15), "TLT", 100.0, "")
        alloc = allocate_pnl(rt.pnl, cfg)
        assert alloc.operator_share == pytest.approx(60.0)
        assert alloc.other_share == pytest.approx(40.0)


# ─────────────────────────────────────────
# SoF-Importer (Statement of Funds, definitive Cash-Wahrheit)
# ─────────────────────────────────────────


class TestSofImporter:
    HEADER = (
        '"AssetClass","Description","Conid","FXRateToBase","Amount","CurrencyPrimary",'
        '"SettleDate","Date","ReportDate","Balance","TradePrice","TradeGross",'
        '"TradeCommission","Expiry","TradeCode","LevelOfDetail"\n'
    )

    def _csv(self, tmp_path, content: str, name: str = "sof.csv"):
        p = tmp_path / name
        p.write_text(content)
        return p

    def _row(self, asset_class, amount, date, desc=""):
        return (
            f'"{asset_class}","{desc}","","1","{amount}","EUR","{date}","{date}",'
            f'"{date}","100","0","0","0","","","BaseCurrency"\n'
        )

    def test_classify_trade_vs_fee_vs_transfer(self, tmp_path):
        from eule.accounting.import_sof import classify, parse_sof_csv
        path = self._csv(tmp_path, (
            self.HEADER
            + self._row("FUT", "-50.0", "20250715", "MES 20DEC25")
            + self._row("",    "-9.27", "20250716")
            + self._row("",    "2000.0", "20240729")  # Cash Receipt
        ))
        rows = parse_sof_csv(path)
        assert len(rows) == 3
        kinds = sorted(classify(r) for r in rows)
        assert kinds == ["fee", "trade", "transfer"]

    def test_skips_non_basecurrency(self, tmp_path):
        from eule.accounting.import_sof import parse_sof_csv
        path = self._csv(tmp_path, (
            self.HEADER
            + self._row("FUT", "-50.0", "20250715", "MES")
            # andere LevelOfDetail-Zeile (z.B. originale Trade-Detail-Zeile)
            + '"FUT","MES","","1","-50","USD","20250715","20250715","20250715",'
              '"100","0","0","0","","","TradeDetail"\n'
        ))
        rows = parse_sof_csv(path)
        assert len(rows) == 1

    def test_aggregate_trades_per_symbol_close_date(self, tmp_path):
        """Alle Tages-Cashflows eines Symbols werden zu einem Roundtrip mit Close-Date."""
        from eule.accounting.import_sof import aggregate_trades, parse_sof_csv
        path = self._csv(tmp_path, (
            self.HEADER
            + self._row("FUT", "-50.0", "20250715", "MES 20DEC25")
            + self._row("FUT", "30.0",  "20250715", "MES 20DEC25")
            + self._row("FUT", "10.0",  "20250716", "MES 20DEC25")
            + self._row("OPT", "20.0",  "20250715", "TLT 16JAN26 86 P")
        ))
        rows = parse_sof_csv(path)
        agg = aggregate_trades(rows)
        assert len(agg) == 2
        by_desc = {a.description: a for a in agg}
        # MES: -50 + 30 + 10 = -10, Close-Date = max(15, 15, 16) = 16
        assert by_desc["MES 20DEC25"].pnl_eur == pytest.approx(-10.0)
        assert by_desc["MES 20DEC25"].posting_date == date(2025, 7, 16)
        assert by_desc["MES 20DEC25"].count == 3
        # TLT: nur eine Zeile
        assert by_desc["TLT 16JAN26 86 P"].pnl_eur == pytest.approx(20.0)
        assert by_desc["TLT 16JAN26 86 P"].posting_date == date(2025, 7, 15)

    def test_aggregate_fees_with_storno(self, tmp_path):
        """Reversal-Posten heben fruehere Aufwendungen am gleichen Tag auf."""
        from eule.accounting.import_sof import aggregate_fees, parse_sof_csv
        path = self._csv(tmp_path, (
            self.HEADER
            + self._row("", "-9.18", "20250804")  # Aufwand
            + self._row("",  "8.57", "20250804")  # Storno
            + self._row("", "-2.00", "20250804")  # weiterer Aufwand
        ))
        rows = parse_sof_csv(path)
        agg = aggregate_fees(rows)
        assert len(agg) == 1
        # Netto: -9.18 + 8.57 - 2.00 = -2.61 (Aufwand)
        assert agg[0].netto_eur == pytest.approx(-2.61)
        assert agg[0].count == 3

    def test_skips_transfers_in_fee_aggregate(self, tmp_path):
        """Cash Receipts / Disbursements (>= 100 EUR) sind keine Fees."""
        from eule.accounting.import_sof import aggregate_fees, parse_sof_csv
        path = self._csv(tmp_path, (
            self.HEADER
            + self._row("", "2000.0", "20240729")  # Cash Receipt
            + self._row("", "-9.27",  "20240729")  # Fee
        ))
        rows = parse_sof_csv(path)
        agg = aggregate_fees(rows)
        assert len(agg) == 1
        assert agg[0].netto_eur == pytest.approx(-9.27)

    def test_dedupe_across_files(self, tmp_path):
        from eule.accounting.import_sof import parse_sof_files
        f1 = self._csv(tmp_path, (
            self.HEADER
            + self._row("FUT", "-50.0", "20250715", "MES")
        ), name="a.csv")
        f2 = self._csv(tmp_path, (
            self.HEADER
            + self._row("FUT", "-50.0", "20250715", "MES")  # dup
            + self._row("FUT", "20.0",  "20250716", "MES")  # neu
        ), name="b.csv")
        rows = parse_sof_files([f1, f2])
        assert len(rows) == 2

    def test_render_fees_yaml_inverts_sign(self, tmp_path):
        """SoF: negative amount = Aufwand. Buchhaltung will positiven Aufwand."""
        from eule.accounting.import_sof import FeeAggregate, render_fees_yaml
        out = render_fees_yaml([
            FeeAggregate(posting_date=date(2025, 8, 4), netto_eur=-9.18, count=10),
            FeeAggregate(posting_date=date(2025, 9, 3), netto_eur=2.50, count=2),
        ])
        # Aufwand-Tag: positiver amount_eur in cash.yaml
        assert "amount_eur: 9.18" in out
        # Storno-Tag: negativer amount_eur in cash.yaml
        assert "amount_eur: -2.50" in out
        assert "paid_from: broker" in out

    def test_render_trades_yaml(self, tmp_path):
        from eule.accounting.import_sof import TradeAggregate, render_trades_yaml
        out = render_trades_yaml([
            TradeAggregate(
                posting_date=date(2025, 7, 15),
                description="MES 20DEC25",
                asset_class="FUT",
                pnl_eur=-20.0,
                count=2,
            ),
        ])
        assert "manual_trades:" in out
        assert "MES 20DEC25" in out
        assert "-20.00" in out
        assert "FUT | sof" in out


# ─────────────────────────────────────────
# Negative expenses → Storno-Buchungen
# ─────────────────────────────────────────


class TestNegativeExpenses:
    """Negative amount_eur in expenses muessen Soll/Haben umkehren (Storno)."""

    def _ledger_with(self, amount_eur):
        from eule.accounting.cash import CashExpense, CashLedger
        return CashLedger(expenses=[
            CashExpense(
                date=date(2025, 8, 4),
                amount_eur=amount_eur,
                paid_from="broker",
                note="Reversal",
            )
        ])

    def test_positive_amount_books_as_expense(self):
        from eule.accounting.journal import postings_for_cash
        cfg = _cfg()
        ps = postings_for_cash(self._ledger_with(10.0), cfg)
        # Hauptbuchung: 6000 an 1200 (Aufwand)
        main = [p for p in ps if "Aufwand" in p.description and "anteil" not in p.description.lower() and "Storno" not in p.description][0]
        assert main.debit == "6000"
        assert main.credit == "1200"
        assert main.amount_eur == pytest.approx(10.0)

    def test_negative_amount_books_as_storno(self):
        from eule.accounting.journal import postings_for_cash
        cfg = _cfg()
        ps = postings_for_cash(self._ledger_with(-10.0), cfg)
        # Hauptbuchung: 1200 an 6000 (Storno)
        main = [p for p in ps if "Storno" in p.description and "Anteil" not in p.description][0]
        assert main.debit == "1200"
        assert main.credit == "6000"
        assert main.amount_eur == pytest.approx(10.0)

    def test_negative_amount_credits_holders(self):
        """Bei Storno bekommen Holder Kapital gutgeschrieben statt belastet."""
        from eule.accounting.journal import postings_for_cash
        cfg = _cfg()
        ps = postings_for_cash(self._ledger_with(-10.0), cfg)
        holder_postings = [p for p in ps if "Storno-Anteil" in p.description]
        assert len(holder_postings) == 2
        # Bei 50:50 capital_share: jeder Holder bekommt 5 EUR Gutschrift
        for p in holder_postings:
            assert p.debit == "6000"  # 6000 an 0H00
            assert p.credit.startswith("01")
            assert p.amount_eur == pytest.approx(5.0)
