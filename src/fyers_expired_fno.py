"""Backward-compatible entry point; implementation lives in src.data."""
from src.data.fyers_expired_fno import (
    BASE_URL,
    TIMEOUT_SECONDS,
    FyersExpiredFnoClient,
    _validate_date_range,
    normalize_historical_payload,
    write_candles_csv,
    write_json,
    main,
)

__all__ = [
    "BASE_URL",
    "TIMEOUT_SECONDS",
    "FyersExpiredFnoClient",
    "_validate_date_range",
    "normalize_historical_payload",
    "write_candles_csv",
    "write_json",
    "main",
]


if __name__ == "__main__":
    main()
