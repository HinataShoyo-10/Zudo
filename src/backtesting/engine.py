from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Sequence
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
    slippage_cost: float
    fees: float
    net_pnl: float
    exit_reason: str = ""
    planned_risk: float = 0.0
    setup_score: float = 0.0


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """EMA with an SMA seed; values before the warm-up period are None."""
    if period < 1:
        raise ValueError("EMA periods must be positive")
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return result
    result[period - 1] = sum(values[:period]) / period
    alpha = 2.0 / (period + 1)
    for i in range(period, len(values)):
        previous = result[i - 1]
        if previous is None:
            raise RuntimeError("EMA warm-up broken")
        result[i] = values[i] * alpha + previous * (1.0 - alpha)
    return result


def vwap(prices: Sequence[float], volumes: Sequence[int]) -> float | None:
    if len(prices) != len(volumes):
        raise ValueError("prices and volumes must have equal lengths")
    total_volume = sum(volumes)
    if total_volume == 0:
        return None
    weighted = sum((price * volume for price, volume in zip(prices, volumes)), 0.0)
    return weighted / total_volume


def parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM format") from exc


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
                else:
                    timestamp = timestamp.astimezone(IST)
                values = {key: float(normalized[key]) for key in ("open", "high", "low", "close", "volume")}
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError(f"Invalid CSV data on line {line_no}: {exc}") from exc
            bar = Bar(timestamp=timestamp, **values)
            if not all(math.isfinite(value) for value in values.values()):
                raise ValueError(f"Prices and volume must be finite (line {line_no})")
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


def _true_range(bar: Bar, previous_close: float | None) -> float:
    if previous_close is None:
        return bar.high - bar.low
    return max(bar.high - bar.low, abs(bar.high - previous_close), abs(bar.low - previous_close))


def atr(bars: Sequence[Bar], period: int = 14) -> list[float | None]:
    if period < 1:
        raise ValueError("ATR period must be positive")
    result: list[float | None] = [None] * len(bars)
    if len(bars) < period + 1:
        return result
    true_ranges: list[float] = []
    for i, bar in enumerate(bars):
        prev_close = bars[i - 1].close if i > 0 else None
        true_ranges.append(_true_range(bar, prev_close))
    first_atr = sum(true_ranges[1:period + 1]) / period
    result[period] = first_atr
    for i in range(period + 1, len(bars)):
        previous_atr = result[i - 1]
        if previous_atr is None:
            raise RuntimeError("ATR history is incomplete")
        result[i] = ((previous_atr * (period - 1)) + true_ranges[i]) / period
    return result


