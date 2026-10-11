from datetime import datetime, timedelta, timezone

from src.backtesting.engine import Bar
from src.paper.session import advance_paper_session, new_paper_state


def _bar(time: str, open_: float, high: float, low: float, close: float) -> Bar:
    timestamp = datetime.fromisoformat(f"2026-10-12T{time}:00+05:30")
    return Bar(timestamp, open_, high, low, close, 1_000.0)


def _candidate(symbol: str = "NSE:AAA-EQ", score: float = 80.0) -> dict:
    return {
        "symbol": symbol,
        "status": "OPPORTUNITY",
        "session_date": "2026-10-12",
        "strategy": "opening_range_breakout",
        "signal_time": "2026-10-12T09:20:00+05:30",
        "planned_entry_time": "2026-10-12T09:25:00+05:30",
        "atr": 1.0,
        "setup_score": score,
        "rank": 1,
    }


def test_paper_session_selects_best_candidate_fills_next_bar_and_closes_target():
    state = new_paper_state(50_000)
    signal_bars = [_bar("09:15", 99, 100, 98, 99), _bar("09:20", 100, 101, 99.5, 101)]
    state = advance_paper_session(
        state,
        {"NSE:AAA-EQ": signal_bars, "NSE:BBB-EQ": signal_bars},
        [_candidate("NSE:AAA-EQ", 80), _candidate("NSE:BBB-EQ", 90)],
        as_of=datetime.fromisoformat("2026-10-12T09:25:00+05:30"),
        slippage_bps=0,
    )
    assert state["pending"]["symbol"] == "NSE:BBB-EQ"
    assert state["position"] is None

    entry_bars = signal_bars + [_bar("09:25", 101, 102, 100.5, 101.5)]
    state = advance_paper_session(
        state,
        {"NSE:AAA-EQ": entry_bars, "NSE:BBB-EQ": entry_bars},
        [],
        as_of=datetime.fromisoformat("2026-10-12T09:30:00+05:30"),
        slippage_bps=0,
    )
    assert state["position"]["symbol"] == "NSE:BBB-EQ"
    assert state["position"]["entry_price"] == 101

    exited_bars = entry_bars + [_bar("09:30", 102, 104, 101, 103)]
    state = advance_paper_session(
        state,
        {"NSE:AAA-EQ": exited_bars, "NSE:BBB-EQ": exited_bars},
        [],
        as_of=datetime.fromisoformat("2026-10-12T09:35:00+05:30"),
        slippage_bps=0,
    )
    assert state["position"] is None
    assert len(state["trades"]) == 1
    assert state["trades"][0]["exit_reason"] == "take_profit"
    assert state["trades"][0]["net_pnl"] > 0
    assert state["trade_taken_today"]


def test_paper_session_is_idempotent_and_stop_wins_if_stop_and_target_touch():
    state = new_paper_state(50_000)
    bars = [
        _bar("09:15", 99, 100, 98, 99),
        _bar("09:20", 100, 101, 99.5, 101),
    ]
    state = advance_paper_session(
        state, {"NSE:AAA-EQ": bars}, [_candidate()],
        as_of=datetime.fromisoformat("2026-10-12T09:25:00+05:30"),
        slippage_bps=0,
    )
    bars.append(_bar("09:25", 101, 102, 100.5, 101.5))
    state = advance_paper_session(
        state, {"NSE:AAA-EQ": bars}, [],
        as_of=datetime.fromisoformat("2026-10-12T09:30:00+05:30"),
        slippage_bps=0,
    )
    bars.append(_bar("09:30", 101, 104, 99, 102))
    state = advance_paper_session(
        state, {"NSE:AAA-EQ": bars}, [],
        as_of=datetime.fromisoformat("2026-10-12T09:35:00+05:30"),
        slippage_bps=0,
    )
    assert state["trades"][0]["exit_reason"] == "stop_loss"
    previous_cash = state["cash"]
    state = advance_paper_session(
        state, {"NSE:AAA-EQ": bars}, [],
        as_of=datetime.fromisoformat("2026-10-12T09:35:00+05:30"),
        slippage_bps=0,
    )
    assert len(state["trades"]) == 1
    assert state["cash"] == previous_cash
