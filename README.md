# Zudo Mini

A **read-only intraday market-data and signal research tool**. Zudo consumes FYERS market updates, builds candles, evaluates opening-range rules, and can send Telegram notifications. It does **not** place, modify, or cancel orders.

> Signals are experimental research candidates, not financial advice or proof of profitability. Validate market data, strategy behavior, slippage, charges, liquidity, and risk before considering any manual trade.

## What it does

- Connects to the FYERS v3 data WebSocket for symbols in `config/watchlist.txt`.
- Builds 1-minute and 5-minute candles.
- Evaluates a 15-minute opening range, VWAP, relative volume, breakout persistence, and retests.
- Sends early warnings and confirmed-candidate notifications to Telegram.
- Includes historical-data utilities, backtests, validation tools, sample data, and unit tests.

Default watchlist: `NSE:SBIN-EQ`. Check the current FYERS symbol master and configure the symbol you actually intend to monitor.

## How the analysis works

```text
FYERS WebSocket -> normalized ticks -> 1m/5m candles
                                     |
                                     v
                     opening range + VWAP + relative volume
                                     |
                                     v
                    WAIT / EARLY_WARNING / CONFIRMED_CANDIDATE
                                     |
                                     v
                       optional Telegram alert (no orders)
```

### Opening range and early warning

The session is interpreted in India time. The opening range is the high and low of the first **15 minutes from 09:15 IST**. The engine waits for complete, aligned opening candles. After the range is finished, a live price crossing above the high or below the low may emit `EARLY_WARNING`. This is only a heads-up; it does not mean the other filters passed.

### Confirmed 5-minute breakout

A completed 5-minute candle can produce a research candidate when:

1. It closes above the opening-range high (bullish) or below the low (bearish).
2. Price is on the aligned side of session VWAP: above for bullish, below for bearish.
3. Relative volume is at least **1.5x** the preceding **10** post-range bars.
4. The close is not more than **0.3%** beyond the range boundary; extended breakouts are rejected instead of chased.

These are provisional defaults, not tuned parameters. The volume comparison is a simple rolling baseline and is not time-of-day normalized.

### Separate 1-minute paths

The runner also evaluates two independent 1-minute hypotheses:

- **Persistent breakout:** by default, two consecutive completed 1-minute closes outside the opening range, with VWAP, relative-volume, and chase filters.
- **Breakout retest:** the previous candle has broken the range; the current completed candle retests the boundary within a default **0.15%** tolerance, then closes back outside with VWAP alignment, relative-volume confirmation, and no excessive extension.

Backtest each path independently. One path passing does not validate the others.

### Volume quality and abstention

FYERS `vol_traded_today` is treated as a cumulative counter. Zudo computes deltas and marks the first observation or a counter reset as unverified. VWAP and relative-volume filters require verified positive volume. Incomplete candles, unavailable volume/VWAP, insufficient history, VWAP misalignment, and overextended breakouts return `WAIT` with a reason rather than forcing a candidate.

The actual FYERS WebSocket fields and volume semantics still need verification using sanitized live samples. A connected socket alone does not prove the data or signals are correct.

### Meaning of alerts and options limitations

- `EARLY_WARNING`: boundary crossed; not confirmed.
- `CONFIRMED_CANDIDATE`: a configured research rule passed; not a recommendation or executed trade.
- `WAIT`: required data or conditions are not satisfied.

The opening-range boundary is a reference/invalidation level, not a guaranteed stop. Index candles do not select an options contract. Before any manual options decision, independently check expiry, strike, lot size, live premium, bid/ask spread, liquidity, quote timestamp, slippage, all charges, and maximum loss. Reject stale/missing quotes rather than guessing.

Implementation details: `src/orb_signals.py`, `src/signal_alert_runner.py`, and `docs/BALANCED_SIGNAL_ENGINE.md`.

## Requirements

- Python 3.10+ recommended.
- A FYERS developer app configured for the FYERS v3 API.
- A Telegram bot and destination chat (optional if running without alerts).
- Git and a terminal; GitHub Codespaces is supported.

## First-time setup

### 1. Clone and install

```bash
git clone https://github.com/HinataShoyo-10/Zudo-mini.git
cd Zudo-mini
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 2. Create local credentials

```bash
cp .env.example .env
```

Edit `.env` locally; never commit or share it. Configure:

```dotenv
FYERS_APP_ID=your_fyers_app_id
FYERS_SECRET_ID=your_fyers_app_secret
FYERS_REDIRECT_URI=https://127.0.0.1/
FYERS_ACCESS_TOKEN=
FYERS_ACCESS_TOKEN_UPDATED_AT=
LOG_LEVEL=INFO

