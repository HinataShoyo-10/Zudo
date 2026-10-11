from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.backtesting.engine import Bar
from src.scanning.fyers_workflow import (
    fetch_symbol_history,
    intraday_history_dates,
    load_symbols,
    premarket_history_dates,
)
from src.scanning.premarket import rank_premarket


def test_default_watchlist_is_bounded_to_fifty_symbols():
    from pathlib import Path

    symbols = load_symbols(Path(__file__).resolve().parents[1] / "config" / "watchlist.txt")
    assert len(symbols) == 50
    assert len(symbols) == len(set(symbols))


def daily_bars(multiplier: float = 1.0, include_today: bool = False) -> list[Bar]:
    start = datetime(2026, 7, 1, 9, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    bars = []
    for index in range(70):
        close = (100.0 + index * multiplier) * multiplier
        bars.append(Bar(
            start + timedelta(days=index),
            close - 0.5,
            close + 1.0,
            close - 1.0,
            close,
            1_500.0 if index == 69 else 1_000.0,
        ))
    if include_today:
        bars.append(Bar(
            datetime(2026, 9, 9, 9, 15, tzinfo=start.tzinfo),
            300.0, 301.0, 299.0, 300.0, 1_000_000.0,
        ))
    return bars


def test_premarket_rank_uses_completed_daily_history_and_shortlists_leaders():
    results = rank_premarket(
        {
            "NSE:AAA-EQ": daily_bars(),
            "NSE:BBB-EQ": daily_bars(0.8),
        },
        as_of=datetime(2026, 9, 10, 8, 0, tzinfo=daily_bars()[0].timestamp.tzinfo),
        shortlist_size=1,
        min_score=55,
    )
    leaders = [row for row in results if row["shortlisted"]]
    assert len(leaders) == 1
    assert leaders[0]["symbol"] == "NSE:AAA-EQ"
    assert leaders[0]["status"] == "WATCH"
    assert leaders[0]["rank"] == 1
    assert leaders[0]["data_as_of"] == "2026-09-08"
    assert leaders[0]["momentum_20d_pct"] > 0
    assert 0 <= leaders[0]["score"] <= 100


def test_premarket_ranking_excludes_same_day_partial_daily_bar():
    bars = daily_bars(include_today=True)
    results = rank_premarket(
        {"NSE:AAA-EQ": bars},
        as_of=datetime(2026, 9, 9, 8, 0, tzinfo=bars[0].timestamp.tzinfo),
    )
    assert results[0]["data_as_of"] == "2026-09-08"
    assert results[0]["close"] == bars[-2].close


def test_premarket_includes_benchmark_relative_strength_levels_rsi_and_liquidity_proxy():
    stock = daily_bars()
    benchmark = daily_bars(0.8)
    results = rank_premarket(
        {"NSE:AAA-EQ": stock},
        benchmark_bars=benchmark,
        as_of=datetime(2026, 9, 10, 8, 0, tzinfo=stock[0].timestamp.tzinfo),
    )
    row = results[0]
    assert row["market_trend"] == "bullish"
    assert row["benchmark_data_as_of"] == "2026-09-08"
    assert row["relative_strength_vs_nifty_20d_pct"] > 0
    assert row["rsi_14"] == pytest.approx(100.0)
    assert row["previous_day_high"] == stock[-1].high
    assert row["previous_day_low"] == stock[-1].low
    assert row["previous_day_close"] == stock[-1].close
    assert row["average_traded_value_20d"] > 0


def test_premarket_benchmark_excludes_current_day_candle():
    bars = daily_bars(include_today=True)
    results = rank_premarket(
        {"NSE:AAA-EQ": daily_bars()},
        benchmark_bars=bars,
        as_of=datetime(2026, 9, 9, 8, 0, tzinfo=bars[0].timestamp.tzinfo),
    )
    assert results[0]["benchmark_data_as_of"] == "2026-09-08"


def test_premarket_ranking_reports_insufficient_data_and_rejects_naive_as_of():
    results = rank_premarket(
        {"NSE:AAA-EQ": daily_bars()[:20]},
        as_of=datetime(2026, 9, 10, 8, tzinfo=daily_bars()[0].timestamp.tzinfo),
    )
    assert results[0]["status"] == "INSUFFICIENT_DATA"
    assert results[0]["average_traded_value_20d"] is None
    with pytest.raises(ValueError, match="timezone-aware"):
        rank_premarket({"NSE:AAA-EQ": daily_bars()}, as_of=datetime(2026, 9, 10))


def test_watchlist_loader_enforces_unique_symbols_and_limit(tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_text("# comments\nNSE:AAA-EQ\n\nNSE:BBB-EQ # comment\n", encoding="utf-8")
    assert load_symbols(path) == ["NSE:AAA-EQ", "NSE:BBB-EQ"]
    path.write_text("NSE:AAA-EQ\nNSE:AAA-EQ\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_symbols(path)


class FakeHistoryClient:
    def __init__(self):
        self.calls = []

    def history(self, data):
        self.calls.append(data)
        timestamp = datetime(2025, 1, 6, 3, 45, tzinfo=timezone.utc).timestamp()
        return {
            "s": "ok",
            "candles": [[timestamp, 100, 102, 99, 101, 1000]],
        }


def test_fyers_history_fetch_is_paced_auditable_and_reports_progress(tmp_path):
    client = FakeHistoryClient()
    sleeps = []
    progress = []
    assets = fetch_symbol_history(
        client,
        ["NSE:AAA-EQ", "NSE:BBB-EQ"],
        "D",
        date(2025, 1, 1),
        date(2025, 1, 7),
        tmp_path,
        request_interval_seconds=0.75,
        sleep=sleeps.append,
        progress_callback=lambda current, total, symbol: progress.append(
            (current, total, symbol)
        ),
    )
    assert len(client.calls) == 2
    assert sleeps == [0.75]
    assert progress == [(1, 2, "NSE:AAA-EQ"), (2, 2, "NSE:BBB-EQ")]
    assert sorted(assets) == ["NSE:AAA-EQ", "NSE:BBB-EQ"]
    assert (tmp_path / "nse_aaa-eq_D.csv").exists()
    assert (tmp_path / "nse_bbb-eq_D.csv").exists()


def test_fyers_fetch_fails_closed_if_any_symbol_has_no_data(tmp_path):
    class PartiallyEmptyClient:
        def history(self, data):
            if data["symbol"] == "NSE:BAD-EQ":
                return {"s": "ok", "candles": []}
            timestamp = datetime(2025, 1, 6, 3, 45, tzinfo=timezone.utc).timestamp()
            return {"s": "ok", "candles": [[timestamp, 100, 102, 99, 101, 1000]]}

    with pytest.raises(RuntimeError, match="NSE:BAD-EQ"):
        fetch_symbol_history(
            PartiallyEmptyClient(),
            ["NSE:GOOD-EQ", "NSE:BAD-EQ"],
            "D",
            date(2025, 1, 1),
            date(2025, 1, 2),
            tmp_path,
            request_interval_seconds=0,
        )


def test_workflow_date_helpers_bound_history_requests():
    today = date(2026, 10, 11)
    assert premarket_history_dates(today) == (today - timedelta(days=180), today)
    assert intraday_history_dates(today) == (today, today)
    with pytest.raises(ValueError, match="between 90 and 365"):
        premarket_history_dates(today, 20)
