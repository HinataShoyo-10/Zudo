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

- Use 2% of configured capital as both the target planned risk and hard planned-risk cap for this research run (₹2,000 at ₹100,000 capital). This is a research assumption to validate, not a guarantee of maximum realized loss.
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


## First options backtest (research-only)

After saving both `nifty_index_5m_2025-01-02_to_09.csv` and the normalized option CSV, run this one-contract prototype:

```bash
python -m src.options_backtest \
  --index-csv data/fyers_expired/nifty_index_5m_2025-01-02_to_09.csv \
  --option-csv data/fyers_expired/nifty_option_2025-01-02_to_09.csv \
  --option-symbol NSE:NIFTY2510923600CE \
  --lot-size 75 \
  --initial-cash 100000 \
  --risk-fraction 0.005 \
  --hard-risk-fraction 0.01 \
  --opening-range-minutes 30 \
  --stop-loss-pct 0.20 \
  --target-r 1.5 \
  --slippage-bps-per-side 5 \
  --out backtest_output_nifty_options
```

For the January 2025 weekly NIFTY contract, 75 is the lot size introduced for new index derivatives in November 2024. Confirm the contract master for the specific symbol/date if using another contract. Official NSE circular: https://nsearchives.nseindia.com/content/circulars/FAOP64990.pdf

### What the prototype does

- Use the first 30 minutes (09:15–09:40 candle-starts) to calculate the NIFTY opening-range high/low.
- When a later NIFTY candle closes above the opening-range high, consider one long-call entry at the **next** five-minute option candle's open.
- Permit at most one signal attempt per session; this version only models the selected call contract.
- Use a 20% option-premium stop and 1.5R take-profit as an initial hypothesis, not recommended or tuned parameters.
- If a candle touches both stop and target, assume the stop was hit first.
- Skip any signal if one lot breaches the 2% planned-risk target and 2% hard planned-risk ceiling, or available cash. Do not increase risk just to force trades.
- Exit at the last available option candle in the same session.
- Report gross P&L separately from slippage, brokerage, and modeled transaction taxes; include skipped signals and their reasons.

The index history can show volume as zero. This prototype never uses index volume for its signals. It requires exact index/option timestamp alignment on shared dates and stops if timestamps do not match.

Outputs in `backtest_output_nifty_options/`:
- `summary.json`: metrics, assumptions, skipped-signal reasons, cost breakdown, and limitations
- `trades.csv`: executed trades (may contain headers and no rows if the risk gate skips every signal)
- `signals.csv`: traded, skipped, and no-signal outcomes by session
- `equity_curve.csv`: intraday marked-to-market equity estimate

### Historical cost assumptions for January 2025

Defaults are an estimate: FYERS options brokerage ₹20 per executed order; NSE options transaction charge 0.03503% of premium turnover; SEBI turnover fee ₹10/crore; STT 0.10% of option premium on the sell side (increased to 0.15% from 1 April 2026); buyer stamp duty 0.003%; and 18% GST on modeled brokerage/exchange/SEBI costs. These defaults are configurable. Verify their applicability using the historical rates, account plan, and contract notes. Official references:
- FYERS equity F&O brokerage: https://support.fyers.in/portal/en/kb/articles/what-are-the-brokerage-charges-for-equity-f-o
- NSE STT rate schedule: https://www.nseindia.com/static/products-services/equity-derivatives-securities-transaction-tax
- NSE transaction-charge revision effective 1 October 2024: https://zerodha.com/marketintel/bulletin/391488/revision-in-transactions-charges-from-1st-october-2024

One contract over six trading sessions is far too small to accept a strategy. OHLC candles do not establish executable bid/ask fills, and a planned stop is not a guaranteed maximum loss. This is a software/hypothesis-testing milestone, not evidence of an edge; no live orders are included.


## Dynamic strike selection (new research prototype)

The fixed-contract command above remains available. To avoid hardcoding one option symbol, use `src.options_dynamic_backtest` with a manifest of the contracts whose historical candles you have downloaded. The selector evaluates the same index breakout on each candidate contract, rejects contracts that fail the existing risk/cash/exposure gates, and chooses the eligible call whose strike is closest to the NIFTY index close at signal time. It does **not** choose by future P&L. This version is calls-only.

Create `data/fyers_expired/nifty_calls_manifest.json` using only real symbols and CSV paths you actually downloaded. Example shape (replace every placeholder with your own files and exact returned contract metadata):

```json
{
  "contracts": [
    {
      "symbol": "NSE:EXACT_CONTRACT_SYMBOL_1",
      "csv": "nifty_call_1.csv",
      "expiry": "YYYY-MM-DD",
      "strike": 23600,
      "lot_size": 75,
      "option_type": "CE"
    },
    {
      "symbol": "NSE:EXACT_CONTRACT_SYMBOL_2",
      "csv": "nifty_call_2.csv",
      "expiry": "YYYY-MM-DD",
      "strike": 23700,
      "lot_size": 75,
      "option_type": "CE"
    }
  ]
}
```

CSV paths are resolved relative to the manifest file. Each CSV must be the normalized five-minute OHLCV history for the symbol on that row. Supply contracts with overlapping trading dates; a contract is skipped on dates when its candles do not align exactly with the index data. Ensure each expiry and lot size is correct for the historical contract. Do not combine different contracts into one CSV unless each row retains its own symbol identity; the current manifest expects one CSV per symbol.

Run:

```bash
python -m src.options_dynamic_backtest \\
  --index-csv data/fyers_expired/nifty_index_5m_2025-01-02_to_09.csv \\
  --contracts-manifest data/fyers_expired/nifty_calls_manifest.json \\
  --initial-cash 100000 \\
  --risk-fraction 0.02 \\
  --hard-risk-fraction 0.02 \\
  --max-exposure-fraction 0.20 \\
  --opening-range-minutes 30 \\
  --stop-loss-pct 0.20 \\
  --target-r 1.5 \\
  --slippage-bps-per-side 5 \\
  --out backtest_output_dynamic
```

The dynamic output writes `summary.json`, `trades.csv`, `signals.csv`, and `candidate_diagnostics.csv`. The diagnostics file records each contract/session candidate, its eligibility status and rejection reason, entry premium, one-lot premium cost, planned risk, strike distance, and expiry. It helps explain why a signal was skipped instead of showing only an aggregate reason. In `summary.json`, `skipped_signals` and `skipped_reasons` count signal sessions (at most once per session), while `candidate_rejections` and `candidate_rejection_reasons` count individual contracts rejected by risk/cash/exposure gates. These are different units and should not be added together. It still has important limits: the candidate universe is only as complete as your downloaded manifest; selection uses strike proximity and affordability, not bid/ask spread, open interest, or liquidity; and historical OHLC does not establish executable fills. Collect several strikes per expiry and multiple expiries if your data plan permits, but respect the FYERS expired-data request allowance. Do not loosen the 2% target-risk / 2% hard-risk rules just to create trades. Research and paper testing only; no order endpoints are called.
