"""Point-in-time premarket ranking from completed daily OHLCV candles."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any, Sequence

from src.backtesting.engine import IST, Bar, _median, atr, ema

MIN_DAILY_BARS = 55


def _rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    changes = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = [max(change, 0.0) for change in changes]
    losses = [max(-change, 0.0) for change in changes]
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    for index in range(period, len(changes)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
    if average_loss == 0:
        return 100.0 if average_gain > 0 else 50.0
    relative_strength = average_gain / average_loss
    return 100.0 - 100.0 / (1.0 + relative_strength)


def rank_premarket(
    assets: dict[str, Sequence[Bar]],
    *,
    as_of: datetime | None = None,
    shortlist_size: int = 5,
    min_score: float = 55.0,
    benchmark_bars: Sequence[Bar] | None = None,
) -> list[dict[str, Any]]:
    """Rank watchlist symbols with daily data known before ``as_of``.

    The score ranks historical trend/momentum conditions; it is not a
    probability estimate and does not predict today's opening gap.
    """
    if not assets:
        raise ValueError("at least one asset is required")
    if shortlist_size < 1:
        raise ValueError("shortlist_size must be positive")
    if not math.isfinite(min_score) or not 0 <= min_score <= 100:
        raise ValueError("min_score must be in [0, 100]")
    cutoff = as_of or datetime.now(IST)
    if cutoff.tzinfo is None or cutoff.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    cutoff = cutoff.astimezone(IST)

    benchmark_closes: list[tuple[date, float]] = []
    if benchmark_bars is not None:
        previous_benchmark_timestamp: datetime | None = None
        previous_benchmark_date: date | None = None
        for bar in benchmark_bars:
            if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
                raise ValueError("benchmark bars must have timezone-aware timestamps")
            timestamp = bar.timestamp.astimezone(IST)
            if previous_benchmark_timestamp is not None and timestamp <= previous_benchmark_timestamp:
                raise ValueError("benchmark bars must be strictly increasing")
            if previous_benchmark_date == timestamp.date():
                raise ValueError("benchmark must contain at most one daily candle per date")
            if not math.isfinite(bar.close) or bar.close <= 0:
                raise ValueError("benchmark closing prices must be finite and positive")
            previous_benchmark_timestamp = timestamp
            previous_benchmark_date = timestamp.date()
            if timestamp.date() < cutoff.date():
                benchmark_closes.append((timestamp.date(), bar.close))
    benchmark_context: dict[str, Any] = {
        "market_trend": "unavailable",
        "benchmark_data_as_of": "",
        "benchmark_momentum_5d_pct": None,
    }
    if benchmark_closes:
        benchmark_values = [close for _, close in benchmark_closes]
        benchmark_ema20 = ema(benchmark_values, 20)[-1]
        benchmark_ema50 = ema(benchmark_values, 50)[-1]
        benchmark_close = benchmark_values[-1]
        trend = "unavailable"
        if benchmark_ema20 is not None and benchmark_ema50 is not None:
            trend = (
                "bullish"
                if benchmark_close > benchmark_ema20 > benchmark_ema50
                else "bearish"
                if benchmark_close < benchmark_ema20 < benchmark_ema50
                else "mixed"
            )
        benchmark_context = {
            "market_trend": trend,
            "benchmark_data_as_of": benchmark_closes[-1][0].isoformat(),
            "benchmark_momentum_5d_pct": (
                (benchmark_values[-1] / benchmark_values[-6] - 1.0) * 100.0
                if len(benchmark_values) >= 6
                else None
            ),
        }

    rows: list[dict[str, Any]] = []
    for symbol, all_bars in assets.items():
        if not symbol.strip():
            raise ValueError("symbols must not be empty")
        ordered: list[Bar] = []
        previous: datetime | None = None
        previous_day: date | None = None
        for bar in all_bars:
            if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
                raise ValueError(f"{symbol}: daily bars must have timezone-aware timestamps")
            timestamp = bar.timestamp.astimezone(IST)
            values = (bar.open, bar.high, bar.low, bar.close, bar.volume)
            if not all(math.isfinite(value) for value in values):
                raise ValueError(f"{symbol}: daily prices and volume must be finite")
            if min(bar.open, bar.high, bar.low, bar.close) <= 0 or bar.volume < 0:
                raise ValueError(f"{symbol}: daily prices must be positive and volume non-negative")
            if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(
                bar.open, bar.close, bar.high
            ):
                raise ValueError(f"{symbol}: invalid daily OHLC relationship")
            if previous is not None and timestamp <= previous:
                raise ValueError(f"{symbol}: daily bars must be strictly increasing")
            if previous_day == timestamp.date():
                raise ValueError(f"{symbol}: more than one daily candle for {timestamp.date()}")
            previous = timestamp
            previous_day = timestamp.date()
            if timestamp.date() < cutoff.date():
                ordered.append(Bar(timestamp, bar.open, bar.high, bar.low, bar.close, bar.volume))

        if len(ordered) < MIN_DAILY_BARS:
            rows.append({
                "symbol": symbol,
                "status": "INSUFFICIENT_DATA",
                "reason": f"requires_{MIN_DAILY_BARS}_completed_daily_bars",
                "data_as_of": ordered[-1].timestamp.date().isoformat() if ordered else "",
                "close": None,
                "previous_day_high": None,
                "previous_day_low": None,
                "previous_day_close": None,
                "ema_20": None,
                "ema_50": None,
                "rsi_14": None,
                "momentum_5d_pct": None,
                "momentum_20d_pct": None,
                "relative_strength_vs_nifty_20d_pct": None,
                "average_traded_value_20d": None,
                "prior_day_relative_volume": None,
                "atr_pct": None,
                "range_position_20d_pct": None,
                **benchmark_context,
                "score": None,
                "rank": None,
                "shortlisted": False,
            })
            continue

        closes = [bar.close for bar in ordered]
        volumes = [bar.volume for bar in ordered]
        fast_ema = ema(closes, 20)
        slow_ema = ema(closes, 50)
        atr_values = atr(ordered, 14)
        latest_close = closes[-1]
        ema20 = fast_ema[-1]
        ema50 = slow_ema[-1]
        daily_atr = atr_values[-1]
        momentum_5d = latest_close / closes[-6] - 1.0
        momentum_20d = latest_close / closes[-21] - 1.0
        relative_strength_20d: float | None = None
        stock_start_date = ordered[-21].timestamp.date()
        stock_end_date = ordered[-1].timestamp.date()
        benchmark_at_start = [
            (bar_date, close) for bar_date, close in benchmark_closes
            if bar_date <= stock_start_date
        ]
        benchmark_at_end = [
            (bar_date, close) for bar_date, close in benchmark_closes
            if bar_date <= stock_end_date
        ]
        if benchmark_at_start and benchmark_at_end:
            first_benchmark_close = benchmark_at_start[-1][1]
            latest_benchmark_close = benchmark_at_end[-1][1]
            if first_benchmark_close is not None and latest_benchmark_close is not None:
                benchmark_return = latest_benchmark_close / first_benchmark_close - 1.0
                relative_strength_20d = (momentum_20d - benchmark_return) * 100.0
        recent_volume_median = _median(volumes[-21:-1])
        relative_volume = volumes[-1] / recent_volume_median if recent_volume_median > 0 else 0.0
        high_20d = max(bar.high for bar in ordered[-20:])
        low_20d = min(bar.low for bar in ordered[-20:])
        range_position = (
            (latest_close - low_20d) / (high_20d - low_20d)
            if high_20d > low_20d
            else 0.0
        )
        atr_pct = daily_atr / latest_close * 100 if daily_atr is not None else None
        rsi_14 = _rsi(closes)
        prior_day = ordered[-1]
        average_traded_value_20d = sum(
            bar.close * bar.volume for bar in ordered[-20:]
        ) / 20.0

        trend_points = 30.0 if latest_close > ema20 and ema20 > ema50 else (
            15.0 if latest_close > ema20 else 0.0
        )
        momentum_points = min(max(momentum_20d, 0.0) / 0.15, 1.0) * 30.0
        recent_points = min(max(momentum_5d, 0.0) / 0.06, 1.0) * 15.0
        volume_points = min(max(relative_volume - 0.8, 0.0) / 1.2, 1.0) * 15.0
        range_points = min(max(range_position, 0.0), 1.0) * 10.0
        score = round(trend_points + momentum_points + recent_points + volume_points + range_points, 2)

        reasons: list[str] = []
        if latest_close <= ema20 or ema20 <= ema50:
            reasons.append("daily_trend_not_aligned")
        if momentum_20d <= 0:
            reasons.append("negative_20_day_momentum")
        if relative_volume < 0.8:
            reasons.append("weak_prior_day_volume")
        if atr_pct is None or atr_pct > 8:
            reasons.append("daily_volatility_too_high_or_unavailable")

        rows.append({
            "symbol": symbol,
            "status": "WATCH" if not reasons and score >= min_score else "NO_TRADE",
            "reason": ";".join(reasons) if reasons else (
                "" if score >= min_score else "premarket_score_below_threshold"
            ),
            "data_as_of": ordered[-1].timestamp.date().isoformat(),
            "close": latest_close,
            "previous_day_high": prior_day.high,
            "previous_day_low": prior_day.low,
            "previous_day_close": prior_day.close,
            "ema_20": ema20,
            "ema_50": ema50,
            "rsi_14": rsi_14,
            "momentum_5d_pct": momentum_5d * 100.0,
            "momentum_20d_pct": momentum_20d * 100.0,
            "relative_strength_vs_nifty_20d_pct": relative_strength_20d,
            "average_traded_value_20d": average_traded_value_20d,
            **benchmark_context,
            "prior_day_relative_volume": relative_volume,
            "atr_pct": atr_pct,
            "range_position_20d_pct": range_position * 100.0,
            "score": score,
            "rank": None,
            "shortlisted": False,
        })

    ranked = sorted(
        (row for row in rows if row["score"] is not None),
        key=lambda row: (-row["score"], row["symbol"]),
    )
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
    leaders = [
        row for row in ranked
        if row["status"] == "WATCH"
    ][:shortlist_size]
    for row in leaders:
        row["shortlisted"] = True

    return sorted(
        rows,
        key=lambda row: (
            row["score"] is None,
            row["rank"] if row["rank"] is not None else math.inf,
            row["symbol"],
        ),
    )
