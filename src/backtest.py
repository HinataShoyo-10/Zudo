"""Offline, long-only EMA crossover backtester for CSV OHLCV candles.

No broker connection or order API is used. Signals are calculated at candle close
and filled at the next candle's open to reduce look-ahead bias.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: int
    gross_pnl: float
    fees: float
    net_pnl: float


def load_bars(path: Path) -> list[Bar]:
    """Load timestamp,open,high,low,close,volume CSV candles."""
    bars: list[Bar] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        fields = {field.strip().lower() for field in (reader.fieldnames or [])}
        missing = required - fields
        if missing:
            raise ValueError(f"CSV missing required columns: {', '.join(sorted(missing))}")
        for line_no, row in enumerate(reader, start=2):
            normalized = {str(k).strip().lower(): v for k, v in row.items() if k is not None}
            try:
                timestamp = datetime.fromisoformat(normalized["timestamp"].strip())
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=IST)
                values = {key: float(normalized[key]) for key in ("open", "high", "low", "close", "volume")}
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid CSV data on line {line_no}: {exc}") from exc
            bar = Bar(timestamp=timestamp, **values)
            if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
                raise ValueError(f"Prices must be positive and volume non-negative (line {line_no})")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
                raise ValueError(f"Invalid OHLC relationship on line {line_no}")
            if bars and timestamp <= bars[-1].timestamp:
                raise ValueError("CSV timestamps must be strictly increasing with no duplicates")
            bars.append(bar)
    if not bars:
        raise ValueError("CSV contains no candles")
    return bars


def ema(values: list[float], period: int) -> list[float | None]:
    """EMA with an SMA seed; values before the warm-up period are None."""
    if period < 1:
        raise ValueError("EMA periods must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    seed_index = period - 1
    result[seed_index] = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        previous = result[i - 1]
        assert previous is not None
        result[i] = values[i] * alpha + previous * (1.0 - alpha)
    return result


def run_backtest(
    bars: list[Bar],
    fast_period: int = 9,
    slow_period: int = 21,
    quantity: int = 1,
    initial_cash: float = 100_000.0,
    fee_per_side: float = 20.0,
    slippage_bps: float = 5.0,
) -> tuple[list[Trade], list[dict[str, float | str]], dict[str, float | int | None]]:
    """Run a long-only EMA crossover. Orders fill at the next bar's open."""
    if fast_period < 1 or slow_period < 2 or fast_period >= slow_period:
        raise ValueError("Require 1 <= fast_period < slow_period")
    if quantity < 1 or initial_cash <= 0 or fee_per_side < 0 or slippage_bps < 0:
        raise ValueError("Quantity/cash must be positive and fees/slippage non-negative")
    if len(bars) < slow_period + 1:
        raise ValueError(f"Need at least {slow_period + 1} candles for this backtest")

    closes = [bar.close for bar in bars]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    slip = slippage_bps / 10_000.0

    trades: list[Trade] = []
    equity_curve: list[dict[str, float | str]] = []
    realized_pnl = 0.0
    position: dict[str, float | str | int] | None = None
    pending: str | None = None
    peak_equity = initial_cash
    max_drawdown = 0.0

    for i, bar in enumerate(bars):
        # Execute only a signal produced by the previous candle's close.
        if pending == "BUY" and position is None:
            entry_price = bar.open * (1.0 + slip)
            position = {
                "entry_time": bar.timestamp.isoformat(),
                "entry_price": entry_price,
                "quantity": quantity,
            }
            pending = None
        elif pending == "SELL" and position is not None:
            exit_price = bar.open * (1.0 - slip)
            entry_price = float(position["entry_price"])
            qty = int(position["quantity"])
            gross = (exit_price - entry_price) * qty
            fees = 2.0 * fee_per_side
            net = gross - fees
            trades.append(Trade(
                entry_time=str(position["entry_time"]),
                exit_time=bar.timestamp.isoformat(),
                entry_price=entry_price,
                exit_price=exit_price,
                quantity=qty,
                gross_pnl=gross,
                fees=fees,
                net_pnl=net,
            ))
            realized_pnl += net
            position = None
            pending = None

        # Mark equity at the current candle close, including estimated costs.
        unrealized = 0.0
        if position is not None:
            unrealized = (bar.close - float(position["entry_price"])) * int(position["quantity"]) - fee_per_side
        equity = initial_cash + realized_pnl + unrealized
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({
            "timestamp": bar.timestamp.isoformat(),
            "equity": equity,
            "drawdown_pct": drawdown * 100.0,
        })

        # Signals use completed closes only; execution occurs at next candle open.
        if i > 0 and fast[i] is not None and slow[i] is not None and fast[i - 1] is not None and slow[i - 1] is not None:
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                pending = "BUY"
            elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                pending = "SELL"

    # Liquidate any remaining position at the final candle close (end-of-test assumption).
    if position is not None:
        last = bars[-1]
        exit_price = last.close * (1.0 - slip)
        entry_price = float(position["entry_price"])
        qty = int(position["quantity"])
        gross = (exit_price - entry_price) * qty
        fees = 2.0 * fee_per_side
        net = gross - fees
        trades.append(Trade(
            entry_time=str(position["entry_time"]),
            exit_time=last.timestamp.isoformat(),
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=qty,
            gross_pnl=gross,
            fees=fees,
            net_pnl=net,
        ))
        realized_pnl += net
        position = None
        final_marked_equity = initial_cash + realized_pnl
        equity_curve[-1]["equity"] = final_marked_equity
        final_peak = max(peak_equity, final_marked_equity)
        final_drawdown = (final_peak - final_marked_equity) / final_peak if final_peak else 0.0
        equity_curve[-1]["drawdown_pct"] = final_drawdown * 100.0
        max_drawdown = max(max_drawdown, final_drawdown)

    wins = sum(1 for trade in trades if trade.net_pnl > 0)
    gross_profit = sum(trade.net_pnl for trade in trades if trade.net_pnl > 0)
    gross_loss = -sum(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    final_equity = initial_cash + realized_pnl
    metrics: dict[str, float | int | None] = {
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "net_pnl": realized_pnl,
        "return_pct": (realized_pnl / initial_cash) * 100.0,
        "trades": len(trades),
        "win_rate_pct": (wins / len(trades) * 100.0) if trades else 0.0,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else None,
        "max_drawdown_pct": max_drawdown * 100.0,
        "fee_per_side": fee_per_side,
        "slippage_bps_per_side": slippage_bps,
    }
    return trades, equity_curve, metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Zudo EMA crossover backtester (no API required).")
    parser.add_argument("--csv", required=True, help="CSV with timestamp,open,high,low,close,volume columns")
    parser.add_argument("--fast", type=int, default=9, help="Fast EMA period (default: 9)")
    parser.add_argument("--slow", type=int, default=21, help="Slow EMA period (default: 21)")
    parser.add_argument("--quantity", type=int, default=1, help="Fixed quantity per trade (default: 1)")
    parser.add_argument("--cash", type=float, default=100000.0, help="Starting cash in INR (default: 100000)")
    parser.add_argument("--fee-per-side", type=float, default=20.0, help="Estimated fee per order side in INR (default: 20)")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Estimated slippage per side in basis points (default: 5)")
    parser.add_argument("--out", default="backtest_output", help="Output directory (default: backtest_output)")
    args = parser.parse_args()

    bars = load_bars(Path(args.csv))
    trades, equity, metrics = run_backtest(
        bars, args.fast, args.slow, args.quantity, args.cash, args.fee_per_side, args.slippage_bps
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(trades[0]).keys()) if trades else [
            "entry_time", "exit_time", "entry_price", "exit_price", "quantity", "gross_pnl", "fees", "net_pnl"
        ])
        writer.writeheader()
        writer.writerows(asdict(trade) for trade in trades)
    with (out_dir / "equity_curve.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["timestamp", "equity", "drawdown_pct"])
        writer.writeheader()
        writer.writerows(equity)
    (out_dir / "summary.json").write_text(json.dumps(metrics, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, allow_nan=False))
    print(f"\nWrote results to {out_dir.resolve()}")


if __name__ == "__main__":
    main()
