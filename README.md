# Zudo

Zudo is a research and **paper-trading-only** toolkit for Indian cash equities using FYERS market data. It includes a historical backtester, a two-stage watchlist scanner, a local dashboard, and a persistent forward paper-trade ledger.

> **Important:** Zudo does not place live broker orders. A scanner opportunity and a paper fill are simulations—not recommendations, guaranteed fills, or evidence that a strategy will make money. Do not rely on this software for passive income or unattended live trading.

## What the system does

1. Before the session, ranks up to 50 configured stocks on completed daily candles.
2. After the open, fetches five-minute candles for the qualified shortlist (up to five names by default).
3. Evaluates the latest completed candle for opening-range breakout and trend-pullback setups.
4. If multiple setups qualify, queues only the highest-scored setup for the paper account.
5. Simulates entry at the next completed five-minute bar's open and tracks stop, target, and end-of-session exit.
6. Persists paper-account state locally so a dashboard rerun does not lose its ledger.

The system is not a continuously hosted trading service. Its automatic refresh only runs while the Streamlit dashboard remains open, connected, and awake. Closing the dashboard stops market polling.

## Requirements

- Python 3.11 or newer
- A FYERS API app and market-data access token for live historical-data fetches
- Internet connectivity for FYERS data requests
- Historical CSV candles for offline scans and backtests

FYERS access, symbol permissions, API quotas, market holidays, and exact API behavior depend on the user's account and current broker configuration.

## Install

Run these commands from the repository root.

### Linux, macOS, or Codespaces

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### Windows PowerShell

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the tests after installation:

```bash
python -m pytest -q
```

## FYERS credentials and authentication

Credentials are required only for FYERS data fetches. Offline backtests and local-CSV scans need no FYERS credentials.

1. Copy `.env.example` to `.env` in the repository root.
2. Set `FYERS_APP_ID`, `FYERS_SECRET_ID`, `FYERS_REDIRECT_URI`, and `FYERS_ACCESS_TOKEN` using values from your own FYERS app.
3. Keep `.env` private. Confirm Git ignores it:

   ```bash
   git check-ignore .env
   ```

Never commit or paste credentials, authorization URLs, callback URLs, authorization codes, or tokens into chat, issues, source files, or terminal arguments.

### Optional local login helper

The local Streamlit helper can start the FYERS authentication flow and save the returned access token in `.env`:

```bash
python -m streamlit run scripts/fyers_auth_web.py --server.address 127.0.0.1 --server.port 8501
```

Use it only on your own machine or a private Codespaces session. Authenticate directly on the official FYERS website; never enter your FYERS password, PIN, OTP, or authenticator code into Zudo. The redirect URI must match exactly what is registered in the FYERS app. If the callback page reports a connection error, check whether the browser address bar still contains the callback URL, then provide that full callback to the helper. More details and safety notes are in [docs/FYERS_AUTH_WEB.md](docs/FYERS_AUTH_WEB.md).

Access tokens expire or become invalid. When FYERS rejects a request, authenticate again and update the local `.env`. Do not assume refresh-token behavior unless verified with current FYERS documentation.

## Run the scanner dashboard

From the repository root:

```bash
python -m streamlit run src/dashboard/app.py
```

Open the local URL printed by Streamlit (normally `http://localhost:8501`). Keep the terminal/process and dashboard running during the session.

### 1. Set paper-account assumptions

The sidebar provides the paper account and risk controls:

- Starting capital defaults to **₹50,000**.
- Risk per trade defaults to **0.5%** of paper equity.
- Maximum exposure defaults to **20%** of paper equity.
- Flat simulated fee defaults to ₹0 per side.
- Slippage defaults to 5 basis points per side.

These are simulation settings, not recommended position sizes. Zero flat fees do not mean real trading is free: taxes, exchange charges, SEBI fees, stamp duty, spread, and other costs may apply.

### 2. Rank stocks before market open

1. Select **Premarket ranking**.
2. Review/edit the FYERS symbols in `config/watchlist.txt` (maximum 50).
3. Choose a shortlist size (the default is five).
4. Click **Fetch daily history and rank watchlist**.

The app fetches about 180 calendar days of daily data for each stock, plus `NSE:NIFTY50-INDEX` for benchmark context. Calls are serial and rate-paced. A 50-stock fetch therefore takes at least around 50 seconds, plus network/API overhead.

