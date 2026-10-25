from datetime import datetime
from zoneinfo import ZoneInfo

from src.feeds import normalize_fyers_message

IST = ZoneInfo("Asia/Kolkata")


def message(volume, second=0):
    return {
        "symbol": "NSE:NIFTY50-INDEX",
        "ltp": "25100.5",
        "last_traded_time": 1791796500 + second,
        "vol_traded_today": volume,
    }


def test_first_cumulative_volume_observation_is_unverified():
    previous = {}
    tick = normalize_fyers_message(message(1000), previous)
    assert tick is not None
    assert tick.volume == 0
    assert tick.volume_valid is False
    assert previous["NSE:NIFTY50-INDEX"] == 1000


def test_cumulative_volume_becomes_delta_after_baseline():
    previous = {"NSE:NIFTY50-INDEX": 1000}
    tick = normalize_fyers_message(message(1017, 1), previous)
    assert tick is not None
    assert tick.volume == 17
    assert tick.volume_valid is True


def test_missing_volume_is_not_treated_as_valid_zero():
    tick = normalize_fyers_message({"symbol": "NSE:X", "ltp": 10}, {})
    assert tick is not None
    assert tick.volume == 0
    assert tick.volume_valid is False


def test_volume_counter_reset_is_marked_invalid_and_rebaselined():
    previous = {"NSE:X": 500}
    tick = normalize_fyers_message(
        {"symbol": "NSE:X", "ltp": 10, "vol_traded_today": 2}, previous
    )
    assert tick is not None
    assert tick.volume_valid is False
    assert previous["NSE:X"] == 2
