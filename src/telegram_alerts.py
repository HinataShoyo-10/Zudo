"""Telegram notification client for Zudo; notification-only, never places orders."""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Any

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
DEFAULT_ENV_FILE = "/workspaces/Zudo/.env"


def load_telegram_config() -> tuple[str, str]:
    """Load local Telegram settings without printing or exposing the bot token."""
    load_dotenv()
    if os.path.isfile(DEFAULT_ENV_FILE):
        load_dotenv(DEFAULT_ENV_FILE, override=False)
    token = (
        os.getenv("Telegram_bot_token", "").strip()
        or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    )
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    missing = []
    if not token:
        missing.append("Telegram_bot_token")
    if not chat_id:
        missing.append("TELEGRAM_CHAT_ID")
    if missing:
        raise ValueError("Missing Telegram configuration in .env: " + ", ".join(missing))
    return token, chat_id


class TelegramAlerter:
    """Small synchronous sender for Telegram Bot API sendMessage."""

    def __init__(self, token: str, chat_id: str, timeout_seconds: float = 10.0) -> None:
        if not token.strip():
            raise ValueError("Telegram bot token must not be empty")
        if not chat_id.strip():
            raise ValueError("Telegram chat ID must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._token = token.strip()
        self._chat_id = chat_id.strip()
        self._timeout = timeout_seconds

    @property
    def api_base(self) -> str:
        return f"https://api.telegram.org/bot{self._token}"

    def send_text(self, text: str) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("Telegram message must not be empty")
        response = requests.post(
            f"{self.api_base}/sendMessage",
            json={
                "chat_id": self._chat_id,
                "text": text[:4096],
                "disable_web_page_preview": True,
            },
            timeout=self._timeout,
        )
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            # Do not include request URL or token in error messages.
            raise RuntimeError(f"Telegram rejected sendMessage: {payload.get('description', 'unknown error')}")
        return payload

    def send_test(self) -> dict[str, Any]:
        return self.send_text(
            "Zudo Telegram test\n"
            "Status: CONNECTED\n"
            "Mode: notifications only; no order was placed."
        )

    def discover_chat_ids(self) -> list[dict[str, str]]:
        """Return chat IDs from recent updates; user must message the bot first."""
        response = requests.get(f"{self.api_base}/getUpdates", timeout=self._timeout)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError("Telegram getUpdates failed")
        found: dict[str, dict[str, str]] = {}
        for update in payload.get("result", []):
            message = update.get("message") or update.get("channel_post") or {}
            chat = message.get("chat") or {}
            if chat.get("id") is not None:
                chat_id = str(chat["id"])
                found[chat_id] = {
                    "chat_id": chat_id,
                    "type": str(chat.get("type", "")),
                    "title": str(chat.get("title") or chat.get("username") or ""),
                }
        return list(found.values())


def format_signal_alert(signal: Any, symbol: str = "NIFTY 50") -> str:
    """Render a signal object without implying that a trade/order was executed."""
    status = str(getattr(signal, "status", "WAIT"))
    direction = getattr(signal, "direction", None) or "NONE"
    timeframe = getattr(signal, "timeframe_minutes", "?")
    timestamp = getattr(signal, "timestamp", "")
    reason = str(getattr(signal, "reason", "unspecified"))
    lines = [
        f"Zudo | {symbol}",
        f"Status: {status}",
        f"Direction: {direction}",
        f"Timeframe: {timeframe}m",
        f"Time: {timestamp}",
        f"Reason: {reason}",
    ]
    for label, attr in (
        ("Price", "reference_price"),
        ("Opening range high", "opening_range_high"),
        ("Opening range low", "opening_range_low"),
        ("VWAP", "vwap"),
        ("Relative volume", "relative_volume"),
        ("Reference/invalidation level", "invalidation_level"),
    ):
        value = getattr(signal, attr, None)
        if value is not None:
            lines.append(f"{label}: {value}")
    if status == "EARLY_WARNING":
        lines.append("Action: Heads-up only. Not a confirmed entry.")
    elif status == "CONFIRMED_CANDIDATE":
        lines.append("Action: Candidate only. Check option quote, spread, liquidity and risk before any manual trade.")
    else:
        lines.append("Action: WAIT. No trade signal.")
    lines.append("No order was placed.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Zudo Telegram notification setup/test")
    parser.add_argument("--test", action="store_true", help="send a test message to TELEGRAM_CHAT_ID")
    parser.add_argument("--discover-chat-id", action="store_true",
                        help="show chat IDs from recent bot updates (message the bot first)")
    args = parser.parse_args()
    if not args.test and not args.discover_chat_id:
        parser.error("choose --test or --discover-chat-id")

    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(message)s")
    try:
        # Chat ID is only required for sending, not discovery.
        load_dotenv()
        if os.path.isfile(DEFAULT_ENV_FILE):
            load_dotenv(DEFAULT_ENV_FILE, override=False)
        token = (os.getenv("Telegram_bot_token", "").strip()
                 or os.getenv("TELEGRAM_BOT_TOKEN", "").strip())
        if not token:
            raise ValueError("Missing Telegram_bot_token in .env")
        client = TelegramAlerter(token, os.getenv("TELEGRAM_CHAT_ID", "discovery-only"))
        if args.discover_chat_id:
            chats = client.discover_chat_ids()
            if not chats:
                print("No chats found. Open your bot in Telegram, press Start, send it a message, then retry.")
                return 1
            for chat in chats:
                print(f"chat_id={chat['chat_id']} type={chat['type']} title={chat['title']}")
            return 0
        _, chat_id = load_telegram_config()
        client = TelegramAlerter(token, chat_id)
        client.send_test()
        print("Telegram test message sent. Check your Telegram chat.")
        return 0
    except (ValueError, requests.RequestException, RuntimeError) as exc:
        print(f"Telegram setup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
