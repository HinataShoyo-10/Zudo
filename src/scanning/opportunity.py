"""Explainable latest-bar scanner for supplied 5-minute cash-equity histories.

The scanner is data-only. It proposes paper candidates from the most recent
completed candle and never reads a later candle to invent an entry fill.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from typing import Any, Callable, Sequence

from src.backtesting.engine import IST, Bar, _median, _session_vwap, atr, ema

OPEN_TIME = time(9, 15)
LAST_BAR_TIME = time(15, 25)
LAST_ENTRY_TIME = time(14, 30)
BAR_INTERVAL_MINUTES = 5
OPENING_RANGE_BARS = 6


def _empty_result(
    symbol: str, session_date: date | None, strategy: str, reason: str
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "session_date": session_date.isoformat() if session_date else "",
        "strategy": strategy,
        "status": "NO_TRADE",
        "reason": reason,
        "signal_time": "",
        "planned_entry_time": "",
        "estimated_entry": None,
        "stop_price": None,
        "target_price": None,
        "quantity": 0,
        "planned_risk": 0.0,
        "setup_score": None,
        "rank": None,
        "latest_bar_time": "",
        "previous_day_high": None,
        "previous_day_low": None,
        "previous_day_close": None,
        "opening_gap_pct": None,
    }


def _latest_regular_session(
    bars: Sequence[Bar],
    as_of: datetime | None = None,
) -> tuple[date | None, list[Bar], str | None]:
    if not bars:
        return None, [], "no_data"
    scan_time = as_of or datetime.now(IST)
    if scan_time.tzinfo is None or scan_time.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    scan_time = scan_time.astimezone(IST)

    normalized: list[Bar] = []
    previous: datetime | None = None
    for bar in bars:
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            raise ValueError("scanner bars must have timezone-aware timestamps")
        timestamp = bar.timestamp.astimezone(IST)
        values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("bar prices and volume must be finite")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
            raise ValueError("bar prices must be positive and volume non-negative")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(
            bar.open, bar.close, bar.high
        ):
            raise ValueError(f"invalid OHLC relationship at {timestamp.isoformat()}")
        if previous is not None and timestamp <= previous:
            raise ValueError("scanner bars must be strictly increasing after IST normalization")
        previous = timestamp
        if timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES) <= scan_time:
            normalized.append(Bar(timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume))
    if not normalized:
        return None, [], "no_completed_bars"

    latest_day = max(bar.timestamp.date() for bar in normalized)
    if latest_day.weekday() >= 5:
        return latest_day, [], "latest_data_is_not_a_trading_session"
    session = [
        bar for bar in normalized
        if bar.timestamp.date() == latest_day
        and OPEN_TIME <= bar.timestamp.time() <= LAST_BAR_TIME
    ]
    if not session:
        return latest_day, [], "no_regular_session_bars"
    expected = [
        datetime.combine(latest_day, OPEN_TIME, tzinfo=IST)
        + timedelta(minutes=BAR_INTERVAL_MINUTES * index)
        for index in range(len(session))
    ]
    if session[0].timestamp != expected[0] or [
        bar.timestamp for bar in session
    ] != expected:
        return latest_day, [], "latest_session_has_missing_or_irregular_bars"
    return latest_day, session, None


def _validate_parameters(
    initial_cash: float,
    risk_fraction: float,
    max_exposure_fraction: float,
    fee_per_side: float,
    slippage_bps: float,
    min_volume_multiple: float,
    min_breakout_atr: float,
    min_body_fraction: float,
    max_opening_range_atr: float,
) -> None:
    if not math.isfinite(initial_cash) or initial_cash <= 0:
        raise ValueError("initial_cash must be finite and positive")
    if not math.isfinite(risk_fraction) or not (0 < risk_fraction <= 0.02):
        raise ValueError("risk_fraction must be in (0, 2%]")
    if not math.isfinite(max_exposure_fraction) or not (0 < max_exposure_fraction <= 1):
        raise ValueError("max_exposure_fraction must be in (0, 1]")
    if not math.isfinite(fee_per_side) or fee_per_side < 0:
        raise ValueError("fee_per_side must be finite and non-negative")
    if not math.isfinite(slippage_bps) or not (0 <= slippage_bps < 10_000):
        raise ValueError("slippage_bps must be in [0, 10,000)")
    if not math.isfinite(min_volume_multiple) or min_volume_multiple < 0:
        raise ValueError("min_volume_multiple must be finite and non-negative")
    if not math.isfinite(min_breakout_atr) or min_breakout_atr < 0:
        raise ValueError("min_breakout_atr must be finite and non-negative")
    if not math.isfinite(min_body_fraction) or not (0 <= min_body_fraction <= 1):
        raise ValueError("min_body_fraction must be in [0, 1]")
    if not math.isfinite(max_opening_range_atr) or max_opening_range_atr <= 0:
        raise ValueError("max_opening_range_atr must be finite and positive")


def _score_setup(
    momentum_atr: float,
    volume_multiple: float,
    trend_spread_atr: float,
    vwap_distance_atr: float,
) -> float:
    momentum_points = min(max(momentum_atr, 0.0) / 1.0, 1.0) * 25
    volume_points = min(max(volume_multiple - 1.0, 0.0) / 2.0, 1.0) * 25
    trend_points = min(max(trend_spread_atr, 0.0) / 1.0, 1.0) * 25
    vwap_points = min(max(vwap_distance_atr, 0.0) / 2.0, 1.0) * 25
    return round(momentum_points + volume_points + trend_points + vwap_points, 2)


def _evaluate_latest_bar(
    *,
    symbol: str,
    session_date: date,
    strategy: str,
    bars: list[Bar],
    range_high: float,
    range_width: float,
    atr_values: list[float | None],
    vwap_values: list[float | None],
    fast_ema: list[float | None],
    slow_ema: list[float | None],
    initial_cash: float,
    risk_fraction: float,
    max_exposure_fraction: float,
    fee_per_side: float,
    slippage_bps: float,
    min_volume_multiple: float,
    min_breakout_atr: float,
    min_body_fraction: float,
    max_opening_range_atr: float,
) -> dict[str, Any]:
    index = len(bars) - 1
    bar = bars[index]
    previous = bars[index - 1]
    empty = _empty_result(symbol, session_date, strategy, "no_setup_on_latest_bar")
    empty["latest_bar_time"] = bar.timestamp.isoformat()

    if index < OPENING_RANGE_BARS:
        return _empty_result(symbol, session_date, strategy, "opening_range_not_complete")

    current_fast = fast_ema[index]
    current_slow = slow_ema[index]
    previous_slow = slow_ema[index - 1]
    previous_fast = fast_ema[index - 1]
    atr_value = atr_values[index]
    vwap_value = vwap_values[index]
    if atr_value is None or atr_value <= 0:
        return _empty_result(symbol, session_date, strategy, "indicator_warmup")

    if strategy == "opening_range_breakout":
        triggered = bar.close > range_high and previous.close <= range_high
        momentum = bar.close - range_high
    else:
        triggered = (
            previous_fast is not None
            and current_fast is not None
            and previous.low <= previous_fast * 1.003
            and bar.close > current_fast
            and bar.close > bar.open
        )
        momentum = bar.close - current_fast if current_fast is not None else 0.0
    if not triggered:
        return empty
    if bar.timestamp.time() > LAST_ENTRY_TIME:
        return _empty_result(symbol, session_date, strategy, "past_entry_cutoff")

    reasons: list[str] = []
    if current_fast is None or current_slow is None or previous_slow is None:
        reasons.append("trend_warmup")
    elif not (current_fast > current_slow and current_slow > previous_slow):
        reasons.append("trend_not_aligned")

    if strategy == "opening_range_breakout":
        if range_width / atr_value > max_opening_range_atr:
            reasons.append("opening_range_too_wide")
        if momentum < min_breakout_atr * atr_value:
            reasons.append("breakout_too_weak")
    elif current_fast is not None and momentum > 0.75 * atr_value:
        reasons.append("entry_overextended")

    if vwap_value is None or bar.close <= vwap_value:
        reasons.append("below_session_vwap")
    candle_range = bar.high - bar.low
    body_fraction = (bar.close - bar.open) / candle_range if candle_range > 0 else 0.0
    if candle_range <= 0 or body_fraction < min_body_fraction:
        reasons.append("weak_signal_candle")
    prior_volumes = [row.volume for row in bars[max(0, index - 6):index]]
    median_volume = _median(prior_volumes) if prior_volumes else 0.0
    volume_multiple = bar.volume / median_volume if median_volume > 0 else None
    if (
        median_volume <= 0
        or volume_multiple is None
        or volume_multiple < min_volume_multiple
    ):
        reasons.append("no_volume_expansion")

    result: dict[str, Any] = {
        "symbol": symbol,
        "session_date": session_date.isoformat(),
        "strategy": strategy,
        "status": "NO_TRADE" if reasons else "OPPORTUNITY",
        "reason": ";".join(dict.fromkeys(reasons)),
        "signal_time": bar.timestamp.isoformat(),
        "latest_bar_time": bar.timestamp.isoformat(),
        "planned_entry_time": (
            bar.timestamp + timedelta(minutes=BAR_INTERVAL_MINUTES)
        ).isoformat(),
        "estimated_entry": None,
        "stop_price": None,
        "target_price": None,
        "quantity": 0,
        "planned_risk": 0.0,
        "setup_score": None,
        "rank": None,
        "close": bar.close,
        "atr": atr_value,
        "vwap": vwap_value,
        "ema_fast": current_fast,
        "ema_slow": current_slow,
        "volume_multiple": volume_multiple,
        "body_fraction": body_fraction,
        "momentum_atr": momentum / atr_value,
        "distance_from_vwap_atr": (
            (bar.close - vwap_value) / atr_value if vwap_value is not None else None
        ),
    }
    if reasons:
        return result

    # No following candle is consulted. This is an indicative paper plan only;
    # the next candle's open is unknown until it arrives.
    slippage = slippage_bps / 10_000.0
    estimated_entry = bar.close * (1.0 + slippage)
    stop_distance = atr_value * 1.5
    stop_price = estimated_entry - stop_distance
    stop_fill_estimate = stop_price * (1.0 - slippage)
    risk_per_share = estimated_entry - stop_fill_estimate
    risk_budget = initial_cash * risk_fraction - 2 * fee_per_side
    quantity = min(
        int(risk_budget / risk_per_share) if risk_per_share > 0 and risk_budget > 0 else 0,
        int(initial_cash * max_exposure_fraction // estimated_entry),
        int(max(0.0, initial_cash - fee_per_side) // estimated_entry),
    )
    if stop_price <= 0 or quantity <= 0:
        result.update(status="NO_TRADE", reason="position_exceeds_cash_or_risk_budget")
        return result

    trend_spread_atr = (
        (current_fast - current_slow) / atr_value
        if current_fast is not None and current_slow is not None
        else 0.0
    )
    vwap_distance_atr = (
        (bar.close - vwap_value) / atr_value if vwap_value is not None else 0.0
    )
    result.update({
        "estimated_entry": estimated_entry,
        "stop_price": stop_price,
        "target_price": estimated_entry + stop_distance * 2.0,
        "quantity": quantity,
        "planned_risk": risk_per_share * quantity + 2 * fee_per_side,
        "setup_score": _score_setup(
            momentum / atr_value,
            volume_multiple or 0.0,
            trend_spread_atr,
            vwap_distance_atr,
        ),
    })
    return result


def scan_asset(
    symbol: str,
    bars: Sequence[Bar],
    *,
    initial_cash: float = 50_000.0,
    risk_fraction: float = 0.005,
    max_exposure_fraction: float = 0.20,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
    min_volume_multiple: float = 1.2,
    min_breakout_atr: float = 0.1,
    min_body_fraction: float = 0.6,
    max_opening_range_atr: float = 5.0,
    as_of: datetime | None = None,
) -> list[dict[str, Any]]:
    """Return ORB and trend-pullback assessments using only each latest bar."""
    if not symbol.strip():
        raise ValueError("symbol must not be empty")
    _validate_parameters(
        initial_cash,
        risk_fraction,
        max_exposure_fraction,
        fee_per_side,
        slippage_bps,
        min_volume_multiple,
        min_breakout_atr,
        min_body_fraction,
        max_opening_range_atr,
    )
    session_date, session, issue = _latest_regular_session(bars, as_of)
    if issue:
        return [
            _empty_result(symbol, session_date, strategy, issue)
            for strategy in ("opening_range_breakout", "trend_pullback")
        ]
    if len(session) < OPENING_RANGE_BARS + 1:
        results = [
            _empty_result(symbol, session_date, strategy, "insufficient_bars_for_setup")
            for strategy in ("opening_range_breakout", "trend_pullback")
        ]
        for row in results:
            row["latest_bar_time"] = session[-1].timestamp.isoformat()
        return results

    opening = session[:OPENING_RANGE_BARS]
    range_high = max(bar.high for bar in opening)
    range_width = range_high - min(bar.low for bar in opening)
    atr_values = atr(session, 14)
    vwap_values = _session_vwap(session)
    closes = [bar.close for bar in session]
    fast_ema = ema(closes, 5)
    slow_ema = ema(closes, 13)
    results = [
        _evaluate_latest_bar(
            symbol=symbol,
            session_date=session_date,
            strategy=strategy,
            bars=session,
            range_high=range_high,
            range_width=range_width,
            atr_values=atr_values,
            vwap_values=vwap_values,
            fast_ema=fast_ema,
            slow_ema=slow_ema,
            initial_cash=initial_cash,
            risk_fraction=risk_fraction,
            max_exposure_fraction=max_exposure_fraction,
            fee_per_side=fee_per_side,
            slippage_bps=slippage_bps,
            min_volume_multiple=min_volume_multiple,
            min_breakout_atr=min_breakout_atr,
            min_body_fraction=min_body_fraction,
            max_opening_range_atr=max_opening_range_atr,
        )
        for strategy in ("opening_range_breakout", "trend_pullback")
    ]
    for row in results:
        if not row["latest_bar_time"]:
            row["latest_bar_time"] = session[-1].timestamp.isoformat()
    return results


def scan_universe(
    assets: dict[str, Sequence[Bar]],
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
    as_of: datetime | None = None,
    daily_context: dict[str, dict[str, Any]] | None = None,
    initial_cash: float = 50_000.0,
    risk_fraction: float = 0.005,
    max_exposure_fraction: float = 0.20,
    fee_per_side: float = 0.0,
    slippage_bps: float = 5.0,
    min_volume_multiple: float = 1.2,
    min_breakout_atr: float = 0.1,
    min_body_fraction: float = 0.6,
    max_opening_range_atr: float = 5.0,
) -> list[dict[str, Any]]:
    """Scan supplied symbols and rank opportunities by transparent setup score."""
    if not assets:
        raise ValueError("at least one asset is required")
    scan_parameters = {
        "initial_cash": initial_cash,
        "risk_fraction": risk_fraction,
        "max_exposure_fraction": max_exposure_fraction,
        "fee_per_side": fee_per_side,
        "slippage_bps": slippage_bps,
        "min_volume_multiple": min_volume_multiple,
        "min_breakout_atr": min_breakout_atr,
        "min_body_fraction": min_body_fraction,
        "max_opening_range_atr": max_opening_range_atr,
    }
    results: list[dict[str, Any]] = []
    for index, (symbol, bars) in enumerate(assets.items(), start=1):
        symbol_results = scan_asset(symbol, bars, as_of=as_of, **scan_parameters)
        context = (daily_context or {}).get(symbol, {})
        previous_close = context.get("previous_day_close")
        if isinstance(previous_close, (int, float)) and previous_close > 0:
            for row in symbol_results:
                row["previous_day_high"] = context.get("previous_day_high")
                row["previous_day_low"] = context.get("previous_day_low")
                row["previous_day_close"] = previous_close
                session_date = row.get("session_date")
                session_open = next(
                    (
                        bar.open
                        for bar in bars
                        if bar.timestamp.astimezone(IST).date().isoformat() == session_date
                        and bar.timestamp.astimezone(IST).time() == OPEN_TIME
                    ),
                    None,
                )
                if isinstance(session_open, (int, float)):
                    row["opening_gap_pct"] = (session_open / previous_close - 1.0) * 100.0
        results.extend(symbol_results)
        if progress_callback is not None:
            progress_callback(index, len(assets), symbol)
    opportunities = sorted(
        (row for row in results if row["status"] == "OPPORTUNITY"),
        key=lambda row: (-row["setup_score"], row["symbol"], row["strategy"]),
    )
    for rank, row in enumerate(opportunities, start=1):
        row["rank"] = rank
    return sorted(
        results,
        key=lambda row: (
            row["status"] != "OPPORTUNITY",
            row["rank"] if row["rank"] is not None else math.inf,
            row["symbol"],
            row["strategy"],
        ),
    )


def latest_session_chart(
    bars: Sequence[Bar], as_of: datetime | None = None
) -> list[dict[str, Any]]:
    """Return close and signal indicators for the latest regular session."""
    _, session, issue = _latest_regular_session(bars, as_of)
    if issue:
        return []
    closes = [bar.close for bar in session]
    fast = ema(closes, 5)
    slow = ema(closes, 13)
    session_vwap = _session_vwap(session)
    return [
        {
            "timestamp": bar.timestamp.isoformat(),
            "close": bar.close,
            "ema_5": fast[index],
            "ema_13": slow[index],
            "session_vwap": session_vwap[index],
        }
        for index, bar in enumerate(session)
    ]
