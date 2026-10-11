import csv
import json
import sys
from datetime import datetime, timedelta

import pytest

from src.backtest import Bar
from src.paper_portfolio import main, run_paper_portfolio


def breakout_session():
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30")
    bars = []
    for index in range(75):
        timestamp = start + timedelta(minutes=5 * index)
        if index < 6:
            values, volume = (99.0, 100.0, 98.0, 99.0), 1500.0
        elif index == 19:
            values, volume = (100.0, 101.5, 100.0, 101.5), 3000.0
        elif index >= 20:
            values, volume = (101.5, 102.5, 101.0, 102.0), 1500.0
        else:
            values, volume = (99.0, 99.5, 98.8, 99.0), 1500.0
        bars.append(Bar(timestamp, *values, volume))
    return bars


def test_paper_portfolio_selects_one_simultaneous_opportunity_and_logs_skip():
    bars = breakout_session()
    result = run_paper_portfolio(
        {"NSE:SBIN-EQ": bars, "NSE:INFY-EQ": bars},
        initial_cash=50_000,
        fee_per_side=0,
        slippage_bps=0,
    )
    summary = result["summary"]
    assert summary["mode"] == "paper_backtest_only"
    assert summary["trades"] == 1
    assert summary["eligible_candidates"] == 2
    assert summary["skipped_candidates"] == 1
    assert result["trades"][0]["symbol"] == "NSE:INFY-EQ"
    assert result["trades"][0]["quantity"] * result["trades"][0]["entry_price"] <= 10_000
    assert result["trades"][0]["planned_risk"] <= 250
    assert result["decisions"][1]["reason"] == "one_trade_per_session_limit"


def test_paper_portfolio_rejects_invalid_risk_parameters():
    with pytest.raises(ValueError, match="risk_fraction"):
        run_paper_portfolio(
            {"NSE:SBIN-EQ": breakout_session()},
            initial_cash=50_000,
            risk_fraction=0.03,
        )


def test_paper_portfolio_reports_asset_progress():
    progress = []
    run_paper_portfolio(
        {"NSE:SBIN-EQ": breakout_session()},
        progress_callback=lambda index, total, symbol: progress.append((index, total, symbol)),
    )
    assert progress == [(1, 1, "NSE:SBIN-EQ")]


def test_paper_portfolio_cli_writes_auditable_reports(tmp_path, monkeypatch):
    source = tmp_path / "bars.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        for bar in breakout_session():
            writer.writerow([
                bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume,
            ])
    output = tmp_path / "paper"
    monkeypatch.setattr(sys, "argv", [
        "paper_portfolio",
        "--asset", f"NSE:SBIN-EQ={source}",
        "--cash", "50000",
        "--fee-per-side", "0",
        "--slippage-bps", "0",
        "--out", str(output),
    ])

    main()

    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary["mode"] == "paper_backtest_only"
    assert (output / "trades.csv").exists()
    assert (output / "decisions.csv").exists()
    assert (output / "equity_curve.csv").exists()
