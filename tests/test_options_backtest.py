from __future__ import annotations

import csv
from datetime import datetime, timedelta, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.options_backtest import (
    CostRates,
    _find_exit,
    load_candles,
    order_charges,
    run_backtest,
)

IST = ZoneInfo("Asia/Kolkata")


def make_day(day="2025-01-06", option_entry_bar=None):
    index_rows = []
    option_rows = []
    start = datetime.fromisoformat(f"{day}T09:15:00+05:30").astimezone(IST)
    for i in range(75):
        ts = start + timedelta(minutes=5 * i)
        # Opening range high is 102. A breakout is confirmed by 09:45 close.
        index = {"timestamp": ts, "open": 101.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 0.0}
        if i == 6:
            index.update({"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0})
        if i >= 7:
            index.update({"open": 103.0, "high": 104.0, "low": 102.0, "close": 103.0})
        index_rows.append(index)

        option = {"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}
        if option_entry_bar is not None and i == 7:
            option.update(option_entry_bar)
        option_rows.append(option)
    return index_rows, option_rows


def test_zero_index_volume_is_allowed():
    rows, _ = make_day()
    assert all(row["volume"] == 0 for row in rows)
    # run_backtest should use index price only and not reject zero volume.
    option_rows = make_day()[1]
    result = run_backtest(rows, option_rows, option_symbol="NSE:NIFTY2510923600CE", lot_size=75)
    assert result["summary"]["index_volume_used_for_signals"] is False
    assert result["summary"]["signals"] >= 1


def test_risk_limit_skips_one_lot_that_exceeds_500_target():
    index_rows, option_rows = make_day()
    result = run_backtest(
        index_rows,
        option_rows,
        option_symbol="NSE:NIFTY2510923600CE",
        lot_size=75,
        initial_cash=100_000,
        risk_fraction=0.005,
        hard_risk_fraction=0.01,
        stop_loss_pct=0.20,
    )
    summary = result["summary"]
    assert summary["signals"] == 1
    assert summary["trades"] == 0
    assert summary["skipped_signals"] == 1
    assert summary["skipped_reasons"]["one_lot_exceeds_planned_risk"] == 1


def test_next_candle_entry_and_intrabar_target_exit():
    index_rows, option_rows = make_day({
        "open": 100.0, "high": 140.0, "low": 90.0, "close": 130.0
    })
    result = run_backtest(
        index_rows,
        option_rows,
        option_symbol="NSE:NIFTY2510923600CE",
        lot_size=1,
        initial_cash=100_000,
        risk_fraction=0.005,
        hard_risk_fraction=0.01,
        stop_loss_pct=0.20,
        target_r=1.5,
        slippage_bps=5.0,
    )
    assert result["summary"]["signals"] == 1
    assert result["summary"]["trades"] == 1
    trade = result["trades"][0]
    assert trade["signal_timestamp"] == "2025-01-06T09:45:00+05:30"
    assert trade["entry_timestamp"] == "2025-01-06T09:50:00+05:30"
    assert trade["exit_timestamp"] == "2025-01-06T09:50:00+05:30"
    assert trade["exit_reason"] == "take_profit"
    # The entry fill is the next option candle's open plus adverse slippage.
    assert trade["entry_fill"] > trade["entry_raw_open"]
    assert trade["exit_fill"] < trade["exit_raw_reference"]


def test_stop_wins_when_stop_and_target_touch_in_same_candle():
    rows = [
        {"timestamp": datetime.fromisoformat("2025-01-06T09:50:00+05:30"),
         "open": 100.0, "high": 140.0, "low": 70.0, "close": 100.0, "volume": 1000.0},
    ]
    exit_i, raw, reason, fill = _find_exit(rows, 0, 100.0, 0.20, 1.5, 0)
    assert exit_i == 0
    assert reason == "stop_loss"
    assert raw == pytest.approx(80.0)
    assert fill == pytest.approx(80.0)


def test_cost_model_applies_stamp_to_buy_and_stt_to_sell():
    rates = CostRates()
    buy = order_charges(100.0, 75, "BUY", rates)
    sell = order_charges(100.0, 75, "SELL", rates)
    assert buy["stamp_duty"] > 0
    assert buy["stt"] == 0
    assert sell["stt"] > 0
    assert sell["stamp_duty"] == 0
    assert buy["brokerage"] == 20
    assert sell["brokerage"] == 20


def test_missing_timestamp_alignment_is_rejected():
    index_rows, option_rows = make_day()
    option_rows.pop(12)
    with pytest.raises(ValueError, match="not fully aligned"):
        run_backtest(index_rows, option_rows, option_symbol="NSE:NIFTY2510923600CE", lot_size=1)


def test_loader_rejects_bad_ohlc_and_accepts_zero_volume(tmp_path: Path):
    path = tmp_path / "bars.csv"
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-06T09:15:00+05:30,100,101,99,100,0\n",
        encoding="utf-8",
    )
    assert load_candles(path)[0]["volume"] == 0
    path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-01-06T09:15:00+05:30,100,99,98,101,0\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="high violates"):
        load_candles(path)
