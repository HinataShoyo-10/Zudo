"""Local FYERS watchlist ranker, intraday paper scanner, and backtest dashboard."""
from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from src.backtesting.engine import Bar, load_bars
from src.paper.portfolio import run_paper_portfolio
from src.paper.session import advance_paper_session, new_paper_state
from src.scanning.fyers_workflow import (
    DEFAULT_REQUEST_INTERVAL_SECONDS,
    create_fyers_client,
    fetch_symbol_history,
    intraday_history_dates,
    load_symbols,
    premarket_history_dates,
)
from src.scanning.opportunity import latest_session_chart, scan_universe
from src.scanning.premarket import rank_premarket

ROOT = Path(__file__).resolve().parents[2]
IST = ZoneInfo("Asia/Kolkata")
WATCHLIST_PATH = ROOT / "config" / "watchlist.txt"
SCANNER_DATA_PATH = ROOT / "data" / "scanner"
PAPER_STATE_PATH = SCANNER_DATA_PATH / "paper_session.json"


def _format_metric(value: Any) -> str:
    return "—" if value is None else f"{value:.2f}"


def _parse_assets(text: str) -> dict[str, list[Bar]]:
    assets: dict[str, list[Bar]] = {}
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Enter at least one SYMBOL=CSV_PATH.")
    for line in lines:
        symbol, separator, raw_path = line.partition("=")
        if not separator or not symbol.strip() or not raw_path.strip():
            raise ValueError(f"Invalid asset line {line!r}; expected SYMBOL=CSV_PATH.")
        symbol = symbol.strip()
        if symbol in assets:
            raise ValueError(f"Duplicate symbol: {symbol}")
        path = Path(raw_path.strip()).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        if not path.is_file():
            raise ValueError(f"CSV not found for {symbol}: {path}")
        assets[symbol] = load_bars(path)
    return assets


def _watchlist_text() -> str:
    return WATCHLIST_PATH.read_text(encoding="utf-8")


