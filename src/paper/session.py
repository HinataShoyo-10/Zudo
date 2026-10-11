"""Persistent paper execution for latest-bar scanner signals."""
from __future__ import annotations

import math
from datetime import datetime, time, timedelta
from typing import Any, Sequence

from src.backtesting.engine import IST, Bar

STATE_VERSION = 1
FINAL_BAR_TIME = time(15, 25)


def new_paper_state(initial_cash: float) -> dict[str, Any]:
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    return {
        "version": STATE_VERSION,
        "initial_cash": initial_cash,
        "cash": initial_cash,
        "trades": [],
        "decisions": [],
        "session_date": "",
        "last_processed_by_symbol": {},
        "pending": None,
        "position": None,
        "trade_taken_today": False,
    }


def _price(position: dict[str, Any], bar: Bar) -> tuple[float, str] | None:
    stop_price = float(position["stop_price"])
    target_price = float(position["target_price"])
    if bar.low <= stop_price:
        return min(bar.open, stop_price), "stop_loss"
    if bar.high >= target_price:
        return max(bar.open, target_price), "take_profit"
    if bar.timestamp.time() == FINAL_BAR_TIME:
        return bar.close, "intraday_square_off"
    return None


def _close_position(
    state: dict[str, Any], bar: Bar, raw_exit: float, reason: str, slippage: float, fee: float
) -> None:
    position = state["position"]
    if position is None:
        raise RuntimeError("cannot close a missing paper position")
    quantity = int(position["quantity"])
    raw_entry = float(position["raw_entry_price"])
    entry_fill = float(position["entry_price"])
    exit_fill = raw_exit * (1.0 - slippage)
    gross = (raw_exit - raw_entry) * quantity
    slippage_cost = ((entry_fill - raw_entry) + (raw_exit - exit_fill)) * quantity
    fees = 2.0 * fee
    net = gross - slippage_cost - fees
    trade = {
        **position,
        "exit_time": bar.timestamp.isoformat(),
        "raw_exit_price": raw_exit,
        "exit_price": exit_fill,
        "exit_reason": reason,
        "gross_pnl": gross,
        "slippage_cost": slippage_cost,
        "fees": fees,
        "net_pnl": net,
        "cash_after": float(state["cash"]) + net,
        "status": "CLOSED",
    }
    state["cash"] = float(state["cash"]) + net
    state["trades"].append(trade)
    state["position"] = None