def _session_vwap(bars: Sequence[Bar]) -> list[float | None]:
    result: list[float | None] = []
    weighted_total = 0.0
    volume_total = 0.0
    for bar in bars:
        typical_price = (bar.high + bar.low + bar.close) / 3.0
        weighted_total += typical_price * bar.volume
        volume_total += bar.volume
        result.append(weighted_total / volume_total if volume_total > 0 else None)
    return result


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _ema_backtest(
    bars: list[Bar],
    fast_period: int,
    slow_period: int,
    quantity: int,
    initial_cash: float,
    fee_per_side: float,
    slippage_bps: float,
    intraday_only: bool,
) -> tuple[list[Trade], list[dict[str, float | str]], dict[str, float | int | None]]:
    """Run the historic long-only EMA crossover used by the legacy prototype."""
    if fast_period < 1 or slow_period < 2 or fast_period >= slow_period:
        raise ValueError("Require 1 <= fast_period < slow_period")
    if quantity < 1 or initial_cash <= 0 or fee_per_side < 0 or slippage_bps < 0:
        raise ValueError("Quantity/cash must be positive and fees/slippage non-negative")
    if len(bars) < slow_period + 1:
        raise ValueError(f"Need at least {slow_period + 1} candles for this backtest")
    if any(bars[i].timestamp.date() > bars[i + 1].timestamp.date() for i in range(len(bars) - 1)):
        raise ValueError("Candles must be ordered by timestamp")

    closes = [bar.close for bar in bars]
    fast, slow = ema(closes, fast_period), ema(closes, slow_period)
    slip = slippage_bps / 10_000.0
    trades: list[Trade] = []
    equity_curve: list[dict[str, float | str]] = []
    realized_pnl = 0.0
    position: dict[str, float | str | int] | None = None
    pending: str | None = None
    peak_equity, max_drawdown = initial_cash, 0.0

    def close_position(bar: Bar, raw_exit: float) -> None:
        nonlocal position, realized_pnl
        if position is None:
            return
        raw_entry = float(position["raw_entry_price"])
        entry_fill = float(position["entry_price"])
        qty = int(position["quantity"])
        exit_fill = raw_exit * (1.0 - slip)
        gross = (raw_exit - raw_entry) * qty
        slippage_cost = ((entry_fill - raw_entry) + (raw_exit - exit_fill)) * qty
        fees = 2.0 * fee_per_side
        net = gross - slippage_cost - fees
        trades.append(
            Trade(
                entry_time=str(position["entry_time"]),
                exit_time=bar.timestamp.isoformat(),
                entry_price=entry_fill,
                exit_price=exit_fill,
                quantity=qty,
                gross_pnl=gross,
                slippage_cost=slippage_cost,
                fees=fees,
                net_pnl=net,
            )
        )
        realized_pnl += net
        position = None

    for i, bar in enumerate(bars):
        same_session_as_previous = i > 0 and bars[i - 1].timestamp.date() == bar.timestamp.date()
        if not same_session_as_previous:
            pending = None

        if pending == "BUY" and position is None:
            fill = bar.open * (1.0 + slip)
            position = {
                "entry_time": bar.timestamp.isoformat(),
                "entry_price": fill,
                "raw_entry_price": bar.open,
                "quantity": quantity,
            }
        elif pending == "SELL" and position is not None:
            close_position(bar, bar.open)
        pending = None

        session_end = i == len(bars) - 1 or bars[i + 1].timestamp.date() != bar.timestamp.date()
        if intraday_only and session_end and position is not None:
            close_position(bar, bar.close)

        unrealized = 0.0
        if position is not None:
            raw_entry = float(position["raw_entry_price"])
            qty = int(position["quantity"])
            unrealized = (bar.close - raw_entry) * qty
            unrealized -= (float(position["entry_price"]) - raw_entry) * qty
            unrealized -= fee_per_side

        equity = initial_cash + realized_pnl + unrealized
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity if peak_equity else 0.0
        max_drawdown = max(max_drawdown, drawdown)
        equity_curve.append({
            "timestamp": bar.timestamp.isoformat(),
            "equity": equity,
            "drawdown_pct": drawdown * 100.0,
        })

        if intraday_only and session_end:
            pending = None
            continue
        if i > 0 and fast[i] is not None and slow[i] is not None and fast[i - 1] is not None and slow[i - 1] is not None:
            if fast[i - 1] <= slow[i - 1] and fast[i] > slow[i]:
                pending = "BUY"
            elif fast[i - 1] >= slow[i - 1] and fast[i] < slow[i]:
                pending = "SELL"

    net_wins = sum(t.net_pnl > 0 for t in trades)
    gross_wins = sum(t.gross_pnl > 0 for t in trades)
    net_profit = sum(t.net_pnl for t in trades if t.net_pnl > 0)
    net_loss = -sum(t.net_pnl for t in trades if t.net_pnl < 0)
    gross_total = sum(t.gross_pnl for t in trades)
    fees_total = sum(t.fees for t in trades)
    slippage_total = sum(t.slippage_cost for t in trades)
    final_equity = initial_cash + realized_pnl
    metrics: dict[str, float | int | None] = {
        "strategy": "ema_crossover",
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "net_pnl": realized_pnl,
        "return_pct": realized_pnl / initial_cash * 100.0,
        "trades": len(trades),
        "gross_pnl_before_costs": gross_total,
        "fees_total": fees_total,
        "slippage_cost_total": slippage_total,
        "gross_win_rate_pct": gross_wins / len(trades) * 100.0 if trades else 0.0,
        "net_win_rate_pct": net_wins / len(trades) * 100.0 if trades else 0.0,
        "profit_factor_net": net_profit / net_loss if net_loss > 0 else None,
        "max_drawdown_pct": max_drawdown * 100.0,
        "fee_per_side": fee_per_side,
        "slippage_bps_per_side": slippage_bps,
        "intraday_only": intraday_only,
    }
    return trades, equity_curve, metrics


