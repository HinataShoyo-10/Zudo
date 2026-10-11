# NIFTY expired-options historical data research

Zudo's current SBIN EMA test is a losing baseline; don't try to rescue it by fitting parameters. The next task is to obtain valid option-contract history, then define and test a separate options strategy.

## Data-access reality

FYERS has separate read-only endpoints for expired F&O expiry dates, contracts for an expiry, and expired-contract candle history. The helper in `src/fyers_expired_fno.py` uses these data endpoints directly and never calls an order endpoint. This endpoint coverage is documented in FYERS' current SDK references; access can depend on the plan and account entitlement.

FYERS currently documents the Standard API plan as having a 10-request-per-day allowance for intraday expired F&O data. Treat this as a scarce research budget: first query expiry dates, then fetch one expiry's contracts, then download one carefully selected contract. Don't loop across all strikes. Recheck your dashboard/official documentation before planning bulk collection.

Official references:
- https://github.com/FyersDev/fyers-c-sdk
- https://github.com/FyersDev/fyers-go-sdk
- https://support.fyers.in/portal/en/kb/articles/what-are-the-fyers-api-rate-limits-for-standard-and-prime-plans
- https://support.fyers.in/portal/en/kb/articles/can-i-view-expired-contracts-in-fyers

## Environment

Set the app ID and access token in the repository-root `.env` (ignored by Git). Do not put credentials in source files or command arguments. Verify with `git check-ignore .env`. Use your existing data-only FYERS app.

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

## Step 1 — List NIFTY expiry dates for a small period

```bash
mkdir -p data/fyers_expired
python -m src.fyers_expired_fno \
  --mode expiries \
  --underlying NSE:NIFTY50-INDEX \
  --from-date 2025-01-01 \
  --to-date 2025-01-31 \
  --out data/fyers_expired/nifty_expiries_jan2025.json
```

## Step 2 — List contracts for one expiry

Copy an actual expiry date from the response. Do not guess a date or construct an option symbol.

```bash
python -m src.fyers_expired_fno \
  --mode contracts \
  --underlying NSE:NIFTY50-INDEX \
  --expiry-date YYYY-MM-DD \
  --out data/fyers_expired/nifty_contracts.json
```

Replace `YYYY-MM-DD` with an actual returned expiry date. Use the response's actual contract symbol in the next step.

## Step 3 — Download one contract's five-minute candles

Choose one liquid near-the-money call or put from the returned symbols for a historical date range before that expiry.

```bash
python -m src.fyers_expired_fno \
  --mode history \
  --symbol 'PASTE-ACTUAL-RETURNED-CONTRACT-SYMBOL' \
  --resolution 5 \
  --from-date YYYY-MM-DD \
  --to-date YYYY-MM-DD \
  --include-oi \
  --include-greeks \
  --out data/fyers_expired/option_contract_history.json
```

The helper deliberately saves the API response as raw JSON first. The exact returned fields and timestamp/candle shape must be inspected and tested before converting it to backtest-ready rows. Don't blindly assume that expired F&O responses have the same schema as normal equity history.

The `data/fyers_expired/` directory is ignored by Git so account-acquired data isn't accidentally committed.

## Risk model direction

- Use 0.5% of configured capital as the target planned risk and 1% as a hard planned-risk cap.
- Position size must be based on option entry-to-stop loss plus a cost/slippage allowance and the contract lot size.
- If one lot exceeds the risk cap, skip the trade.
- Avoid undefined-risk option selling in the initial version.
- A stop price is not a guaranteed maximum loss. Gaps, spread, and slippage can exceed the planned risk.
- Include brokerage, STT, exchange and clearing charges, GST, SEBI charges, stamp duty, and applicable expiry/settlement costs using the actual trade date and current applicable schedule. Verify via the broker calculator and contract notes rather than treating a single flat fee as total cost.
- Historical OHLC alone cannot reconstruct executable bid/ask or true intrabar stop fills. Prefer actual bid/ask when a reliable source exists; otherwise use conservative spread/slippage stress assumptions and label the limitation.

No live orders are included or enabled.


## One-command validation gate

After you have saved the expiry, contracts, and option-history API responses, run this **single command** to validate the whole downloaded-data set. It makes no network requests. The optional stock CSV validates your existing SBIN data in the same run.

```bash
python -m src.validate_market_data \
  --expiry-json data/fyers_expired/nifty_expiries_jan2025.json \
  --contracts-json data/fyers_expired/nifty_contracts_2025-01-09.json \
  --options-json data/fyers_expired/nifty_option_2025-01-06.json \
  --expected-expiry 2025-01-09 \
  --expected-trading-date 2025-01-06 \
  --expected-symbol NSE:NIFTY2510923600CE \
  --resolution 5 \
  --stock-csv data/sbin_5m.csv \
  --report-out data/fyers_expired/validation_report.json
```

Adjust the input paths and expected symbol to match the files/contracts you actually downloaded. Do not paste invented filenames into the command; use your actual saved filenames.

The single report checks:
- JSON files load and API responses report success;
- requested expiry appears in the returned option-expiry list;
- contracts JSON is for that expiry and the historical symbol appears in its contract list;
- history symbol, timeframe, columns, row lengths, numeric values, timestamps, OHLC invariants, option delta range, market-session hours, and date selection;
- duplicate/out-of-order timestamps and missing five-minute slots;
- the optional stock CSV's schema, numeric values, timestamps, OHLC, session, and gaps.

Warnings for missing candle slots are not automatically treated as failures because illiquid option contracts can legitimately have no candle in some intervals. Review gap warnings against the instrument and provider behavior. This validator checks consistency and basic data quality; it does not prove that the vendor data is economically correct or guarantee fill realism.

The command prints one JSON summary with overall `PASS`, `WARN`, or `FAIL`, and exits with code 1 when errors are found. It can also save that same summary to `--report-out`. Run the unit tests separately when source code changes:

```bash
python -m pytest -q
```
