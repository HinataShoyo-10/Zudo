# Zudo — Indian Intraday Market Data & Backtesting

FYERS API v3 market-data starter plus an offline backtester and a read-only expired F&O data research pipeline. No order-placement code is included.

## Does backtesting require the FYERS API?

**No.** The stock backtester reads local historical OHLCV CSV files. FYERS API is optional if you want to download historical candles programmatically.

## Requirements

- Python 3.11+
- FYERS API v3 app and valid access token for FYERS market data
- Historical OHLCV data for offline research

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

The CSV must contain `timestamp,open,high,low,close,volume`. Timestamps should be ISO-8601; timestamps without a timezone are treated as IST. Rows must be strictly increasing with no duplicates.

```bash
python -m src.backtest --csv data/example_ohlcv.csv
python -m src.backtest --csv data/your_candles.csv
```

The included `data/example_ohlcv.csv` is synthetic and is only a software smoke test. It is not real market data or evidence of profitability.

Default strategy: long-only 9/21 EMA crossover, quantity 1, ₹100,000 starting equity, ₹20 estimated fee per side, and 5 basis points slippage per side. These are rough placeholders, not a complete representation of Indian market charges. Default backtests now enforce intraday session exits; output reports gross P&L, fees, slippage, net win rate, and drawdown separately.

```bash
python -m src.backtest --csv data/your_candles.csv --fast 5 --slow 20 --quantity 1 --cash 100000 --fee-per-side 20 --slippage-bps 5 --out backtest_output
```

Output contains `trades.csv`, `equity_curve.csv`, and `summary.json`. Before interpreting results, validate real charges, slippage, liquidity, timestamps, session boundaries, and out-of-sample performance. Results do not guarantee future returns.

## Download real historical stock candles from FYERS

Keep `.env` ignored by Git. Set `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` locally:

```bash
python -m src.fyers_history --symbol NSE:SBIN-EQ --resolution 5 --from-date 2025-01-01 --to-date 2025-01-31 --out data/sbin_5m.csv
```

The downloader uses the history endpoint only and never places orders.

## NIFTY expired options data (read-only research)

Use the separate documented workflow in [docs/OPTIONS_DATA.md](docs/OPTIONS_DATA.md). It uses FYERS' expired F&O endpoints to query expiry dates, list actual returned contract symbols, and download one contract's raw historical response. We intentionally save raw JSON until the current response schema has been inspected and tested.

FYERS' current documentation says Standard API access has a limited daily allowance for intraday expired F&O historical requests. Check the current plan limits before collecting data; do not bulk-loop through every strike. Source: [FYERS API rate limits](https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans).

## FYERS live market-data feed

```bash
python -m src.main --source sample
python -m src.main --source fyers
```

The sample feed is synthetic. The live WebSocket adapter still needs validation against actual FYERS callbacks before unattended use. Do not enable live trading.

## Layout

- `src/backtest.py`: offline CSV backtester with intraday exits and cost reporting
- `src/fyers_history.py`: standard historical OHLCV downloader
- `src/fyers_expired_fno.py`: read-only expired F&O research data client
- `src/validate_market_data.py`: one-command quality gate for the stock CSV and saved options API responses
- `src/options_backtest.py`: research-only NIFTY opening-range breakout simulator for one specified call contract\n- `src/options_dynamic_backtest.py`: research-only dynamic nearest-ATM call selection with deterministic brain gates
- `src/nifty_validation.py`: repeatable NIFTY historical validation report and slippage/strict-liquidity stress scenarios
- `docs/OPTIONS_DATA.md`: NIFTY expired-options data steps, limits, and safety notes
- `docs/RESEARCH_ROADMAP.md`: project validation gates
- `tests/`: unit tests

## Trading brain foundation

See [docs/TRADING_BRAIN.md](docs/TRADING_BRAIN.md) for the deterministic decision pipeline and safety gates. The brain gates are integrated into the dynamic options backtester. OHLCV-only research explicitly flags missing bid/ask as liquidity-unverified; use strict mode when point-in-time quotes are available and required.

### NIFTY historical validation and stress test

After you have the local NIFTY index CSV and expired-options manifest, run the same strategy across multiple slippage assumptions and a strict quote-availability scenario:

```bash
python -m src.nifty_validation \
  --index-csv data/fyers_expired/nifty_index_5m.csv \
  --contracts-manifest data/fyers_expired/nifty_calls_manifest.json \
  --out backtest_output_nifty_validation
```

Replace the index CSV path with the actual filename in your workspace. The manifest's option CSV paths are resolved relative to the manifest file. The report includes dataset coverage, per-scenario trades, P&L, and candidate diagnostics. It does not tune parameters or prove profitability. Outputs are ignored by Git.

## Safety and limitations

- The EMA crossover is only a baseline, not a validated strategy.
- Historical OHLC data alone cannot reconstruct real bid/ask fills or exact intrabar stop order.
- Costs must include all applicable brokerage and regulatory charges, not just a flat fee.
- Planned risk per trade is not guaranteed maximum loss during gaps or poor fills.
- No live orders are included in this project.