Telegram_bot_token=your_telegram_bot_token
TELEGRAM_CHAT_ID=your_telegram_chat_id
```

The redirect URI must exactly match the value registered in the FYERS developer dashboard. The Telegram token may alternatively use the name `TELEGRAM_BOT_TOKEN`.

Verify that `.env` is ignored before entering secrets:

```bash
git check-ignore .env
```

It should print `.env`. If it does not, stop and fix `.gitignore`.

## Start everything with one command

```bash
chmod +x start.sh
./start.sh
```

The script checks the token timestamp, skips login when it is no more than 12 hours old, and asks whether to re-authenticate or continue when the token is missing, older than 12 hours, or the timestamp is invalid. When authenticating, it starts the local Streamlit helper; complete FYERS login in the official website, paste the full callback URL into the masked field, wait for the success message, then return to the terminal and press Enter. The script checks configuration, sends a Telegram test, then starts the read-only feed.

The 12-hour check is an **age check**, not a live validation request to FYERS. For older `.env` files without a timestamp, the first run estimates it from the file modification time; that cannot establish the actual issue time. If you continue with an old token, FYERS may reject it.

In Codespaces the auth helper binds to `127.0.0.1:8501`; open the private forwarded port if prompted. Do not expose the authentication page publicly.

## Manual authentication and startup

### A. Authenticate with FYERS

With the virtual environment activated and `.env` configured:

```bash
python -m streamlit run scripts/fyers_auth_web.py --server.address 127.0.0.1 --server.port 8501
```

Open the local page (or your private Codespaces forwarded port), click **Generate secure FYERS login link**, and complete login/verification on the official FYERS site. Paste the entire redirected URL into the masked field. The helper validates the returned `state`, exchanges the temporary authorization code, then saves `FYERS_ACCESS_TOKEN` and `FYERS_ACCESS_TOKEN_UPDATED_AT` to `.env`.

Stop Streamlit with `Ctrl+C` after success. Never paste callback URLs, authorization codes, app secrets, access tokens, or Telegram tokens into chat, issues, logs, or commits.

### B. Test Telegram

```bash
python -m src.telegram_alerts --test
```

This verifies Telegram delivery only; it does not validate the feed or trading logic.

### C. Start the feed

```bash
python -m src.main --source fyers --telegram-alerts
```

Other modes:

```bash
# FYERS feed, no Telegram alerts
python -m src.main --source fyers

# Synthetic sample stream; not real market data
python -m src.main --source sample
```

Manual startup does not perform the 12-hour prompt from `start.sh`; it attempts to use the token currently in `.env`.

## Useful commands

```bash
# Unit tests
python -m pytest -q

# Sample OHLCV baseline backtest
python -m src.backtest --csv data/example_ohlcv.csv

# CLI help
python -m src.main --help
python -m src.backtest --help
python -m src.nifty_validation --help
```

Tests validate code behavior on their test data, not profitability. Review `docs/OPTIONS_DATA.md`, `docs/RESEARCH_ROADMAP.md`, and `docs/TRADING_BRAIN.md` before interpreting historical results.

## Configuration and troubleshooting

- Watchlist: `config/watchlist.txt`, one FYERS symbol per line.
- Credentials: repository-root `.env`; never commit it.
- Token timestamp: `FYERS_ACCESS_TOKEN_UPDATED_AT`, stored as UTC ISO time.
- Auth logs: `logs/fyers_auth.log`.
- Signal defaults: `ORBConfig` in `src/orb_signals.py`.

If authentication fails, check the app credentials and exact redirect URI, then generate a fresh login link. If no ticks arrive, verify market hours, symbol spelling, subscription, and token validity; an idle socket outside market hours can be normal. If no alerts arrive, the engine may be abstaining because its opening range, volume, VWAP, or relative-volume history is incomplete. If Telegram testing fails, check the bot token and chat ID without printing them.

## Safety and known limitations

- No live order-placement capability is included.
- Live FYERS volume interpretation and candle aggregation require validation.
- Index signals alone cannot determine option premium, spread, liquidity, or a suitable contract.
- Historical OHLC data cannot fully model real bid/ask fills or intrabar stop execution.
- Include brokerage, taxes, regulatory charges, spread, slippage, and gaps in risk analysis.
- No alert or backtest result guarantees future performance.

See the `docs/` directory for deeper implementation and research notes.
