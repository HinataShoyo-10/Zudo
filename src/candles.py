from datetime import datetime, timedelta
from typing import Optional

from src.models import Candle, Tick


def floor_time(ts: datetime, interval_minutes: int = 5) -> datetime:
    if ts.tzinfo is None or ts.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    if interval_minutes <= 0:
        raise ValueError("interval_minutes must be positive")
    minute = (ts.minute // interval_minutes) * interval_minutes
    return ts.replace(minute=minute, second=0, microsecond=0)


class CandleBuilder:
    """Aggregate ordered price updates and validated volume deltas into candles."""

    def __init__(self, interval_minutes: int = 5) -> None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        self.interval_minutes = interval_minutes
        self.interval = timedelta(minutes=interval_minutes)
        self._current: dict[str, dict] = {}
        self._last_timestamp: dict[str, datetime] = {}

    def update(self, tick: Tick) -> Optional[Candle]:
        last = self._last_timestamp.get(tick.symbol)
        if last is not None and tick.timestamp < last:
            raise ValueError(f"out-of-order tick for {tick.symbol}")
        self._last_timestamp[tick.symbol] = tick.timestamp
        start = floor_time(tick.timestamp, self.interval_minutes)
        current = self._current.get(tick.symbol)

        if current is None:
            self._current[tick.symbol] = self._new_state(tick, start)
            return None
        if start < current["start"]:
            raise ValueError("tick belongs to an already-closed candle")
        if start == current["start"]:
            current["high"] = max(current["high"], tick.price)
            current["low"] = min(current["low"], tick.price)
            current["close"] = tick.price
            current["ticks"] += 1
            current["volume"] += tick.volume if tick.volume_valid else 0
            current["volume_valid"] = current["volume_valid"] and tick.volume_valid
            return None

        completed = self._to_candle(tick.symbol, current)
        self._current[tick.symbol] = self._new_state(tick, start)
        return completed

    def flush(self, symbol: str) -> Optional[Candle]:
        current = self._current.pop(symbol, None)
        return None if current is None else self._to_candle(symbol, current)

    def _new_state(self, tick: Tick, start: datetime) -> dict:
        return {
            "start": start, "end": start + self.interval,
            "open": tick.price, "high": tick.price, "low": tick.price,
            "close": tick.price, "volume": tick.volume if tick.volume_valid else 0,
            "ticks": 1, "volume_valid": tick.volume_valid,
        }

    @staticmethod
    def _to_candle(symbol: str, state: dict) -> Candle:
        return Candle(symbol=symbol, **state)
