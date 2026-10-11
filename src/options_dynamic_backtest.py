"""Research-only dynamic-strike NIFTY call options backtester.

Reads a manifest of independently downloaded option-contract CSVs. On each
index breakout, it selects the nearest-ATM eligible call using only the
signal-time index close, the next candle's option open, and the configured
risk/cash gates. It does not rank contracts by future performance and places
no orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import time
from pathlib import Path
from typing import Any

from src.options_backtest import CostRates, load_candles, run_backtest, parse_time


def _strike_from_symbol(symbol: str) -> float:
    match = re.search(r"(\d{5,6})(?:CE|PE)$", symbol.upper())
    if not match:
        raise ValueError(f"Cannot infer strike from option symbol {symbol!r}; specify strike in manifest")
    return float(match.group(1))


def load_manifest(path: Path, base_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load {contracts:[{symbol,csv,lot_size,strike,expiry,option_type}]} manifest."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("contracts")
    if not isinstance(items, list) or not items:
        raise ValueError("manifest must contain a non-empty 'contracts' array")
    root = base_dir or path.parent
    seen: set[str] = set()
    contracts = []
    for item in items:
        symbol = str(item.get("symbol", "")).strip()
        csv_name = str(item.get("csv", "")).strip()
        option_type = str(item.get("option_type", "CE")).upper()
        if not symbol or not csv_name:
            raise ValueError("each contract requires non-empty symbol and csv")
        if symbol in seen:
            raise ValueError(f"duplicate contract symbol in manifest: {symbol}")
        seen.add(symbol)
        if option_type != "CE":
            continue  # This first dynamic version intentionally supports calls only.
        lot_size = int(item.get("lot_size", 0))
        if lot_size <= 0:
            raise ValueError(f"{symbol}: lot_size must be positive")
        strike_value = item.get("strike")\n        strike = float(strike_value) if strike_value is not None else _strike_from_symbol(symbol)
        expiry = str(item.get("expiry", ""))
        if not expiry:
            raise ValueError(f"{symbol}: specify expiry in manifest")
        csv_path = Path(csv_name)
        if not csv_path.is_absolute():
            csv_path = root / csv_path
        contracts.append({
            "symbol": symbol, "csv": csv_path, "lot_size": lot_size,
            "strike": strike, "expiry": expiry, "option_type": option_type,
        })
    if not contracts:
        raise ValueError("manifest has no call contracts (option_type must be CE)")
    return contracts


def run_dynamic_backtest(
    index_rows: list[dict[str, Any]],
    contract_specs: list[dict[str, Any]],
    contract_rows: dict[str, list[dict[str, Any]]],
    *,
    initial_cash: float = 100_000.0,
    risk_fraction: float = 0.005,
    hard_risk_fraction: float = 0.01,
    max_exposure_fraction: float = 0.20,
    opening_range_minutes: int = 30,
    stop_loss_pct: float = 0.20,
    target_r: float = 1.5,
    max_entry_time: time = time(14, 30),
    slippage_bps: float = 5.0,
    rates: CostRates = CostRates(),
) -> dict[str, Any]:
    """Select nearest-ATM call among contracts passing entry-time risk gates."""
    by_day: dict[str, list[dict[str, Any]]] = {}
    for row in index_rows:
        by_day.setdefault(row["timestamp"].date().isoformat(), []).append(row)
    for rows in by_day.values():
        rows.sort(key=lambda r: r["timestamp"])

    all_trades: list[dict[str, Any]] = []
    all_signals: list[dict[str, Any]] = []
    skipped = Counter()
    days_processed = 0
    cash = initial_cash

    for day, day_index in sorted(by_day.items()):
        candidates = []
        daily_signal: dict[str, Any] | None = None
        for spec in contract_specs:
            rows = [r for r in contract_rows[spec["symbol"]] if r["timestamp"].date().isoformat() == day]
            if not rows:
                continue
            try:
                result = run_backtest(
                    day_index, rows, option_symbol=spec["symbol"], lot_size=spec["lot_size"],
                    initial_cash=cash, risk_fraction=risk_fraction,
                    hard_risk_fraction=hard_risk_fraction,
                    opening_range_minutes=opening_range_minutes,
                    stop_loss_pct=stop_loss_pct, target_r=target_r,
                    max_entry_time=max_entry_time, slippage_bps=slippage_bps,
                    rates=rates, max_exposure_fraction=max_exposure_fraction,
                )
            except ValueError:
                # Missing/misaligned history makes this contract ineligible for this day.
                continue
            signal = next((s for s in result["signals"] if s["status"] != "NO_SIGNAL"), None)
            if signal is None:
                continue
            daily_signal = signal
            if result["trades"]:
                trade = result["trades"][0]
                candidates.append((abs(spec["strike"] - float(signal["index_close_at_signal"])), spec, trade, signal))
            elif signal.get("skip_reason"):
                skipped[signal["skip_reason"]] += 1

        if candidates:
            # Choose by strike proximity only, never by future P&L.
            _, chosen_spec, trade, signal = min(candidates, key=lambda x: (x[0], x[1]["strike"], x[1]["symbol"]))
            trade["trade_id"] = len(all_trades) + 1
            trade["cash_before_trade"] = cash
            cash += float(trade["net_pnl"])
            trade["cash_after_trade"] = cash
            all_trades.append(trade)
            signal = dict(signal)
            signal.update({"status": "TRADED", "skip_reason": "", "selected_strike": chosen_spec["strike"],
                           "selected_expiry": chosen_spec["expiry"], "selected_option_type": "CE"})
            all_signals.append(signal)
            days_processed += 1
        elif daily_signal is not None:
            signal = dict(daily_signal)
            signal["status"] = "SKIPPED"
            signal.setdefault("skip_reason", "no_eligible_contract")
            all_signals.append(signal)
        else:
            all_signals.append({
                "signal_timestamp": "", "entry_timestamp": "", "option_symbol": "",
                "opening_range_high": "", "opening_range_low": "", "index_close_at_signal": "",
                "status": "NO_SIGNAL", "skip_reason": "", "planned_risk": 0.0,
                "quantity": 0, "lots": 0,
            })

    net_pnl = sum(float(t["net_pnl"]) for t in all_trades)
    gross_pnl = sum(float(t["gross_pnl"]) for t in all_trades)
    total_costs = sum(float(t["trading_charges"]) for t in all_trades)
    total_slippage = sum(float(t["slippage_cost"]) for t in all_trades)
    wins = [t for t in all_trades if float(t["net_pnl"]) > 0]
    losses = [t for t in all_trades if float(t["net_pnl"]) < 0]
    gross_losses = -sum(min(0.0, float(t["net_pnl"])) for t in all_trades)
    summary = {
        "strategy": "NIFTY 30-minute opening-range breakout; dynamically selected nearest-ATM eligible call",
        "selection_method": "closest strike to index close at signal time among contracts passing risk/cash gates; no future-P&L ranking",
        "contracts_in_manifest": len(contract_specs),
        "initial_cash": initial_cash, "final_equity": initial_cash + net_pnl,
        "net_pnl": net_pnl, "return_pct": net_pnl / initial_cash * 100.0,
        "trades": len(all_trades), "signals": sum(s["status"] != "NO_SIGNAL" for s in all_signals),
        "no_signal_days": sum(s["status"] == "NO_SIGNAL" for s in all_signals),
        "skipped_signals": sum(s["status"] == "SKIPPED" for s in all_signals),
        "skipped_reasons": dict(sorted(skipped.items())),
        "trading_days_processed": len(by_day), "days_with_trades": days_processed,
        "gross_pnl_before_costs": gross_pnl, "slippage_cost_total": total_slippage,
        "trading_charges_total": total_costs,
        "net_win_rate_pct": len(wins) / len(all_trades) * 100.0 if all_trades else 0.0,
        "profit_factor_net": sum(max(0.0, float(t["net_pnl"])) for t in all_trades) / gross_losses if gross_losses else (None if wins else 0.0),
        "risk_fraction_target": risk_fraction, "hard_risk_fraction": hard_risk_fraction,
        "max_exposure_fraction": max_exposure_fraction, "opening_range_minutes": opening_range_minutes,
        "stop_loss_pct_of_option_premium": stop_loss_pct * 100.0, "take_profit_r": target_r,
        "index_volume_used_for_signals": False, "intraday_only": True, "research_only": True,
        "limitations": [
            "Calls only; contract universe is limited to symbols and history supplied in the manifest.",
            "Contract is selected from entry-time strike proximity and affordability, not liquidity/spread or future outcomes.",
            "Candidate backtests require exact same-day index/option timestamp alignment.",
            "OHLC fills, slippage, and transaction charges are estimates; stops are not guaranteed loss bounds.",
            "Opening-range breakout is an unvalidated hypothesis; no live orders are placed.",
        ],
    }
    return {"summary": summary, "trades": all_trades, "signals": all_signals}


def main() -> None:
    parser = argparse.ArgumentParser(description="Research-only dynamic-strike NIFTY call backtest; never places orders.")
    parser.add_argument("--index-csv", required=True, help="NIFTY 5-minute index OHLCV CSV")
    parser.add_argument("--contracts-manifest", required=True, help="JSON manifest of option symbols, CSVs, expiry, strike and lot size")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005)
    parser.add_argument("--hard-risk-fraction", type=float, default=0.01)
    parser.add_argument("--max-exposure-fraction", type=float, default=0.20)
    parser.add_argument("--opening-range-minutes", type=int, default=30)
    parser.add_argument("--stop-loss-pct", type=float, default=0.20)
    parser.add_argument("--target-r", type=float, default=1.5)
    parser.add_argument("--max-entry-time", type=parse_time, default=time(14, 30))
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--out", required=True, help="Output directory")
    args = parser.parse_args()
    try:
        index_rows = load_candles(Path(args.index_csv))
        specs = load_manifest(Path(args.contracts_manifest))
        data = {s["symbol"]: load_candles(s["csv"]) for s in specs}
        result = run_dynamic_backtest(
            index_rows, specs, data, initial_cash=args.initial_cash,
            risk_fraction=args.risk_fraction, hard_risk_fraction=args.hard_risk_fraction,
            max_exposure_fraction=args.max_exposure_fraction,
            opening_range_minutes=args.opening_range_minutes, stop_loss_pct=args.stop_loss_pct,
            target_r=args.target_r, max_entry_time=args.max_entry_time,
            slippage_bps=args.slippage_bps_per_side,
        )
    except (OSError, ValueError, csv.Error, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(result["summary"], indent=2, allow_nan=False) + "\n", encoding="utf-8")
    _write_csv(out / "trades.csv", result["trades"])
    _write_csv(out / "signals.csv", result["signals"])
    print(json.dumps(result["summary"], indent=2))
    print(f"Saved dynamic backtest outputs to {out}")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(k for row in rows for k in row)) if rows else ["trade_id", "option_symbol", "net_pnl"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
