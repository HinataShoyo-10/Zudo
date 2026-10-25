from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
import requests

from src.telegram_alerts import TelegramAlerter, format_signal_alert


def test_send_text_calls_telegram_without_logging_token():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    with patch("src.telegram_alerts.requests.post", return_value=response) as post:
        client = TelegramAlerter("secret-token", "12345")
        result = client.send_text("hello")
    assert result["ok"] is True
    assert post.call_args.args[0] == "https://api.telegram.org/botsecret-token/sendMessage"
    assert post.call_args.kwargs["json"]["chat_id"] == "12345"
    assert post.call_args.kwargs["json"]["text"] == "hello"
    assert post.call_args.kwargs["timeout"] == 10.0


def test_send_text_rejects_empty_message():
    client = TelegramAlerter("token", "12345")
    with pytest.raises(ValueError):
        client.send_text("  ")


def test_send_text_rejects_api_failure():
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": False, "description": "chat not found"}
    with patch("src.telegram_alerts.requests.post", return_value=response):
        with pytest.raises(RuntimeError, match="chat not found"):
            TelegramAlerter("token", "12345").send_text("hello")


def test_format_early_warning_is_explicitly_not_an_entry():
    signal = SimpleNamespace(
        status="EARLY_WARNING", direction="BULLISH", timeframe_minutes=5,
        timestamp="2026-10-25T10:00:00+05:30", reason="developing_breakout",
        reference_price="25100", opening_range_high="25090", opening_range_low="24950",
        vwap=None, relative_volume=None, invalidation_level="25090",
    )
    message = format_signal_alert(signal)
    assert "Heads-up only. Not a confirmed entry." in message
    assert "No order was placed." in message


def test_format_candidate_includes_manual_risk_reminder():
    signal = SimpleNamespace(
        status="CONFIRMED_CANDIDATE", direction="BEARISH", timeframe_minutes=1,
        timestamp="2026-10-25T10:00:00+05:30", reason="one_minute_retest",
        reference_price="25000", opening_range_high="25100", opening_range_low="25050",
        vwap="25020", relative_volume="1.8", invalidation_level="25050",
    )
    message = format_signal_alert(signal)
    assert "Check option quote, spread, liquidity and risk" in message
    assert "No order was placed." in message
