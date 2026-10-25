# Balanced NIFTY signal engine (research-only)

## Design

Zudo now has a pure, read-only ORB signal module in `src/orb_signals.py`. It does not place orders and does not yet send Telegram messages.

- **Early warning:** a live last price crossing the completed 15-minute opening range can emit `EARLY_WARNING`. This is informational only; it intentionally does not claim that volume/VWAP filters passed.
- **Primary path:** completed 5-minute candle breakout, session VWAP alignment, relative-volume threshold, and a maximum extension/chase check.
- **Separate fast path:** completed 1-minute candles use the same 15-minute opening range, VWAP and relative-volume filters, plus consecutive closes outside the range; a separate completed-candle retest evaluator is also available. Both must be backtested independently and is not automatically enabled in a live service.
- **No trade:** invalid/missing volume, incomplete opening range, insufficient relative-volume warm-up, VWAP misalignment, and overextended breakouts return `WAIT` with a reason.
- **Invalidation:** the opening-range boundary is returned as a reference/invalidation level. It is not a guaranteed stop price or an order instruction.

Defaults are provisional research parameters, not tuned or evidence of profitability: 15-minute opening range, relative volume at least 1.5x the preceding 10 bars, and maximum breakout extension of 0.3%. The relative-volume comparison is a simple rolling baseline, not time-of-day normalized. This should be improved or explicitly accepted before evaluation.

## Data caveats

FYERS WebSocket message schemas can vary by feed mode/version. Verify the actual callback fields against official docs and saved sanitized messages. If the field is cumulative traded volume, convert it to a non-negative per-update delta; the first observation cannot reliably reconstruct the volume already traded during the current candle. Consequently, do not trust live volume signals until feed semantics, resets, missing updates, and candle aggregation have been verified.

Index candles can identify directional setups but cannot choose an option contract by themselves. A separate point-in-time option contract/quote source is required to check expiry, lot size, bid/ask spread, premium, volume and timestamp alignment. If quotes are unavailable or stale, reject the contract rather than guess.

## Local validation

```bash
python -m pytest -q
```

The unit tests use synthetic candles only and validate code behavior, not strategy profitability. Next steps are a sanitized live-feed capture test, historical 1-minute and 5-minute signal replay, cost-aware independent backtests, and then paper alerts. Telegram and live trading are not connected by this module.
