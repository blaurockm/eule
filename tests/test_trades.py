"""Tests fuer Trade-Loading und Roundtrip-Erkennung."""

from datetime import date, datetime, timezone

from eule.models import HaseTrade, Roundtrip
from eule.trades import detect_roundtrips, get_open_trades, summarize_roundtrips


def _make_trade(
    ts: str,
    side: str,
    price: float,
    value: float,
    *,
    strategy_key: str = "spx-0dte-mon-put",
    symbol: str = "SPX 250307P05465",
    trade_ref: str | None = "ref-123",
    fees: float = 1.05,
) -> HaseTrade:
    """Hilfsfunktion fuer Trade-Erzeugung."""
    dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    return HaseTrade(
        ts=dt,
        date=dt.date(),
        strategy_key=strategy_key,
        symbol=symbol,
        asset_class="OPT",
        side=side,
        qty=1.0,
        price=price,
        value=value,
        fees=fees,
        trade_ref=trade_ref,
        order_id="order-1" if trade_ref else None,
    )


class TestHaseTradeModel:
    def test_is_expiry_true(self):
        """buy mit price=0 und trade_ref=None ist ein Expiry."""
        t = _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None)
        assert t.is_expiry is True

    def test_is_expiry_false_has_ref(self):
        """buy mit trade_ref ist kein Expiry (echter Rueckkauf)."""
        t = _make_trade("2025-03-07T16:00:00", "buy", 1.50, 150.0, trade_ref="ref-456")
        assert t.is_expiry is False

    def test_is_expiry_false_sell(self):
        """sell ist nie ein Expiry."""
        t = _make_trade("2025-03-07T15:00:00", "sell", 2.45, 245.0)
        assert t.is_expiry is False

    def test_is_expiry_false_nonzero_price(self):
        """buy mit price > 0 ist kein Expiry, auch ohne trade_ref."""
        t = _make_trade("2025-03-07T16:00:00", "buy", 0.50, 50.0, trade_ref=None)
        assert t.is_expiry is False

    def test_to_dict(self):
        t = _make_trade("2025-03-07T15:00:00", "sell", 2.45, 245.0)
        d = t.to_dict()
        assert d["side"] == "sell"
        assert d["price"] == 2.45
        assert d["is_expiry"] is False
        assert "ts" in d
        assert "date" in d