The score uses completed candles only and is a relative heuristic—not a probability of winning. Ranking inputs include EMA20/EMA50 trend, five- and 20-session momentum, prior-day relative volume, 20-session range position, ATR%, RSI(14), relative strength versus NIFTY, prior-day OHLC, and an average traded-value proxy. NIFTY trend is contextual and does not itself veto stock candidates.

The watchlist is an editable starter universe, not a dynamically maintained NIFTY 50 constituent list. Review symbols against the current FYERS symbol master and index constituents. If any required symbol history fails, the fetch reports the failure; it does not present partial coverage as a complete ranking.

### 3. Scan after the open and paper trade

1. Select **After-open paper scan**.
2. Confirm the premarket ranking is recent and the shortlist is correct.
3. Keep **Auto-check every 5 minutes** enabled (default), or click **Fetch today's 5-minute candles and check setups** manually.
4. Review the results, no-trade reasons, chart, and **Paper trade ledger**.

The dashboard only fetches intraday history for shortlisted symbols. It checks the latest completed five-minute candle for the opening-range breakout and trend-pullback setups. It shows previous-day high/low/close and the opening gap where the required data is available.

When there are multiple qualifying setups, Zudo selects the highest setup score and allows at most one trade per session:

- It queues a paper entry after a completed signal candle.
- It waits until the following five-minute candle is complete, then simulates an entry at that candle's open with configured slippage.
- Position quantity is bounded by the paper risk, exposure, and available-cash limits.
- The stop, target, and end-of-session square-off are simulated from OHLC bars.
- If a candle reaches both stop and target, the stop is assumed to have filled first.
- The ledger shows pending entries, open positions, closed trades, and execution decisions.

These are simplified OHLC-based fills. Historical bars cannot establish executable bid/ask, queue position, latency, or the true order of intrabar prices. Gaps and illiquidity can cause actual losses beyond planned risk. The paper ledger is saved in the Git-ignored `data/scanner/paper_session.json`.

**Polling is not a background service.** Automatic checks require the dashboard to stay open, the machine/session to remain awake, and the app to remain connected. The app checks weekday and session hours but does not independently verify NSE holidays. Do not treat it as unattended execution.

### Paper account persistence and reset

The paper ledger persists between dashboard reruns. It is separate from the historical backtest and is not synchronized with a broker account.

To intentionally reset the simulated account:

1. Stop the Streamlit app.
2. Back up `data/scanner/paper_session.json` if you want to keep its history.
3. Remove or rename only that paper-state file.
4. Start the dashboard again and set the desired starting capital.

Do not reset the ledger while the app is running. This is a manual local operation; the software does not automatically erase trade history.

## Offline backtesting

Backtesting uses local OHLCV CSVs and does not require a FYERS connection. CSVs must contain:

```text
timestamp,open,high,low,close,volume
```

Use ISO-8601 timestamps. Naive timestamps are treated as IST by the loader. Records must be ordered and valid. The ORB mode requires complete regular NSE five-minute sessions (09:15 through 15:25 IST); incomplete/irregular sessions are skipped.

The included `data/example_ohlcv.csv` is synthetic and exists only as a smoke test. It is not real market evidence.

### Single-symbol backtest

```bash
python -m src.backtest \
  --csv data/your_5m_candles.csv \
  --strategy orb \
  --cash 50000 \
  --risk-fraction 0.005 \
  --max-exposure-fraction 0.20 \
  --fee-per-side 0 \
  --slippage-bps 5 \
  --out backtest_output
```

Strategies:

- `orb` (default): filtered, long-only opening-range breakout with ATR-based stop and target, risk/exposure sizing, and intraday square-off.
- `ema`: legacy long-only EMA crossover baseline; it is retained for comparison, not presented as a validated strategy.

ORB filters include trend alignment, price above session VWAP, breakout distance, candle body strength, recent volume expansion, and a maximum opening-range width. Filter thresholds are exposed as command-line options; tuning them on a final holdout period would bias the result.

Results are written to `trades.csv`, `equity_curve.csv`, and `summary.json`. For same-bar stop/target touches, the backtester assumes the stop first. Slippage and fees are estimates; the default zero flat fee excludes applicable taxes and charges.

### Multi-symbol historical portfolio simulation

```bash
python -m src.paper_portfolio \
  --asset NSE:SBIN-EQ=data/sbin_5m.csv \
  --asset NSE:INFY-EQ=data/infy_5m.csv \
  --cash 50000 \
  --risk-fraction 0.005 \
  --max-exposure-fraction 0.20 \
  --fee-per-side 0 \
  --slippage-bps 5 \
  --out paper_portfolio_output
```

