"""Minimal Telegram Bot API sender. No message parsing or command execution is enabled."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str, config: dict[str, Any]):
        if not bot_token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        self.chat_id = chat_id
        self.endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        self.delay = max(0.0, float(config.get("message_delay_seconds", 3)))
        self.max_chars = min(4096, int(config.get("max_message_chars", 3900)))
        self.session = requests.Session()

    def send(self, text: str) -> None:
        if not text or len(text) > self.max_chars:
            raise ValueError(f"Telegram message must be 1-{self.max_chars} characters")
        response = self.session.post(
            self.endpoint,
            json={"chat_id": self.chat_id, "text": text, "disable_web_page_preview": True},
            timeout=30,
        )
        if not response.ok:
            # Avoid logging the token-bearing URL.
            raise RuntimeError(f"Telegram send failed with HTTP {response.status_code}: {response.text[:300]}")
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram rejected the message: {body.get('description', 'unknown error')}")
        log.info("Telegram message delivered")
        if self.delay:
            time.sleep(self.delay)
