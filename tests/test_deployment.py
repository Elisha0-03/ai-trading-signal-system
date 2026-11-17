"""Offline regression tests for fail-closed scheduled execution."""
import os
import unittest
from unittest.mock import patch

import main
from src.config import load_settings


class CredentialSafetyTests(unittest.TestCase):
    def test_every_missing_or_blank_secret_blocks_all_live_work(self):
        names = ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
        settings = load_settings()
        for missing in names:
            for blank in ("", "   "):
                environment = {name: "offline-placeholder" for name in names}
                environment[missing] = blank
                with self.subTest(missing=missing, blank=repr(blank)):
                    with patch.dict(os.environ, environment, clear=True), \
                         patch("main.capture_cycle") as capture, \
                         patch("main.GeminiClient") as gemini, \
                         patch("main.TelegramClient") as telegram:
                        with self.assertRaisesRegex(RuntimeError, missing):
                            main.run_cycle(settings)
                        capture.assert_not_called()
                        gemini.assert_not_called()
                        telegram.assert_not_called()

    def test_no_credentials_blocks_all_live_work(self):
        settings = load_settings()
        with patch.dict(os.environ, {}, clear=True), \
             patch("main.capture_cycle") as capture, \
             patch("main.GeminiClient") as gemini, \
             patch("main.TelegramClient") as telegram:
            with self.assertRaisesRegex(RuntimeError, "Missing required environment variables"):
                main.run_cycle(settings)
            capture.assert_not_called()
            gemini.assert_not_called()
            telegram.assert_not_called()


if __name__ == "__main__":
    unittest.main()
