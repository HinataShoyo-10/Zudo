# Zudo — Indian Intraday Market Data & Backtesting

FYERS API v3 market-data starter plus an **offline backtester**. No order-placement code is included.

## Does backtesting require the FYERS API?

**No.** The backtester reads local historical OHLCV CSV files and does not import or authenticate with FYERS. You can code and run it entirely in GitHub Codespaces. FYERS API is optional if you want to download historical candles programmatically; it is needed for this repository's live FYERS feed.

## Requirements

- Python 3.11+
- FYERS API v3 app and valid access token only for live market data
- Historical OHLCV CSV file for offline backtests

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
cp .env.example .env
```

## Offline backtesting (no API/token required)

The CSV must contain these columns: `timestamp,open,high,low,close,volume`. Timestamps should be ISO-8601; timestamps without a timezone are treated as India Standard Time (IST). Rows must be in strictly increasing timestamp order. Use real historical candles from a source you are authorized to use; the backtester does not download data.

```bash
python -m src.backtest --csv data/your_candles.csv
```

Defaults: long-only 9/21 EMA crossover, quantity 1, starting equity ₹100,000, estimated ₹20 fee per side, and 5 basis points slippage per side. These cost assumptions are configurable and are only rough placeholders, not a complete representation of all Indian market charges. Signals are calculated on candle close and filled at the next candle open; any open position is liquidated at the last candle close at the end of the test.

Example with custom settings:

```bash
python -m src.backtest --csv data/your_candles.csv --fast 5 --slow 20 --quantity 1 --cash 100000 --fee-per-side 20 --slippage-bps 5 --out backtest_output
```

Output directory contains:
- `trades.csv`: entry/exit prices and trade P&L after estimated costs
- `equity_curve.csv`: per-candle marked-to-market equity and drawdown
- `summary.json`: net P&L, return, trade count, win rate, profit factor and maximum drawdown

A tiny synthetic fixture is used in unit tests only. It is not evidence of strategy profitability. Before relying on results, validate data quality, corporate actions, instrument selection, realistic charges, slippage, liquidity, intraday square-off rules, and out-of-sample performance. Results do not guarantee future returns.

## Sample mode (live-data pipeline smoke test)

```bash
python -m src.main --source sample
```

Synthetic sample ticks are for testing code only; they are not market data.

## FYERS live mode

Set `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` in a local `.env` file and confirm symbols in `config/watchlist.txt`. Then run:

```bash
python -m src.main --source fyers
```

Never commit or share credentials. The current WebSocket adapter needs validation against actual FYERS callback payloads and your account before unattended use.

FYERS's History API can also provide historical candles, but that is an optional data-acquisition step, separate from running the offline backtester. See the [FYERS Data API support docs](https://support.fyers.in/portal/en/kb/fyers-api-integrations/fyers-api/api-v3/data-api).

## Layout

- `src/models.py`: timezone-aware Tick and Candle types
- `src/candles.py`: five-minute OHLC candle aggregation
- `src/feeds.py`: sample feed and FYERS WebSocket adapter
- `src/indicators.py`: EMA and VWAP helpers
- `src/backtest.py`: offline CSV backtesting engine
- `src/main.py`: market-data command-line entry point
- `tests/`: unit tests
- `config/watchlist.txt`: FYERS symbols

## Known limitations

- Backtester is long-only and uses a demonstration EMA crossover, not a validated trading strategy.
- Estimated fees/slippage are simplified; taxes, exchange charges, STT, GST, stamp duty, spread, market impact and other costs may not be fully represented.
- OHLC data cannot reveal the true order of intrabar price movements; this starter uses next-bar-open fills.
- The live feed adapter has not been validated against actual account callback payloads.
- Live adapter currently uses zero volume until interval-volume delta handling is validated. Do not use volume-based signals yet.
- No orders are placed and no account state is changed.