def _store_premarket_results(results: list[dict[str, Any]]) -> None:
    SCANNER_DATA_PATH.mkdir(parents=True, exist_ok=True)
    (SCANNER_DATA_PATH / "premarket_ranking.json").write_text(
        json.dumps(results, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _load_saved_premarket_results() -> list[dict[str, Any]]:
    path = SCANNER_DATA_PATH / "premarket_ranking.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not all(isinstance(row, dict) for row in payload):
        raise ValueError(f"Invalid saved premarket ranking: {path}")
    return payload


def _load_paper_state(initial_cash: float) -> dict[str, Any]:
    if not PAPER_STATE_PATH.is_file():
        return new_paper_state(initial_cash)
    payload = json.loads(PAPER_STATE_PATH.read_text(encoding="utf-8"))
    required = {
        "version", "initial_cash", "cash", "trades", "decisions",
        "session_date", "last_processed_by_symbol", "pending",
        "position", "trade_taken_today",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        raise ValueError(f"Invalid saved paper trading state: {PAPER_STATE_PATH}")
    if payload.get("version") != 1:
        raise ValueError(f"Unsupported saved paper state version: {PAPER_STATE_PATH}")
    return payload


def _store_paper_state(state: dict[str, Any]) -> None:
    SCANNER_DATA_PATH.mkdir(parents=True, exist_ok=True)
    temporary_path = PAPER_STATE_PATH.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(state, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(PAPER_STATE_PATH)


def _session_is_open(now: datetime) -> bool:
    return now.weekday() < 5 and time(9, 20) <= now.time() <= time(15, 30)


st.set_page_config(page_title="Zudo · Stock Research", page_icon="📊", layout="wide")
st.title("Zudo · 50-Stock Momentum & Paper Scanner")
st.caption("FYERS market data · Historical premarket rank → intraday paper setup check · No orders")
st.warning(
    "This is a research scanner, not a profitable strategy guarantee. Rankings describe past "
    "completed candles; they are not probabilities. All trade candidates are paper-only."
)

with st.sidebar:
    st.header("Workflow")
    mode = st.radio(
        "Select stage",
        ["Premarket ranking", "After-open paper scan", "Historical paper backtest"],
    )
    st.header("Paper account")
    initial_cash = st.number_input("Starting capital (₹)", min_value=1_000, value=50_000, step=5_000)
    risk_fraction = st.number_input(
        "Risk per trade (%)", min_value=0.1, max_value=2.0, value=0.5, step=0.1
    ) / 100
    max_exposure_fraction = st.number_input(
        "Max exposure (%)", min_value=1.0, max_value=100.0, value=20.0, step=1.0
    ) / 100
    fee_per_side = st.number_input("Flat fee per side (₹)", min_value=0.0, value=0.0, step=1.0)
    slippage_bps = st.number_input("Slippage per side (bps)", min_value=0.0, value=5.0, step=1.0)
    st.divider()
    st.header("Intraday setup filters")
    min_volume_multiple = st.number_input(
        "Min volume vs prior six bars", min_value=0.0, value=1.2, step=0.1
    )
    min_breakout_atr = st.number_input(
        "Min breakout distance (ATR)", min_value=0.0, value=0.1, step=0.05
    )
    min_body_fraction = st.number_input(
        "Min bullish candle body (%)", min_value=0.0, max_value=100.0, value=60.0, step=5.0
    ) / 100
    max_opening_range_atr = st.number_input(
        "Max opening range (ATR)", min_value=0.1, value=5.0, step=0.5
    )

if mode == "Premarket ranking":
    st.subheader("1 · Rank up to 50 watchlist stocks before the session")
    symbols_text = st.text_area(
        "FYERS symbols (editable; one per line)",
        value=_watchlist_text(),
        height=220,
    )
    options = st.columns(2)
    shortlist_size = options[0].selectbox("Shortlist size for after-open scan", [3, 5, 8, 10], index=1)
    request_interval = options[1].number_input(
        "Delay between FYERS requests (seconds)", min_value=0.5, max_value=10.0,
        value=DEFAULT_REQUEST_INTERVAL_SECONDS, step=0.5,
        help="Serial calls protect the API from bursts; ranking 50 symbols takes at least about 49 seconds.",
    )
    rank_clicked = st.button("Fetch daily history and rank watchlist", type="primary")
    if rank_clicked:
        try:
            symbols = load_symbols(WATCHLIST_PATH, limit=50) if symbols_text == _watchlist_text() else None
            if symbols is None:
                temp_path = SCANNER_DATA_PATH / "editable_watchlist.txt"
                temp_path.parent.mkdir(parents=True, exist_ok=True)
                temp_path.write_text(symbols_text, encoding="utf-8")
                symbols = load_symbols(temp_path, limit=50)
            today = datetime.now(IST).date()
            start, end = premarket_history_dates(today)
            client = create_fyers_client(ROOT / ".env")
            progress = st.progress(0, text="Fetching completed daily candles from FYERS…")
            status = st.empty()

            def report_daily_progress(index: int, total: int, symbol: str) -> None:
                progress.progress(index / total, text=f"Fetched {index} of {total} symbols")
                status.write(f"Fetched daily history for `{symbol}`.")

            daily_assets = fetch_symbol_history(
                client,
                symbols,
                "D",
                start,
                end,
                SCANNER_DATA_PATH,
                request_interval_seconds=request_interval,
                progress_callback=report_daily_progress,
            )
            benchmark_assets: dict[str, list[Bar]] = {}
            benchmark_error = ""
            try:
                benchmark_assets = fetch_symbol_history(
                    client,
                    ["NSE:NIFTY50-INDEX"],
                    "D",
                    start,
                    end,
                    SCANNER_DATA_PATH,
                    request_interval_seconds=request_interval,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                benchmark_error = str(exc)
            results = rank_premarket(
                daily_assets,
                as_of=datetime.now(IST),
                shortlist_size=shortlist_size,
                benchmark_bars=benchmark_assets.get("NSE:NIFTY50-INDEX"),
            )
            _store_premarket_results(results)
            st.session_state["premarket_results"] = results
            st.session_state["premarket_symbols"] = symbols
            st.session_state["shortlist_size"] = shortlist_size
            st.success("Premarket ranking complete. FYERS calls were data-only.")
            if benchmark_error:
                st.warning(
                    "NIFTY market-regime and relative-strength context are unavailable; "
                    f"the stock ranking remains usable without that context. FYERS: {benchmark_error}"
                )
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(str(exc))

    if "premarket_results" not in st.session_state:
        try:
            st.session_state["premarket_results"] = _load_saved_premarket_results()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            st.error(f"Could not load saved premarket ranking: {exc}")
    results = st.session_state.get("premarket_results", [])
    if results:
        shortlist = [row for row in results if row["shortlisted"]]
        st.caption(
            f"Completed daily data only · ranked {len(results)} symbols · "
            f"{len(shortlist)} shortlisted · score is a relative heuristic, not a win probability"
        )
        market_trend = next(
            (row.get("market_trend") for row in results if row.get("market_trend")),
            "unavailable",
        )
        market_as_of = next(
            (row.get("benchmark_data_as_of") for row in results if row.get("benchmark_data_as_of")),
            "",
        )
        st.metric("NIFTY daily regime", f"{market_trend} · {market_as_of or 'data unavailable'}")
        st.caption(
            "The score does not include verified company news/events or live premarket indicative "
            "prices. Opening gap is measured after the first 09:15 candle; historical candles "
            "cannot provide executable spread/depth."
        )
        if shortlist:
            st.success("Leaders are queued for the after-open paper scan.")
        table = pd.DataFrame(results)
        columns = [
            "rank", "symbol", "status", "shortlisted", "score", "data_as_of",
            "market_trend", "momentum_5d_pct", "momentum_20d_pct",
            "relative_strength_vs_nifty_20d_pct", "rsi_14", "prior_day_relative_volume",
            "average_traded_value_20d", "previous_day_high", "previous_day_low",
            "previous_day_close", "atr_pct", "range_position_20d_pct", "reason",
        ]
        st.dataframe(table.reindex(columns=columns), use_container_width=True, hide_index=True)
        st.download_button(
            "Download premarket ranking CSV",
            table.to_csv(index=False).encode("utf-8"),
            "premarket_ranking.csv",
            "text/csv",
        )
    else:
        st.info("Fetch daily history to generate a premarket ranking. No API request is made until you click the button.")

elif mode == "After-open paper scan":
    st.subheader("2 · Check only the premarket leaders after the open")
    results = st.session_state.get("premarket_results", [])
    if not results:
        try:
            results = _load_saved_premarket_results()
            st.session_state["premarket_results"] = results
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            st.error(f"Could not load premarket ranking: {exc}")
            results = []
    leaders = [row for row in results if row.get("shortlisted")]
    if not leaders:
        st.info("Run premarket ranking first; only qualified leaders will be checked intraday.")
    else:
        today = datetime.now(IST).date()
        now = datetime.now(IST)
        ranking_date = max((row.get("data_as_of", "") for row in leaders), default="")
        ranking_is_current = False
        daily_context_is_available = all(
            row.get("previous_day_high") is not None
            and row.get("previous_day_low") is not None
            and row.get("previous_day_close") is not None
            for row in leaders
        )
        st.caption(
            f"Shortlist: {', '.join(row['symbol'] for row in leaders)} · "
            f"ranking data through {ranking_date} · today {today}"
        )
        if ranking_date and ranking_date >= today.isoformat():
            st.warning("The saved ranking must be from a completed session before today; rerun premarket ranking.")
        elif ranking_date:
            ranked_on = date.fromisoformat(ranking_date)
            ranking_is_current = (today - ranked_on).days <= 4
            if not ranking_is_current:
                st.warning("The premarket ranking is stale. Rerun it with current daily history before scanning.")
        if not daily_context_is_available:
            st.warning(
                "This saved ranking predates the PDH/PDL/PDC context needed for gap reporting. "
                "Rerun Premarket ranking before scanning."
            )
        if not _session_is_open(now):
            st.warning("NSE regular session is normally 09:15–15:30 IST on trading days. Refresh intraday data only during the session.")
        try:
            if "paper_session_state" not in st.session_state:
                st.session_state["paper_session_state"] = _load_paper_state(float(initial_cash))
            paper_state_ready = True
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            st.error(f"Paper trading state could not be loaded: {exc}")
            paper_state_ready = False
        auto_refresh = st.checkbox(
            "Auto-check every 5 minutes during market hours",
            value=True,
            help="Enabled by default. Fetches 5-minute histories for shortlisted symbols only and updates the paper ledger. Keep the dashboard open. No broker orders are placed.",
        )

        @st.fragment(run_every=300 if auto_refresh else None)
        def monitor_shortlist() -> None:
            current = datetime.now(IST)
            refresh = st.button(
                "Fetch today's 5-minute candles and check setups",
                type="primary",
                disabled=(
                    not _session_is_open(current)
                    or not ranking_is_current
                    or not daily_context_is_available
                    or not paper_state_ready
                ),
            )
            if (
                auto_refresh
                and _session_is_open(current)
                and ranking_is_current
                and daily_context_is_available
                and paper_state_ready
            ):
                refresh = True
            if refresh:
                try:
                    symbols = [row["symbol"] for row in leaders]
                    client = create_fyers_client(ROOT / ".env")
                    start, end = intraday_history_dates(current.date())
                    bars_by_symbol = fetch_symbol_history(
                        client,
                        symbols,
                        "5",
                        start,
                        end,
                        SCANNER_DATA_PATH,
                        request_interval_seconds=DEFAULT_REQUEST_INTERVAL_SECONDS,
                    )
                    scan_results = scan_universe(
                        bars_by_symbol,
                        daily_context={row["symbol"]: row for row in leaders},
                        as_of=datetime.now(IST),
                        initial_cash=float(initial_cash),
                        risk_fraction=risk_fraction,
                        max_exposure_fraction=max_exposure_fraction,
                        fee_per_side=fee_per_side,
                        slippage_bps=slippage_bps,
                        min_volume_multiple=min_volume_multiple,
                        min_breakout_atr=min_breakout_atr,
                        min_body_fraction=min_body_fraction,
                        max_opening_range_atr=max_opening_range_atr,
                    )
                    paper_state = advance_paper_session(
                        st.session_state["paper_session_state"],
                        bars_by_symbol,
                        scan_results,
                        as_of=datetime.now(IST),
                        risk_fraction=risk_fraction,
                        max_exposure_fraction=max_exposure_fraction,
                        fee_per_side=fee_per_side,
                        slippage_bps=slippage_bps,
                    )
                    _store_paper_state(paper_state)
                    st.session_state["paper_session_state"] = paper_state
                    st.session_state["intraday_scan_results"] = scan_results
                    st.session_state["intraday_scan_time"] = datetime.now(IST).isoformat()
                    st.session_state["intraday_scan_assets"] = bars_by_symbol
                    st.success("Scan complete; eligible setups were sent to the paper ledger only.")
                except (OSError, RuntimeError, ValueError) as exc:
                    st.error(f"Intraday scan failed: {exc}")

            scan_results = st.session_state.get("intraday_scan_results", [])
            if scan_results:
                st.caption(
                    "Last refreshed: "
                    + st.session_state.get("intraday_scan_time", "unknown")
                )
                opportunities = [row for row in scan_results if row["status"] == "OPPORTUNITY"]
                if opportunities:
                    st.success(
                        f"{len(opportunities)} setup(s) pass filters. The highest-ranked setup may be queued for a paper trade."
                    )
                    frame = pd.DataFrame(opportunities)
                    visible = [
                        "rank", "symbol", "strategy", "setup_score", "session_date",
                        "previous_day_high", "previous_day_low", "previous_day_close",
                        "opening_gap_pct",
                        "signal_time", "planned_entry_time", "estimated_entry", "stop_price",
                        "target_price", "quantity", "planned_risk", "volume_multiple",
                    ]
                    st.dataframe(frame[visible], use_container_width=True, hide_index=True)
                    st.download_button(
                        "Download intraday opportunities CSV",
                        frame.to_csv(index=False).encode("utf-8"),
                        "intraday_opportunities.csv",
                        "text/csv",
                    )
                else:
                    st.info("No shortlisted stock has a qualifying setup on its latest completed bar.")
                latest_candle_times = [
                    datetime.fromisoformat(row["latest_bar_time"]).astimezone(IST)
                    for row in scan_results
                    if row.get("latest_bar_time")
                ]
                if latest_candle_times:
                    newest_candle = max(latest_candle_times)
                    candle_age = datetime.now(IST) - newest_candle
                    st.caption(f"Latest completed 5-minute bar in scan: {newest_candle:%H:%M %Z}")
                    if candle_age > timedelta(minutes=15):
                        st.error("Intraday data is stale; refresh before reviewing paper candidates.")
                st.subheader("Chart review")
                st.caption(
                    "PDH/PDL/PDC come from the last completed daily candle. Gap is first 09:15 "
                    "bar open versus prior close. News/event checks and live spread/depth are "
                    "not included in this data-only version."
                )
                chart_assets = st.session_state["intraday_scan_assets"]
                selected = st.selectbox("Shortlisted company", sorted(chart_assets))
                chart = latest_session_chart(chart_assets[selected], as_of=datetime.now(IST))
                if chart:
                    chart_frame = pd.DataFrame(chart).set_index("timestamp")
                    st.line_chart(
                        chart_frame[["close", "ema_5", "ema_13", "session_vwap"]],
                        y_label="Price (₹)",
                    )
                decision_frame = pd.DataFrame(scan_results)
                st.subheader("All setup decisions and no-trade reasons")
                st.dataframe(decision_frame, use_container_width=True, hide_index=True)
                st.download_button(
                    "Download all setup decisions CSV",
                    decision_frame.to_csv(index=False).encode("utf-8"),
                    "intraday_scan_decisions.csv",
                    "text/csv",
                )
            paper_state = st.session_state.get("paper_session_state")
            if paper_state:
                st.subheader("Paper trade ledger · no broker orders")
                st.caption(
                    "Paper execution waits for the next completed five-minute bar and uses its open "
                    "with configured slippage. Stop/target exits use candle OHLC; if both touch, "
                    "the stop is assumed first. At most one trade is taken per session, chosen by "
                    "the highest setup score. Auto-check must stay enabled and this dashboard open."
                )
                ledger_columns = st.columns(4)
                paper_cash = float(paper_state["cash"])
                paper_initial = float(paper_state["initial_cash"])
                ledger_columns[0].metric("Paper cash", f"₹{paper_cash:,.2f}")
                ledger_columns[1].metric(
                    "Realized P&L", f"₹{paper_cash - paper_initial:,.2f}"
                )
                ledger_columns[2].metric("Closed trades", str(len(paper_state["trades"])))
                ledger_columns[3].metric(
                    "Paper status",
                    "Open position" if paper_state["position"] else (
                        "Entry pending" if paper_state["pending"] else "Flat"
                    ),
                )
                if paper_state["pending"]:
                    st.info(f"Pending paper entry: {paper_state['pending']}")
                if paper_state["position"]:
                    st.dataframe(
                        pd.DataFrame([paper_state["position"]]),
                        use_container_width=True,
                        hide_index=True,
                    )
                if paper_state["trades"]:
                    trade_frame = pd.DataFrame(paper_state["trades"])
                    st.dataframe(trade_frame, use_container_width=True, hide_index=True)
                    st.download_button(
                        "Download paper session trades CSV",
                        trade_frame.to_csv(index=False).encode("utf-8"),
                        "paper_session_trades.csv",
                        "text/csv",
                    )
                if paper_state["decisions"]:
                    with st.expander("Paper execution decisions"):
                        st.dataframe(
                            pd.DataFrame(paper_state["decisions"]),
                            use_container_width=True,
                            hide_index=True,
                        )

        monitor_shortlist()

else:
    st.subheader("Historical paper backtest")
    default_lines = "\n".join(
        f"NSE:{path.stem.removesuffix('_5m').upper()}-EQ={path.relative_to(ROOT)}"
        for path in sorted((ROOT / "data").glob("*_5m.csv"))
    )
    asset_lines = st.text_area(
        "Historical assets (one SYMBOL=CSV_PATH per line)",
        value=default_lines,
        height=120,
    )
    run_backtest = st.button("Run historical paper backtest", type="primary")
    if run_backtest:
        try:
            assets = _parse_assets(asset_lines)
            result = run_paper_portfolio(
                assets,
                initial_cash=float(initial_cash),
                risk_fraction=risk_fraction,
                max_exposure_fraction=max_exposure_fraction,
                fee_per_side=fee_per_side,
                slippage_bps=slippage_bps,
                min_volume_multiple=min_volume_multiple,
                min_breakout_atr=min_breakout_atr,
                min_body_fraction=min_body_fraction,
                max_opening_range_atr=max_opening_range_atr,
            )
            st.session_state["paper_backtest_result"] = result
            st.success("Historical paper backtest completed.")
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"Backtest failed: {exc}")

    if "paper_backtest_result" in st.session_state:
        result = st.session_state["paper_backtest_result"]
        summary = result["summary"]
        columns = st.columns(5)
        columns[0].metric("Final equity", f"₹{summary['final_equity']:,.2f}")
        columns[1].metric("Net P&L", f"₹{summary['net_pnl']:,.2f}", f"{summary['return_pct']:.2f}%")
        columns[2].metric("Trades", str(summary["trades"]))
        columns[3].metric("Win rate", f"{summary['win_rate_pct']:.1f}%")
        columns[4].metric("Profit factor", _format_metric(summary["profit_factor"]))
        st.caption(
            "Historical replay only · complete trade exit drawdown does not include intratrade risk · "
            "costs exclude applicable taxes/charges."
        )
        if result["equity_curve"]:
            equity = pd.DataFrame(result["equity_curve"]).set_index("timestamp")
            st.line_chart(equity[["equity"]], y_label="Equity (₹)")
            st.line_chart(equity[["drawdown_pct"]], y_label="Realized drawdown (%)")
        left, right = st.columns(2)
        with left:
            st.subheader("Trades")
            trades = result["trades"]
            if trades:
                trade_frame = pd.DataFrame(trades)
                st.dataframe(trade_frame, use_container_width=True, hide_index=True)
                st.download_button(
                    "Download paper trades CSV",
                    trade_frame.to_csv(index=False).encode("utf-8"),
                    "paper_trades.csv",
                    "text/csv",
                )
            else:
                st.info("No trades were taken.")
        with right:
            st.subheader("No-trade / portfolio reasons")
            st.json({
                "filtered_no_trade_reasons": summary["filtered_no_trade_reasons"],
                "portfolio_skip_reasons": summary["skip_reasons"],
            })
