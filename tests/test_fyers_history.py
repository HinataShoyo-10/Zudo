from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.fyers_history import download_history, normalize_response, write_csv


def epoch(year, month, day, hour=3, minute=45):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


def test_normalize_response_converts_epoch_to_ist():
    rows = normalize_response({"s": "ok", "candles": [[epoch(2025, 1, 2), 100, 102, 99, 101, 1200]]})
    assert rows[0]["timestamp"] == "2025-01-02T09:15:00+05:30"
    assert rows[0]["close"] == 101.0
    assert rows[0]["volume"] == 1200.0


def test_normalize_response_rejects_api_error():
    with pytest.raises(RuntimeError, match="history request failed"):
        normalize_response({"s": "error", "message": "invalid symbol"})


def test_normalize_response_rejects_bad_ohlc():
    with pytest.raises(ValueError, match="OHLC"):
        normalize_response({"s": "ok", "candles": [[epoch(2025, 1, 2), 100, 99, 98, 101, 100]]})


class FakeClient:
    def __init__(self):
        self.calls = []

    def history(self, data):
        self.calls.append(data)
        day = data["range_from"]
        if day == "2025-01-01":
            candles = [[epoch(2025, 1, 1), 100, 102, 99, 101, 1000]]
        elif day == "2025-01-03":
            candles = [
                [epoch(2025, 1, 3), 101, 103, 100, 102, 1100],
                # Deliberate duplicate to verify de-duplication.
                [epoch(2025, 1, 1), 100, 102, 99, 101, 1000],
            ]
        else:
            candles = []
        return {"s": "ok", "candles": candles}


def test_download_chunks_and_deduplicates():
    client = FakeClient()
    rows = download_history(
        client, "NSE:SBIN-EQ", "5", date(2025, 1, 1), date(2025, 1, 3), max_days_per_request=2
    )
    assert len(client.calls) == 2
    assert client.calls[0]["range_from"] == "2025-01-01"
    assert client.calls[0]["range_to"] == "2025-01-02"
    assert client.calls[1]["range_from"] == "2025-01-03"
    assert len(rows) == 2
    assert rows[0]["timestamp"] < rows[1]["timestamp"]


def test_download_rejects_invalid_date_range():
    with pytest.raises(ValueError, match="on or after"):
        download_history(FakeClient(), "NSE:SBIN-EQ", "5", date(2025, 1, 2), date(2025, 1, 1))


def test_write_csv_creates_parent_and_expected_columns(tmp_path: Path):
    output = tmp_path / "nested" / "candles.csv"
    rows = [{"timestamp": "2025-01-02T09:15:00+05:30", "open": 100, "high": 102, "low": 99, "close": 101, "volume": 1000}]
    write_csv(rows, output)
    text = output.read_text(encoding="utf-8")
    assert text.splitlines()[0] == "timestamp,open,high,low,close,volume"
    assert "2025-01-02T09:15:00+05:30" in text
