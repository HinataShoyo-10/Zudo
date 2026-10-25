import argparse
import logging
import os

from dotenv import load_dotenv

from src.candles import CandleBuilder
from src.feeds import FyersTickSource, SampleTickSource, load_watchlist
from src.signal_alert_runner import SignalAlertRunner
from src.telegram_alerts import TelegramAlerter, load_telegram_config

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("zudo.market_data")


def run(source, alert_runner: SignalAlertRunner | None = None) -> None:
    builder = CandleBuilder(interval_minutes=5)
    emitted = 0
    try:
        for tick in source:
            if alert_runner is not None:
                alert_runner.process_tick(tick)
            candle = builder.update(tick)
            if candle:
                emitted += 1
                logger.info("CANDLE symbol=%s start=%s O=%s H=%s L=%s C=%s V=%s updates=%s",
                            candle.symbol, candle.start.isoformat(), candle.open, candle.high,
                            candle.low, candle.close, candle.volume, candle.ticks)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        logger.info("Stopped; emitted=%d candles. No orders placed.", emitted)


def main() -> None:
    parser = argparse.ArgumentParser(description="Zudo read-only market-data starter")
    parser.add_argument("--source", choices=["sample", "fyers"], default="sample")
    parser.add_argument("--telegram-alerts", action="store_true",
                        help="send early warnings and confirmed candidates to Telegram; never places orders")
    args = parser.parse_args()
    alert_runner = None
    if args.telegram_alerts:
        try:
            bot_token, chat_id = load_telegram_config()
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        alert_runner = SignalAlertRunner(TelegramAlerter(bot_token, chat_id))
        logger.info("Telegram signal alerts enabled; no order-placement capability is used")
    if args.source == "sample":
        run(SampleTickSource(), alert_runner)
        return
    app_id, token = os.getenv("FYERS_APP_ID", "").strip(), os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not token:
        raise SystemExit("Missing FYERS_APP_ID or FYERS_ACCESS_TOKEN in .env")
    symbols = load_watchlist()
    logger.info("Starting FYERS read-only feed for %s", symbols)
    run(FyersTickSource(app_id, token, symbols), alert_runner)


if __name__ == "__main__":
    main()
