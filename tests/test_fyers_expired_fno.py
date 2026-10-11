from __future__ import annotations

import pytest

from src.fyers_expired_fno import BASE_URL, FyersExpiredFnoClient, _validate_date_range


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.payload)


def test_expiry_dates_calls_read_only_endpoint_with_params():
    http = FakeHttp({"s": "ok", "data": ["2025-01-30"]})
    client = FyersExpiredFnoClient("app-id", "token-value", http=http)
    response = client.expiry_dates("NSE:NIFTY50-INDEX", "2025-01-01", "2025-01-31")

    assert response["s"] == "ok"
    url, kwargs = http.calls[0]
    assert url == f"{BASE_URL}/expiry-dates"
    assert kwargs["params"] == {
        "symbol": "NSE:NIFTY50-INDEX",
        "range_from": "2025-01-01",
        "range_to": "2025-01-31",
        "date_format": "1",
    }
    assert kwargs["headers"]["Authorization"] == "app-id:token-value"
    assert kwargs["timeout"] == 30


def test_contract_symbols_uses_underlying_and_expiry():
    http = FakeHttp({"s": "ok", "data": []})
    client = FyersExpiredFnoClient("app-id", "token", http=http)
    client.contract_symbols("NSE:NIFTY50-INDEX", "2025-01-30")
    assert http.calls[0][0] == f"{BASE_URL}/underlying-symbols"
    assert http.calls[0][1]["params"] == {
        "symbol": "NSE:NIFTY50-INDEX",
        "expiry_date": "2025-01-30",
    }


def test_historical_contract_includes_flags():
    http = FakeHttp({"s": "ok", "candles": []})
    client = FyersExpiredFnoClient("app-id", "token", http=http)
    client.historical_contract(
        "NSE:NIFTY25JAN23000CE", "5", "2025-01-01", "2025-01-30",
        include_greeks=True, include_oi=True,
    )
    url, kwargs = http.calls[0]
    assert url == f"{BASE_URL}/historical-data"
    assert kwargs["params"] == {
        "symbol": "NSE:NIFTY25JAN23000CE",
        "resolution": "5",
        "date_format": "1",
        "range_from": "2025-01-01",
        "range_to": "2025-01-30",
        "include_greeks": "1",
        "include_oi": "1",
    }


@pytest.mark.parametrize("start,end", [
    ("not-a-date", "2025-01-31"),
    ("2025-02-01", "2025-01-31"),
])
def test_date_range_validation_rejects_invalid_or_reversed_dates(start, end):
    with pytest.raises(ValueError):
        _validate_date_range(start, end)


def test_api_error_is_reported_without_changing_endpoint_scope():
    http = FakeHttp({"s": "error", "message": "invalid symbol"})
    client = FyersExpiredFnoClient("app-id", "token", http=http)
    with pytest.raises(RuntimeError, match="invalid symbol"):
        client.expiry_dates("NSE:NIFTY50-INDEX", "2025-01-01", "2025-01-31")


def test_empty_credentials_are_rejected():
    with pytest.raises(ValueError, match="FYERS_APP_ID"):
        FyersExpiredFnoClient("", "token", http=FakeHttp({"s": "ok"}))
