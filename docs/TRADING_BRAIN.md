# Zudo Trading Brain: build and validation plan

## Purpose

The "brain" is a decision pipeline, not a single predictive model. It must be able to return **NO TRADE** and explain why. Profitability is not assumed.

## Current research-only foundation

- `src/brain.py` implements deterministic pre-trade gates for a candidate option: input validity, timestamp alignment, bid/ask spread, volume presence, single-lot exposure, target risk, and hard risk ceiling.
- Candidate selection is based on strike proximity among candidates that pass gates. It does not rank contracts by future P&L.
- `tests/test_brain.py` covers hard risk caps, high-premium rejection, missing liquidity, timestamp misalignment, selection, and valid no-trade outcomes.
- `src/options_dynamic_backtest.py` now calls the deterministic brain gates before accepting a candidate. OHLCV-only research mode explicitly marks missing bid/ask as `liquidity_unverified_missing_bid_ask`; `--require-liquidity-data` switches to strict rejection. The brain risk estimate remains an additional approximation and does not replace the detailed charge model in the backtester.

## Architecture

1. **Data contract and quality gate** — normalize timezone, reject duplicate/out-of-order candles, validate OHLC, detect gaps, and ensure index/option timestamps align. Never use index volume as a signal.
2. **Signal engine** — pure functions over completed candles. Every signal records the exact timestamp and input values. Entry is no earlier than the next tradable bar.
3. **Contract universe** — use point-in-time contract metadata, correct expiry and lot size, and only contracts whose data was available at decision time. Record why each candidate was excluded.
4. **Risk and execution-cost gate** — target risk defaults to 0.5% of equity, hard ceiling 1%, and exposure cap 20%. Include fees, slippage, and available spread information. If no contract qualifies, do not trade.
5. **Backtest engine** — explicit fills, stop/target collision policy, intraday square-off, fees, slippage, and full trade/equity ledger. Never allow future candles to influence entry selection.
6. **Evaluation** — compare with no-trade and simple baselines; report net returns, drawdown, expectancy, profit factor, turnover, costs, skipped trades, and sample size. Split by chronological train/validation/test periods; freeze rules before the final holdout.
7. **Paper trader** — same signal/risk code as backtest, but simulated execution only. Log every intended order, fill assumption, rejection, and market snapshot.
8. **Live readiness gate** — separate project phase requiring extended paper results, failure recovery, broker constraints, operational monitoring, and user approval. No live order placement is included now.

## AI model integration (future, not yet enabled)

Use an AI model as an optional research analyst, not as the authority that can authorize trades. A future adapter may return a strict, versioned JSON assessment of a completed-candle market snapshot (for example: regime label, setup-quality score, concise rationale, and abstain flag). The deterministic engine must validate schema, timestamp, model version, and latency; malformed, late, or unavailable output means **NO TRADE** or the explicitly defined deterministic fallback. The model cannot set quantity, change risk caps, invent quotes, bypass data checks, or call broker order endpoints.

Evaluate a model against the same chronological holdout as the deterministic baseline, using identical fills, fees, and slippage. Compare net expectancy, drawdown, calibration, coverage/abstention, and stability across regimes. Require repeatable out-of-sample improvement after costs before retaining it. Start with a replaceable provider interface rather than coupling the project to one vendor. Gemini and Llama are plausible candidates to benchmark; neither should be assumed to predict short-horizon option prices profitably without evidence.

## Non-negotiable controls

- No live orders during research development.
- Never increase risk limits just to create trades.
- No future-P&L-based contract selection.
- Missing or invalid data means reject/skip, not guess.
- Stop-loss levels do not guarantee a maximum loss, particularly around gaps or illiquid options.
- Backtest outputs are not proof of future profitability.
- Keep API credentials in ignored local environment files; never commit tokens.

## Next implementation sequence

1. Integrate the brain gates into the dynamic options backtester and preserve a per-candidate diagnostics table.
2. Extend the data schema to ingest point-in-time bid/ask and liquidity data; run strict mode whenever those quotes are available and required.
3. Add walk-forward evaluation and a baseline comparison report.
4. Add a deterministic paper-trading simulator with persistent event logs and a daily risk kill switch.
5. Add monitoring and operational failure tests before considering any broker execution layer.

## Acceptance criteria before considering live use

- All unit and integration tests pass.
- No look-ahead leakage is found by explicit tests and code review.
- Costs and fill assumptions are calibrated and documented.
- Multiple market regimes and a chronological holdout are evaluated.
- Paper trading is run for a meaningful period with reconciliation and incident logs.
- Risk limits, stop behavior, daily loss controls, and recovery paths are tested.
- Results are reviewed critically; there is no minimum return that can be promised.
