from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.validate_market_data import (
    Validation,
    _check_ohlc,
    _session_issue,
    _validate_contracts,
    _validate_expiries,
    _validate_raw_option_history,
)

IST = ZoneInfo("Asia/Kolkata")
COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume", "open_interest",
    "implied_volatility", "delta", "gamma", "theta", "vega",
]


def sample_history() -> dict:
    return {
        "candles": [
            [1736135100, 436.5, 458.75, 435.1, 450.0, 10952, 349800, 17.18, 0.86, 0.0006, -13.14, 4.99],
            [1736135400, 450.0, 460.0, 440.0, 455.0, 12000, 350000, 17.2, 0.87, 0.0006, -13.0, 5.0],
        ],
        "columns": COLUMNS,
        "greeks_at": "candle_close",
        "resolution": "5",
        "s": "ok",
        "schema_version": 1,
        "symbol": "NSE:NIFTY2510923600CE",
    }


def test_session_grid_accepts_timezone_aware_ist_timestamp():
    ts = datetime(2025, 1, 6, 9, 15, tzinfo=IST)
    assert _session_issue(ts, 5) is None


def test_ohlc_validator_detects_invalid_high():
    assert _check_ohlc(
        {"open": 101.0, "high": 100.0, "low": 99.0, "close": 100.0, "volume": 5.0},
        3,
    ) == "row 3: high is below open/low/close"


def test_valid_option_history_passes_integrity_check_and_reports_gaps():
    v = Validation()
    symbol, resolution = _validate_raw_option_history(
        sample_history(),
        v,
        expected_symbol="NSE:NIFTY2510923600CE",
        expected_date="2025-01-06",
        expected_resolution=5,
    )
    assert symbol == "NSE:NIFTY2510923600CE"
    assert resolution == "5"
    assert v.errors == 0
    assert any(c["check"] == "options_candle_integrity" and c["status"] == "PASS" for c in v.checks)
    assert any(c["check"] == "options_candle_gaps" and c["status"] == "WARN" for c in v.checks)


def test_option_history_rejects_bad_ohlc_and_delta():
    payload = sample_history()
    payload["candles"][0][2] = 400.0  # high below open and close
    payload["candles"][1][8] = 1.4    # delta should be in [-1, 1]
    v = Validation()
    _validate_raw_option_history(payload, v, None, None, 5)
    check = next(c for c in v.checks if c["check"] == "options_candle_integrity")
    assert check["status"] == "FAIL"
    assert check["details"]["total_invalid_rows"] == 2


def test_option_history_rejects_wrong_trading_date():
    v = Validation()
    _validate_raw_option_history(sample_history(), v, None, "2025-01-07", 5)
    check = next(c for c in v.checks if c["check"] == "options_candle_integrity")
    assert check["status"] == "FAIL"


def test_expiry_metadata_checks_selected_option_expiry():
    v = Validation()
    expiries = {
        "s": "ok",
        "data": {
            "symbol": "NIFTY",
            "expiry_dates": {"futures": ["2025-01-30"], "options": ["2025-01-02", "2025-01-09"]},
        },
    }
    assert "2025-01-09" in _validate_expiries(expiries, v, "2025-01-09")
    assert v.errors == 0


def test_contracts_and_history_must_link_to_same_symbol_and_expiry():
    v = Validation()
    contracts = {
        "s": "ok",
        "data": {
            "symbol": "NIFTY",
            "expiry_date": "2025-01-09",
            "contracts": {"futures": [], "options": ["NSE:NIFTY2510923600CE"]},
        },
    }
    symbols = _validate_contracts(contracts, v, "2025-01-09")
    assert "NSE:NIFTY2510923600CE" in symbols
    assert not [c for c in v.checks if c["status"] == "FAIL"]


def test_bad_api_status_fails_even_if_payload_has_candles():
    payload = sample_history()
    payload["s"] = "error"
    v = Validation()
    _validate_raw_option_history(payload, v, None, None, 5)
    assert v.errors > 0
    assert next(c for c in v.checks if c["check"] == "options_api_status")["status"] == "FAIL"
