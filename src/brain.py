"""Deterministic pre-trade gates for Zudo's research-only options brain.

This module deliberately does not generate signals or place orders. It ranks only
candidates that pass hard risk, exposure, and data-quality gates. Missing liquidity
fields are rejected by default rather than guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from src.options_backtest import CostRates, order_charges


@dataclass(frozen=True)
class BrainConfig:
    capital: float = 100_000.0
    risk_fraction: float = 0.005
    hard_risk_fraction: float = 0.01
    max_exposure_fraction: float = 0.20
    stop_loss_fraction: float = 0.20
    slippage_bps_per_side: float = 5.0
    max_spread_fraction: float = 0.03
    min_volume: float = 1.0
    require_liquidity_data: bool = True
    cost_rates: CostRates = CostRates()

    def validate(self) -> None:
        if not isfinite(self.capital) or self.capital <= 0:
            raise ValueError("capital must be finite and positive")
        if not (0 < self.risk_fraction <= self.hard_risk_fraction <= 0.01):
            raise ValueError("require 0 < risk_fraction <= hard_risk_fraction <= 1%")
        if not (0 < self.max_exposure_fraction <= 1):
            raise ValueError("max_exposure_fraction must be in (0, 1]")
        if not (0 < self.stop_loss_fraction < 1):
            raise ValueError("stop_loss_fraction must be in (0, 1)")
        if self.slippage_bps_per_side < 0:
            raise ValueError("slippage_bps_per_side cannot be negative")
        if not (0 < self.max_spread_fraction <= 1):
            raise ValueError("max_spread_fraction must be in (0, 1]")
        if self.min_volume < 0:
            raise ValueError("min_volume cannot be negative")


@dataclass(frozen=True)
class OptionCandidate:
    symbol: str
    strike: float
    entry_premium: float
    lot_size: int
    index_price: float
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    timestamp_aligned: bool = True
    data_valid: bool = True


@dataclass(frozen=True)
class CandidateAssessment:
    symbol: str
    eligible: bool
    reasons: tuple[str, ...]
    entry_estimate: float | None
    one_lot_exposure: float | None
    estimated_stop_risk: float | None
    strike_distance: float | None
    warnings: tuple[str, ...] = ()


def assess_candidate(candidate: OptionCandidate, config: BrainConfig) -> CandidateAssessment:
    """Apply hard gates using only entry-time information, never future P&L."""
    config.validate()
    reasons: list[str] = []
    values = (candidate.strike, candidate.entry_premium, candidate.index_price)
    if not candidate.symbol.strip():
        reasons.append("missing_symbol")
    if not all(isfinite(v) for v in values):
        reasons.append("non_finite_price")
    if candidate.strike <= 0 or candidate.entry_premium <= 0 or candidate.index_price <= 0:
        reasons.append("non_positive_price")
    if candidate.lot_size <= 0:
        reasons.append("invalid_lot_size")
    if not candidate.timestamp_aligned:
        reasons.append("timestamp_misalignment")
    if not candidate.data_valid:
        reasons.append("invalid_market_data")

    warnings: list[str] = []
    if candidate.bid is None or candidate.ask is None:
        if config.require_liquidity_data:
            reasons.append("missing_bid_ask")
        else:
            warnings.append("liquidity_unverified_missing_bid_ask")
    elif (not isfinite(candidate.bid) or not isfinite(candidate.ask)
          or candidate.bid <= 0 or candidate.ask < candidate.bid):
        reasons.append("invalid_bid_ask")
    else:
        mid = (candidate.bid + candidate.ask) / 2
        if mid <= 0 or (candidate.ask - candidate.bid) / mid > config.max_spread_fraction:
            reasons.append("spread_too_wide")

    if candidate.volume is None:
        reasons.append("missing_volume")
    elif not isfinite(candidate.volume) or candidate.volume < config.min_volume:
        reasons.append("insufficient_volume")

    if reasons:
        return CandidateAssessment(candidate.symbol, False, tuple(dict.fromkeys(reasons)), None, None, None, None)

    entry = candidate.entry_premium * (1 + config.slippage_bps_per_side / 10_000)
    exit_fill = entry * (1 - config.stop_loss_fraction) * (1 - config.slippage_bps_per_side / 10_000)
    exposure = entry * candidate.lot_size
    # Match options_backtest._planned_risk: price loss plus estimated charges
    # on both the entry and stop exit. Prices use the same adverse slippage model.
    entry_charges = order_charges(entry, candidate.lot_size, "BUY", config.cost_rates)["total"]
    exit_charges = order_charges(exit_fill, candidate.lot_size, "SELL", config.cost_rates)["total"]
    risk = max(0.0, (entry - exit_fill) * candidate.lot_size) + entry_charges + exit_charges
    risk_budget = config.capital * config.risk_fraction
    hard_budget = config.capital * config.hard_risk_fraction
    exposure_budget = config.capital * config.max_exposure_fraction
    if exposure > exposure_budget:
        reasons.append("one_lot_exceeds_exposure_limit")
    if risk > risk_budget:
        reasons.append("one_lot_exceeds_target_risk")
    if risk > hard_budget:
        reasons.append("one_lot_exceeds_hard_risk_limit")

    return CandidateAssessment(
        candidate.symbol, not reasons, tuple(reasons), entry, exposure, risk,
        abs(candidate.strike - candidate.index_price), tuple(warnings),
    )


def select_candidate(
    candidates: Iterable[OptionCandidate], config: BrainConfig
) -> tuple[CandidateAssessment | None, list[CandidateAssessment]]:
    """Choose nearest-to-ATM among eligible candidates; no outcome-based ranking."""
    assessments = [assess_candidate(candidate, config) for candidate in candidates]
    eligible = [item for item in assessments if item.eligible]
    eligible.sort(key=lambda item: (item.strike_distance, item.estimated_stop_risk, item.symbol))
    return (eligible[0] if eligible else None), assessments
