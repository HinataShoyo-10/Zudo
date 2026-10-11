from collections.abc import Sequence
from decimal import Decimal


def ema(values: Sequence[Decimal], period: int) -> list[Decimal]:
    """EMA seeded with the first value; validate warm-up before research use."""
    if period <= 0:
        raise ValueError("period must be positive")
    if not values:
        return []
    alpha = Decimal(2) / Decimal(period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (Decimal(1) - alpha))
    return result


def vwap(prices: Sequence[Decimal], volumes: Sequence[int]) -> Decimal | None:
    if len(prices) != len(volumes):
        raise ValueError("prices and volumes must have equal lengths")
    total_volume = sum(volumes)
    if total_volume == 0:
        return None
    weighted = sum((price * volume for price, volume in zip(prices, volumes)), Decimal("0"))
    return weighted / Decimal(total_volume)