def _run_breakout_backtest(
    bars: list[Bar],
    *,
    initial_cash: float = 100_000.0,
    opening_range_minutes: int = 30,
    atr_period: int = 14,
    risk_fraction: float = 0.01,
    stop_atr_multiple: float = 1.5,
    target_r: float = 2.0,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
    max_exposure_fraction: float = 0.2,
    max_entry_time: time = time(14, 30),
    min_volume_multiple: float = 1.2,
    min_breakout_atr: float = 0.1,
    min_body_fraction: float = 0.6,
    max_opening_range_atr: float = 5.0,
) -> tuple[list[Trade], list[dict[str, float | str]], dict[str, float | int | None]]:
    """Backtest long-only 5-minute opening-range breakouts with capped risk.

    A signal is confirmed on a completed candle and entered at the following
    candle's open. Intra-candle stop/target collisions resolve to the stop;
    stop gaps fill at the worse opening price. Incomplete sessions are skipped.
    """
    if not bars:
        raise ValueError("bars must not be empty")
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if not math.isfinite(risk_fraction) or not (0 < risk_fraction <= 0.02):
        raise ValueError("risk_fraction must be between 0 and 2%")
    if not math.isfinite(stop_atr_multiple) or stop_atr_multiple <= 0:
        raise ValueError("stop_atr_multiple must be finite and positive")
    if not math.isfinite(target_r) or target_r <= 0:
        raise ValueError("target_r must be finite and positive")
    if opening_range_minutes <= 0 or opening_range_minutes % 5 or opening_range_minutes >= 375:
        raise ValueError("opening_range_minutes must be a positive 5-minute multiple below 375")
    if atr_period < 1:
        raise ValueError("atr_period must be positive")
    if not math.isfinite(fee_per_side) or fee_per_side < 0:
        raise ValueError("fee_per_side must be finite and non-negative")
    if not math.isfinite(slippage_bps) or slippage_bps < 0:
        raise ValueError("slippage_bps must be finite and non-negative")
    if slippage_bps >= 10_000:
        raise ValueError("slippage_bps must be less than 10,000")
    if not math.isfinite(max_exposure_fraction) or not (0 < max_exposure_fraction <= 1):
        raise ValueError("max_exposure_fraction must be in (0, 1]")
    if not math.isfinite(min_volume_multiple) or min_volume_multiple < 0:
        raise ValueError("min_volume_multiple must be finite and non-negative")
    if not math.isfinite(min_breakout_atr) or min_breakout_atr < 0:
        raise ValueError("min_breakout_atr must be finite and non-negative")
    if not math.isfinite(min_body_fraction) or not (0 <= min_body_fraction <= 1):
        raise ValueError("min_body_fraction must be in [0, 1]")
    if not math.isfinite(max_opening_range_atr) or max_opening_range_atr <= 0:
        raise ValueError("max_opening_range_atr must be finite and positive")

    by_day: dict[datetime.date, list[Bar]] = {}
    previous_timestamp: datetime | None = None
    for bar in bars:
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("bar timestamps must be timezone-aware")
        timestamp = bar.timestamp.astimezone(IST)
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bar prices and volume must be finite")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            raise ValueError("bar prices must be positive and volume non-negative")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            raise ValueError(f"invalid OHLC relationship at {timestamp.isoformat()}")
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("bars must be strictly increasing after IST normalization")
        previous_timestamp = timestamp
        local_bar = Bar(timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume)
        by_day.setdefault(timestamp.date(), []).append(local_bar)

    trades: list[Trade] = []
    equity_curve: list[dict[str, float | str]] = []
    realized_pnl = 0.0
    peak_equity = initial_cash
    max_drawdown = 0.0
    skipped_incomplete_sessions = 0
    valid_sessions = 0
    signals = 0
    skipped_risk = 0
    raw_breakouts = 0
    no_trade_reasons: dict[str, int] = {}
    slip = slippage_bps / 10_000.0
    expected_bars = 75  # 09:15 through 15:25, inclusive, at five-minute intervals.
    open_time = time(9, 15)
    final_bar_time = time(15, 25)

    for day in sorted(by_day):
        expected_times = [
            datetime.combine(day, open_time, tzinfo=IST) + timedelta(minutes=5 * i)
            for i in range(expected_bars)
        ]
        day_bars = by_day[day]
        session_bars = [bar for bar in day_bars if open_time <= bar.timestamp.time() <= final_bar_time]
        if (
            len(session_bars) != expected_bars
            or [bar.timestamp for bar in session_bars] != expected_times
        ):
            skipped_incomplete_sessions += 1
            continue
        if day.weekday() >= 5:
            skipped_incomplete_sessions += 1
            continue

        range_count = opening_range_minutes // 5
        opening_bars = session_bars[:range_count]
        range_high = max(bar.high for bar in opening_bars)
        atr_values = atr(session_bars, atr_period)
        vwap_values = _session_vwap(session_bars)
        closes = [bar.close for bar in session_bars]
        fast_trend = ema(closes, 5)
        slow_trend = ema(closes, 13)
        valid_sessions += 1
        position: dict[str, float | int | str] | None = None
        traded_today = False

        for index, bar in enumerate(session_bars):
            if position is None and not traded_today and index > range_count:
                signal_bar = session_bars[index - 1]
                signal_atr = atr_values[index - 1]
                if (
                    signal_atr is not None
                    and signal_atr > 0
                    and signal_bar.close > range_high
                    and session_bars[index - 2].close <= range_high
                    and signal_bar.timestamp.time() <= max_entry_time
                    and bar.timestamp.time() <= max_entry_time
                ):
                    raw_breakouts += 1
                    vetoes: list[str] = []
                    prior_volumes = [item.volume for item in session_bars[max(0, index - 7):index - 1]]
                    signal_range = signal_bar.high - signal_bar.low
                    body_fraction = (
                        (signal_bar.close - signal_bar.open) / signal_range
                        if signal_range > 0 else 0.0
                    )
                    fast_value = fast_trend[index - 1]
                    slow_value = slow_trend[index - 1]
                    previous_slow = slow_trend[index - 2]
                    session_vwap = vwap_values[index - 1]

                    opening_range_width = (
                        max(item.high for item in opening_bars)
                        - min(item.low for item in opening_bars)
                    )
                    if opening_range_width / signal_atr > max_opening_range_atr:
                        vetoes.append("opening_range_too_wide")
                    if signal_bar.close < range_high + min_breakout_atr * signal_atr:
                        vetoes.append("breakout_too_weak")
                    if fast_value is None or slow_value is None or previous_slow is None:
                        vetoes.append("trend_warmup")
                    elif not (fast_value > slow_value and slow_value > previous_slow):
                        vetoes.append("trend_not_aligned")
                    if session_vwap is None or signal_bar.close <= session_vwap:
                        vetoes.append("below_session_vwap")
                    if signal_range <= 0 or body_fraction < min_body_fraction:
                        vetoes.append("weak_signal_candle")
                    if (
                        len(prior_volumes) < 6
                        or _median(prior_volumes) <= 0
                        or signal_bar.volume < _median(prior_volumes) * min_volume_multiple
                    ):
                        vetoes.append("no_volume_expansion")

                    signals += 1
                    if vetoes:
                        reason = vetoes[0]
                        no_trade_reasons[reason] = no_trade_reasons.get(reason, 0) + 1
                    else:
                        entry_fill = bar.open * (1.0 + slip)
                        stop_distance = signal_atr * stop_atr_multiple
                        stop_price = entry_fill - stop_distance
                        if stop_price <= 0:
                            skipped_risk += 1
                        else:
                            stop_fill = stop_price * (1.0 - slip)
                            risk_per_share = entry_fill - stop_fill
                            risk_budget = max(0.0, initial_cash + realized_pnl) * risk_fraction
                            exposure_budget = max(0.0, initial_cash + realized_pnl) * max_exposure_fraction
                            risk_after_fees = risk_budget - 2.0 * fee_per_side
                            quantity_by_risk = int(risk_after_fees / risk_per_share) if risk_after_fees > 0 else 0
                            quantity_by_exposure = int(exposure_budget // entry_fill)
                            quantity_by_cash = int(max(0.0, initial_cash + realized_pnl - fee_per_side) // entry_fill)
                            quantity = min(quantity_by_risk, quantity_by_exposure, quantity_by_cash)
                            if quantity < 1:
                                skipped_risk += 1
                            else:
                                position = {
                                    "entry_time": bar.timestamp.isoformat(),
                                    "entry_price": entry_fill,
                                    "raw_entry_price": bar.open,
                                    "quantity": quantity,
                                    "stop_price": stop_price,
                                    "target_price": entry_fill + stop_distance * target_r,
                                    "planned_risk": risk_per_share * quantity + 2.0 * fee_per_side,
                                    "setup_score": (
                                        (signal_bar.close - range_high) / signal_atr
                                        + signal_bar.volume / max(_median(prior_volumes), 1e-12)
                                        + (float(fast_value) - float(slow_value)) / signal_atr
                                    ),
                                }
                                traded_today = True

            if position is not None:
                entry_fill = float(position["entry_price"])
                quantity = int(position["quantity"])
                stop_price = float(position["stop_price"])
                target_price = float(position["target_price"])
                exit_reason = ""
                raw_exit = 0.0
                if bar.low <= stop_price:
                    exit_reason = "stop_loss"
                    raw_exit = min(bar.open, stop_price)
                elif bar.high >= target_price:
                    exit_reason = "take_profit"
                    raw_exit = max(bar.open, target_price)
                elif index == len(session_bars) - 1:
                    exit_reason = "intraday_square_off"
                    raw_exit = bar.close

                if exit_reason:
                    exit_fill = raw_exit * (1.0 - slip)
                    gross = (raw_exit - float(position["raw_entry_price"])) * quantity
                    slippage_cost = (
                        (entry_fill - float(position["raw_entry_price"]))
                        + (raw_exit - exit_fill)
                    ) * quantity
                    fees = 2.0 * fee_per_side
                    net = gross - slippage_cost - fees
                    trades.append(Trade(
                        entry_time=str(position["entry_time"]),
                        exit_time=bar.timestamp.isoformat(),
                        entry_price=entry_fill,
                        exit_price=exit_fill,
                        quantity=quantity,
                        gross_pnl=gross,
                        slippage_cost=slippage_cost,
                        fees=fees,
                        net_pnl=net,
                        exit_reason=exit_reason,
                        planned_risk=float(position["planned_risk"]),
                        setup_score=float(position["setup_score"]),
                    ))
                    realized_pnl += net
                    position = None

            equity = initial_cash + realized_pnl
            if position is not None:
                qty = int(position["quantity"])
                mark = (bar.close - float(position["entry_price"])) * qty
                mark -= qty * bar.close * slip + 2.0 * fee_per_side
                equity += mark
            peak_equity = max(peak_equity, equity)
            drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
            max_drawdown = max(max_drawdown, drawdown)
            equity_curve.append({
                "timestamp": bar.timestamp.isoformat(),
                "equity": equity,
                "drawdown_pct": drawdown * 100.0,
            })

    final_equity = initial_cash + realized_pnl
    net_wins = [trade for trade in trades if trade.net_pnl > 0]
    net_losses = -sum(trade.net_pnl for trade in trades if trade.net_pnl < 0)
    net_profit = sum(trade.net_pnl for trade in net_wins)
    metrics: dict[str, float | int | None] = {
        "strategy": "opening_range_breakout",
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "net_pnl": realized_pnl,
        "return_pct": realized_pnl / initial_cash * 100.0,
        "trades": len(trades),
        "signals": signals,
        "raw_breakouts": raw_breakouts,
        "no_trade_reasons": dict(sorted(no_trade_reasons.items())),
        "skipped_risk": skipped_risk,
        "valid_sessions": valid_sessions,
        "skipped_incomplete_sessions": skipped_incomplete_sessions,
        "gross_pnl_before_costs": sum(trade.gross_pnl for trade in trades),
        "fees_total": sum(trade.fees for trade in trades),
        "slippage_cost_total": sum(trade.slippage_cost for trade in trades),
        "net_win_rate_pct": len(net_wins) / len(trades) * 100.0 if trades else 0.0,
        "profit_factor_net": net_profit / net_losses if net_losses > 0 else None,
        "max_drawdown_pct": max_drawdown * 100.0,
        "opening_range_minutes": opening_range_minutes,
        "atr_period": atr_period,
        "risk_fraction": risk_fraction,
        "max_exposure_fraction": max_exposure_fraction,
        "min_volume_multiple": min_volume_multiple,
        "min_breakout_atr": min_breakout_atr,
        "min_body_fraction": min_body_fraction,
        "max_opening_range_atr": max_opening_range_atr,
        "stop_atr_multiple": stop_atr_multiple,
        "target_r": target_r,
        "fee_per_side": fee_per_side,
        "slippage_bps_per_side": slippage_bps,
        "intraday_only": True,
        "research_only": True,
    }
    return trades, equity_curve, metrics


def run_backtest(
    bars: list[Bar],
    fast_period: int = 9,
    slow_period: int = 21,
    quantity: int = 1,
    initial_cash: float = 100_000.0,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
    intraday_only: bool = True,
    *,
    strategy: str = "ema",
    opening_range_minutes: int = 30,
    atr_period: int = 14,
    risk_fraction: float = 0.01,
    stop_atr_multiple: float = 1.5,
    target_r: float = 2.0,
    max_exposure_fraction: float = 0.2,
    max_entry_time: time = time(14, 30),
    min_volume_multiple: float = 1.2,
    min_breakout_atr: float = 0.1,
    min_body_fraction: float = 0.6,
    max_opening_range_atr: float = 5.0,
) -> tuple[list[Trade], list[dict[str, float | str]], dict[str, float | int | None]]:
    """Run the selected strategy; the legacy EMA crossover remains the default for compatibility."""
    strategy = strategy.lower()
    if strategy == "ema":
        return _ema_backtest(
            bars,
            fast_period=fast_period,
            slow_period=slow_period,
            quantity=quantity,
            initial_cash=initial_cash,
            fee_per_side=fee_per_side,
            slippage_bps=slippage_bps,
            intraday_only=intraday_only,
        )
    if strategy == "orb":
        return _run_breakout_backtest(
            bars,
            initial_cash=initial_cash,
            opening_range_minutes=opening_range_minutes,
            atr_period=atr_period,
            risk_fraction=risk_fraction,
            stop_atr_multiple=stop_atr_multiple,
            target_r=target_r,
            fee_per_side=fee_per_side,
            slippage_bps=slippage_bps,
            max_exposure_fraction=max_exposure_fraction,
            max_entry_time=max_entry_time,
            min_volume_multiple=min_volume_multiple,
            min_breakout_atr=min_breakout_atr,
            min_body_fraction=min_body_fraction,
            max_opening_range_atr=max_opening_range_atr,
        )
    raise ValueError(f"Unsupported backtest strategy: {strategy!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Zudo backtester for FYERS-like OHLCV CSVs.")
    parser.add_argument("--csv", required=True, help="CSV with timestamp,open,high,low,close,volume columns")
    parser.add_argument("--strategy", choices=["ema", "orb"], default="orb", help="Strategy to evaluate")
    parser.add_argument("--fast", type=int, default=9, help="Fast EMA period (default: 9)")
    parser.add_argument("--slow", type=int, default=21, help="Slow EMA period (default: 21)")
    parser.add_argument("--quantity", type=int, default=1, help="Fixed quantity per EMA trade (default: 1)")
    parser.add_argument("--cash", type=float, default=100000.0, help="Starting cash in INR (default: 100000)")
    parser.add_argument("--fee-per-side", type=float, default=0.0, help="Estimated broker fee per order side in INR (default: 0)")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="Estimated slippage per side in basis points (default: 5)")
    parser.add_argument("--allow-overnight", action="store_true", help="Disable intraday session liquidation (research only)")
    parser.add_argument("--opening-range-minutes", type=int, default=30, help="Opening range in minutes for breakout mode")
    parser.add_argument("--atr-period", type=int, default=14, help="ATR lookback for breakout mode")
    parser.add_argument("--risk-fraction", type=float, default=0.01, help="Per-trade risk fraction in breakout mode")
    parser.add_argument("--stop-atr-multiple", type=float, default=1.5, help="Risk stop size in ATR units")
    parser.add_argument("--target-r", type=float, default=2.0, help="Target multiple of risk for breakout mode")
    parser.add_argument("--max-exposure-fraction", type=float, default=0.2, help="Maximum position value as a fraction of equity")
    parser.add_argument("--last-entry-time", type=parse_time, default=time(14, 30), help="Latest entry time in IST (HH:MM)")
    parser.add_argument("--min-volume-multiple", type=float, default=1.2, help="Minimum breakout volume vs preceding six bars")
    parser.add_argument("--min-breakout-atr", type=float, default=0.1, help="Minimum breakout close above range in ATR units")
    parser.add_argument("--min-body-fraction", type=float, default=0.6, help="Minimum bullish candle body divided by full range")
    parser.add_argument("--max-opening-range-atr", type=float, default=5.0, help="Skip sessions with a wider opening range in ATR units")
    parser.add_argument("--out", default="backtest_output", help="Output directory (default: backtest_output)")
    args = parser.parse_args()

    bars = load_bars(Path(args.csv))
    trades, equity, metrics = run_backtest(
        bars,
        args.fast,
        args.slow,
        args.quantity,
        args.cash,
        args.fee_per_side,
        args.slippage_bps,
        intraday_only=not args.allow_overnight,
        strategy=args.strategy,
        opening_range_minutes=args.opening_range_minutes,
        atr_period=args.atr_period,
        risk_fraction=args.risk_fraction,
        stop_atr_multiple=args.stop_atr_multiple,
        target_r=args.target_r,
        max_exposure_fraction=args.max_exposure_fraction,
        max_entry_time=args.last_entry_time,
        min_volume_multiple=args.min_volume_multiple,
        min_breakout_atr=args.min_breakout_atr,
        min_body_fraction=args.min_body_fraction,
        max_opening_range_atr=args.max_opening_range_atr,
    )
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    trade_fields = list(asdict(trades[0]).keys()) if trades else [
        "entry_time", "exit_time", "entry_price", "exit_price", "quantity",
        "gross_pnl", "slippage_cost", "fees", "net_pnl",
    ]
    with (out_dir / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trade_fields)
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
