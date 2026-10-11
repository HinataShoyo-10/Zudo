"""Research-only NIFTY opening-range breakout options backtester.

Signals are generated from NIFTY index OHLC candles. Long-call entries are
filled no earlier than the next candle's option open. No order endpoints are
called. This first prototype supports one fixed call contract, one trade per
session, stop/target exits, risk-based lot sizing, estimated NSE/FYERS costs,
and end-of-session square-off.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close", "volume"}
MARKET_OPEN = time(9, 15)
LAST_BAR_START = time(15, 25)


@dataclass(frozen=True)
class CostRates:
    """Historical NSE options assumptions for Jan 2025, configurable in CLI.

    Rates are fractions of option premium turnover (not percentages):
    exchange 0.03503%, STT 0.10% on sell-side premium, stamp duty 0.003% on
    the buy side, and SEBI turnover fee ₹10/crore. GST is applied to brokerage,
    exchange charges, and SEBI fee. Verify these against the applicable
    contract note/official schedule before treating net results as final.
    """

    brokerage_per_order: float = 20.0
    exchange_rate: float = 0.0003503
    sebi_rate: float = 0.000001
    stt_sell_rate: float = 0.001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18


def parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("time must use HH:MM format") from exc


def parse_timestamp(value: Any) -> datetime:
    raw = str(value).strip()
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=IST)
    return parsed.astimezone(IST)


def number(value: Any, field: str, line_num: int) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"line {line_num}: {field} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"line {line_num}: {field} must be finite")
    return result


def load_candles(path: Path) -> list[dict[str, Any]]:
    """Load, normalize, and structurally validate a 5-minute OHLCV CSV."""
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        fieldnames = {str(k).strip().lower() for k in (reader.fieldnames or [])}
        if not REQUIRED_COLUMNS.issubset(fieldnames):
            raise ValueError(
                f"{path}: CSV needs columns {sorted(REQUIRED_COLUMNS)}; found {sorted(fieldnames)}"
            )
        rows = list(reader)

    if not rows:
        raise ValueError(f"{path}: CSV contains no candles")

    candles: list[dict[str, Any]] = []
    seen: set[datetime] = set()
    previous: datetime | None = None
    for line_num, original in enumerate(rows, start=2):
        row = {str(k).strip().lower(): val for k, val in original.items() if k is not None}
        try:
            ts = parse_timestamp(row["timestamp"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ValueError(f"{path}: line {line_num}: invalid timestamp") from exc
        if ts in seen:
            raise ValueError(f"{path}: line {line_num}: duplicate timestamp {ts.isoformat()}")
        if previous is not None and ts <= previous:
            raise ValueError(f"{path}: line {line_num}: timestamps not strictly increasing")
        seen.add(ts)
        previous = ts
        if ts.weekday() >= 5 or not (MARKET_OPEN <= ts.timetz().replace(tzinfo=None) <= LAST_BAR_START):
            raise ValueError(f"{path}: line {line_num}: timestamp outside regular NSE session: {ts}")
        minutes = int((datetime.combine(ts.date(), ts.timetz().replace(tzinfo=None)) -
                       datetime.combine(ts.date(), MARKET_OPEN)).total_seconds() // 60)
        if minutes % 5:
            raise ValueError(f"{path}: line {line_num}: timestamp not aligned to 5-minute grid: {ts}")

        values = {field: number(row[field], field, line_num)
                  for field in ("open", "high", "low", "close", "volume")}
        if min(values[k] for k in ("open", "high", "low", "close")) <= 0:
            raise ValueError(f"{path}: line {line_num}: OHLC prices must be positive")
        if values["volume"] < 0:
            raise ValueError(f"{path}: line {line_num}: volume cannot be negative")
        if values["high"] < max(values["open"], values["low"], values["close"]):
            raise ValueError(f"{path}: line {line_num}: high violates OHLC invariant")
        if values["low"] > min(values["open"], values["high"], values["close"]):
            raise ValueError(f"{path}: line {line_num}: low violates OHLC invariant")

        item = {"timestamp": ts, **values}
        if "open_interest" in row and row["open_interest"] not in (None, ""):
            item["open_interest"] = number(row["open_interest"], "open_interest", line_num)
        candles.append(item)
    return candles


def order_charges(price: float, quantity: int, side: str, rates: CostRates) -> dict[str, float]:
    """Estimate charges for one executed order (all rates applied to premium)."""
    turnover = price * quantity
    brokerage = rates.brokerage_per_order
    exchange = turnover * rates.exchange_rate
    sebi = turnover * rates.sebi_rate
    stamp = turnover * rates.stamp_buy_rate if side == "BUY" else 0.0
    stt = turnover * rates.stt_sell_rate if side == "SELL" else 0.0
    gst = (brokerage + exchange + sebi) * rates.gst_rate
    return {
        "brokerage": brokerage,
        "exchange_charges": exchange,
        "sebi_fee": sebi,
        "stt": stt,
        "stamp_duty": stamp,
        "gst": gst,
        "total": brokerage + exchange + sebi + stt + stamp + gst,
    }


def _entry_fill(raw_open: float, slippage_bps: float) -> float:
    return raw_open * (1.0 + slippage_bps / 10_000.0)


def _exit_fill(raw_price: float, slippage_bps: float) -> float:
    return raw_price * (1.0 - slippage_bps / 10_000.0)


def _planned_risk(
    entry_fill: float,
    stop_price: float,
    quantity: int,
    slippage_bps: float,
    rates: CostRates,
) -> tuple[float, dict[str, float]]:
    stop_fill = _exit_fill(stop_price, slippage_bps)
    entry_costs = order_charges(entry_fill, quantity, "BUY", rates)
    stop_exit_costs = order_charges(stop_fill, quantity, "SELL", rates)
    risk = max(0.0, entry_fill - stop_fill) * quantity + entry_costs["total"] + stop_exit_costs["total"]
    return risk, {"entry_costs_est": entry_costs["total"], "stop_exit_costs_est": stop_exit_costs["total"]}


def _size_position(
    entry_fill: float,
    lot_size: int,
    available_cash: float,
    risk_fraction: float,
    hard_risk_fraction: float,
    slippage_bps: float,
    stop_loss_pct: float,
    rates: CostRates,
) -> tuple[int, float, str]:
    target_budget = available_cash * risk_fraction
    hard_budget = available_cash * hard_risk_fraction
    allowed_risk = min(target_budget, hard_budget)
    stop_price = entry_fill * (1.0 - stop_loss_pct)
    if stop_price <= 0:
        return 0, 0.0, "invalid_stop_price"

    max_affordable_lots = int(available_cash // (entry_fill * lot_size))
    chosen_lots = 0
    chosen_risk = 0.0
    chosen_entry_cost = 0.0
    for lots in range(1, min(max_affordable_lots, 100_000) + 1):
        quantity = lots * lot_size
        risk, _ = _planned_risk(entry_fill, stop_price, quantity, slippage_bps, rates)
        buy_costs = order_charges(entry_fill, quantity, "BUY", rates)["total"]
        required_cash = entry_fill * quantity + buy_costs
        if risk <= allowed_risk + 1e-9 and required_cash <= available_cash + 1e-9:
            chosen_lots, chosen_risk, chosen_entry_cost = lots, risk, buy_costs
        # Risk and premium debit rise with lots; once exceeded, later sizes won't fit.
        if risk > allowed_risk or required_cash > available_cash:
            break
    if chosen_lots == 0:
        one_lot_risk, _ = _planned_risk(entry_fill, stop_price, lot_size, slippage_bps, rates)
        if max_affordable_lots < 1 or entry_fill * lot_size > available_cash:
            return 0, one_lot_risk, "one_lot_exceeds_available_cash"
        return 0, one_lot_risk, "one_lot_exceeds_planned_risk"
    return chosen_lots * lot_size, chosen_risk, ""


def _find_exit(
    option_rows: list[dict[str, Any]],
    entry_index: int,
    entry_fill: float,
    stop_loss_pct: float,
    target_r: float,
    slippage_bps: float,
) -> tuple[int, float, str, float]:
    """Return exit bar index, raw reference price, reason, execution fill.

    If both stop and target are touched within one candle, assume the stop was
    hit first. On a gap through a stop/target, use the worse/better open as
    appropriate before applying adverse slippage.
    """
    stop_price = entry_fill * (1.0 - stop_loss_pct)
    target_price = entry_fill * (1.0 + stop_loss_pct * target_r)
    entry_day = option_rows[entry_index]["timestamp"].date()
    same_day = [
        (i, row) for i, row in enumerate(option_rows)
        if row["timestamp"].date() == entry_day and i >= entry_index
    ]
    if not same_day:
        raise ValueError("no same-session option candles after entry")
    last_idx, last_row = same_day[-1]

    for idx, row in same_day:
        # Worst-case priority when the same bar could have touched both levels.
        if row["low"] <= stop_price:
            raw_exit = min(row["open"], stop_price) if row["open"] < stop_price else stop_price
            return idx, raw_exit, "stop_loss", _exit_fill(raw_exit, slippage_bps)
        if row["high"] >= target_price:
            raw_exit = max(row["open"], target_price) if row["open"] > target_price else target_price
            return idx, raw_exit, "take_profit", _exit_fill(raw_exit, slippage_bps)

    raw_exit = float(last_row["close"])
    return last_idx, raw_exit, "intraday_square_off", _exit_fill(raw_exit, slippage_bps)


def _equity_curve(
    timeline: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    initial_cash: float,
    rates: CostRates,
    slippage_bps: float,
) -> list[dict[str, Any]]:
    exits_by_ts: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    entries_by_ts: dict[datetime, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        exits_by_ts[trade["_exit_ts"]].append(trade)
        entries_by_ts[trade["_entry_ts"]].append(trade)

    realized = initial_cash
    active: dict[str, Any] | None = None
    curve = []
    for row in timeline:
        ts = row["timestamp"]
        # Set entry state first, then close it if entry/exit share a bar.
        for trade in entries_by_ts.get(ts, []):
            active = trade
        # Closed position P&L becomes realized at the exit timestamp.
        for trade in exits_by_ts.get(ts, []):
            realized += trade["net_pnl"]
            if active is trade:
                active = None

        unrealized_net = 0.0
        qty = 0
        if active is not None and active["_entry_ts"] <= ts < active["_exit_ts"]:
            qty = int(active["quantity"])
            mark_value = float(row["option_close"]) if "option_close" in row else float(active["_last_mark"])
            estimated_exit_fill = _exit_fill(mark_value, slippage_bps)
            exit_cost = order_charges(estimated_exit_fill, qty, "SELL", rates)["total"]
            unrealized_net = (
                (mark_value - float(active["entry_fill"])) * qty
                - float(active["entry_costs"])
                - exit_cost
            )
        curve.append({
            "timestamp": ts.isoformat(),
            "realized_equity": realized,
            "unrealized_net_pnl": unrealized_net,
            "equity": realized + unrealized_net,
            "open_quantity": qty,
        })
    return curve


def _max_drawdown_pct(equity_rows: list[dict[str, Any]], initial_cash: float) -> float:
    peak = initial_cash
    max_drawdown = 0.0
    for row in equity_rows:
        value = float(row["equity"])
        peak = max(peak, value)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - value) / peak * 100.0)
    return max_drawdown


def run_backtest(
    index_rows: list[dict[str, Any]],
    option_rows: list[dict[str, Any]],
    *,
    option_symbol: str,
    lot_size: int,
    initial_cash: float = 100_000.0,
    risk_fraction: float = 0.005,
    hard_risk_fraction: float = 0.01,
    opening_range_minutes: int = 30,
    stop_loss_pct: float = 0.20,
    target_r: float = 1.5,
    max_entry_time: time = time(14, 30),
    slippage_bps: float = 5.0,
    rates: CostRates = CostRates(),
) -> dict[str, Any]:
    """Run one-call ORB prototype against synchronized 5-minute CSV rows."""
    if not option_symbol.strip():
        raise ValueError("option_symbol must not be empty")
    if lot_size <= 0 or initial_cash <= 0:
        raise ValueError("lot_size and initial_cash must be positive")
    if not (0 < risk_fraction <= hard_risk_fraction <= 0.01):
        raise ValueError("require 0 < risk_fraction <= hard_risk_fraction <= 1%")
    if opening_range_minutes <= 0 or opening_range_minutes % 5 != 0:
        raise ValueError("opening_range_minutes must be a positive multiple of 5")
    if not (0 < stop_loss_pct < 1) or target_r <= 0:
        raise ValueError("stop_loss_pct must be between 0 and 1; target_r must be positive")
    if slippage_bps < 0 or rates.brokerage_per_order < 0:
        raise ValueError("cost and slippage assumptions cannot be negative")

    index_by_ts = {row["timestamp"]: row for row in index_rows}
    option_by_ts = {row["timestamp"]: row for row in option_rows}
    option_dates = {row["timestamp"].date() for row in option_rows}
    index_dates = {row["timestamp"].date() for row in index_rows}
    shared_dates = sorted(option_dates & index_dates)
    if not shared_dates:
        raise ValueError("index and option CSVs have no overlapping trading dates")

    # A signal/execution study requires identical timestamp grids on selected dates.
    selected_index = [r for r in index_rows if r["timestamp"].date() in set(shared_dates)]
    selected_option = [r for r in option_rows if r["timestamp"].date() in set(shared_dates)]
    index_ts = {r["timestamp"] for r in selected_index}
    option_ts = {r["timestamp"] for r in selected_option}
    if index_ts != option_ts:
        missing_option = sorted(index_ts - option_ts)
        missing_index = sorted(option_ts - index_ts)
        raise ValueError(
            "Index/option timestamps are not fully aligned. "
            f"Missing option bars: {len(missing_option)} (examples: {[x.isoformat() for x in missing_option[:3]]}); "
            f"missing index bars: {len(missing_index)} (examples: {[x.isoformat() for x in missing_index[:3]]})"
        )

    by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in selected_index:
        by_date[row["timestamp"].date()].append(row)
    options_by_date: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in selected_option:
        options_by_date[row["timestamp"].date()].append(row)

    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    cash = initial_cash
    opening_bars_needed = opening_range_minutes // 5
    skipped_reasons: dict[str, int] = defaultdict(int)
    signal_count = 0
    processed_days = 0

    for day, day_index in sorted(by_date.items()):
        day_index.sort(key=lambda r: r["timestamp"])
        day_option = sorted(options_by_date[day], key=lambda r: r["timestamp"])
        if len(day_index) < opening_bars_needed + 2:
            skipped_reasons["insufficient_session_bars"] += 1
            continue
        opening_end = (datetime.combine(day, MARKET_OPEN) + timedelta(minutes=opening_range_minutes)).time()
        opening = [r for r in day_index if r["timestamp"].time() < opening_end]
        if len(opening) != opening_bars_needed:
            skipped_reasons["incomplete_opening_range"] += 1
            continue
        opening_high = max(r["high"] for r in opening)
        opening_low = min(r["low"] for r in opening)
        processed_days += 1
        by_day_ts = {r["timestamp"]: i for i, r in enumerate(day_option)}
        took_signal = False
        for index_i, index_bar in enumerate(day_index):
            if index_bar["timestamp"].time() < opening_end or index_bar["timestamp"].time() > max_entry_time:
                continue
            if index_bar["close"] <= opening_high:
                continue
            # Signal is known only after this index candle closes, so use next
            # index/option candle's open. Never fill at the signal candle close.
            if index_i + 1 >= len(day_index):
                continue
            entry_index_bar = day_index[index_i + 1]
            entry_ts = entry_index_bar["timestamp"]
            if entry_ts.date() != day or entry_ts.time() > max_entry_time:
                continue
            took_signal = True
            signal_count += 1
            option_entry_i = by_day_ts.get(entry_ts)
            signal_item: dict[str, Any] = {
                "signal_timestamp": index_bar["timestamp"].isoformat(),
                "entry_timestamp": entry_ts.isoformat(),
                "option_symbol": option_symbol,
                "opening_range_high": opening_high,
                "opening_range_low": opening_low,
                "index_close_at_signal": index_bar["close"],
                "status": "SKIPPED",
                "skip_reason": "",
                "planned_risk": 0.0,
                "quantity": 0,
                "lots": 0,
            }
            if option_entry_i is None:
                signal_item["skip_reason"] = "missing_option_entry_bar"
                signals.append(signal_item)
                skipped_reasons[signal_item["skip_reason"]] += 1
                break
            raw_entry = float(day_option[option_entry_i]["open"])
            entry_fill = _entry_fill(raw_entry, slippage_bps)
            quantity, planned_risk, skip_reason = _size_position(
                entry_fill, lot_size, cash, risk_fraction, hard_risk_fraction,
                slippage_bps, stop_loss_pct, rates,
            )
            signal_item["planned_risk"] = planned_risk
            signal_item["quantity"] = quantity
            signal_item["lots"] = quantity // lot_size
            if quantity == 0:
                signal_item["skip_reason"] = skip_reason
                signals.append(signal_item)
                skipped_reasons[skip_reason] += 1
                break

            entry_cost_details = order_charges(entry_fill, quantity, "BUY", rates)
            entry_costs = entry_cost_details["total"]
            required_cash = entry_fill * quantity + entry_costs
            if required_cash > cash + 1e-9:
                signal_item["skip_reason"] = "insufficient_cash_after_entry_costs"
                signals.append(signal_item)
                skipped_reasons[signal_item["skip_reason"]] += 1
                break

            exit_i, raw_exit, exit_reason, exit_fill = _find_exit(
                day_option, option_entry_i, entry_fill, stop_loss_pct, target_r, slippage_bps
            )
            exit_ts = day_option[exit_i]["timestamp"]
            exit_cost_details = order_charges(exit_fill, quantity, "SELL", rates)
            exit_costs = exit_cost_details["total"]
            gross_pnl = (raw_exit - raw_entry) * quantity
            slippage_cost = (entry_fill - raw_entry) * quantity + (raw_exit - exit_fill) * quantity
            trading_charges = entry_costs + exit_costs
            net_pnl = gross_pnl - slippage_cost - trading_charges
            trade = {
                "trade_id": len(trades) + 1,
                "option_symbol": option_symbol,
                "signal_timestamp": index_bar["timestamp"].isoformat(),
                "entry_timestamp": entry_ts.isoformat(),
                "exit_timestamp": exit_ts.isoformat(),
                "entry_index_close": index_bar["close"],
                "opening_range_high": opening_high,
                "opening_range_low": opening_low,
                "entry_raw_open": raw_entry,
                "entry_fill": entry_fill,
                "exit_raw_reference": raw_exit,
                "exit_fill": exit_fill,
                "exit_reason": exit_reason,
                "lot_size": lot_size,
                "lots": quantity // lot_size,
                "quantity": quantity,
                "planned_risk": planned_risk,
                "gross_pnl": gross_pnl,
                "slippage_cost": slippage_cost,
                "brokerage": entry_cost_details["brokerage"] + exit_cost_details["brokerage"],
                "exchange_charges": entry_cost_details["exchange_charges"] + exit_cost_details["exchange_charges"],
                "sebi_fee": entry_cost_details["sebi_fee"] + exit_cost_details["sebi_fee"],
                "stt": entry_cost_details["stt"] + exit_cost_details["stt"],
                "stamp_duty": entry_cost_details["stamp_duty"] + exit_cost_details["stamp_duty"],
                "gst": entry_cost_details["gst"] + exit_cost_details["gst"],
                "trading_charges": trading_charges,
                "net_pnl": net_pnl,
                "entry_costs": entry_costs,
                "_entry_ts": entry_ts,
                "_exit_ts": exit_ts,
                "_last_mark": day_option[exit_i]["close"],
            }
            # Long option paid upfront; deduct premium+entry costs, then credit
            # sale proceeds less exit charges at intraday close.
            cash += net_pnl
            trades.append(trade)
            signal_item.update({"status": "TRADED", "skip_reason": "", "planned_risk": planned_risk})
            signals.append(signal_item)
            break
        if not took_signal:
            # Add a daily no-signal record so empty days aren't silently ignored.
            signals.append({
                "signal_timestamp": "",
                "entry_timestamp": "",
                "option_symbol": option_symbol,
                "opening_range_high": opening_high,
                "opening_range_low": opening_low,
                "index_close_at_signal": "",
                "status": "NO_SIGNAL",
                "skip_reason": "",
                "planned_risk": 0.0,
                "quantity": 0,
                "lots": 0,
            })

    selected_timeline = []
    for row in selected_index:
        opt = option_by_ts[row["timestamp"]]
        selected_timeline.append({
            "timestamp": row["timestamp"],
            "index_close": row["close"],
            "option_close": opt["close"],
        })
    # Equity calculation replays closed trades and marks an active position with
    # the option close, net of entry costs and estimated costs to liquidate.
    equity_rows = _equity_curve(selected_timeline, trades, initial_cash, rates, slippage_bps)
    # Remove private values from exported trades.
    exported_trades = [
        {k: value for k, value in trade.items() if not k.startswith("_")}
        for trade in trades
    ]
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] < 0]
    net_pnl = sum(t["net_pnl"] for t in trades)
    gross_pnl = sum(t["gross_pnl"] for t in trades)
    total_slippage = sum(t["slippage_cost"] for t in trades)
    total_charges = sum(t["trading_charges"] for t in trades)
    final_equity = initial_cash + net_pnl
    gross_wins = sum(max(0.0, t["gross_pnl"]) for t in trades)
    gross_losses = -sum(min(0.0, t["gross_pnl"]) for t in trades)
    net_profit_factor = (
        sum(max(0.0, t["net_pnl"]) for t in trades) /
        -sum(min(0.0, t["net_pnl"]) for t in trades)
        if losses else (None if wins else 0.0)
    )
    summary = {
        "strategy": "NIFTY 30-minute opening-range breakout; long call only",
        "option_symbol": option_symbol,
        "initial_cash": initial_cash,
        "final_equity": final_equity,
        "net_pnl": net_pnl,
        "return_pct": (net_pnl / initial_cash * 100.0),
        "trades": len(trades),
        "signals": signal_count,
        "skipped_signals": sum(1 for signal in signals if signal["status"] == "SKIPPED"),
        "skipped_reasons": dict(sorted(skipped_reasons.items())),
        "trading_days_processed": processed_days,
        "gross_pnl_before_costs": gross_pnl,
        "slippage_cost_total": total_slippage,
        "brokerage_total": sum(t["brokerage"] for t in trades),
        "exchange_charges_total": sum(t["exchange_charges"] for t in trades),
        "sebi_fee_total": sum(t["sebi_fee"] for t in trades),
        "stt_total": sum(t["stt"] for t in trades),
        "stamp_duty_total": sum(t["stamp_duty"] for t in trades),
        "gst_total": sum(t["gst"] for t in trades),
        "trading_charges_total": total_charges,
        "net_win_rate_pct": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "gross_win_rate_pct": sum(1 for t in trades if t["gross_pnl"] > 0) / len(trades) * 100.0 if trades else 0.0,
        "profit_factor_net": net_profit_factor,
        "max_drawdown_pct": _max_drawdown_pct(equity_rows, initial_cash),
        "risk_fraction_target": risk_fraction,
        "hard_risk_fraction": hard_risk_fraction,
        "opening_range_minutes": opening_range_minutes,
        "stop_loss_pct_of_option_premium": stop_loss_pct * 100.0,
        "take_profit_r": target_r,
        "lot_size": lot_size,
        "slippage_bps_per_side": slippage_bps,
        "cost_rates": {
            "brokerage_per_order": rates.brokerage_per_order,
            "nse_option_exchange_charge_pct": rates.exchange_rate * 100,
            "stt_sell_pct_of_premium": rates.stt_sell_rate * 100,
            "stamp_duty_buy_pct_of_premium": rates.stamp_buy_rate * 100,
            "sebi_fee_pct_of_premium": rates.sebi_rate * 100,
            "gst_rate_pct": rates.gst_rate * 100,
        },
        "data_dates": [d.isoformat() for d in shared_dates],
        "index_volume_used_for_signals": False,
        "intraday_only": True,
        "research_only": True,
        "limitations": [
            "One fixed long-call contract only; no puts or dynamic strike selection.",
            "Option candles are OHLC, not bid/ask quotes; fills and slippage are estimates.",
            "If stop and target touch in one candle, stop is assumed first.",
            "Opening-range breakout settings are a baseline hypothesis, not a validated edge.",
            "Historical charges are estimates; verify every rate for the trade dates/account.",
            "A planned stop does not guarantee maximum loss in a gap or illiquid market.",
        ],
    }
    return {
        "summary": summary,
        "trades": exported_trades,
        "signals": signals,
        "equity_curve": equity_rows,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]], empty_columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows and not empty_columns:
        path.write_text("", encoding="utf-8")
        return
    columns = list(dict.fromkeys(k for row in rows for k in row)) if rows else list(empty_columns or [])
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Research-only NIFTY opening-range breakout options backtest; never places orders."
    )
    parser.add_argument("--index-csv", required=True, help="NIFTY 5-minute index OHLCV CSV")
    parser.add_argument("--option-csv", required=True, help="Selected NIFTY call contract's normalized 5-minute CSV")
    parser.add_argument("--option-symbol", required=True, help="Contract symbol matching the option CSV")
    parser.add_argument("--lot-size", type=int, required=True, help="Exchange lot size for this historical contract")
    parser.add_argument("--initial-cash", type=float, default=100000.0)
    parser.add_argument("--risk-fraction", type=float, default=0.005, help="Target planned risk, default 0.5%")
    parser.add_argument("--hard-risk-fraction", type=float, default=0.01, help="Hard planned-risk ceiling, max 1%")
    parser.add_argument("--opening-range-minutes", type=int, default=30)
    parser.add_argument("--stop-loss-pct", type=float, default=0.20, help="Stop distance as a fraction of entry option premium; default 20%%")
    parser.add_argument("--target-r", type=float, default=1.5, help="Target multiple of planned stop distance")
    parser.add_argument("--max-entry-time", type=parse_time, default=time(14, 30))
    parser.add_argument("--slippage-bps-per-side", type=float, default=5.0)
    parser.add_argument("--brokerage-per-order", type=float, default=20.0)
    parser.add_argument("--exchange-rate", type=float, default=0.0003503, help="Exchange charge as fraction of premium turnover; Jan 2025 NSE options default 0.03503%%")
    parser.add_argument("--sebi-rate", type=float, default=0.000001, help="SEBI fee as fraction of turnover; default Rs 10/crore")
    parser.add_argument("--stt-sell-rate", type=float, default=0.001, help="Sell-side option STT rate; Jan 2025 default 0.10%% of premium")
    parser.add_argument("--stamp-buy-rate", type=float, default=0.00003, help="Buy-side option stamp duty; default 0.003%%")
    parser.add_argument("--gst-rate", type=float, default=0.18)
    parser.add_argument("--out", required=True, help="Output directory for summary/trades/signals/equity curve")
    args = parser.parse_args()

    try:
        index_rows = load_candles(Path(args.index_csv))
        option_rows = load_candles(Path(args.option_csv))
        rates = CostRates(
            brokerage_per_order=args.brokerage_per_order,
            exchange_rate=args.exchange_rate,
            sebi_rate=args.sebi_rate,
            stt_sell_rate=args.stt_sell_rate,
            stamp_buy_rate=args.stamp_buy_rate,
            gst_rate=args.gst_rate,
        )
        result = run_backtest(
            index_rows,
            option_rows,
            option_symbol=args.option_symbol,
            lot_size=args.lot_size,
            initial_cash=args.initial_cash,
            risk_fraction=args.risk_fraction,
            hard_risk_fraction=args.hard_risk_fraction,
            opening_range_minutes=args.opening_range_minutes,
            stop_loss_pct=args.stop_loss_pct,
            target_r=args.target_r,
            max_entry_time=args.max_entry_time,
            slippage_bps=args.slippage_bps_per_side,
            rates=rates,
        )
    except (OSError, ValueError, csv.Error) as exc:
        parser.error(str(exc))

    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(output / "trades.csv", result["trades"], empty_columns=[
        "trade_id", "option_symbol", "signal_timestamp", "entry_timestamp", "exit_timestamp",
        "entry_index_close", "opening_range_high", "opening_range_low", "entry_raw_open",
        "entry_fill", "exit_raw_reference", "exit_fill", "exit_reason", "lot_size", "lots",
        "quantity", "planned_risk", "gross_pnl", "slippage_cost", "brokerage",
        "exchange_charges", "sebi_fee", "stt", "stamp_duty", "gst", "trading_charges", "net_pnl",
    ])
    _write_csv(output / "signals.csv", result["signals"])
    _write_csv(output / "equity_curve.csv", result["equity_curve"])
    print(json.dumps(result["summary"], indent=2, ensure_ascii=False))
    print(f"Saved backtest outputs to {output}")
    if not result["trades"]:
        print("No trades executed. Review the signals.csv skip reasons; do not relax risk limits just to force trades.")


if __name__ == "__main__":
    main()
