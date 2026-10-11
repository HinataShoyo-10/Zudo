"""Backward-compatible entry point; implementation lives in src.data."""
from src.data.validate_market_data import (
    Validation,
    _read_json,
    _as_ist,
    _number,
    _check_ohlc,
    _session_issue,
    _gap_details,
    _validate_raw_option_history,
    _validate_expiries,
    _validate_contracts,
    _validate_stock_csv,
    main,
)

__all__ = [
    "Validation",
    "_read_json",
    "_as_ist",
    "_number",
    "_check_ohlc",
    "_session_issue",
    "_gap_details",
    "_validate_raw_option_history",
    "_validate_expiries",
    "_validate_contracts",
    "_validate_stock_csv",
    "main",
]


if __name__ == "__main__":
    main()
