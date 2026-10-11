from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
import pytest

from src.candles import CandleBuilder, floor_time
from src.models import Tick

IST = ZoneInfo("Asia/Kolkata")


def make_tick(minute, price, second=0):
    return Tick("TEST", datetime(2026, 10, 12, 9, minute, second, tzinfo=IST), Decimal(price), 0)


def test_floor_time():
    assert floor_time(datetime(2026, 10, 12, 9, 19, 20, tzinfo=IST)).minute == 15


def test_ohlc_and_rollover():
    builder = CandleBuilder()
    assert builder.update(make_tick(15, "100.0")) is None
    assert builder.update(make_tick(16, "102.0")) is None
    candle = builder.update(make_tick(20, "101.0"))
    assert candle is not None
    assert (candle.open, candle.high, candle.low, candle.close) == (
        Decimal("100.0"), Decimal("102.0"), Decimal("100.0"), Decimal("102.0"))
    assert candle.volume == 0
    assert candle.ticks == 2


def test_flush():
    builder = CandleBuilder()
    builder.update(make_tick(15, "100"))
    assert builder.flush("TEST").close == Decimal("100")
    assert builder.flush("TEST") is None


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError):
        Tick("TEST", datetime(2026, 10, 12, 9, 15), Decimal("100"))


def test_out_of_order_rejected():
    builder = CandleBuilder()
    builder.update(make_tick(16, "100", 10))
    with pytest.raises(ValueError, match="out-of-order"):
        builder.update(make_tick(16, "101", 5))