def advance_paper_session(
    state: dict[str, Any],
    assets: dict[str, Sequence[Bar]],
    scan_results: Sequence[dict[str, Any]],
    *,
    as_of: datetime,
    risk_fraction: float = 0.005,
    max_exposure_fraction: float = 0.20,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
) -> dict[str, Any]:
    """Advance paper orders from completed bars; repeated inputs are idempotent.

    At most the highest-scored eligible setup is selected and one trade is
    allowed per session. Entries use the next completed five-minute candle's
    open; stop/target collisions conservatively resolve to the stop.
    """
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if not math.isfinite(risk_fraction) or not 0 < risk_fraction <= 0.02:
        raise ValueError("risk_fraction must be in (0, 2%]")
    if not math.isfinite(max_exposure_fraction) or not 0 < max_exposure_fraction <= 1:
        raise ValueError("max_exposure_fraction must be in (0, 1]")
    if not math.isfinite(fee_per_side) or fee_per_side < 0:
        raise ValueError("fee_per_side must be finite and non-negative")
    if not math.isfinite(slippage_bps) or not 0 <= slippage_bps < 10_000:
        raise ValueError("slippage_bps must be in [0, 10,000)")
    if state.get("version") != STATE_VERSION:
        raise ValueError("unsupported paper state version; reset the paper account")

    scan_time = as_of.astimezone(IST)
    current_date = scan_time.date().isoformat()
    slippage = slippage_bps / 10_000.0
    if not state.get("session_date"):
        state["session_date"] = current_date
    elif state["session_date"] != current_date:
        if state["position"] is not None:
            position = state["position"]
            last_time = datetime.fromisoformat(position["last_mark_time"])
            last_price = float(position["last_mark_price"])
            last_bar = Bar(last_time, last_price, last_price, last_price, last_price, 0.0)
            _close_position(
                state,
                last_bar,
                last_price,
                "session_rollover_square_off_last_mark",
                slippage,
                fee_per_side,
            )
        if state["pending"] is not None:
            state["decisions"].append({
                "date": state["session_date"],
                "symbol": state["pending"]["symbol"],
                "status": "CANCELLED",
                "reason": "session_ended_before_entry",
                "signal_time": state["pending"]["signal_time"],
            })
        state["pending"] = None
        state["session_date"] = current_date
        state["trade_taken_today"] = False

    new_bars: list[tuple[datetime, str, Bar]] = []
    for symbol, bars in assets.items():
        last_timestamp = state["last_processed_by_symbol"].get(symbol, "")
        for bar in bars:
            if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
                raise ValueError(f"{symbol}: paper bars must have timezone-aware timestamps")
            timestamp = bar.timestamp.astimezone(IST)
            if timestamp + timedelta(minutes=5) > scan_time:
                continue
            if timestamp.isoformat() > last_timestamp:
                new_bars.append((
                    timestamp,
                    symbol,
                    Bar(timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume),
                ))
    new_bars.sort(key=lambda item: (item[0], item[1]))

    for timestamp, symbol, bar in new_bars:
        state["last_processed_by_symbol"][symbol] = timestamp.isoformat()
        pending = state["pending"]
        if (
            pending is not None
            and pending["symbol"] == symbol
            and timestamp.isoformat() > pending["entry_time"]
        ):
            state["decisions"].append({
                "date": current_date,
                "symbol": symbol,
                "status": "CANCELLED",
                "reason": "entry_bar_missing",
                "signal_time": pending["signal_time"],
            })
            state["pending"] = None
            pending = None
        if (
            pending is not None
            and pending["symbol"] == symbol
            and pending["entry_time"] == timestamp.isoformat()
        ):
            entry_raw = bar.open
            entry_fill = entry_raw * (1.0 + slippage)
            stop_distance = float(pending["atr"]) * 1.5
            stop_price = entry_fill - stop_distance
            risk_per_share = entry_fill - stop_price * (1.0 - slippage)
            equity = float(state["cash"])
            quantity = min(
                int(max(0.0, equity * risk_fraction - 2 * fee_per_side) / risk_per_share)
                if risk_per_share > 0 else 0,
                int(equity * max_exposure_fraction // entry_fill),
                int(max(0.0, equity - fee_per_side) // entry_fill),
            )
            state["pending"] = None
            state["trade_taken_today"] = True
            if quantity < 1:
                state["decisions"].append({
                    "date": current_date,
                    "symbol": symbol,
                    "status": "SKIPPED",
                    "reason": "position_exceeds_cash_or_risk_budget",
                    "signal_time": pending["signal_time"],
                })
            else:
                position = {
                    "date": current_date,
                    "symbol": symbol,
                    "strategy": pending["strategy"],
                    "signal_time": pending["signal_time"],
                    "entry_time": timestamp.isoformat(),
                    "raw_entry_price": entry_raw,
                    "entry_price": entry_fill,
                    "stop_price": stop_price,
                    "target_price": entry_fill + stop_distance * 2.0,
                    "quantity": quantity,
                    "planned_risk": risk_per_share * quantity + 2 * fee_per_side,
                    "setup_score": pending["setup_score"],
                    "status": "OPEN",
                    "last_mark_time": timestamp.isoformat(),
                    "last_mark_price": bar.close,
                }
                state["position"] = position
                state["decisions"].append({
                    "date": current_date,
                    "symbol": symbol,
                    "status": "PAPER_FILLED",
                    "reason": "",
                    "signal_time": pending["signal_time"],
                    "entry_time": timestamp.isoformat(),
                })

        position = state["position"]
        if position is not None and position["symbol"] == symbol:
            position["last_mark_time"] = timestamp.isoformat()
            position["last_mark_price"] = bar.close
            exit_event = _price(position, bar)
            if exit_event is not None:
                raw_exit, reason = exit_event
                _close_position(state, bar, raw_exit, reason, slippage, fee_per_side)

    if (
        state["pending"] is None
        and state["position"] is None
        and not state["trade_taken_today"]
    ):
        opportunities = [
            row for row in scan_results
            if row.get("status") == "OPPORTUNITY"
            and row.get("session_date") == current_date
            and row.get("symbol") in assets
            and isinstance(row.get("atr"), (int, float))
            and float(row["atr"]) > 0
        ]
        if opportunities:
            chosen = max(
                opportunities,
                key=lambda row: (
                    float(row.get("setup_score") or 0.0),
                    -int(row.get("rank") or 999_999),
                    str(row["symbol"]),
                ),
            )
            entry_time = chosen.get("planned_entry_time")
            signal_time = chosen.get("signal_time")
            try:
                parsed_entry = datetime.fromisoformat(entry_time)
                parsed_signal = datetime.fromisoformat(signal_time)
            except (TypeError, ValueError) as exc:
                raise ValueError("paper opportunity has invalid signal/entry timestamps") from exc
            if (
                parsed_entry.tzinfo is None
                or parsed_signal.tzinfo is None
                or parsed_entry.astimezone(IST) <= parsed_signal.astimezone(IST)
            ):
                raise ValueError("paper opportunity entry must follow its signal")
            state["pending"] = {
                "symbol": chosen["symbol"],
                "strategy": chosen["strategy"],
                "signal_time": parsed_signal.astimezone(IST).isoformat(),
                "entry_time": parsed_entry.astimezone(IST).isoformat(),
                "atr": float(chosen["atr"]),
                "setup_score": float(chosen.get("setup_score") or 0.0),
            }
            state["decisions"].append({
                "date": current_date,
                "symbol": chosen["symbol"],
                "status": "PAPER_PENDING",
                "reason": "awaiting_next_completed_bar_open",
                "signal_time": state["pending"]["signal_time"],
                "entry_time": state["pending"]["entry_time"],
                "setup_score": state["pending"]["setup_score"],
            })

    return state
