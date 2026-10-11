"""Read-only downloader for FYERS expired F&O historical data.

This uses the FYERS data endpoints only; it never calls an order endpoint.
It writes raw JSON responses deliberately until the account's returned schema
has been inspected and tested against actual data.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import date
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api-t1.fyers.in/data/history/fno/expired"
TIMEOUT_SECONDS = 30


def _validate_date_range(start: str, end: str) -> None:
    try:
        start_date = date.fromisoformat(start)
        end_date = date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError("Dates must use YYYY-MM-DD format") from exc
    if end_date < start_date:
        raise ValueError("to-date must be on or after from-date")


class FyersExpiredFnoClient:
    """Minimal client for FYERS data-only expired F&O endpoints."""

    def __init__(self, app_id: str, access_token: str, http: Any = requests):
        if not app_id.strip() or not access_token.strip():
            raise ValueError("FYERS_APP_ID and FYERS_ACCESS_TOKEN must be configured")
        self._headers = {"Authorization": f"{app_id}:{access_token}"}
        self._http = http

    def _get(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        response = self._http.get(
            f"{BASE_URL}/{endpoint}",
            params=params,
            headers=self._headers,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("FYERS response was not a JSON object")
        if payload.get("s") == "error":
            message = payload.get("message") or payload.get("code") or "unspecified API error"
            raise RuntimeError(f"FYERS expired F&O request failed: {message}")
        return payload

    def expiry_dates(self, underlying: str, start: str, end: str) -> dict[str, Any]:
        _validate_date_range(start, end)
        if not underlying.strip():
            raise ValueError("underlying symbol must not be empty")
        return self._get(
            "expiry-dates",
            {
                "symbol": underlying,
                "range_from": start,
                "range_to": end,
                "date_format": "1",
            },
        )

    def contract_symbols(self, underlying: str, expiry_date: str) -> dict[str, Any]:
        try:
            date.fromisoformat(expiry_date)
        except ValueError as exc:
            raise ValueError("expiry-date must use YYYY-MM-DD format") from exc
        if not underlying.strip():
            raise ValueError("underlying symbol must not be empty")
        return self._get(
            "underlying-symbols",
            {"symbol": underlying, "expiry_date": expiry_date},
        )

    def historical_contract(
        self,
        symbol: str,
        resolution: str,
        start: str,
        end: str,
        include_greeks: bool = False,
        include_oi: bool = False,
    ) -> dict[str, Any]:
        _validate_date_range(start, end)
        if not symbol.strip():
            raise ValueError("contract symbol must not be empty")
        if resolution not in {"1", "2", "3", "5", "10", "15", "20", "30", "45", "60", "120", "240", "D", "1D"}:
            raise ValueError(f"Unsupported resolution: {resolution}")
        return self._get(
            "historical-data",
            {
                "symbol": symbol,
                "resolution": resolution,
                "date_format": "1",
                "range_from": start,
                "range_to": end,
                "include_greeks": "1" if include_greeks else "0",
                "include_oi": "1" if include_oi else "0",
            },
        )


def write_json(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Download FYERS expired F&O history (read-only; no orders).")
    parser.add_argument("--mode", choices=("expiries", "contracts", "history"), required=True)
    parser.add_argument("--underlying", default="NSE:NIFTY50-INDEX", help="Underlying index/symbol")
    parser.add_argument("--symbol", help="Expired option contract symbol for --mode history")
    parser.add_argument("--expiry-date", help="Expiry date YYYY-MM-DD for --mode contracts")
    parser.add_argument("--from-date", help="Start date YYYY-MM-DD")
    parser.add_argument("--to-date", help="End date YYYY-MM-DD")
    parser.add_argument("--resolution", default="5", help="Intraday candle resolution, default 5 minutes")
    parser.add_argument("--include-greeks", action="store_true")
    parser.add_argument("--include-oi", action="store_true")
    parser.add_argument("--out", required=True, help="Output JSON path; use an untracked data/ path")
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    app_id = os.getenv("FYERS_APP_ID", "")
    access_token = os.getenv("FYERS_ACCESS_TOKEN", "")
    if not app_id or not access_token:
        parser.error("Set FYERS_APP_ID and FYERS_ACCESS_TOKEN in the local ignored .env file")

    client = FyersExpiredFnoClient(app_id, access_token)
    try:
        if args.mode == "expiries":
            if not args.from_date or not args.to_date:
                parser.error("--mode expiries requires --from-date and --to-date")
            payload = client.expiry_dates(args.underlying, args.from_date, args.to_date)
        elif args.mode == "contracts":
            if not args.expiry_date:
                parser.error("--mode contracts requires --expiry-date")
            payload = client.contract_symbols(args.underlying, args.expiry_date)
        else:
            if not args.symbol or not args.from_date or not args.to_date:
                parser.error("--mode history requires --symbol, --from-date and --to-date")
            payload = client.historical_contract(
                args.symbol, args.resolution, args.from_date, args.to_date,
                include_greeks=args.include_greeks, include_oi=args.include_oi,
            )
        output = Path(args.out)
        write_json(payload, output)
        print(f"Saved FYERS response to {output} (top-level keys: {', '.join(sorted(payload))})")
        print("Review the returned schema before using this data in a backtest.")
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        # Avoid printing request headers, app credentials, or access tokens.
        parser.error(f"FYERS data request failed: {exc}")


if __name__ == "__main__":
    main()
