"""Backward-compatible entry point; implementation lives in src.backtesting."""
from src.backtesting.options import (
    CostRates,
    parse_time,
    parse_timestamp,
    number,
    load_candles,
    order_charges,
    _entry_fill,
    _exit_fill,
    _planned_risk,
    _size_position,
    _find_exit,
    _equity_curve,
    _max_drawdown_pct,
    run_backtest,
    _write_csv,
    main,
)

__all__ = [
    "CostRates",
    "parse_time",
    "parse_timestamp",
    "number",
    "load_candles",
    "order_charges",
    "_entry_fill",
    "_exit_fill",
    "_planned_risk",
    "_size_position",
    "_find_exit",
    "_equity_curve",
    "_max_drawdown_pct",
    "run_backtest",
    "_write_csv",
    "main",
]


if __name__ == "__main__":
    main()
