import argparse
import logging
import os

from dotenv import load_dotenv

from src.candles import CandleBuilder
from src.feeds import FyersTickSource, SampleTickSource, load_watchlist

load_dotenv()
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("zudo.market_data")


def run(source) -> None:
    builder = CandleBuilder(interval_minutes=5)
    emitted = 0
    try:
        for tick in source:
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
    args = parser.parse_args()
    if args.source == "sample":
        run(SampleTickSource())
        return
    app_id, token = os.getenv("FYERS_APP_ID", "").strip(), os.getenv("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not token:
        raise SystemExit("Missing FYERS_APP_ID or FYERS_ACCESS_TOKEN in .env")
    symbols = load_watchlist()
    logger.info("Starting FYERS read-only feed for %s", symbols)
    run(FyersTickSource(app_id, token, symbols))


if __name__ == "__main__":
    main()
