import logging
import queue
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Protocol
from zoneinfo import ZoneInfo

from src.models import Tick

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger(__name__)


class TickSource(Protocol):
    def __iter__(self) -> Iterator[Tick]: ...


class SampleTickSource:
    """Deterministic synthetic updates for testing; not market data."""

    def __init__(self, symbol: str = "SAMPLE", count: int = 40) -> None:
        self.symbol, self.count = symbol, count

    def __iter__(self) -> Iterator[Tick]:
        start = datetime(2026, 10, 12, 9, 15, tzinfo=IST)
        base = Decimal("100.00")
        for i in range(self.count):
            cents = ((i * 7) % 13) - 6 + (i // 10)
            yield Tick(self.symbol, start + timedelta(seconds=15 * i),
                       base + Decimal(cents) / Decimal("100"), 0)


def load_watchlist(path: str = "config/watchlist.txt") -> list[str]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    symbols = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    if not symbols:
        raise ValueError(f"No symbols configured in {path}")
    return symbols


def normalize_fyers_message(
    message: dict,
    last_cumulative_volume: dict[str, int],
) -> Tick | None:
    """Normalize a full-mode FYERS SymbolUpdate message.

    FYERS' vol_traded_today is treated as a cumulative counter. The first observed
    value and counter resets are marked invalid because their true per-candle delta
    cannot be reconstructed from that single message. Missing volume is never
    silently converted into valid zero volume.
    """
    if not isinstance(message, dict):
        return None
    symbol, price = message.get("symbol"), message.get("ltp")
    if not symbol or price is None:
        return None
    raw_ts = message.get("last_traded_time") or message.get("tt")
    try:
        timestamp = datetime.fromtimestamp(float(raw_ts), tz=IST) if raw_ts else datetime.now(IST)
    except (TypeError, ValueError, OSError, OverflowError):
        timestamp = datetime.now(IST)

    volume_valid = False
    volume_delta = 0
    raw_volume = message.get("vol_traded_today")
    if raw_volume is not None:
        try:
            cumulative = int(raw_volume)
            if cumulative >= 0:
                previous = last_cumulative_volume.get(str(symbol))
                last_cumulative_volume[str(symbol)] = cumulative
                if previous is not None and cumulative >= previous:
                    volume_delta = cumulative - previous
                    volume_valid = True
                elif previous is not None and cumulative < previous:
                    logger.warning("FYERS cumulative volume reset for %s; marking this update invalid", symbol)
        except (TypeError, ValueError, OverflowError):
            logger.debug("Invalid FYERS volume field for %s: %r", symbol, raw_volume)

    return Tick(str(symbol), timestamp, Decimal(str(price)), volume_delta, volume_valid)


class FyersTickSource:
    """Initial FYERS v3 Data WebSocket adapter. Read-only; no order API usage."""

    def __init__(self, app_id: str, access_token: str, symbols: list[str]) -> None:
        if not app_id or not access_token:
            raise ValueError("FYERS_APP_ID and FYERS_ACCESS_TOKEN are required")
        if not symbols:
            raise ValueError("At least one symbol is required")
        self.app_id, self.access_token, self.symbols = app_id, access_token, symbols

    def __iter__(self) -> Iterator[Tick]:
        from fyers_apiv3.FyersWebsocket import data_ws

        updates: queue.Queue[Tick] = queue.Queue()
        last_cumulative_volume: dict[str, int] = {}
        last_tick_log: dict[str, float] = {}
        last_wait_log = 0.0
        socket = None

        def on_message(message):
            try:
                tick = normalize_fyers_message(message, last_cumulative_volume)
                if tick is None:
                    logger.debug("Ignoring unsupported FYERS message: %r", message)
                    return
                if not tick.volume_valid:
                    logger.debug("Volume not yet validated for %s; signals must abstain", tick.symbol)
                updates.put(tick)
                now = time.monotonic()
                previous_log = last_tick_log.get(tick.symbol, 0.0)
                if now - previous_log >= 60:
                    logger.info(
                        "FYERS tick received symbol=%s timestamp=%s price=%s volume_delta=%s volume_valid=%s",
                        tick.symbol, tick.timestamp.isoformat(), tick.price, tick.volume, tick.volume_valid,
                    )
                    last_tick_log[tick.symbol] = now
            except Exception:
                logger.exception("Could not normalize FYERS message: %r", message)

        def on_connect():
            logger.info("FYERS socket connected; subscribing to %d symbols", len(self.symbols))
            socket.subscribe(symbols=self.symbols, data_type="SymbolUpdate")
            socket.keep_running()

        def on_error(error):
            logger.error("FYERS WebSocket error: %s", error)

        def on_close(message):
            logger.warning("FYERS WebSocket closed: %s", message)

        Path("logs").mkdir(parents=True, exist_ok=True)
        socket = data_ws.FyersDataSocket(
            access_token=f"{self.app_id}:{self.access_token}",
            log_path="logs",
            litemode=False,
            write_to_file=False,
            reconnect=True,
            on_connect=on_connect,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        threading.Thread(target=socket.connect, daemon=True).start()
        while True:
            try:
                yield updates.get(timeout=5)
            except queue.Empty:
                now = time.monotonic()
                if now - last_wait_log >= 60:
                    logger.info(
                        "Waiting for FYERS ticks; socket may be connected but no updates have arrived yet. "
                        "Check market hours, symbol subscription, and feed permissions."
                    )
                    last_wait_log = now
