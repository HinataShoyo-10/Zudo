import csv
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.backtest import Bar, ema, load_bars, run_backtest


def make_bars(closes):
    start = datetime.fromisoformat("2025-01-01T09:15:00+05:30")
    result = []
    for i, close in enumerate(closes):
        result.append(Bar(start + timedelta(minutes=5 * i), close, close + 1, close - 1, close, 1000))
    return result


def test_ema_sma_seed_and_warmup():
    assert ema([1.0, 2.0, 3.0, 4.0], 3) == [None, None, 2.0, 3.0]


def test_load_bars_accepts_csv_and_assumes_ist_for_naive_timestamp(tmp_path):
    path = tmp_path / "candles.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-01 09:15:00,100,102,99,101,1200\n",
        encoding="utf-8",
    )
    bars = load_bars(path)
    assert len(bars) == 1
    assert bars[0].timestamp.utcoffset() == timedelta(hours=5, minutes=30)


def test_load_bars_rejects_unsorted_timestamps(tmp_path):
    path = tmp_path / "candles.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-01T09:20:00+05:30,100,102,99,101,1200\n"
        "2025-01-01T09:15:00+05:30,100,102,99,101,1200\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="strictly increasing"):
        load_bars(path)


def test_backtest_runs_without_api_and_returns_metrics():
    closes = [108 - i for i in range(8)] + [101 + i * 2 for i in range(12)] + [123 - i * 3 for i in range(12)]
    bars = make_bars(closes)
    trades, equity, metrics = run_backtest(
        bars, fast_period=2, slow_period=4, fee_per_side=0, slippage_bps=0
    )
    assert len(equity) == len(bars)
    assert metrics["trades"] >= 1
    assert metrics["final_equity"] == pytest.approx(100000 + metrics["net_pnl"])
    assert all(trade.quantity == 1 for trade in trades)
    assert metrics["gross_pnl_before_costs"] == pytest.approx(sum(t.gross_pnl for t in trades))


def test_intraday_mode_closes_positions_before_date_changes_and_separates_costs():
    closes = [108 - i for i in range(8)] + [101 + i * 2 for i in range(12)] + [123 - i * 3 for i in range(12)]
    bars = make_bars(closes)
    # Split the synthetic series across two sessions while preserving order.
    day_two_start = datetime.fromisoformat("2025-01-02T09:15:00+05:30")
    for i in range(16, len(bars)):
        old = bars[i]
        bars[i] = Bar(
            day_two_start + timedelta(minutes=5 * (i - 16)),
            old.open, old.high, old.low, old.close, old.volume,
        )

    trades, _, metrics = run_backtest(
        bars, fast_period=2, slow_period=4, fee_per_side=20, slippage_bps=5
    )
    assert all(
        datetime.fromisoformat(t.entry_time).date() == datetime.fromisoformat(t.exit_time).date()
        for t in trades
    )
    for trade in trades:
        assert trade.fees == pytest.approx(40)
        assert trade.slippage_cost >= 0
        assert trade.net_pnl == pytest.approx(
            trade.gross_pnl - trade.slippage_cost - trade.fees
        )
    assert metrics["fees_total"] == pytest.approx(sum(t.fees for t in trades))
    assert metrics["slippage_cost_total"] == pytest.approx(sum(t.slippage_cost for t in trades))
    assert metrics["final_equity"] == pytest.approx(100000 + metrics["net_pnl"])


def test_invalid_ema_periods_raise():
    with pytest.raises(ValueError):
        run_backtest(make_bars(list(range(100, 110))), fast_period=5, slow_period=5)
