from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.models import Candle
from src.orb_signals import ORBConfig, evaluate_completed_candles, evaluate_early_warning

IST = ZoneInfo("Asia/Kolkata")


def make_candle(minute, o, h, l, c, volume, interval=5, day=12):
    start = datetime(2026, 10, day, 9, 15, tzinfo=IST) + timedelta(minutes=minute - 15)
    return Candle("NSE:NIFTY50-INDEX", start, start + timedelta(minutes=interval),
                  Decimal(str(o)), Decimal(str(h)), Decimal(str(l)), Decimal(str(c)),
                  volume, 5)


def five_minute_history(last_close="101.2", last_volume=200):
    bars = [
        make_candle(15, 99, 100, 98, 99, 100),
        make_candle(20, 99, 101, 99, 100, 100),
        make_candle(25, 100, 100, 99, 99.5, 100),
    ]
    # Ten completed post-range bars establish relative-volume warm-up.
    for minute in range(30, 80, 5):
        bars.append(make_candle(minute, 99.5, 100, 99, 99.5, 100))
    bars.append(make_candle(80, 100, 102, 100, last_close, last_volume))
    return bars


def test_five_minute_confirmation_requires_filters_and_warmup():
    signal = evaluate_completed_candles(five_minute_history(), 5)
    assert signal.status == "CONFIRMED_CANDIDATE"
    assert signal.direction == "BULLISH"
    assert signal.reason == "five_minute_breakout_confirmed"
    assert signal.invalidation_level == Decimal("101")


def test_chase_filter_rejects_extended_breakout():
    bars = five_minute_history(last_close="102", last_volume=200)
    signal = evaluate_completed_candles(bars, 5)
    assert signal.status == "WAIT"
    assert signal.reason == "breakout_too_extended_do_not_chase"


def test_volume_filter_rejects_low_volume_breakout():
    signal = evaluate_completed_candles(five_minute_history(last_volume=100), 5)
    assert signal.status == "WAIT"
    assert signal.reason == "relative_volume_below_threshold"


def test_early_warning_is_not_a_confirmed_candidate():
    bars = [
        make_candle(15, 99, 100, 98, 99, 100),
        make_candle(20, 99, 101, 99, 100, 100),
        make_candle(25, 100, 100, 99, 99.5, 100),
    ]
    warning = evaluate_early_warning(Decimal("101.5"), bars[-1].end, bars)
    assert warning.status == "EARLY_WARNING"
    assert warning.direction == "BULLISH"
    assert "do_not_enter" in warning.reason


def test_missing_volume_returns_wait():
    bars = five_minute_history()
    bars[-1] = make_candle(80, 100, 102, 100, 101, 0)
    signal = evaluate_completed_candles(bars, 5)
    assert signal.status == "WAIT"
    assert signal.reason == "vwap_unavailable_volume_unverified"


def test_empty_input_returns_wait():
    signal = evaluate_completed_candles([], 5)
    assert signal.status == "WAIT"
    assert signal.reason == "no_session_candles"
