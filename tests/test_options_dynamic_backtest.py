from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.options_dynamic_backtest import load_manifest, run_dynamic_backtest
from src.options_backtest import CostRates

IST = ZoneInfo("Asia/Kolkata")


def make_day():
    index_rows, option_rows = [], []
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30").astimezone(IST)
    for i in range(75):
        ts = start + timedelta(minutes=5 * i)
        idx = {"timestamp": ts, "open": 101.0, "high": 102.0, "low": 99.0, "close": 101.0, "volume": 0.0}
        if i == 6:
            idx.update({"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0})
        if i >= 7:
            idx.update({"open": 103.0, "high": 104.0, "low": 102.0, "close": 103.0})
        index_rows.append(idx)
        option_rows.append({"timestamp": ts, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0})
    return index_rows, option_rows


def test_dynamic_selector_chooses_nearest_strike_not_best_future_pnl():
    index_rows, option_rows = make_day()
    specs = [
        {"symbol": "NSE:NIFTY2510923600CE", "csv": Path("a.csv"), "lot_size": 1,
         "strike": 100, "expiry": "2025-01-09", "option_type": "CE"},
        {"symbol": "NSE:NIFTY2510923700CE", "csv": Path("b.csv"), "lot_size": 1,
         "strike": 103, "expiry": "2025-01-09", "option_type": "CE"},
    ]
    result = run_dynamic_backtest(index_rows, specs, {
        specs[0]["symbol"]: option_rows, specs[1]["symbol"]: option_rows,
    }, initial_cash=100_000, risk_fraction=0.005, hard_risk_fraction=0.01,
       max_exposure_fraction=0.20, rates=CostRates())
    assert result["summary"]["trades"] == 1
    assert result["trades"][0]["option_symbol"] == specs[1]["symbol"]
    assert result["summary"]["research_only"] is True
    assert len(result["candidate_diagnostics"]) == 2
    assert all(row["status"] == "ELIGIBLE" for row in result["candidate_diagnostics"])
    assert all("one_lot_premium" in row and "strike_distance" in row for row in result["candidate_diagnostics"])


def test_manifest_resolves_relative_csv_paths_and_strike(tmp_path: Path):
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"contracts": [{
        "symbol": "NSE:NIFTY2510923600CE", "csv": "bars.csv",
        "lot_size": 75, "expiry": "2025-01-09"
    }]}), encoding="utf-8")
    contracts = load_manifest(path)
    assert contracts[0]["csv"] == tmp_path / "bars.csv"
    assert contracts[0]["strike"] == 23600


def test_manifest_requires_expiry(tmp_path: Path):
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"contracts": [{
        "symbol": "NSE:NIFTY2510923600CE", "csv": "bars.csv", "lot_size": 75
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="specify expiry"):
        load_manifest(path)


def test_skipped_signal_count_is_per_session_not_per_rejected_contract():
    index_rows, option_rows = make_day()
    specs = [
        {"symbol": "NSE:NIFTY2510923600CE", "csv": Path("a.csv"), "lot_size": 75,
         "strike": 103, "expiry": "2025-01-09", "option_type": "CE"},
        {"symbol": "NSE:NIFTY2510923700CE", "csv": Path("b.csv"), "lot_size": 75,
         "strike": 104, "expiry": "2025-01-09", "option_type": "CE"},
    ]
    expensive_rows = [dict(row, open=400.0, high=410.0, low=390.0, close=400.0) for row in option_rows]
    result = run_dynamic_backtest(index_rows, specs, {
        specs[0]["symbol"]: expensive_rows, specs[1]["symbol"]: expensive_rows,
    }, initial_cash=100_000, risk_fraction=0.005, hard_risk_fraction=0.01,
       max_exposure_fraction=0.20, rates=CostRates())
    assert result["summary"]["signals"] == 1
    assert result["summary"]["skipped_signals"] == 1
    assert result["summary"]["candidate_rejections"] == 2
    assert sum(result["summary"]["skipped_reasons"].values()) == 1
    assert sum(result["summary"]["candidate_rejection_reasons"].values()) == 2



def test_dynamic_backtest_surfaces_missing_bid_ask_as_unverified():
    index_rows, option_rows = make_day()
    spec = {"symbol": "NSE:NIFTY2510923600CE", "csv": Path("a.csv"), "lot_size": 1,
            "strike": 103, "expiry": "2025-01-09", "option_type": "CE"}
    result = run_dynamic_backtest(index_rows, [spec], {spec["symbol"]: option_rows})
    assert result["summary"]["trades"] == 1
    assert result["summary"]["liquidity_unverified_candidates"] == 1
    assert "liquidity_unverified_missing_bid_ask" in result["candidate_diagnostics"][0]["brain_warnings"]


def test_dynamic_backtest_strict_liquidity_mode_rejects_ohlcv_only_data():
    index_rows, option_rows = make_day()
    spec = {"symbol": "NSE:NIFTY2510923600CE", "csv": Path("a.csv"), "lot_size": 1,
            "strike": 103, "expiry": "2025-01-09", "option_type": "CE"}
    result = run_dynamic_backtest(
        index_rows, [spec], {spec["symbol"]: option_rows}, require_liquidity_data=True
    )
    assert result["summary"]["trades"] == 0
    assert result["summary"]["skipped_signals"] == 1
    assert "missing_bid_ask" in result["candidate_diagnostics"][0]["reason"]
