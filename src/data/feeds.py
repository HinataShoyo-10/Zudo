import logging
import queue
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Protocol
from zoneinfo import ZoneInfo

from src.data.models import Tick

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
        socket = None

        def on_message(message):
            try:
                if not isinstance(message, dict):
                    logger.debug("Ignoring non-dict FYERS message: %r", message)
                    return
                symbol, price = message.get("symbol"), message.get("ltp")
                if not symbol or price is None:
                    logger.debug("Ignoring message without symbol/ltp: %r", message)
                    return
                raw_ts = message.get("last_traded_time") or message.get("tt")
                try:
                    timestamp = datetime.fromtimestamp(float(raw_ts), tz=IST) if raw_ts else datetime.now(IST)
                except (TypeError, ValueError, OSError, OverflowError):
                    timestamp = datetime.now(IST)
                updates.put(Tick(str(symbol), timestamp, Decimal(str(price)), 0))
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
            yield updates.get()
