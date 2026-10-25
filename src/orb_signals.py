"""Research-only NIFTY opening-range signal rules; never places orders.

Only completed candles are accepted. Intrabar early warnings are deliberately a
separate event type so they cannot be mistaken for confirmed entry candidates.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

from src.models import Candle

IST = ZoneInfo("Asia/Kolkata")
Direction = Literal["BULLISH", "BEARISH"]
Status = Literal["EARLY_WARNING", "CONFIRMED_CANDIDATE", "WAIT"]


@dataclass(frozen=True)
class ORBConfig:
    session_start: time = time(9, 15)
    opening_range_minutes: int = 15
    min_relative_volume: Decimal = Decimal("1.5")
    volume_lookback: int = 10
    retest_tolerance_fraction: Decimal = Decimal("0.0015")
    max_chase_fraction: Decimal = Decimal("0.003")
    one_minute_consecutive_closes: int = 2

    def validate(self) -> None:
        if self.opening_range_minutes <= 0:
            raise ValueError("opening_range_minutes must be positive")
        if self.volume_lookback < 2:
            raise ValueError("volume_lookback must be at least 2")
        if self.min_relative_volume <= 0:
            raise ValueError("min_relative_volume must be positive")
        if self.max_chase_fraction <= 0 or self.retest_tolerance_fraction < 0:
            raise ValueError("chase must be positive and retest tolerance non-negative")
        if self.one_minute_consecutive_closes < 1:
            raise ValueError("one_minute_consecutive_closes must be positive")


@dataclass(frozen=True)
class ORBSignal:
    status: Status
    direction: Direction | None
    timeframe_minutes: int
    timestamp: datetime
    reason: str
    opening_range_high: Decimal | None = None
    opening_range_low: Decimal | None = None
    vwap: Decimal | None = None
    relative_volume: Decimal | None = None
    reference_price: Decimal | None = None
    invalidation_level: Decimal | None = None


def _session_candles(candles: Sequence[Candle], interval_minutes: int) -> list[Candle]:
    if interval_minutes not in (1, 5):
        raise ValueError("Only 1-minute and 5-minute candles are supported")
    if not candles:
        return []
    ordered = list(candles)
    if any(current.start <= previous.start for previous, current in zip(ordered, ordered[1:])):
        raise ValueError("Candles must have unique, increasing timestamps")
    latest_date = ordered[-1].start.astimezone(IST).date()
    selected = [
        candle for candle in ordered
        if candle.start.astimezone(IST).date() == latest_date
        and candle.start.astimezone(IST).time().replace(tzinfo=None) >= time(9, 15)
        and candle.start.astimezone(IST).time().replace(tzinfo=None) < time(15, 30)
    ]
    expected = interval_minutes
    for previous, current in zip(selected, selected[1:]):
        if current.start <= previous.start:
            raise ValueError("Candles must have unique, increasing timestamps")
        # Gaps are permitted in the input, but opening-range completeness is checked below.
        if (current.start - previous.start).total_seconds() < expected * 60:
            raise ValueError("Overlapping or duplicate candles")
    return selected


def _opening_range(
    candles: Sequence[Candle], interval_minutes: int, config: ORBConfig
) -> tuple[Decimal, Decimal, datetime] | None:
    bars_needed = config.opening_range_minutes // interval_minutes
    if config.opening_range_minutes % interval_minutes:
        raise ValueError("opening_range_minutes must be divisible by candle interval")
    if len(candles) < bars_needed:
        return None
    opening = list(candles[:bars_needed])
    first_start = opening[0].start.astimezone(IST)
    if first_start.time().replace(tzinfo=None) != config.session_start:
        return None
    for index, candle in enumerate(opening):
        expected_start = first_start.timestamp() + index * interval_minutes * 60
        if candle.start.astimezone(IST).timestamp() != expected_start:
            return None
    # Opening-range levels are price-only; FYERS' first cumulative-volume
    # observation may be unverified even though its OHLC prices are usable.
    return max(c.high for c in opening), min(c.low for c in opening), opening[-1].end


def _session_vwap(candles: Sequence[Candle]) -> Decimal | None:
    # Use verified deltas only. The first candle can be excluded because the
    # initial cumulative counter reading has no reconstructable delta.
    candles = [c for c in candles if c.volume > 0 and c.volume_valid]
    if not candles:
        return None
    total_volume = sum(c.volume for c in candles)
    if total_volume <= 0:
        return None
    weighted = sum(
        (((c.high + c.low + c.close) / Decimal(3)) * c.volume for c in candles),
        Decimal("0"),
    )
    return weighted / Decimal(total_volume)


def _relative_volume(candles: Sequence[Candle], lookback: int) -> Decimal | None:
    if len(candles) < lookback + 1:
        return None
    previous = candles[-lookback - 1:-1]
    if any(c.volume <= 0 or not c.volume_valid for c in previous) or candles[-1].volume <= 0 or not candles[-1].volume_valid:
        return None
    average = Decimal(sum(c.volume for c in previous)) / Decimal(lookback)
    if average <= 0:
        return None
    return Decimal(candles[-1].volume) / average


def evaluate_completed_candles(
    candles: Sequence[Candle],
    interval_minutes: int,
    config: ORBConfig = ORBConfig(),
) -> ORBSignal:
    """Evaluate a completed 5m primary setup or a separately tested 1m path."""
    config.validate()
    session = _session_candles(candles, interval_minutes)
    timestamp = session[-1].end if session else (candles[-1].end if candles else datetime.now(IST))
    if not session:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "no_session_candles")

    opening = _opening_range(session, interval_minutes, config)
    if opening is None:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "opening_range_incomplete_or_volume_unverified")
    range_high, range_low, range_end = opening
    post_range = [c for c in session if c.start >= range_end]
    if not post_range:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "opening_range_not_finished",
                         range_high, range_low)
    current = post_range[-1]
    if current.volume <= 0 or not current.volume_valid:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "vwap_unavailable_volume_unverified",
                         range_high, range_low, reference_price=current.close)
    vwap = _session_vwap(session)
    rel_volume = _relative_volume(post_range, config.volume_lookback)
    if vwap is None:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "vwap_unavailable_volume_unverified",
                         range_high, range_low, relative_volume=rel_volume, reference_price=current.close)
    if rel_volume is None:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "relative_volume_warmup_or_missing",
                         range_high, range_low, vwap, reference_price=current.close)

    if current.close > range_high:
        direction: Direction = "BULLISH"
        invalidation = range_high
        aligned = current.close > vwap
        distance = (current.close - range_high) / range_high
    elif current.close < range_low:
        direction = "BEARISH"
        invalidation = range_low
        aligned = current.close < vwap
        distance = (range_low - current.close) / range_low
    else:
        return ORBSignal("WAIT", None, interval_minutes, timestamp, "price_inside_opening_range",
                         range_high, range_low, vwap, rel_volume, current.close)

    if not aligned:
        return ORBSignal("WAIT", direction, interval_minutes, timestamp, "vwap_not_aligned",
                         range_high, range_low, vwap, rel_volume, current.close, invalidation)
    if rel_volume < config.min_relative_volume:
        return ORBSignal("WAIT", direction, interval_minutes, timestamp, "relative_volume_below_threshold",
                         range_high, range_low, vwap, rel_volume, current.close, invalidation)
    if distance > config.max_chase_fraction:
        return ORBSignal("WAIT", direction, interval_minutes, timestamp, "breakout_too_extended_do_not_chase",
                         range_high, range_low, vwap, rel_volume, current.close, invalidation)

    if interval_minutes == 1:
        needed = config.one_minute_consecutive_closes
        recent = post_range[-needed:]
        if len(recent) < needed:
            return ORBSignal("WAIT", direction, interval_minutes, timestamp, "one_minute_confirmation_warmup",
                             range_high, range_low, vwap, rel_volume, current.close, invalidation)
        if direction == "BULLISH" and not all(c.close > range_high for c in recent):
            return ORBSignal("WAIT", direction, interval_minutes, timestamp, "one_minute_breakout_not_persistent",
                             range_high, range_low, vwap, rel_volume, current.close, invalidation)
        if direction == "BEARISH" and not all(c.close < range_low for c in recent):
            return ORBSignal("WAIT", direction, interval_minutes, timestamp, "one_minute_breakout_not_persistent",
                             range_high, range_low, vwap, rel_volume, current.close, invalidation)
        reason = "one_minute_breakout_confirmed_research_only"
    else:
        reason = "five_minute_breakout_confirmed"

    return ORBSignal("CONFIRMED_CANDIDATE", direction, interval_minutes, timestamp, reason,
                     range_high, range_low, vwap, rel_volume, current.close, invalidation)


def evaluate_early_warning(
    last_price: Decimal,
    timestamp: datetime,
    completed_candles: Sequence[Candle],
    config: ORBConfig = ORBConfig(),
) -> ORBSignal:
    """Create a non-entry warning when a live price crosses a known opening range.

    Volume/VWAP filters are intentionally not claimed here: live tick-volume semantics
    must be validated before warnings can be promoted to candidate entries.
    """
    config.validate()
    session = _session_candles(completed_candles, 5)
    opening = _opening_range(session, 5, config)
    if opening is None:
        return ORBSignal("WAIT", None, 5, timestamp, "opening_range_incomplete_or_volume_unverified")
    range_high, range_low, range_end = opening
    if timestamp.astimezone(IST).date() != session[-1].start.astimezone(IST).date():
        return ORBSignal("WAIT", None, 5, timestamp, "stale_session_data", range_high, range_low)
    if timestamp < range_end:
        return ORBSignal("WAIT", None, 5, timestamp, "opening_range_not_finished", range_high, range_low)
    if last_price > range_high:
        return ORBSignal("EARLY_WARNING", "BULLISH", 5, timestamp,
                         "developing_breakout_not_confirmed_do_not_enter_from_warning",
                         range_high, range_low, reference_price=last_price, invalidation_level=range_high)
    if last_price < range_low:
        return ORBSignal("EARLY_WARNING", "BEARISH", 5, timestamp,
                         "developing_breakdown_not_confirmed_do_not_enter_from_warning",
                         range_high, range_low, reference_price=last_price, invalidation_level=range_low)
    return ORBSignal("WAIT", None, 5, timestamp, "price_inside_opening_range",
                     range_high, range_low, reference_price=last_price)


def evaluate_one_minute_retest(
    candles: Sequence[Candle],
    config: ORBConfig = ORBConfig(),
) -> ORBSignal:
    """Detect a completed 1m retest after a prior breakout; research-only candidate."""
    config.validate()
    session = _session_candles(candles, 1)
    timestamp = session[-1].end if session else (candles[-1].end if candles else datetime.now(IST))
    if not session:
        return ORBSignal("WAIT", None, 1, timestamp, "no_session_candles")
    opening = _opening_range(session, 1, config)
    if opening is None:
        return ORBSignal("WAIT", None, 1, timestamp, "opening_range_incomplete_or_volume_unverified")
    range_high, range_low, range_end = opening
    post_range = [c for c in session if c.start >= range_end]
    if len(post_range) < 2:
        return ORBSignal("WAIT", None, 1, timestamp, "retest_history_incomplete",
                         range_high, range_low)
    previous, current = post_range[-2], post_range[-1]
    vwap = _session_vwap(session)
    rel_volume = _relative_volume(post_range, config.volume_lookback)
    if vwap is None:
        return ORBSignal("WAIT", None, 1, timestamp, "vwap_unavailable_volume_unverified",
                         range_high, range_low, relative_volume=rel_volume, reference_price=current.close)
    if rel_volume is None:
        return ORBSignal("WAIT", None, 1, timestamp, "relative_volume_warmup_or_missing",
                         range_high, range_low, vwap, reference_price=current.close)

    tolerance = config.retest_tolerance_fraction
    bullish_retest = (
        previous.close > range_high
        and current.low <= range_high * (Decimal("1") + tolerance)
        and current.close > range_high
        and current.close > vwap
    )
    bearish_retest = (
        previous.close < range_low
        and current.high >= range_low * (Decimal("1") - tolerance)
        and current.close < range_low
        and current.close < vwap
    )
    if not bullish_retest and not bearish_retest:
        return ORBSignal("WAIT", None, 1, timestamp, "no_valid_breakout_retest",
                         range_high, range_low, vwap, rel_volume, current.close)
    if rel_volume < config.min_relative_volume:
        return ORBSignal("WAIT", "BULLISH" if bullish_retest else "BEARISH", 1, timestamp,
                         "relative_volume_below_threshold", range_high, range_low, vwap,
                         rel_volume, current.close, range_high if bullish_retest else range_low)
    direction: Direction = "BULLISH" if bullish_retest else "BEARISH"
    level = range_high if bullish_retest else range_low
    distance = abs(current.close - level) / level
    if distance > config.max_chase_fraction:
        return ORBSignal("WAIT", direction, 1, timestamp, "breakout_too_extended_do_not_chase",
                         range_high, range_low, vwap, rel_volume, current.close, level)
    return ORBSignal("CONFIRMED_CANDIDATE", direction, 1, timestamp,
                     "one_minute_retest_confirmed_research_only", range_high, range_low,
                     vwap, rel_volume, current.close, level)
