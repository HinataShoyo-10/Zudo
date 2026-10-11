from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timedelta

import pytest

from src.backtesting.engine import Bar
from src.scanning.cli import main
from src.scanning.opportunity import latest_session_chart, scan_asset, scan_universe


def breakout_bars(volume: float = 3_000.0) -> list[Bar]:
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30")
    bars = []
    for index in range(20):
        timestamp = start + timedelta(minutes=5 * index)
        if index < 6:
            values, bar_volume = (99.0, 100.0, 98.0, 99.0), 1_500.0
        elif index == 19:
            values, bar_volume = (100.0, 102.0, 100.0, 101.5), volume
        else:
            values, bar_volume = (99.0, 99.5, 98.8, 99.0), 1_500.0
        bars.append(Bar(timestamp, *values, bar_volume))
    return bars


def test_scan_ranks_latest_bar_setups_with_sized_paper_plans():
    results = scan_universe({
        "NSE:SBIN-EQ": breakout_bars(),
        "NSE:INFY-EQ": breakout_bars(4_500.0),
    })
    opportunities = [row for row in results if row["status"] == "OPPORTUNITY"]
    assert opportunities
    assert [row["rank"] for row in opportunities] == list(range(1, len(opportunities) + 1))
    assert opportunities[0]["symbol"] == "NSE:INFY-EQ"
    assert opportunities[0]["planned_entry_time"] == "2025-01-06T10:55:00+05:30"
    assert opportunities[0]["estimated_entry"] == pytest.approx(101.5 * 1.0005)
    assert opportunities[0]["quantity"] > 0
    assert opportunities[0]["planned_risk"] <= 250
    assert all(row["reason"] == "" for row in opportunities)


def test_universe_scan_adds_prior_day_levels_and_opening_gap():
    bars = breakout_bars()
    results = scan_universe(
        {"NSE:SBIN-EQ": bars},
        daily_context={
            "NSE:SBIN-EQ": {
                "previous_day_high": 102.0,
                "previous_day_low": 98.0,
                "previous_day_close": 100.0,
            }
        },
    )
    assert all(row["previous_day_high"] == 102.0 for row in results)
    assert all(row["previous_day_low"] == 98.0 for row in results)
    assert all(row["previous_day_close"] == 100.0 for row in results)
    assert all(row["opening_gap_pct"] == pytest.approx(-1.0) for row in results)


def test_scanner_never_uses_a_future_bar_to_claim_an_entry_fill():
    bars = breakout_bars()
    scan = scan_asset("NSE:SBIN-EQ", bars)
    candidate = next(row for row in scan if row["strategy"] == "opening_range_breakout")
    assert candidate["status"] == "OPPORTUNITY"
    estimated_entry = candidate["estimated_entry"]

    bars.append(Bar(
        bars[-1].timestamp + timedelta(minutes=5),
        150.0, 151.0, 149.0, 150.5, 8_000.0,
    ))
    later_scan = scan_asset("NSE:SBIN-EQ", bars)
    later_breakout = next(row for row in later_scan if row["strategy"] == "opening_range_breakout")
    assert later_breakout["status"] == "NO_TRADE"
    assert later_breakout["estimated_entry"] is None
    assert estimated_entry < 150.0


def test_scanner_ignores_an_incomplete_current_candle():
    bars = breakout_bars()
    bars.append(Bar(
        bars[-1].timestamp + timedelta(minutes=5),
        150.0, 151.0, 149.0, 150.5, 8_000.0,
    ))
    scan = scan_asset(
        "NSE:SBIN-EQ",
        bars,
        as_of=bars[-1].timestamp + timedelta(minutes=3),
    )
    breakout = next(row for row in scan if row["strategy"] == "opening_range_breakout")
    assert breakout["status"] == "OPPORTUNITY"
    assert breakout["signal_time"] == "2025-01-06T10:50:00+05:30"
    assert breakout["planned_entry_time"] == "2025-01-06T10:55:00+05:30"


def test_scanner_rejects_irregular_latest_sessions_and_explains_no_setup():
    bars = breakout_bars()
    bars.pop(10)
    results = scan_asset("NSE:SBIN-EQ", bars)
    assert all(row["reason"] == "latest_session_has_missing_or_irregular_bars" for row in results)

    flat = breakout_bars()
    flat[-1] = Bar(flat[-1].timestamp, 99.0, 99.5, 98.8, 99.0, 1_500.0)
    results = scan_asset("NSE:SBIN-EQ", flat)
    assert all(row["status"] == "NO_TRADE" for row in results)
    assert all(row["reason"] == "no_setup_on_latest_bar" for row in results)


def test_scanner_validates_input_and_reports_empty_universe():
    with pytest.raises(ValueError, match="timezone-aware"):
        scan_asset("NSE:SBIN-EQ", [
            Bar(datetime(2025, 1, 6, 9, 15), 100, 101, 99, 100, 1000),
        ])
    with pytest.raises(ValueError, match="at least one asset"):
        scan_universe({})


def test_latest_session_chart_contains_price_and_indicators():
    chart = latest_session_chart(breakout_bars())
    assert len(chart) == 20
    assert chart[-1]["close"] == 101.5
    assert chart[-1]["ema_5"] is not None
    assert chart[-1]["ema_13"] is not None
    assert chart[-1]["session_vwap"] is not None


def test_scanner_cli_writes_ranked_csv_and_json(tmp_path, monkeypatch):
    source = tmp_path / "sbin.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in breakout_bars():
            writer.writerow([
                bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume,
            ])
    output = tmp_path / "scan"
    monkeypatch.setattr(sys, "argv", [
        "scanner",
        "--asset", f"NSE:SBIN-EQ={source}",
        "--out", str(output),
    ])

    main()

    results = json.loads((output / "scan.json").read_text(encoding="utf-8"))
    assert any(row["status"] == "OPPORTUNITY" for row in results)
    with (output / "scan.csv").open(newline="", encoding="utf-8") as stream:
        assert len(list(csv.DictReader(stream))) == 2
