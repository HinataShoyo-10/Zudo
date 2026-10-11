from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from src.options_dynamic_backtest import load_manifest, run_dynamic_backtest
from src.options_backtest import CostRates
from tests.test_options_backtest import make_day


def test_dynamic_selector_chooses_nearest_strike_not_best_future_pnl():
    index_rows, option_rows = make_day()
    # Both contracts have identical bars; the selector must use strike proximity.
    specs = [
        {"symbol": "NSE:NIFTY2510923600CE", "csv": Path("a.csv"), "lot_size": 1,
         "strike": 100, "expiry": "2025-01-09", "option_type": "CE"},
        {"symbol": "NSE:NIFTY2510923700CE", "csv": Path("b.csv"), "lot_size": 1,
         "strike": 103, "expiry": "2025-01-09", "option_type": "CE"},
    ]
    result = run_dynamic_backtest(index_rows, specs, {
        specs[0]["symbol"]: option_rows,
        specs[1]["symbol"]: option_rows,
    }, initial_cash=100_000, risk_fraction=0.005, hard_risk_fraction=0.01,
       max_exposure_fraction=0.20, rates=CostRates())
    assert result["summary"]["trades"] == 1
    assert result["trades"][0]["option_symbol"] == specs[1]["symbol"]
    assert result["summary"]["research_only"] is True


def test_manifest_resolves_relative_csv_paths_and_strike(tmp_path: Path):
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"contracts": [{
        "symbol": "NSE:NIFTY2510923600CE", "csv": "bars.csv",
        "lot_size": 75, "expiry": "2025-01-09"
    }]}), encoding="utf-8")
    contracts = load_manifest(path)
    assert contracts[0]["csv"] == tmp_path / "bars.csv"
    assert contracts[0]["strike"] == 23600


def test_manifest_requires_expiry_and_lot_size(tmp_path: Path):
    path = tmp_path / "contracts.json"
    path.write_text(json.dumps({"contracts": [{
        "symbol": "NSE:NIFTY2510923600CE", "csv": "bars.csv", "lot_size": 75
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="specify expiry"):
        load_manifest(path)
