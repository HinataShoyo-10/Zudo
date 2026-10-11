from decimal import Decimal
import pytest

from src.indicators import ema, vwap


def test_ema():
    result = ema([Decimal("10"), Decimal("12"), Decimal("14")], 2)
    assert len(result) == 3
    assert result[0] == Decimal("10")
    assert result[1] == Decimal("11.33333333333333333333333333")


def test_vwap():
    assert vwap([Decimal("10"), Decimal("20")], [1, 3]) == Decimal("17.5")


def test_vwap_zero_volume():
    assert vwap([Decimal("10")], [0]) is None


def test_vwap_length_mismatch():
    with pytest.raises(ValueError):
        vwap([Decimal("10")], [])
