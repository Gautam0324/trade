"""
Unit tests for Tick Processing, Normalization, Validation, and PriceStore.
"""

import unittest
from trader.market_data.instrument_manager import InstrumentManager
from trader.market_data.tick_parser import TickParser
from trader.market_data.tick_validator import TickValidator
from trader.market_data.price_store import PriceStore
from trader.market_data.connection_monitor import ConnectionMonitor, ConnectionState

class TestTickProcessing(unittest.TestCase):
    def setUp(self):
        self.im = InstrumentManager([
            {"symbol": "RELIANCE", "token": "2885", "exchange": "NSE"},
            {"symbol": "TCS", "token": "11536", "exchange": "NSE"}
        ])
        self.parser = TickParser(self.im)
        self.validator = TickValidator(self.im)
        self.price_store = PriceStore(stale_threshold_secs=2.0)
        self.monitor = ConnectionMonitor()

    def test_valid_tick_parsing_and_normalization(self):
        raw_tick = {
            "token": "2885",
            "last_traded_price": 116290,
            "subscription_mode": 2,
            "volume_trade_for_the_day": 500000,
            "sequence_number": 101,
            "exchange_timestamp": 1730266057000
        }
        parsed = self.parser.parse_tick(raw_tick)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["symbol"], "RELIANCE")
        self.assertEqual(parsed["token"], "2885")
        self.assertEqual(parsed["ltp"], 1162.90)
        self.assertEqual(parsed["volume"], 500000.0)

        is_valid, reason = self.validator.validate_tick(parsed)
        self.assertTrue(is_valid, reason)

        # Store in price store
        self.price_store.update_tick(parsed)
        self.assertEqual(self.price_store.get_latest_price("RELIANCE"), 1162.90)
        self.assertFalse(self.price_store.is_feed_stale("RELIANCE"))

    def test_invalid_ltp_rejected(self):
        invalid_tick = {
            "symbol": "RELIANCE",
            "token": "2885",
            "ltp": -50.0,
            "timestamp": "2026-10-30T10:00:00+05:30"
        }
        is_valid, reason = self.validator.validate_tick(invalid_tick)
        self.assertFalse(is_valid)
        self.assertIn("Non-positive LTP", reason)

    def test_unregistered_token_rejected(self):
        unreg_tick = {
            "symbol": "FAKE_SYM",
            "token": "999999",
            "ltp": 100.0,
            "timestamp": "2026-10-30T10:00:00+05:30"
        }
        is_valid, reason = self.validator.validate_tick(unreg_tick)
        self.assertFalse(is_valid)
        self.assertIn("Unregistered instrument", reason)

    def test_duplicate_sequence_detected(self):
        tick1 = {"symbol": "RELIANCE", "token": "2885", "ltp": 1160.0, "sequence_number": 500}
        tick2 = {"symbol": "RELIANCE", "token": "2885", "ltp": 1160.5, "sequence_number": 500}

        val1, _ = self.validator.validate_tick(tick1)
        self.assertTrue(val1)

        val2, reason = self.validator.validate_tick(tick2)
        self.assertFalse(val2)
        self.assertIn("Duplicate tick sequence", reason)

    def test_connection_monitor_metrics(self):
        self.monitor.set_state(ConnectionState.CONNECTED)
        self.monitor.record_tick(is_valid=True)
        self.monitor.record_tick(is_valid=False, error_reason="Bad LTP")
        self.monitor.record_tick(is_valid=False, is_duplicate=True)

        status = self.monitor.get_status_dict()
        self.assertEqual(status["status"], "CONNECTED")
        self.assertEqual(status["total_ticks_received"], 3)
        self.assertEqual(status["valid_ticks_processed"], 1)
        self.assertEqual(status["invalid_ticks_rejected"], 1)
        self.assertEqual(status["duplicate_ticks_dropped"], 1)

if __name__ == "__main__":
    unittest.main()

