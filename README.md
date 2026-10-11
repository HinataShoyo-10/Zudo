# Zudo — Indian Intraday Market Data

Broker-connected research starter for FYERS API v3. **Read-only: no order-placement code.**

## Requirements
- Python 3.11+
- Linux VPS
- FYERS API v3 app and current access token for live data

## Install
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

## Sample mode
```bash
python -m src.main --source sample
pytest -q
```
Synthetic sample data is for testing code only; it is not market data.

## FYERS live mode
Set `FYERS_APP_ID` and `FYERS_ACCESS_TOKEN` in the local `.env` file and confirm symbols in `config/watchlist.txt`. Then run:
```bash
python -m src.main --source fyers
```
Never commit or share credentials. This initial adapter needs validation against actual FYERS callback payloads and your account before unattended use.

## Layout
- `src/models.py`: timezone-aware Tick and Candle types
- `src/candles.py`: 5-minute OHLC candle aggregation
- `src/feeds.py`: sample feed and FYERS WebSocket adapter
- `src/indicators.py`: EMA and VWAP helpers
- `src/main.py`: command-line entry point
- `tests/`: unit tests
- `config/watchlist.txt`: FYERS symbols

## Known limitations
- Sample feed is synthetic.
- Price updates are not necessarily individual trades. The adapter intentionally sets volume to zero until verified interval-volume deltas are implemented; do not use volume-based signals yet.
- Candle builder expects ordered timestamps and rejects out-of-order updates.
- No orders are placed and no account state is changed.
