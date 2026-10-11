"""Command-line latest-session opportunity scanner for local OHLCV files."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from src.backtesting.engine import Bar, load_bars
from src.scanning.opportunity import scan_universe


def _asset_argument(value: str) -> tuple[str, Path]:
    symbol, separator, csv_path = value.partition("=")
    if not separator or not symbol.strip() or not csv_path.strip():
        raise argparse.ArgumentTypeError("assets must use SYMBOL=CSV_PATH")
    return symbol.strip(), Path(csv_path.strip())


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row))
    if not columns:
        columns = ["symbol", "status", "reason"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank latest-bar paper candidates from local 5-minute OHLCV; no orders."
    )
    parser.add_argument(
        "--asset", action="append", required=True, type=_asset_argument, metavar="SYMBOL=CSV",
        help="Repeat once per company stock; CSV requires timestamp,open,high,low,close,volume",
    )
    parser.add_argument("--cash", type=float, default=50_000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--max-exposure-fraction", type=float, default=0.20)
    parser.add_argument("--fee-per-side", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=5.0)
    parser.add_argument("--out", type=Path, default=Path("scan_output"))
    args = parser.parse_args()

    try:
        assets: dict[str, list[Bar]] = {}
        for symbol, path in args.asset:
            if symbol in assets:
                raise ValueError(f"duplicate asset symbol: {symbol}")
            assets[symbol] = load_bars(path)
        results = scan_universe(
            assets,
            initial_cash=args.cash,
            risk_fraction=args.risk_fraction,
            max_exposure_fraction=args.max_exposure_fraction,
            fee_per_side=args.fee_per_side,
            slippage_bps=args.slippage_bps,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "scan.json").write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(args.out / "scan.csv", results)
    opportunities = [row for row in results if row["status"] == "OPPORTUNITY"]
    print(json.dumps({
        "mode": "paper_candidate_scan_only",
        "symbols": sorted(assets),
        "opportunities": len(opportunities),
        "scan_rows": len(results),
        "reports": [str((args.out / name).resolve()) for name in ("scan.csv", "scan.json")],
    }, indent=2))


if __name__ == "__main__":
    main()
