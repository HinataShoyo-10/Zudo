import pytest

from src.brain import BrainConfig, OptionCandidate, assess_candidate, select_candidate


def candidate(symbol="NIFTY-ATM", strike=100, premium=20, **kwargs):
    defaults = dict(
        symbol=symbol,
        strike=strike,
        entry_premium=premium,
        lot_size=75,
        index_price=100,
        bid=premium - 0.1,
        ask=premium + 0.1,
        volume=1000,
        timestamp_aligned=True,
        data_valid=True,
    )
    defaults.update(kwargs)
    return OptionCandidate(**defaults)


def test_config_keeps_risk_ceiling_at_one_percent():
    BrainConfig().validate()
    with pytest.raises(ValueError):
        BrainConfig(hard_risk_fraction=0.02).validate()


def test_affordable_candidate_passes_gates():
    result = assess_candidate(candidate(), BrainConfig())
    assert result.eligible
    assert result.reasons == ()
    assert result.estimated_stop_risk < 500
    assert result.one_lot_exposure < 20_000


def test_high_premium_candidate_is_rejected_for_risk():
    result = assess_candidate(candidate(premium=100), BrainConfig())
    assert not result.eligible
    assert "one_lot_exceeds_target_risk" in result.reasons
    assert "one_lot_exceeds_hard_risk_limit" in result.reasons


def test_missing_liquidity_is_not_silently_accepted():
    result = assess_candidate(candidate(bid=None, ask=None, volume=None), BrainConfig())
    assert not result.eligible
    assert "missing_bid_ask" in result.reasons
    assert "missing_volume" in result.reasons


def test_wide_spread_and_misalignment_are_rejected():
    result = assess_candidate(
        candidate(bid=10, ask=20, timestamp_aligned=False), BrainConfig()
    )
    assert not result.eligible
    assert "spread_too_wide" in result.reasons
    assert "timestamp_misalignment" in result.reasons


def test_selects_nearest_atm_only_among_eligible_contracts():
    selected, assessments = select_candidate(
        [
            candidate(symbol="near-but-expensive", strike=100, premium=100),
            candidate(symbol="farther-but-affordable", strike=105, premium=20),
            candidate(symbol="nearest-affordable", strike=101, premium=20),
        ],
        BrainConfig(),
    )
    assert selected is not None
    assert selected.symbol == "nearest-affordable"
    assert len(assessments) == 3


def test_no_trade_is_valid_result():
    selected, assessments = select_candidate(
        [candidate(premium=100)], BrainConfig()
    )
    assert selected is None
    assert len(assessments) == 1



def test_research_mode_warns_when_quotes_are_missing_but_does_not_fake_them():
    result = assess_candidate(
        candidate(bid=None, ask=None),
        BrainConfig(require_liquidity_data=False),
    )
    assert result.eligible
    assert result.warnings == ("liquidity_unverified_missing_bid_ask",)
    assert result.reasons == ()


def test_strict_mode_rejects_missing_quotes_even_if_volume_exists():
    result = assess_candidate(
        candidate(bid=None, ask=None),
        BrainConfig(require_liquidity_data=True),
    )
    assert not result.eligible
    assert "missing_bid_ask" in result.reasons


def test_estimated_risk_includes_transaction_costs():
    result = assess_candidate(candidate(premium=20, lot_size=75), BrainConfig())
    price_only_risk = result.entry_estimate * (1 - (1 - BrainConfig().stop_loss_fraction) * (1 - BrainConfig().slippage_bps_per_side / 10_000)) * 75
    assert result.estimated_stop_risk > price_only_risk
