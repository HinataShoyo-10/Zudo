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


def make_breakout_session(entry_open=101.5, exit_bar=None):
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30")
    bars = []
    for i in range(75):
        timestamp = start + timedelta(minutes=5 * i)
        if i < 6:
            values = (99.0, 100.0, 98.0, 99.0)
            volume = 1500.0
        elif i == 6:
            values = (99.5, 102.0, 99.0, 101.8)
            volume = 1500.0
        elif i == 14:
            values = (99.5, 102.0, 99.0, 101.8)
            volume = 3000.0
        elif i == 15:
            values = (entry_open, entry_open + 0.5, entry_open - 0.5, entry_open)
            volume = 1500.0
        elif exit_bar is not None and i == 16:
            values = exit_bar
            volume = 1500.0
        else:
            values = (99.0, 99.5, 98.8, 99.0)
            volume = 1500.0
        bars.append(Bar(timestamp, *values, volume))
    return bars


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


def test_load_bars_converts_utc_fyers_timestamps_to_ist(tmp_path):
    path = tmp_path / "utc_candles.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-06T03:45:00+00:00,100,102,99,101,1200\n",
        encoding="utf-8",
    )
    assert load_bars(path)[0].timestamp.isoformat() == "2025-01-06T09:15:00+05:30"


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


def test_cli_help_succeeds():
    import sys
    from src.backtest import main

    original_argv = sys.argv
    try:
        sys.argv = ["backtest", "--help"]
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 0
    finally:
        sys.argv = original_argv


def test_breakout_backtest_uses_session_breakout_and_risk_cap():
    bars = make_breakout_session(exit_bar=(101.5, 110.0, 101.0, 109.0))
    result = run_backtest(
        bars,
        initial_cash=75_000.0,
        strategy="orb",
        opening_range_minutes=30,
        atr_period=3,
        risk_fraction=0.01,
        stop_atr_multiple=1.5,
        target_r=2.0,
        fee_per_side=0,
        slippage_bps=0,
    )
    trades, _, metrics = result
    assert metrics["strategy"] == "opening_range_breakout"
    assert metrics["initial_cash"] == pytest.approx(75_000.0)
    assert metrics["trades"] == 1
    assert trades[0].exit_reason == "take_profit"
    assert trades[0].entry_time == "2025-01-06T10:30:00+05:30"
    assert trades[0].exit_price > trades[0].entry_price
    assert trades[0].fees == 0
    assert trades[0].planned_risk <= metrics["initial_cash"] * metrics["risk_fraction"]
    assert trades[0].net_pnl > 0
    assert metrics["raw_breakouts"] == 2
    assert metrics["no_trade_reasons"]["trend_warmup"] == 1


def test_breakout_zero_fee_setting_still_charges_slippage():
    bars = make_breakout_session(exit_bar=(101.5, 110.0, 101.0, 109.0))
    trades, _, metrics = run_backtest(
        bars, strategy="orb", atr_period=3, fee_per_side=0, slippage_bps=10
    )
    assert metrics["fees_total"] == 0
    assert metrics["slippage_cost_total"] > 0
    assert trades[0].net_pnl == pytest.approx(
        trades[0].gross_pnl - trades[0].slippage_cost
    )


def test_breakout_collision_uses_stop_and_stop_gap_fills_worse_open():
    bars = make_breakout_session(exit_bar=(90.0, 110.0, 85.0, 100.0))
    trades, _, metrics = run_backtest(
        bars, strategy="orb", atr_period=3, fee_per_side=0, slippage_bps=0
    )
    assert metrics["trades"] == 1
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].exit_price == pytest.approx(90.0)
    assert trades[0].net_pnl < 0


def test_breakout_skips_incomplete_session_and_does_not_force_minimum_size():
    incomplete = make_breakout_session()[:-1]
    _, curve, incomplete_metrics = run_backtest(incomplete, strategy="orb", atr_period=3)
    assert incomplete_metrics["trades"] == 0
    assert incomplete_metrics["valid_sessions"] == 0
    assert incomplete_metrics["skipped_incomplete_sessions"] == 1
    assert curve == []

    too_small = make_breakout_session()
    _, _, small_metrics = run_backtest(
        too_small,
        initial_cash=100.0,
        strategy="orb",
        atr_period=3,
        risk_fraction=0.01,
        fee_per_side=0,
        slippage_bps=0,
    )
    assert small_metrics["trades"] == 0
    assert small_metrics["skipped_risk"] == 1
