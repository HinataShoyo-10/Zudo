"""Run one combined quality gate over Zudo's stock and options market data.

This is a local, read-only validator. It makes no API calls and never places
orders. Errors fail the command; warnings are reported without failing it.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
REQUIRED_OHLCV = {"timestamp", "open", "high", "low", "close", "volume"}
OPTION_EXTRA_COLUMNS = {
    "open_interest", "implied_volatility", "delta", "gamma", "theta", "vega"
}
MARKET_OPEN = time(9, 15)
LAST_BAR_START = time(15, 25)
SESSION_CLOSE = time(15, 30)


class Validation:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.errors = 0
        self.warnings = 0

    def add(self, name: str, status: str, message: str, details: Any = None) -> None:
        item: dict[str, Any] = {"check": name, "status": status, "message": message}
        if details is not None:
            item["details"] = details
        self.checks.append(item)
        if status == "FAIL":
            self.errors += 1
        elif status == "WARN":
            self.warnings += 1

    def report(self) -> dict[str, Any]:
        return {
            "overall": "FAIL" if self.errors else ("WARN" if self.warnings else "PASS"),
            "error_count": self.errors,
            "warning_count": self.warnings,
            "checks": self.checks,
        }


def _read_json(path: Path, v: Validation, check_name: str) -> dict[str, Any] | None:
    try:
        with path.open(encoding="utf-8") as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("JSON root must be an object")
        v.add(check_name, "PASS", f"Loaded {path}")
        return payload
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        v.add(check_name, "FAIL", f"Cannot read {path}: {exc}")
        return None


def _as_ist(raw: Any) -> datetime:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("timestamp is not finite")
    # FYERS' observed expired F&O response uses Unix seconds, not milliseconds.
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(IST)


def _number(value: Any, field: str, row_number: int) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {row_number}: {field} is not numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"row {row_number}: {field} is not finite")
    return result


def _check_ohlc(item: dict[str, float], row_number: int) -> str | None:
    o, h, low, c = item["open"], item["high"], item["low"], item["close"]
    if min(o, h, low, c) < 0:
        return f"row {row_number}: negative OHLC price"
    if h < max(o, low, c):
        return f"row {row_number}: high is below open/low/close"
    if low > min(o, h, c):
        return f"row {row_number}: low is above open/high/close"
    if item["volume"] < 0:
        return f"row {row_number}: negative volume"
    return None


def _session_issue(ts: datetime, resolution: int) -> str | None:
    if ts.weekday() >= 5:
        return f"{ts.isoformat()} falls on a weekend"
    # datetime.time() retains tzinfo for aware timestamps; compare local wall
    # clock values without tzinfo because the input has already been converted to IST.
    wall_time = ts.timetz().replace(tzinfo=None)
    if wall_time < MARKET_OPEN or wall_time > LAST_BAR_START:
        return f"{ts.isoformat()} is outside the regular 09:15–15:25 IST candle-start window"
    elapsed = int((datetime.combine(ts.date(), wall_time) -
                   datetime.combine(ts.date(), MARKET_OPEN)).total_seconds() // 60)
    if elapsed % resolution != 0:
        return f"{ts.isoformat()} is not aligned to a {resolution}-minute session grid"
    return None


def _gap_details(timestamps: list[datetime], resolution: int) -> dict[str, Any]:
    per_day: dict[date, set[datetime]] = defaultdict(set)
    for ts in timestamps:
        per_day[ts.date()].add(ts)
    missing_by_day: dict[str, int] = {}
    for day, values in sorted(per_day.items()):
        expected = set()
        current = datetime.combine(day, MARKET_OPEN, tzinfo=IST)
        final = datetime.combine(day, LAST_BAR_START, tzinfo=IST)
        while current <= final:
            expected.add(current)
            current += timedelta(minutes=resolution)
        missing_by_day[day.isoformat()] = len(expected - values)
    return {
        "observed_dates": len(per_day),
        "missing_expected_candle_slots": sum(missing_by_day.values()),
        "missing_slots_by_date": missing_by_day,
    }


def _validate_raw_option_history(
    payload: dict[str, Any],
    v: Validation,
    expected_symbol: str | None,
    expected_date: str | None,
    expected_resolution: int,
) -> tuple[str | None, str | None]:
    symbol = payload.get("symbol") if isinstance(payload.get("symbol"), str) else None
    if payload.get("s") != "ok":
        v.add("options_api_status", "FAIL", f"Expected s='ok', got {payload.get('s')!r}")
    else:
        v.add("options_api_status", "PASS", "FYERS response status is ok")

    if expected_symbol and symbol != expected_symbol:
        v.add("options_symbol", "FAIL", f"Expected {expected_symbol!r}, got {symbol!r}")
    elif symbol:
        v.add("options_symbol", "PASS", f"Contract symbol: {symbol}")
    else:
        v.add("options_symbol", "FAIL", "History response has no string symbol")

    resolution = str(payload.get("resolution", ""))
    if resolution != str(expected_resolution):
        v.add("options_resolution", "FAIL",
              f"Expected {expected_resolution}-minute candles, got {resolution!r}")
    else:
        v.add("options_resolution", "PASS", f"Resolution is {resolution} minutes")

    columns = payload.get("columns")
    candles = payload.get("candles")
    if not isinstance(columns, list) or not columns or not all(isinstance(c, str) for c in columns):
        v.add("options_schema", "FAIL", "Missing/non-list columns array")
        return symbol, None
    normalized = [c.strip().lower() for c in columns]
    if len(set(normalized)) != len(normalized):
        v.add("options_schema", "FAIL", "Columns include duplicate names")
        return symbol, None
    missing = REQUIRED_OHLCV - set(normalized)
    unknown = set(normalized) - REQUIRED_OHLCV - OPTION_EXTRA_COLUMNS - {"timestamp"}
    if missing or unknown:
        v.add("options_schema", "FAIL", "Candle schema is not recognized",
              {"missing_required": sorted(missing), "unknown_columns": sorted(unknown)})
        return symbol, None
    if not isinstance(candles, list):
        v.add("options_schema", "FAIL", "Missing/non-list candles array")
        return symbol, None
    v.add("options_schema", "PASS", "Required OHLCV and supported optional option columns are present",
          {"columns": normalized, "schema_version": payload.get("schema_version"),
           "greeks_at": payload.get("greeks_at")})

    index = {name: i for i, name in enumerate(normalized)}
    timestamps: list[datetime] = []
    seen: set[datetime] = set()
    bad_rows: list[str] = []
    out_of_order = 0
    previous: datetime | None = None
    actual_dates: set[str] = set()
    greek_ranges: dict[str, dict[str, float]] = {}
    for row_num, row in enumerate(candles, start=1):
        try:
            if not isinstance(row, list) or len(row) != len(normalized):
                raise ValueError(f"row {row_num}: length does not match columns")
            ts = _as_ist(row[index["timestamp"]])
            if ts in seen:
                raise ValueError(f"row {row_num}: duplicate timestamp {ts.isoformat()}")
            seen.add(ts)
            if previous is not None and ts <= previous:
                out_of_order += 1
            previous = ts
            timestamps.append(ts)
            actual_dates.add(ts.date().isoformat())
            issue = _session_issue(ts, expected_resolution)
            if issue:
                raise ValueError(f"row {row_num}: {issue}")
            if expected_date and ts.date().isoformat() != expected_date:
                raise ValueError(f"row {row_num}: expected date {expected_date}, got {ts.date().isoformat()}")
            values: dict[str, float] = {}
            for field in ("open", "high", "low", "close", "volume"):
                values[field] = _number(row[index[field]], field, row_num)
            issue = _check_ohlc(values, row_num)
            if issue:
                raise ValueError(issue)
            if "open_interest" in index:
                oi = _number(row[index["open_interest"]], "open_interest", row_num)
                if oi < 0:
                    raise ValueError(f"row {row_num}: negative open_interest")
            for greek in OPTION_EXTRA_COLUMNS - {"open_interest"}:
                if greek in index:
                    val = _number(row[index[greek]], greek, row_num)
                    if greek == "delta" and not -1.000001 <= val <= 1.000001:
                        raise ValueError(f"row {row_num}: delta outside [-1, 1]: {val}")
                    bounds = greek_ranges.setdefault(greek, {"min": val, "max": val})
                    bounds["min"] = min(bounds["min"], val)
                    bounds["max"] = max(bounds["max"], val)
        except (ValueError, TypeError, OverflowError, OSError) as exc:
            bad_rows.append(str(exc))

    if not candles:
        v.add("options_candles", "FAIL", "History response contains no candles")
        return symbol, None
    if bad_rows:
        v.add("options_candle_integrity", "FAIL", f"{len(bad_rows)} invalid candle row(s)",
              {"examples": bad_rows[:10], "total_invalid_rows": len(bad_rows)})
    else:
        v.add("options_candle_integrity", "PASS", f"Validated {len(candles)} candle rows; OHLC, numeric fields, timestamps, and session boundaries are consistent",
              {"first_ist": min(timestamps).isoformat(), "last_ist": max(timestamps).isoformat(),
               "trading_dates": sorted(actual_dates), "greek_ranges": greek_ranges})

    if out_of_order:
        v.add("options_ordering", "WARN", f"{out_of_order} timestamp(s) are not strictly increasing in source order")
    else:
        v.add("options_ordering", "PASS", "Source candles are strictly time-ordered")

    gaps = _gap_details(timestamps, expected_resolution)
    if gaps["missing_expected_candle_slots"]:
        v.add("options_candle_gaps", "WARN",
              "Some expected 5-minute session slots are absent; investigate, but illiquid options may legitimately have missing bars",
              gaps)
    else:
        v.add("options_candle_gaps", "PASS", "No missing session-grid slots", gaps)
    v.add("options_greeks_timestamp", "PASS" if not payload.get("greeks_at") or payload.get("greeks_at") == "candle_close" else "WARN",
          f"greeks_at={payload.get('greeks_at')!r}; interpret Greeks using the provider's stated timing")
    return symbol, resolution


def _validate_expiries(payload: dict[str, Any] | None, v: Validation, expected_expiry: str) -> set[str]:
    if payload is None:
        v.add("expiry_metadata", "FAIL", "Expiry JSON could not be loaded")
        return set()
    data = payload.get("data", {})
    expiry_dates = data.get("expiry_dates", {}) if isinstance(data, dict) else {}
    options = expiry_dates.get("options", []) if isinstance(expiry_dates, dict) else []
    if payload.get("s") != "ok" or not isinstance(options, list):
        v.add("expiry_metadata", "FAIL", "Expiry response status/schema is invalid")
        return set()
    valid = {str(item) for item in options}
    if expected_expiry not in valid:
        v.add("expiry_metadata", "FAIL", f"Selected expiry {expected_expiry} is not in returned option expiry dates",
              {"returned_expiries": sorted(valid)})
    else:
        v.add("expiry_metadata", "PASS", f"Selected expiry {expected_expiry} appears in returned option expiries",
              {"option_expiry_count": len(valid), "underlying": data.get("symbol")})
    return valid


def _validate_contracts(payload: dict[str, Any] | None, v: Validation, expected_expiry: str) -> set[str]:
    if payload is None:
        v.add("contracts_metadata", "FAIL", "Contracts JSON could not be loaded")
        return set()
    data = payload.get("data", {})
    contract_data = data.get("contracts", {}) if isinstance(data, dict) else {}
    options = contract_data.get("options", []) if isinstance(contract_data, dict) else []
    issues = []
    if payload.get("s") != "ok":
        issues.append(f"status is {payload.get('s')!r}")
    if not isinstance(options, list) or not options or not all(isinstance(x, str) for x in options):
        issues.append("options contract list is absent, empty, or malformed")
        options = []
    if data.get("expiry_date") != expected_expiry:
        issues.append(f"expiry_date is {data.get('expiry_date')!r}, expected {expected_expiry!r}")
    if issues:
        v.add("contracts_metadata", "FAIL", "; ".join(issues))
        return set()
    v.add("contracts_metadata", "PASS", f"Found {len(options)} option contract symbols for {expected_expiry}",
          {"underlying": data.get("symbol"), "sample_symbols": options[:4]})
    return set(options)


def _validate_stock_csv(path: Path, v: Validation, expected_resolution: int) -> None:
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            fields = {name.strip().lower() for name in (reader.fieldnames or [])}
            if not REQUIRED_OHLCV.issubset(fields):
                v.add("stock_csv_schema", "FAIL", "Stock CSV is missing required columns",
                      {"required": sorted(REQUIRED_OHLCV), "actual": sorted(fields)})
                return
            rows = list(reader)
    except (OSError, csv.Error) as exc:
        v.add("stock_csv_read", "FAIL", f"Cannot read {path}: {exc}")
        return
    if not rows:
        v.add("stock_csv_rows", "FAIL", f"{path} has no data rows")
        return

    v.add("stock_csv_schema", "PASS", f"CSV contains required columns: {path}")
    timestamps: list[datetime] = []
    seen: set[datetime] = set()
    bad_rows: list[str] = []
    previous: datetime | None = None
    # Normalize headers so they are treated case-insensitively.
    for row_num, original in enumerate(rows, start=2):
        row = {str(k).strip().lower(): val for k, val in original.items() if k is not None}
        try:
            parsed = datetime.fromisoformat(str(row["timestamp"]).strip().replace("Z", "+00:00"))
            ts = parsed.replace(tzinfo=IST) if parsed.tzinfo is None else parsed.astimezone(IST)
            if ts in seen:
                raise ValueError(f"line {row_num}: duplicate timestamp {ts.isoformat()}")
            if previous is not None and ts <= previous:
                raise ValueError(f"line {row_num}: timestamps are not strictly increasing")
            previous = ts
            seen.add(ts)
            timestamps.append(ts)
            issue = _session_issue(ts, expected_resolution)
            if issue:
                raise ValueError(f"line {row_num}: {issue}")
            values = {field: _number(row[field], field, row_num) for field in ("open", "high", "low", "close", "volume")}
            issue = _check_ohlc(values, row_num)
            if issue:
                raise ValueError(issue)
        except (ValueError, TypeError, KeyError, OverflowError, OSError) as exc:
            bad_rows.append(str(exc))
    if bad_rows:
        v.add("stock_csv_integrity", "FAIL", f"{len(bad_rows)} invalid CSV row(s)",
              {"examples": bad_rows[:10], "total_invalid_rows": len(bad_rows)})
        return
    v.add("stock_csv_integrity", "PASS", f"Validated {len(rows)} stock candle rows",
          {"first_ist": min(timestamps).isoformat(), "last_ist": max(timestamps).isoformat(),
           "trading_dates": len({ts.date() for ts in timestamps})})
    gaps = _gap_details(timestamps, expected_resolution)
    if gaps["missing_expected_candle_slots"]:
        v.add("stock_csv_gaps", "WARN", "Stock CSV has missing session-grid slots; verify against source/calendar",
              gaps)
    else:
        v.add("stock_csv_gaps", "PASS", "No missing session-grid slots", gaps)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One local quality gate for downloaded SBIN/NIFTY market data; no API calls or orders."
    )
    parser.add_argument("--expiry-json", required=True, help="Saved FYERS expiry-date response JSON")
    parser.add_argument("--contracts-json", required=True, help="Saved FYERS contracts response JSON")
    parser.add_argument("--options-json", required=True, help="Saved raw FYERS option history response JSON")
    parser.add_argument("--expected-expiry", required=True, help="Expected option expiry YYYY-MM-DD")
    parser.add_argument("--expected-trading-date", help="Expected trading date YYYY-MM-DD for option candles")
    parser.add_argument("--expected-symbol", help="Option symbol expected in both contracts and history")
    parser.add_argument("--resolution", type=int, default=5, help="Candle interval in minutes (default: 5)")
    parser.add_argument("--stock-csv", help="Optional SBIN/stock OHLCV CSV to validate in the same run")
    parser.add_argument("--report-out", help="Optional path for JSON validation report")
    args = parser.parse_args()

    v = Validation()
    try:
        date.fromisoformat(args.expected_expiry)
        if args.expected_trading_date:
            date.fromisoformat(args.expected_trading_date)
        if args.resolution <= 0 or args.resolution > 240:
            raise ValueError("resolution must be between 1 and 240 minutes")
    except ValueError as exc:
        parser.error(str(exc))

    expiry_payload = _read_json(Path(args.expiry_json), v, "expiry_file")
    contracts_payload = _read_json(Path(args.contracts_json), v, "contracts_file")
    history_payload = _read_json(Path(args.options_json), v, "options_history_file")

    returned_expiries = _validate_expiries(expiry_payload, v, args.expected_expiry)
    returned_contracts = _validate_contracts(contracts_payload, v, args.expected_expiry)
    history_symbol: str | None = None
    if history_payload is not None:
        history_symbol, _ = _validate_raw_option_history(
            history_payload, v, args.expected_symbol, args.expected_trading_date, args.resolution
        )
    if args.expected_expiry not in returned_expiries:
        # The earlier metadata check already records the fail; no duplicate warning.
        pass
    if history_symbol and returned_contracts and history_symbol not in returned_contracts:
        v.add("option_contract_link", "FAIL", "History symbol is absent from selected-expiry contracts",
              {"history_symbol": history_symbol, "expiry": args.expected_expiry})
    elif history_symbol and returned_contracts:
        v.add("option_contract_link", "PASS", "History symbol is present in selected-expiry contract list")
    if args.expected_symbol and history_symbol == args.expected_symbol and returned_contracts and args.expected_symbol not in returned_contracts:
        v.add("expected_symbol_membership", "FAIL", "Requested symbol is missing from contracts list")

    if args.stock_csv:
        _validate_stock_csv(Path(args.stock_csv), v, args.resolution)

    report = v.report()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if args.report_out:
        output_path = Path(args.report_out)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Report saved to {output_path}")
    sys.exit(1 if v.errors else 0)


if __name__ == "__main__":
    main()
