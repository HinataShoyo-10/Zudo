"""Research-only multi-symbol cash-equity paper portfolio backtest.

This tool replays local OHLCV data and writes a virtual trade ledger. It never
connects to a broker and never submits orders.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Callable

from src.backtesting.engine import Bar, Trade, load_bars, run_backtest


def _asset_argument(value: str) -> tuple[str, Path]:
    symbol, separator, path = value.partition("=")
    symbol = symbol.strip()
    if not separator or not symbol or not path.strip():
        raise argparse.ArgumentTypeError("assets must use SYMBOL=CSV_PATH")
    return symbol, Path(path.strip())


def run_paper_portfolio(
    assets: dict[str, list[Bar]],
    *,
    initial_cash: float = 50_000.0,
    risk_fraction: float = 0.005,
    max_exposure_fraction: float = 0.20,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
    max_entry_time: time = time(14, 30),
    min_volume_multiple: float = 1.2,
    min_breakout_atr: float = 0.1,
    min_body_fraction: float = 0.6,
    max_opening_range_atr: float = 5.0,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """Simulate one selected cash-equity trade at a time across a symbol universe.

    Simultaneous setups are ranked only by pre-entry signal quality. While a
    position is open and for the remainder of that session, other signals are
    skipped. Rejected setups are retained in the output for audit.
    """
    if not assets:
        raise ValueError("at least one asset is required")
    if len(assets) != len(set(assets)):
        raise ValueError("asset symbols must be unique")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if not math.isfinite(risk_fraction) or not (0 < risk_fraction <= 0.02):
        raise ValueError("risk_fraction must be between 0 and 2%")
    if not math.isfinite(max_exposure_fraction) or not (0 < max_exposure_fraction <= 1):
        raise ValueError("max_exposure_fraction must be in (0, 1]")

    candidates: list[tuple[str, Trade]] = []
    raw_signal_count = 0
    incomplete_sessions = 0
    valid_sessions = 0
    filtered_reasons: dict[str, int] = {}
    for asset_index, (symbol, bars) in enumerate(assets.items(), start=1):
        trades, _, metrics = run_backtest(
            bars,
            initial_cash=initial_cash,
            fee_per_side=fee_per_side,
            slippage_bps=slippage_bps,
            strategy="orb",
            risk_fraction=risk_fraction,
            max_exposure_fraction=max_exposure_fraction,
            max_entry_time=max_entry_time,
            min_volume_multiple=min_volume_multiple,
            min_breakout_atr=min_breakout_atr,
            min_body_fraction=min_body_fraction,
            max_opening_range_atr=max_opening_range_atr,
        )
        raw_signal_count += int(metrics["raw_breakouts"])
        incomplete_sessions += int(metrics["skipped_incomplete_sessions"])
        valid_sessions += int(metrics["valid_sessions"])
        for reason, count in metrics["no_trade_reasons"].items():
            filtered_reasons[reason] = filtered_reasons.get(reason, 0) + int(count)
        candidates.extend((symbol, trade) for trade in trades)
        if progress_callback is not None:
            progress_callback(asset_index, len(assets), symbol)

    candidates.sort(key=lambda item: (
        item[1].entry_time,
        -item[1].setup_score,
        item[0],
    ))
    cash = initial_cash
    accepted: list[dict[str, Any]] = []
    decisions: list[dict[str, Any]] = []
    equity_curve: list[dict[str, float | str]] = []
    peak_equity = initial_cash
    max_drawdown = 0.0
    last_exit: datetime | None = None
    last_trade_date = None

    for symbol, candidate in candidates:
        entry = datetime.fromisoformat(candidate.entry_time)
        exit_time = datetime.fromisoformat(candidate.exit_time)
        decision: dict[str, Any] = {
            "symbol": symbol,
            "entry_time": candidate.entry_time,
            "exit_time": candidate.exit_time,
            "setup_score": candidate.setup_score,
            "status": "",
            "reason": "",
        }
        if last_trade_date == entry.date():
            decision.update(status="SKIPPED", reason="one_trade_per_session_limit")
            decisions.append(decision)
            continue
        if last_exit is not None and entry <= last_exit:
            decision.update(status="SKIPPED", reason="another_position_open")
            decisions.append(decision)
            continue

        unit_risk = candidate.planned_risk / candidate.quantity if candidate.quantity else 0.0
        if unit_risk <= 0:
            decision.update(status="SKIPPED", reason="invalid_candidate_risk")
            decisions.append(decision)
            continue
        risk_budget = cash * risk_fraction
        exposure_budget = cash * max_exposure_fraction
        quantity = min(
            int(risk_budget // unit_risk),
            int(exposure_budget // candidate.entry_price),
            int(max(0.0, cash - fee_per_side) // candidate.entry_price),
        )
        if quantity < 1:
            decision.update(status="SKIPPED", reason="position_exceeds_cash_or_risk_budget")
            decisions.append(decision)
            continue

        scale = quantity / candidate.quantity
        gross = candidate.gross_pnl * scale
        slippage = candidate.slippage_cost * scale
        fees = 2.0 * fee_per_side
        net = gross - slippage - fees
        planned_risk = unit_risk * quantity
        trade = asdict(candidate)
        trade.update({
            "symbol": symbol,
            "quantity": quantity,
            "gross_pnl": gross,
            "slippage_cost": slippage,
            "fees": fees,
            "net_pnl": net,
            "planned_risk": planned_risk,
            "cash_before": cash,
            "cash_after": cash + net,
        })
        accepted.append(trade)
        decision.update(status="TAKEN", reason="")
        decisions.append(decision)
        cash += net
        last_exit = exit_time
        last_trade_date = entry.date()
        equity_curve.append({
            "timestamp": exit_time.isoformat(),
            "equity": cash,
            "drawdown_pct": 0.0,
        })
        peak_equity = max(peak_equity, cash)
        drawdown_pct = (peak_equity - cash) / peak_equity * 100.0
        max_drawdown = max(max_drawdown, drawdown_pct / 100.0)
        equity_curve[-1]["drawdown_pct"] = drawdown_pct

    net_pnl = cash - initial_cash
    wins = [trade for trade in accepted if trade["net_pnl"] > 0]
    profits = sum(trade["net_pnl"] for trade in accepted if trade["net_pnl"] > 0)
    losses = -sum(trade["net_pnl"] for trade in accepted if trade["net_pnl"] < 0)
    summary = {
        "mode": "paper_backtest_only",
        "strategy": "filtered_opening_range_breakout_cash_equities",
        "initial_cash": initial_cash,
        "final_equity": cash,
        "net_pnl": net_pnl,
        "return_pct": net_pnl / initial_cash * 100.0,
        "trades": len(accepted),
        "win_rate_pct": len(wins) / len(accepted) * 100.0 if accepted else 0.0,
        "profit_factor": profits / losses if losses else None,
        "realized_max_drawdown_pct": max_drawdown * 100.0,
        "raw_breakouts": raw_signal_count,
        "eligible_candidates": len(candidates),
        "skipped_candidates": len(decisions) - len(accepted),
        "skip_reasons": _count_reasons(decisions),
        "filtered_no_trade_reasons": dict(sorted(filtered_reasons.items())),
        "valid_symbol_sessions": valid_sessions,
        "symbols": sorted(assets),
        "incomplete_symbol_sessions": incomplete_sessions,
        "risk_fraction": risk_fraction,
        "max_exposure_fraction": max_exposure_fraction,
        "min_volume_multiple": min_volume_multiple,
        "min_breakout_atr": min_breakout_atr,
        "min_body_fraction": min_body_fraction,
        "max_opening_range_atr": max_opening_range_atr,
        "fee_per_side": fee_per_side,
        "slippage_bps_per_side": slippage_bps,
        "limitations": [
            "No live orders are submitted; this is a historical paper simulation.",
            "Cash equities only; options and leveraged products are excluded.",
            "Flat brokerage is configurable, but exchange/regulatory taxes are not modeled.",
            "Drawdown is measured at completed-trade exits; intratrade drawdown is not included.",
            "A short history or a positive return does not establish a durable trading edge.",
        ],
    }
    return {
        "summary": summary,
        "trades": accepted,
        "decisions": decisions,
        "equity_curve": equity_curve,
    }


def _count_reasons(decisions: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for decision in decisions:
        if decision["status"] != "TAKEN":
            reason = str(decision["reason"])
            counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(dict.fromkeys(key for row in rows for key in row)) if rows else ["symbol", "status", "reason"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Historical multi-symbol cash-equity paper backtest; never places broker orders."
    )
    parser.add_argument(
        "--asset", action="append", type=_asset_argument, required=True,
        metavar="SYMBOL=CSV", help="Repeat for each symbol; CSV must contain 5-minute OHLCV",
    )
    parser.add_argument("--cash", type=float, default=50_000.0, help="Starting portfolio cash in INR")
    parser.add_argument("--risk-fraction", type=float, default=0.005, help="Maximum planned risk per trade")
    parser.add_argument("--max-exposure-fraction", type=float, default=0.20, help="Maximum notional exposure per position")
    parser.add_argument("--fee-per-side", type=float, default=0.0, help="Flat simulated brokerage per order")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Adverse simulated slippage per side")
    parser.add_argument("--out", type=Path, default=Path("paper_portfolio_output"))
    args = parser.parse_args()

    try:
        assets: dict[str, list[Bar]] = {}
        for symbol, path in args.asset:
            if symbol in assets:
                raise ValueError(f"duplicate asset symbol: {symbol}")
            assets[symbol] = load_bars(path)
        result = run_paper_portfolio(
            assets,
            initial_cash=args.cash,
            risk_fraction=args.risk_fraction,
            max_exposure_fraction=args.max_exposure_fraction,
            fee_per_side=args.fee_per_side,
            slippage_bps=args.slippage_bps,
        )
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(args.out / "trades.csv", result["trades"])
    _write_csv(args.out / "decisions.csv", result["decisions"])
    _write_csv(args.out / "equity_curve.csv", result["equity_curve"])
    print(json.dumps(result["summary"], indent=2, allow_nan=False))
    print(f"\nWrote paper results to {args.out.resolve()}")


if __name__ == "__main__":
    main()
