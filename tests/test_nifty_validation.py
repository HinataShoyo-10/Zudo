from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from src.nifty_validation import run_validation, validate_dataset

IST = ZoneInfo("Asia/Kolkata")


def _write_candles(path: Path, option: bool = False) -> None:
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30").astimezone(IST)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        for i in range(75):
            ts = start + timedelta(minutes=5 * i)
            o = 100.0
            h, l, c = 102.0, 99.0, 101.0
            if not option and i == 6:
                o, h, l, c = 102.0, 104.0, 101.0, 103.0
            elif not option and i >= 7:
                o, h, l, c = 103.0, 104.0, 102.0, 103.0
            writer.writerow({
                "timestamp": ts.isoformat(), "open": o, "high": h, "low": l,
                "close": c, "volume": 0 if not option else 1000,
            })


def test_validate_dataset_reports_session_overlap():
    start = datetime.fromisoformat("2025-01-06T09:15:00+05:30").astimezone(IST)
    index = [{"timestamp": start, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 0}]
    option = [{"timestamp": start, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 5}]
    report = validate_dataset(index, [{"symbol": "CE", "strike": 100, "expiry": "2025-01-09"}], {"CE": option})
    assert report["index_sessions"] == 1
    assert report["contracts"][0]["sessions_overlapping_index"] == 1
    assert "does not prove executable fills" in report["research_warning"]


def test_validation_runner_writes_slippage_scenarios_and_strict_quote_case(tmp_path: Path):
    index_path, option_path = tmp_path / "index.csv", tmp_path / "option.csv"
    _write_candles(index_path)
    _write_candles(option_path, option=True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"contracts": [{
        "symbol": "NSE:NIFTY2510923600CE", "csv": "option.csv",
        "lot_size": 1, "strike": 103, "expiry": "2025-01-09", "option_type": "CE",
    }]}), encoding="utf-8")
    out = tmp_path / "out"
    report = run_validation(index_path, manifest, out)
    assert len(report["scenarios"]) == 5
    assert (out / "scenario_summary.csv").exists()
    assert (out / "validation_report.json").exists()
    strict = next(s for s in report["scenarios"] if s["scenario"] == "strict_quotes_5bps")
    assert strict["trades"] == 0
    assert strict["liquidity_data_required"] is True
