"""Download historical OHLCV candles from FYERS into a normalized CSV.

This module is data acquisition only: it never places, modifies, or cancels orders.
FYERS limits minute-resolution history requests by date range, so the downloader
splits long ranges into bounded chunks and de-duplicates overlapping boundaries.
"""
from __future__ import annotations

import argparse
import csv
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
CSV_FIELDS = ["timestamp", "open", "high", "low", "close", "volume"]
MINUTE_RESOLUTIONS = {"1", "2", "3", "5", "10", "15", "20", "30", "45", "60", "120", "240"}
SECOND_RESOLUTIONS = {"5S", "10S", "15S", "30S", "45S"}


def _validate_dates(start: date, end: date) -> None:
    if end < start:
        raise ValueError("end date must be on or after start date")


def _chunks(start: date, end: date, max_days: int):
    """Yield inclusive date ranges no longer than max_days calendar days."""
    if max_days < 1:
        raise ValueError("max_days must be positive")
    cursor = start
    while cursor <= end:
        chunk_end = min(end, cursor + timedelta(days=max_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def normalize_response(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a successful FYERS history response to normalized CSV row dicts."""
    if not isinstance(response, dict):
        raise ValueError("FYERS history response must be a dictionary")
    if response.get("s") != "ok":
        message = response.get("message") or response.get("code") or "unknown API error"
        raise RuntimeError(f"FYERS history request failed: {message}")
    candles = response.get("candles")
    if not isinstance(candles, list):
        raise ValueError("FYERS response does not contain a candles list")

    rows: list[dict[str, Any]] = []
    for index, candle in enumerate(candles):
        if not isinstance(candle, (list, tuple)) or len(candle) < 6:
            raise ValueError(f"Invalid candle at response index {index}")
        try:
            timestamp = datetime.fromtimestamp(float(candle[0]), tz=timezone.utc).astimezone(IST)
            open_price, high, low, close, volume = map(float, candle[1:6])
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            raise ValueError(f"Invalid candle values at response index {index}") from exc
        if min(open_price, high, low, close) <= 0 or volume < 0:
            raise ValueError(f"Invalid prices or volume at response index {index}")
        if high < max(open_price, close, low) or low > min(open_price, close, high):
            raise ValueError(f"Invalid OHLC relationship at response index {index}")
        rows.append({
            "timestamp": timestamp.isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
    return rows


def download_history(
    client: Any,
    symbol: str,
    resolution: str,
    start: date,
    end: date,
    max_days_per_request: int = 100,
) -> list[dict[str, Any]]:
    """Fetch and return sorted, unique candle rows; accepts a FYERS-compatible client."""
    _validate_dates(start, end)
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    allowed = MINUTE_RESOLUTIONS | SECOND_RESOLUTIONS | {"D", "1D", "1W", "1M"}
    if resolution not in allowed:
        raise ValueError(f"Unsupported resolution: {resolution}")
    # FYERS documents smaller lookback limits for seconds and longer intervals.
    if resolution in SECOND_RESOLUTIONS:
        max_days_per_request = min(max_days_per_request, 30)
    elif resolution in {"D", "1D", "1W", "1M"}:
        max_days_per_request = min(max_days_per_request, 366)
    else:
        max_days_per_request = min(max_days_per_request, 100)
    if max_days_per_request < 1:
        raise ValueError("max_days_per_request must be positive")

    by_timestamp: dict[str, dict[str, Any]] = {}
    for chunk_start, chunk_end in _chunks(start, end, max_days_per_request):
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": chunk_start.isoformat(),
            "range_to": chunk_end.isoformat(),
            "cont_flag": "1",
        }
        response = client.history(data=payload)
        for row in normalize_response(response):
            by_timestamp[row["timestamp"]] = row

    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    return rows


def write_csv(rows: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Download FYERS historical candles to OHLCV CSV.")
    parser.add_argument("--symbol", required=True, help="FYERS symbol, e.g. NSE:SBIN-EQ")
    parser.add_argument("--resolution", default="5", help="FYERS interval, e.g. 1, 5, 15, 60, D")
    parser.add_argument("--from-date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--out", required=True, help="Output CSV path")
    args = parser.parse_args()

    try:
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
    except ValueError as exc:
        parser.error(f"dates must use YYYY-MM-DD: {exc}")

    # Load .env if python-dotenv is installed; environment variables also work.
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    app_id = os.getenv("FYERS_APP_ID")
    access_token = os.getenv("FYERS_ACCESS_TOKEN")
    if not app_id or not access_token:
        parser.error("Set FYERS_APP_ID and FYERS_ACCESS_TOKEN in the environment or .env")

    from fyers_apiv3 import fyersModel

    client = fyersModel.FyersModel(client_id=app_id, token=access_token, is_async=False, log_path="")
    rows = download_history(client, args.symbol, args.resolution, start, end)
    write_csv(rows, Path(args.out))
    print(f"Saved {len(rows)} unique candles to {args.out}")
    if not rows:
        print("Warning: the requested period returned no candles; verify symbol, dates, and API response.")


if __name__ == "__main__":
    main()
