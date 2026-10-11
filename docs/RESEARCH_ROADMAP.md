# Zudo research roadmap

This document is the project's decision gate from a market-data prototype toward a potentially testable intraday system. It is not a promise of profitability.

## Current state

- Offline CSV backtester supports the legacy long-only EMA crossover and a 5-minute opening-range breakout with ATR-based stops, risk/exposure sizing, conservative gap and intrabar collision handling, and intraday square-off.
- ORB requires trend alignment, price above session VWAP, a minimum breakout distance and candle-body fraction, recent volume expansion, and a bounded opening range; each rejected breakout is reported in `no_trade_reasons`.
- ORB currently assumes zero flat brokerage by default and 5 bps slippage per side; this deliberately does not model applicable taxes/regulatory charges or point-in-time executable quotes.
- ORB rejects incomplete sessions rather than silently backtesting partial 5-minute data. Its rules remain a hypothesis, not a validated edge.
- `src.paper_portfolio` provides an offline, multi-symbol cash-equity paper portfolio replay with shared capital, at most one position per session, setup-score tie-breaking, and an auditable skip/trade ledger. It is not a live paper-trading service and does not place broker orders.
- `src.scanning.premarket` ranks up to 50 symbols using completed daily history only, excluding any daily candle dated today. It ranks trend, 5-/20-session momentum, previous-day relative volume, range position, and ATR; the score is not a probability of profit.
- `src.scanning.fyers_workflow` fetches one symbol at a time with configurable pacing, writes auditable local histories, bounds watchlist size, and aborts ranking if any symbol's data is missing.
- `src.scanning.opportunity` checks ORB and trend-pullback setups from latest completed 5-minute bars, exposes no-trade reasons and risk-sized paper estimates, and does not infer next-bar fills from future candles.
- `src.dashboard.app` provides premarket ranking, shortlist-only after-open checks (manual or 5-minute refresh while the dashboard is open), and historical paper backtesting. It never places broker orders.
- Synthetic candles are only a software smoke test, not market evidence.
- Current workspace cash-equity coverage is limited to the local SBIN sample from January 2025; fresh FYERS multi-symbol histories are required for a current scan.
- The bundled `config/watchlist.txt` is an editable 50-symbol starter universe, not a dynamically verified current NIFTY 50 list. Verify symbols/composition; failed data pulls are reported and prevent partial rankings.
- FYERS historical candle downloader has been added; it must be run with the user's own credentials and its output manually audited.
- The WebSocket adapter, execution assumptions, and broker integration are not yet validated.
- No live order placement should be enabled during research.

## Research sequence

### Gate 1 — Verify data and code

1. Run `python -m pytest -q` and fix all failures.
2. Download a small real sample for one liquid NSE cash-market instrument and a five-minute interval.
3. Check timestamp timezone and session boundaries, duplicates, missing intervals, OHLC invariants, zero-volume bars, and corporate-action discontinuities.
4. Confirm the downloaded CSV loads in the backtester and manually reconcile several candles against an independent chart/data view.
5. Confirm the market-session policy explicitly; do not silently treat overnight or out-of-session bars as regular intraday candles.

**Pass condition:** repeatable, auditable data with no unexplained anomalies.

### Gate 2 — Make simulation assumptions explicit

Before interpreting performance, specify order timing, fill price, spread/slippage, all applicable charges, quantity/lot sizing, stop/target behavior, square-off policy, and treatment of gaps. OHLC bars do not reveal the order of intrabar highs and lows; do not assume a stop and target were both executable in a favorable order.

**Pass condition:** trade-level results can be reproduced and costs are not materially understated.

### Gate 3 — Test hypotheses without fitting noise

Research a small pre-registered set of explainable ideas, for example:
- Opening-range breakout with a fixed session window and volatility filter.
- Volatility contraction followed by expansion.
- Mean reversion after an unusually large move, with a defined invalidation condition.

For each idea, write the hypothesis, entry/exit rules, eligible instruments, time windows, and reasons to reject it before running parameter sweeps. Keep the existing EMA crossover as a baseline only.

Use chronological development, validation, and untouched test periods. If tuning is needed, use rolling walk-forward evaluation. Report every experiment rather than only the best result.

**Pass condition:** the strategy remains net-positive after conservative costs in unseen periods and is not dependent on one narrow parameter choice. This is a screening rule, not proof of future profitability.

### Gate 4 — Risk and forward testing

- Set a maximum risk per trade and daily loss limit based on a tolerable rupee loss, not desired income.
- Add a kill switch for stale data, rejected orders, duplicate signals, clock/session problems, and breached limits.
- Paper-trade for a meaningful sample of trades and multiple market conditions; compare simulated signals with actual executable quotes.
- Re-estimate realistic costs and slippage from observed behavior.

**Pass condition:** stable operations and forward results consistent with conservative historical expectations.

### Gate 5 — Deployment review

Only after the earlier gates pass, review current broker API requirements, static-IP requirements for order placement, permissions, exchange/broker rules, monitoring, alerts, recovery, and capital-at-risk. Start with the smallest practical size and keep a human override. Never use borrowed money or money needed for living expenses.

## Reporting standard

Every experiment should record:
- data source, instrument, resolution, date range, and data-quality notes;
- exact rules and parameter values;
- gross and net P&L after itemized costs;
- number of trades, average net trade, win rate, profit factor, maximum drawdown, and exposure;
- results by day/month and by market regime;
- baseline comparison, untouched out-of-sample results, and stress tests;
- known limitations and the reason the strategy is accepted or rejected.

A strategy is not accepted because its equity curve looks good. The objective is to try to disprove the edge before risking capital.