This is an offline historical replay using a shared paper account. It writes a trade ledger, candidate decisions, an equity curve, and a summary. It is not the same as the dashboard's forward paper session.

## Fetch historical stock data from FYERS

Set `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` in `.env`, then run:

```bash
python -m src.fyers_history \
  --symbol NSE:SBIN-EQ \
  --resolution 5 \
  --from-date 2025-01-01 \
  --to-date 2025-01-31 \
  --out data/sbin_5m.csv
```

`--resolution` supports FYERS candle intervals including `1`, `5`, `15`, `60`, and `D`. The downloader uses history endpoints only. Downloaded market data and scanner state are ignored by Git; validate downloaded CSVs before relying on them.

## Scan local candle files without FYERS

```bash
python -m src.scanning.cli \
  --asset NSE:SBIN-EQ=data/sbin_5m.csv \
  --asset NSE:INFY-EQ=data/infy_5m.csv \
  --cash 50000 \
  --risk-fraction 0.005 \
  --out scan_output
```

The CLI reports latest-bar paper candidates and no-trade reasons to `scan.csv` and `scan.json`. It does not execute trades or manage a persistent paper account.

## Options research

The repository contains read-only expired F&O data utilities and research backtests. Options are not part of the current five-stock dashboard paper workflow and are not live-traded.

- Follow [docs/OPTIONS_DATA.md](docs/OPTIONS_DATA.md) for fetching and validating expired NIFTY contract history and sample research commands.
- See [docs/TRADING_BRAIN.md](docs/TRADING_BRAIN.md) for deterministic risk gates and validation status.
- Options data requests can have separate and limited FYERS quotas. Verify current entitlements before making requests.

## Market feed prototype

The sample/live feed entry point is:

```bash
python -m src.main --source sample
python -m src.main --source fyers
```

The sample source is synthetic. The FYERS WebSocket adapter is a data-feed prototype and is not validated for unattended operation. Neither command places orders.

## Tests and troubleshooting

Run all tests:

```bash
python -m pytest -q
```

Common problems:

- **FYERS authentication/data error:** verify that the token is current, app ID and token belong together, and the account has access to the endpoint/symbol.
- **No ranking or incomplete history:** confirm watchlist symbols and history permissions. A failed symbol is reported instead of silently omitted.
- **No after-open scan:** verify that the ranking is recent, at least one symbol is shortlisted, it is within weekday market hours, and the required prior-day fields exist. The app does not verify exchange holidays.
- **No opportunity:** `NO_TRADE` is an intended scanner outcome. Inspect the reported filters; do not weaken thresholds just to force a trade.
- **Dashboard not refreshing:** keep Streamlit running and the browser open. Auto-refresh only runs while the app process is alive and the browser session is active.
- **Paper ledger is unexpected:** stop the app and back up `data/scanner/paper_session.json` before inspecting or resetting it.
- **Missing Python packages:** activate the project virtual environment, then run `python -m pip install -r requirements.txt`.

## Project layout

```text
config/watchlist.txt           Editable FYERS watchlist (up to 50 symbols)
src/backtesting/               Historical stock and options backtest engines
src/data/                      Candle ingestion, FYERS adapters, and validation
src/dashboard/app.py           Streamlit ranking, after-open scan, and paper ledger
src/paper/portfolio.py          Offline multi-symbol historical portfolio replay
src/paper/session.py            Persistent forward paper-session execution
src/risk/                       Risk gates for research workflows
src/scanning/                   Premarket ranker, intraday scanner, FYERS workflow
src/strategies/                 Technical indicators and strategy components
tests/                          Automated tests
docs/                           Authentication, options research, and roadmap guides
```

## Known limitations and safety

- No edge or profit is guaranteed. Backtests do not establish future performance.
- The scanner score is a heuristic, not a calibrated probability.
- Company news/events, GIFT NIFTY, Bank NIFTY/India VIX, live indicative premarket prices, sector-relative strength, and executable bid/ask/depth are not integrated.
- The traded-value metric is a coarse close-times-volume proxy, not verified turnover or proof of liquidity.
- Historical OHLC cannot reproduce real fill quality, spread, latency, or intrabar sequencing.
- Current dashboard session filtering does not independently check exchange holidays.
- FYERS symbol coverage, data permissions, and API quotas must be checked by the user.
- The project is paper-only. There is no live order placement.

See [docs/RESEARCH_ROADMAP.md](docs/RESEARCH_ROADMAP.md) for the staged validation gates required before considering any future deployment.
