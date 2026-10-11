"""Rate-paced, data-only FYERS history workflow for the stock scanner."""
from __future__ import annotations

import os
import re
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import requests

from src.backtesting.engine import Bar, load_bars
from src.data.fyers_history import download_history, write_csv

MAX_WATCHLIST_SYMBOLS = 50
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.0


def load_symbols(path: Path, limit: int = MAX_WATCHLIST_SYMBOLS) -> list[str]:
    """Load a comment-aware symbol file and enforce the API request bound."""
    if limit < 1:
        raise ValueError("limit must be positive")
    symbols = [
        item
        for line in path.read_text(encoding="utf-8").splitlines()
        if (item := line.partition("#")[0].strip())
    ]
    if not symbols:
        raise ValueError(f"No symbols configured in {path}")
    if len(symbols) != len(set(symbols)):
        raise ValueError(f"Duplicate symbols in {path}")
    if len(symbols) > limit:
        raise ValueError(f"Watchlist has {len(symbols)} symbols; maximum is {limit}")
    return symbols


def create_fyers_client(env_path: Path) -> Any:
    """Create the read-only history client using credentials already configured locally."""
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
    except ImportError:
        pass
    app_id = os.getenv("FYERS_APP_ID", "").strip()
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not access_token:
        raise ValueError("Set FYERS_APP_ID and FYERS_ACCESS_TOKEN in .env before fetching data.")
    from fyers_apiv3 import fyersModel

    return fyersModel.FyersModel(
        client_id=app_id,
        token=access_token,
        is_async=False,
        log_path="",
    )


def fetch_symbol_history(
    client: Any,
    symbols: list[str],
    resolution: str,
    start: date,
    end: date,
    output_dir: Path,
    *,
    request_interval_seconds: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    progress_callback: Callable[[int, int, str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, list[Bar]]:
    """Fetch all requested symbols serially with a bounded, configurable delay.

    Any failed symbol aborts the scan with explicit per-symbol details; partial
    coverage is never passed off as a complete watchlist ranking.
    """
    if not symbols:
        raise ValueError("at least one symbol is required")
    if len(symbols) != len(set(symbols)):
        raise ValueError("symbols must be unique")
    if len(symbols) > MAX_WATCHLIST_SYMBOLS:
        raise ValueError(f"at most {MAX_WATCHLIST_SYMBOLS} symbols may be fetched at once")
    if request_interval_seconds < 0:
        raise ValueError("request_interval_seconds must be non-negative")

    assets: dict[str, list[Bar]] = {}
    failures: list[str] = []
    safe_names: dict[str, str] = {}
    for index, symbol in enumerate(symbols, start=1):
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", symbol).strip("_").lower()
        if not safe_name:
            failures.append(f"{symbol!r}: invalid symbol filename")
            continue
        other_symbol = safe_names.get(safe_name)
        if other_symbol is not None and other_symbol != symbol:
            failures.append(f"{symbol}: filename collides with {other_symbol}")
            continue
        safe_names[safe_name] = symbol
        try:
            if index > 1 and request_interval_seconds:
                sleep(request_interval_seconds)
            rows = download_history(client, symbol, resolution, start, end)
            if not rows:
                raise ValueError("FYERS returned no candles")
            path = output_dir / f"{safe_name}_{resolution}.csv"
            write_csv(rows, path)
            assets[symbol] = load_bars(path)
        except (OSError, RuntimeError, ValueError, requests.RequestException) as exc:
            failures.append(f"{symbol}: {type(exc).__name__}: {exc}")
        if progress_callback is not None:
            progress_callback(index, len(symbols), symbol)

    if failures:
        raise RuntimeError(
            "FYERS history incomplete; no ranking/scan was produced:\n" + "\n".join(failures)
        )
    return assets


def premarket_history_dates(today: date, lookback_days: int = 180) -> tuple[date, date]:
    if lookback_days < 90 or lookback_days >= 366:
        raise ValueError("daily lookback_days must be between 90 and 365")
    return today - timedelta(days=lookback_days), today


def intraday_history_dates(today: date) -> tuple[date, date]:
    return today, today