class TestRoundtripDetection:
    def test_single_roundtrip_expiry(self):
        """sell -> buy(price=0, no ref) = Roundtrip mit Expiry."""
        trades = [
            _make_trade("2025-03-02T15:35:00", "sell", 2.45, 245.0, trade_ref="ref-1"),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        rts = detect_roundtrips(trades)

        assert len(rts) == 1
        rt = rts[0]
        assert rt.exit_is_expiry is True
        assert rt.entry_price == 2.45
        assert rt.exit_price == 0.0
        assert rt.holding_days == 5
        assert rt.strategy_key == "spx-0dte-mon-put"

    def test_single_roundtrip_close(self):
        """sell -> buy(price>0, with ref) = Roundtrip mit Rueckkauf."""
        trades = [
            _make_trade("2025-03-10T15:35:00", "sell", 3.00, 300.0, trade_ref="ref-2"),
            _make_trade("2025-03-12T16:00:00", "buy", 1.50, 150.0, trade_ref="ref-3"),
        ]
        rts = detect_roundtrips(trades)

        assert len(rts) == 1
        rt = rts[0]
        assert rt.exit_is_expiry is False
        assert rt.entry_value == 300.0
        assert rt.exit_value == 150.0

    def test_multiple_roundtrips_fifo(self):
        """Mehrere sells -> buys werden FIFO gepaart."""
        trades = [
            _make_trade("2025-03-02T15:35:00", "sell", 2.00, 200.0, trade_ref="ref-1"),
            _make_trade("2025-03-03T15:35:00", "sell", 3.00, 300.0, trade_ref="ref-2"),
            _make_trade("2025-03-05T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        rts = detect_roundtrips(trades)

        assert len(rts) == 2
        # FIFO: erster sell wird mit erstem buy gepaart
        assert rts[0].entry_price == 2.00
        assert rts[0].exit_date == date(2025, 3, 5)
        assert rts[1].entry_price == 3.00
        assert rts[1].exit_date == date(2025, 3, 7)

    def test_different_symbols_separate(self):
        """Verschiedene Symbole bilden separate Roundtrips."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, symbol="SPX A", trade_ref="r1"),
            _make_trade("2025-03-02T15:01:00", "sell", 3.00, 300.0, symbol="SPX B", trade_ref="r2"),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, symbol="SPX A", trade_ref=None, fees=0.0),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, symbol="SPX B", trade_ref=None, fees=0.0),
        ]
        rts = detect_roundtrips(trades)

        assert len(rts) == 2
        symbols = {r.symbol for r in rts}
        assert symbols == {"SPX A", "SPX B"}

    def test_different_strategies_separate(self):
        """Verschiedene Strategien bilden separate Roundtrips."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, strategy_key="strat-a", trade_ref="r1"),
            _make_trade("2025-03-02T15:01:00", "sell", 3.00, 300.0, strategy_key="strat-b", trade_ref="r2"),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, strategy_key="strat-a", trade_ref=None, fees=0.0),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, strategy_key="strat-b", trade_ref=None, fees=0.0),
        ]
        rts = detect_roundtrips(trades)

        assert len(rts) == 2
        strategies = {r.strategy_key for r in rts}
        assert strategies == {"strat-a", "strat-b"}

    def test_no_trades_empty(self):
        assert detect_roundtrips([]) == []

    def test_only_sells_no_roundtrips(self):
        """Nur sells ohne buys = keine Roundtrips."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, trade_ref="r1"),
        ]
        assert detect_roundtrips(trades) == []

    def test_unmatched_buy_ignored(self):
        """buy ohne vorherigen sell wird ignoriert."""
        trades = [
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        assert detect_roundtrips(trades) == []


class TestOpenTrades:
    def test_all_closed(self):
        """Wenn alle Trades gepaart sind, keine offenen."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, trade_ref="r1"),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        assert get_open_trades(trades) == []

    def test_open_sell(self):
        """sell ohne buy = offene Position."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, trade_ref="r1"),
        ]
        open_trades = get_open_trades(trades)
        assert len(open_trades) == 1
        assert open_trades[0].trade_ref == "r1"

    def test_partial_close(self):
        """2 sells + 1 buy = 1 offene Position."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, trade_ref="r1"),
            _make_trade("2025-03-03T15:00:00", "sell", 3.00, 300.0, trade_ref="r2"),
            _make_trade("2025-03-05T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        open_trades = get_open_trades(trades)
        assert len(open_trades) == 1
        assert open_trades[0].trade_ref == "r2"  # FIFO: erster sell gepaart


class TestRoundtripPnL:
    def test_pnl_expiry_full_profit(self):
        """Sell -> Expiry: voller Profit = entry_value - fees."""
        trades = [
            _make_trade("2025-03-02T15:00:00", "sell", 2.45, 245.0, trade_ref="r1", fees=1.05),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
        ]
        rts = detect_roundtrips(trades)
        rt = rts[0]
        assert rt.pnl == 245.0 - 0.0 - 1.05  # 243.95

    def test_pnl_close_partial_profit(self):
        """Sell -> Close: Profit = entry_value - exit_value - fees."""
        trades = [
            _make_trade("2025-03-10T15:00:00", "sell", 3.00, 300.0, trade_ref="r1", fees=1.05),
            _make_trade("2025-03-12T16:00:00", "buy", 1.50, 150.0, trade_ref="r2", fees=1.05),
        ]
        rts = detect_roundtrips(trades)
        rt = rts[0]
        assert rt.pnl == 300.0 - 150.0 - 2.10  # 147.90

    def test_pnl_percent(self):
        """P&L% = pnl / entry_value * 100."""
        trades = [
            _make_trade("2025-03-10T15:00:00", "sell", 3.00, 300.0, trade_ref="r1", fees=0.0),
            _make_trade("2025-03-12T16:00:00", "buy", 1.50, 150.0, trade_ref="r2", fees=0.0),
        ]
        rts = detect_roundtrips(trades)
        assert rts[0].pnl_percent == 50.0  # (300-150)/300 * 100


class TestSummary:
    def test_empty(self):
        s = summarize_roundtrips([])
        assert s["count"] == 0
        assert s["win_rate"] == 0.0

    def test_basic_summary(self):
        trades = [
            # Roundtrip 1: profit
            _make_trade("2025-03-02T15:00:00", "sell", 2.00, 200.0, trade_ref="r1", fees=0.0),
            _make_trade("2025-03-07T22:00:00", "buy", 0.0, 0.0, trade_ref=None, fees=0.0),
            # Roundtrip 2: profit (close)
            _make_trade("2025-03-10T15:00:00", "sell", 3.00, 300.0, trade_ref="r2", fees=0.0, symbol="SPX B"),
            _make_trade("2025-03-12T16:00:00", "buy", 1.50, 150.0, trade_ref="r3", fees=0.0, symbol="SPX B"),
        ]
        rts = detect_roundtrips(trades)
        s = summarize_roundtrips(rts)

        assert s["count"] == 2
        assert s["winners"] == 2
        assert s["losers"] == 0
        assert s["win_rate"] == 100.0
        assert s["total_pnl"] == 350.0  # 200 + 150
        assert s["expired_count"] == 1
