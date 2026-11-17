"""Offline regression tests for the Gemini free-tier request budget."""
from __future__ import annotations

import copy
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import main
from src.config import ConfigurationError, _validate, enabled_instruments, enabled_strategies, load_settings
from src.gemini_client import GeminiClient, GeminiQuotaError, GeminiRequestBudgetError
from src.scheduler import seconds_until_next_cycle


class FreeQuotaConfigurationTests(unittest.TestCase):
    def test_expected_work_is_twenty_screenshots_and_five_analyses(self):
        settings = load_settings()

        self.assertEqual(len(enabled_instruments(settings)), 5)
        self.assertEqual(len(settings["timeframes"]), 4)
        self.assertEqual(main.expected_work(settings), (20, 5))
        self.assertEqual(set(enabled_strategies(settings)), {"v57_swing"})

    def test_three_daily_cycles_and_fifteen_daily_requests_are_consistent(self):
        settings = load_settings()
        scheduler = settings["scheduler"]

        self.assertEqual(int(scheduler["cycle_interval_minutes"]), 480)
        self.assertEqual(scheduler["delay_after_close_seconds"], 4620)
        self.assertEqual(1440 // scheduler["cycle_interval_minutes"], 3)
        self.assertEqual(main.expected_work(settings)[1] * 3, 15)
        for hour in (1, 9, 17):
            now = datetime(2026, 11, 17, hour, 16, tzinfo=timezone.utc)
            self.assertEqual(seconds_until_next_cycle(now, 480, 4620), 60)
        now = datetime(2026, 11, 17, 17, 18, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_next_cycle(now, 480, 4620), 479 * 60)
        import yaml
        workflow = yaml.safe_load((settings["root"] / ".github/workflows/trading-signals.yml").read_text())
        # PyYAML uses YAML 1.1 and parses GitHub's unquoted `on` key as True.
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(events["schedule"], [{"cron": "17 1,9,17 * * *"}])
        self.assertNotIn("workflow_dispatch", events)

    def test_free_tier_client_has_no_retries_and_five_request_cycle_budget(self):
        settings = load_settings()
        gemini = settings["gemini"]

        self.assertEqual(int(gemini["max_retries"]), 0)
        self.assertEqual(int(gemini["rate_limit_per_minute"]), 4)
        self.assertEqual(int(gemini["max_requests_per_cycle"]), 5)

    def test_validation_rejects_twenty_enabled_instruments_over_five_request_budget(self):
        settings = load_settings()
        settings["instruments"] = [
            {"symbol": f"TEST:{index}", "name": f"Test {index}", "enabled": True}
            for index in range(20)
        ]
        settings["strategies"] = {
            "v57_swing": settings["strategies"]["v57_swing"]
        }

        with self.assertRaises(ConfigurationError):
            _validate(settings)

    def test_validation_rejects_retries_greater_than_zero(self):
        settings = load_settings()
        settings["gemini"]["max_retries"] = 1

        with self.assertRaises(ConfigurationError):
            _validate(settings)

    def test_validation_rejects_quota_exhausting_fifteen_minute_interval(self):
        settings = load_settings()
        settings["scheduler"]["cycle_interval_minutes"] = 15

        with self.assertRaises(ConfigurationError):
            _validate(settings)

    def test_validation_rejects_paid_policy_and_startup_run(self):
        for section, key, value in (("gemini", "allow_paid", True), ("gemini", "usage_tier", "paid"),
                                    ("scheduler", "run_immediately", True)):
            with self.subTest(key=key):
                settings = load_settings()
                settings[section][key] = value
                with self.assertRaises(ConfigurationError):
                    _validate(settings)


class GeminiFreeQuotaClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()["gemini"]
        self.settings = copy.deepcopy(self.settings)
        self.settings.update({"max_retries": 0, "max_requests_per_cycle": 5, "rate_limit_per_minute": 4})

    @staticmethod
    def _success_response() -> Mock:
        response = Mock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {
            "candidates": [{"content": {"parts": [{"text": '{"direction":"NO_ENTRY"}'}]}}]
        }
        return response

    @staticmethod
    def _images(tmp: str) -> dict[str, Path]:
        image = Path(tmp) / "chart.png"
        image.write_bytes(b"offline-test-image")
        return {"15m": image}

    def test_sixth_analysis_fails_before_a_sixth_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = GeminiClient("offline-key", self.settings)
            client.gate.wait = Mock()
            client.session.post = Mock(side_effect=[self._success_response() for _ in range(5)])

            for number in range(5):
                self.assertEqual(
                    client.analyze("offline prompt", f"TEST:{number}", "Test", self._images(tmp)),
                    '{"direction":"NO_ENTRY"}',
                )

            with self.assertRaises(GeminiRequestBudgetError):
                client.analyze("offline prompt", "TEST:6", "Test", self._images(tmp))

            self.assertEqual(client.requests_attempted, 5)
            self.assertEqual(client.session.post.call_count, 5)

    def test_http_429_raises_quota_error_without_retry_even_if_configured(self):
        settings = copy.deepcopy(self.settings)
        settings["max_retries"] = 4
        client = GeminiClient("offline-key", settings)
        client.gate.wait = Mock()
        response = Mock()
        response.status_code = 429
        response.headers = {}
        response.text = "quota exceeded"
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        client.session.post = Mock(return_value=response)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(GeminiQuotaError):
                client.analyze("offline prompt", "TEST:429", "Test", self._images(tmp))

        self.assertEqual(client.requests_attempted, 1)
        client.session.post.assert_called_once()

    def test_http_500_uses_default_zero_retry_and_one_post_call(self):
        response = Mock()
        response.status_code = 500
        response.headers = {}
        response.text = "offline server error"
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        client = GeminiClient("offline-key", self.settings)
        client.gate.wait = Mock()
        client.session.post = Mock(return_value=response)

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                client.analyze("offline prompt", "TEST:500", "Test", self._images(tmp))

        self.assertEqual(client.requests_attempted, 1)
        client.session.post.assert_called_once()

    def test_timeout_consumes_an_attempt_without_retry(self):
        client = GeminiClient("offline-key", self.settings)
        client.gate.wait = Mock()
        client.session.post = Mock(side_effect=requests.Timeout("offline"))
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                client.analyze("offline prompt", "TEST:TIMEOUT", "Test", self._images(tmp))
        self.assertEqual(client.requests_attempted, 1)
        client.session.post.assert_called_once()


class QuotaCyclePropagationTests(unittest.TestCase):
    def test_run_cycle_re_raises_quota_error_instead_of_reporting_success(self):
        settings = load_settings()
        image = Path(tempfile.gettempdir()) / "quota-cycle-test.png"
        image.write_bytes(b"offline-test-image")
        quota_error = GeminiRequestBudgetError("cycle request budget exhausted")
        fake_client = Mock()
        fake_client.analyze.side_effect = quota_error

        with patch.dict(os.environ, {"GEMINI_API_KEY": "offline-key"}, clear=True), \
             patch("main.GeminiClient", return_value=fake_client), \
             patch("main.capture_cycle", return_value=({"FX:EURUSD": {"15m": image, "1h": image, "4h": image, "1D": image}}, {})), \
             patch("main.prompt_for", return_value="offline prompt"):
            with self.assertRaises(GeminiRequestBudgetError):
                main.run_cycle(settings, no_telegram=True)

        fake_client.analyze.assert_called_once()

    def test_http_quota_stops_before_another_analysis_or_telegram_send(self):
        settings = load_settings()
        images = {item["name"]: Path("offline-never-opened.png") for item in settings["timeframes"]}
        captures = {item["symbol"]: images for item in enabled_instruments(settings)}
        environment = {name: "offline-placeholder" for name in ("GEMINI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        with patch.dict(os.environ, environment, clear=True), \
             patch("main.capture_cycle", return_value=(captures, {})), \
             patch("main.GeminiClient") as gemini, patch("main.TelegramClient") as telegram:
            gemini.return_value.analyze.side_effect = GeminiQuotaError("offline 429")
            with self.assertRaises(GeminiQuotaError):
                main.run_cycle(settings)
            gemini.return_value.analyze.assert_called_once()
            telegram.return_value.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
