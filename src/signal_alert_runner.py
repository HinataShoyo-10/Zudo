"""Read-only bridge from completed market candles to Telegram signal alerts."""
from __future__ import annotations

import logging
from datetime import date
from typing import Callable

from src.candles import CandleBuilder
from src.feeds import IST
from src.models import Candle, Tick
from src.orb_signals import (
    ORBConfig,
    ORBSignal,
    evaluate_completed_candles,
    evaluate_early_warning,
    evaluate_one_minute_retest,
)
from src.telegram_alerts import TelegramAlerter, format_signal_alert

logger = logging.getLogger(__name__)


class SignalAlertRunner:
    """Evaluate 1m/5m ORB paths and send only warnings/candidates, never orders."""

    def __init__(
        self,
        alerter: TelegramAlerter,
        config: ORBConfig = ORBConfig(),
        on_signal: Callable[[ORBSignal], None] | None = None,
    ) -> None:
        self.alerter = alerter
        self.config = config
        self.on_signal = on_signal
        self._builders = {1: CandleBuilder(1), 5: CandleBuilder(5)}
        self._candles: dict[int, dict[str, list[Candle]]] = {1: {}, 5: {}}
        self._seen_alerts: set[tuple] = set()
        self._active_date: date | None = None

    def process_tick(self, tick: Tick) -> list[ORBSignal]:
        """Update candle builders and evaluate any newly available signal conditions."""
        session_date = tick.timestamp.astimezone(IST).date()
        if self._active_date != session_date:
            self._seen_alerts.clear()
            self._active_date = session_date
            # Histories are session-scoped; prevent stale prior-day candles being reused.
            self._candles = {1: {}, 5: {}}
            self._builders = {1: CandleBuilder(1), 5: CandleBuilder(5)}

        emitted: list[ORBSignal] = []
        completed_by_interval: dict[int, Candle] = {}
        for interval, builder in self._builders.items():
            candle = builder.update(tick)
            if candle is not None:
                history = self._candles[interval].setdefault(candle.symbol, [])
                history.append(candle)
                # Bound memory; evaluator independently filters to the latest session.
                self._candles[interval][candle.symbol] = history[-400:]
                completed_by_interval[interval] = candle

        history_5m = self._candles[5].get(tick.symbol, [])
        if history_5m:
            warning = evaluate_early_warning(
                tick.price, tick.timestamp, history_5m, self.config
            )
            if warning.status == "EARLY_WARNING":
                emitted.append(warning)
                self._send_once(warning, tick.symbol)

        candle_5m = completed_by_interval.get(5)
        if candle_5m is not None:
            signal = evaluate_completed_candles(
                self._candles[5][candle_5m.symbol], 5, self.config
            )
            if signal.status == "CONFIRMED_CANDIDATE":
                emitted.append(signal)
                self._send_once(signal, candle_5m.symbol)

        candle_1m = completed_by_interval.get(1)
        if candle_1m is not None:
            history_1m = self._candles[1][candle_1m.symbol]
            for signal in (
                evaluate_completed_candles(history_1m, 1, self.config),
                evaluate_one_minute_retest(history_1m, self.config),
            ):
                if signal.status == "CONFIRMED_CANDIDATE":
                    emitted.append(signal)
                    self._send_once(signal, candle_1m.symbol)
        return emitted

    def _send_once(self, signal: ORBSignal, symbol: str) -> None:
        # Warnings are deduplicated per direction/session; candidate alerts per bar/path.
        local_date = signal.timestamp.astimezone(IST).date()
        if signal.status == "EARLY_WARNING":
            key = (local_date, signal.status, signal.timeframe_minutes, signal.direction)
        else:
            key = (
                local_date, signal.status, signal.timeframe_minutes, signal.direction,
                signal.timestamp.isoformat(), signal.reason,
            )
        if key in self._seen_alerts:
            return
        self._seen_alerts.add(key)
        if self.on_signal is not None:
            self.on_signal(signal)
        try:
            self.alerter.send_text(format_signal_alert(signal, symbol=symbol))
            logger.info(
                "Telegram alert sent status=%s symbol=%s timeframe=%sm direction=%s",
                signal.status, symbol, signal.timeframe_minutes, signal.direction,
            )
        except Exception:
            # A Telegram outage must not terminate the market-data loop.
            logger.exception("Telegram alert delivery failed; market-data processing continues")
