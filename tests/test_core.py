from datetime import datetime, timezone
from decimal import Decimal
import os
import tempfile
import unittest
from pathlib import Path

from src.config import enabled_instruments, enabled_strategies, load_settings
from src.scheduler import seconds_until_next_cycle
from src.signal_processor import format_instrument_message, parse_signal
from src.screenshot_capture import cleanup_old_cycles


class ConfigTests(unittest.TestCase):
    def test_brief_scale_is_configured(self):
        settings = load_settings()
        self.assertEqual(len(settings["instruments"]), 20)
        self.assertEqual(len(enabled_instruments(settings)), 5)
        self.assertEqual(len(settings["timeframes"]), 4)
        self.assertEqual(len(settings["strategies"]), 3)
        self.assertEqual(list(enabled_strategies(settings)), ["v57_swing"])

    def test_counts_match_requested_cycle(self):
        settings = load_settings()
        self.assertEqual(len(enabled_instruments(settings)) * len(settings["timeframes"]), 20)
        self.assertEqual(len(enabled_instruments(settings)) * len(enabled_strategies(settings)), 5)


class SignalTests(unittest.TestCase):
    def test_valid_buy(self):
        signal = parse_signal('{"direction":"BUY","confidence":"MEDIUM","entry_price":100,"stop_loss":98,"take_profit":104,"technical_justification":"Breakout and retest.","narrative":"Wait for confirmation."}')
        self.assertEqual(signal.direction, "BUY")
        self.assertEqual(signal.entry_price, Decimal("100"))

    def test_fenced_no_entry_and_ignore_prices(self):
        signal = parse_signal('```json\n{"direction":"NO_ENTRY","confidence":"LOW","entry_price":44,"stop_loss":43,"take_profit":45,"technical_justification":"No alignment.","narrative":"Wait."}\n```')
        self.assertEqual(signal.direction, "NO_ENTRY")
        self.assertIsNone(signal.entry_price)

    def test_reject_inconsistent_buy_risk_levels(self):
        raw = '{"direction":"BUY","confidence":"HIGH","entry_price":100,"stop_loss":101,"take_profit":104,"technical_justification":"x","narrative":"y"}'
        with self.assertRaisesRegex(ValueError, "BUY levels"):
            parse_signal(raw)

    def test_reject_non_finite_price(self):
        raw = '{"direction":"SELL","confidence":"LOW","entry_price":"NaN","stop_loss":110,"take_profit":90,"technical_justification":"x","narrative":"y"}'
        with self.assertRaisesRegex(ValueError, "finite"):
            parse_signal(raw)

    def test_digest_identifies_instrument_and_strategy(self):
        settings = load_settings()
        strategy = settings["strategies"]["v47_intraday"]
        signal = parse_signal('{"direction":"NO_ENTRY","confidence":"LOW","entry_price":null,"stop_loss":null,"take_profit":null,"technical_justification":"No setup.","narrative":"Wait."}')
        text = format_instrument_message({"name": "Test", "symbol": "TEST:XYZ"}, [("v47_intraday", strategy, signal)])
        self.assertIn("TEST:XYZ", text)
        self.assertIn("V47.0 Intraday", text)
        self.assertIn("NO_ENTRY", text)


class RetentionTests(unittest.TestCase):
    def test_cleanup_removes_only_expired_cycle_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expired = root / "20200101T000000Z"
            unrelated = root / "keep-this-folder"
            expired.mkdir()
            unrelated.mkdir()
            os.utime(expired, (0, 0))
            cleanup_old_cycles(root, 3)
            self.assertFalse(expired.exists())
            self.assertTrue(unrelated.exists())


class ScheduleTests(unittest.TestCase):
    def test_next_boundary_and_delay(self):
        now = datetime(2026, 1, 1, 12, 7, tzinfo=timezone.utc)
        self.assertEqual(seconds_until_next_cycle(now, 15, 5), 485)

    def test_naive_datetime_treated_as_utc(self):
        now = datetime(2026, 1, 1, 12, 15)
        self.assertEqual(seconds_until_next_cycle(now, 15, 5), 5)


if __name__ == "__main__":
    unittest.main()
