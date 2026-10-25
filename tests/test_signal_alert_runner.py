from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.models import Tick
from src.signal_alert_runner import SignalAlertRunner

IST = ZoneInfo("Asia/Kolkata")


class FakeAlerter:
    def __init__(self):
        self.messages = []

    def send_text(self, message):
        self.messages.append(message)
        return {"ok": True}


def test_runner_sends_and_deduplicates_early_warning():
    alerter = FakeAlerter()
    runner = SignalAlertRunner(alerter)
    start = datetime(2026, 10, 26, 9, 15, tzinfo=IST)

    # Three completed 5-minute opening-range bars, with valid synthetic deltas.
    prices = [Decimal("99"), Decimal("100"), Decimal("99.5")]
    for index, price in enumerate(prices):
        ts = start + timedelta(minutes=5 * index)
        runner.process_tick(Tick("NSE:NIFTY50-INDEX", ts, price, 100, True))

    # This update closes the 09:25 bar and crosses above the completed range.
    cross_time = start + timedelta(minutes=15)
    runner.process_tick(Tick("NSE:NIFTY50-INDEX", cross_time, Decimal("101.5"), 10, True))
    runner.process_tick(Tick("NSE:NIFTY50-INDEX", cross_time + timedelta(seconds=1),
                             Decimal("101.6"), 10, True))

    assert len(alerter.messages) == 1
    assert "Status: EARLY_WARNING" in alerter.messages[0]
    assert "No order was placed." in alerter.messages[0]
