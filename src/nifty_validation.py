"""Run repeatable NIFTY historical validation and slippage stress scenarios.

Research-only: uses local historical candles, makes no network or broker calls,
and never places orders. OHLCV-only results are explicitly liquidity-unverified.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from src.options_backtest import CostRates, load_candles
from src.options_dynamic_backtest import load_manifest, run_dynamic_backtest


def validate_dataset(index_rows: list[dict[str, Any]], specs: list[dict[str, Any]],
                     contract_rows: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    index_days = sorted({row["timestamp"].date().isoformat() for row in index_rows})
    contract_stats = []
    for spec in specs:
        rows = contract_rows[spec["symbol"]]
        days = sorted({row["timestamp"].date().isoformat() for row in rows})
        contract_stats.append({
            "symbol": spec["symbol"], "strike": spec["strike"], "expiry": spec["expiry"],
            "candles": len(rows), "days": len(days),
            "sessions_overlapping_index": len(set(days) & set(index_days)),
            "first_timestamp": rows[0]["timestamp"].isoformat(),
            "last_timestamp": rows[-1]["timestamp"].isoformat(),
        })
    return {
        "index_candles": len(index_rows), "index_sessions": len(index_days),
        "index_first_timestamp": index_rows[0]["timestamp"].isoformat(),
        "index_last_timestamp": index_rows[-1]["timestamp"].isoformat(),
        "contracts_in_manifest": len(specs), "contracts": contract_stats,
        "research_warning": "Historical OHLCV alone does not prove executable fills or liquidity.",
    }


def run_validation(index_csv: Path, manifest_path: Path, out_dir: Path,
                   *, initial_cash: float = 100_000.0,
                   risk_fraction: float = 0.005, hard_risk_fraction: float = 0.01,
                   max_exposure_fraction: float = 0.20,
                   opening_range_minutes: int = 30, stop_loss_pct: float = 0.20,
                   target_r: float = 1.5) -> dict[str, Any]:
    index_rows = load_candles(index_csv)
    specs = load_manifest(manifest_path)
    contract_rows = {spec["symbol"]: load_candles(spec["csv"]) for spec in specs}
    dataset = validate_dataset(index_rows, specs, contract_rows)
    scenarios = [
        {"name": "ohlcv_slippage_5bps", "slippage_bps": 5.0, "strict_liquidity": False},
        {"name": "ohlcv_slippage_10bps", "slippage_bps": 10.0, "strict_liquidity": False},
        {"name": "ohlcv_slippage_20bps", "slippage_bps": 20.0, "strict_liquidity": False},
        {"name": "ohlcv_slippage_50bps", "slippage_bps": 50.0, "strict_liquidity": False},
        {"name": "strict_quotes_5bps", "slippage_bps": 5.0, "strict_liquidity": True},
    ]
    summaries = []
    out_dir.mkdir(parents=True, exist_ok=True)
    for scenario in scenarios:
        result = run_dynamic_backtest(
            index_rows, specs, contract_rows,
            initial_cash=initial_cash, risk_fraction=risk_fraction,
            hard_risk_fraction=hard_risk_fraction,
            max_exposure_fraction=max_exposure_fraction,
            opening_range_minutes=opening_range_minutes,
            stop_loss_pct=stop_loss_pct, target_r=target_r,
            slippage_bps=scenario["slippage_bps"], rates=CostRates(),
            require_liquidity_data=scenario["strict_liquidity"],
        )
        summary = dict(result["summary"])
        summary.update({
            "scenario": scenario["name"],
            "slippage_bps_per_side": scenario["slippage_bps"],
            "strict_liquidity": scenario["strict_liquidity"],
            "dataset_sessions": dataset["index_sessions"],
            "dataset_index_candles": dataset["index_candles"],
        })
        summaries.append(summary)
        scenario_dir = out_dir / scenario["name"]
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "summary.json").write_text(
            json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )
        _write_csv(scenario_dir / "trades.csv", result["trades"])
        _write_csv(scenario_dir / "signals.csv", result["signals"])
        _write_csv(scenario_dir / "candidate_diagnostics.csv", result["candidate_diagnostics"])

    report = {
        "purpose": "Historical robustness check; not a profitability guarantee.",
        "strategy_parameters_held_constant": {
            "opening_range_minutes": opening_range_minutes,
            "stop_loss_pct": stop_loss_pct, "target_r": target_r,
            "risk_fraction": risk_fraction, "hard_risk_fraction": hard_risk_fraction,
            "max_exposure_fraction": max_exposure_fraction,
        },
        "dataset": dataset,
        "scenarios": summaries,
        "interpretation_rules": [
            "Do not select parameters by whichever scenario has the best P&L.",
            "No trade is a valid result; do not loosen risk limits to force trades.",
            "OHLCV scenarios are liquidity-unverified when historical bid/ask is absent.",
            "A short sample is a mechanics check, not statistical evidence of edge.",
            "Costs are estimates and must be checked against applicable dated charges.",
        ],
    }
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    _write_csv(out_dir / "scenario_summary.csv", summaries)
    return report


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    columns = list(dict.fromkeys(key for row in rows for key in row)) if rows else ["scenario"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only NIFTY historical stress test")
    parser.add_argument("--index-csv", required=True, type=Path)
    parser.add_argument("--contracts-manifest", required=True, type=Path)
    parser.add_argument("--out", default=Path("backtest_output_nifty_validation"), type=Path)
    parser.add_argument("--initial-cash", default=100_000.0, type=float)
    parser.add_argument("--risk-fraction", default=0.005, type=float)
    parser.add_argument("--hard-risk-fraction", default=0.01, type=float)
    parser.add_argument("--max-exposure-fraction", default=0.20, type=float)
    parser.add_argument("--opening-range-minutes", default=30, type=int)
    parser.add_argument("--stop-loss-pct", default=0.20, type=float)
    parser.add_argument("--target-r", default=1.5, type=float)
    args = parser.parse_args()
    try:
        report = run_validation(
            args.index_csv, args.contracts_manifest, args.out,
            initial_cash=args.initial_cash, risk_fraction=args.risk_fraction,
            hard_risk_fraction=args.hard_risk_fraction,
            max_exposure_fraction=args.max_exposure_fraction,
            opening_range_minutes=args.opening_range_minutes,
            stop_loss_pct=args.stop_loss_pct, target_r=args.target_r,
        )
    except (OSError, ValueError, KeyError, TypeError, csv.Error, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(json.dumps({
        "dataset": report["dataset"],
        "scenarios": [{
            "scenario": s["scenario"], "trades": s["trades"],
            "net_pnl": s["net_pnl"], "return_pct": s["return_pct"],
            "max_drawdown_pct": s.get("max_drawdown_pct"),
            "liquidity_unverified_candidates": s.get("liquidity_unverified_candidates"),
            "skipped_signals": s["skipped_signals"],
        } for s in report["scenarios"]],
        "report": str(args.out / "validation_report.json"),
    }, indent=2))


if __name__ == "__main__":
    main()
