"""
Unit tests for Strategies and StrategyManager.
"""

import unittest
from trader.strategies.strategy_manager import StrategyManager
from trader.strategies.ema_channel import EMAChannelStrategy
from trader.strategies.confluence import ConfluenceStrategy

class TestStrategies(unittest.TestCase):
    def setUp(self):
        self.manager = StrategyManager({"min_confluence_score": 60.0})

    def test_ema_channel_bullish(self):
        strat = EMAChannelStrategy()
        candles = {
            "close": [100.0] * 35 + [105.0],
            "high": [102.0] * 35 + [106.0],
            "low": [98.0] * 35 + [104.0],
            "timestamp": [f"10:{i:02d}" for i in range(36)]
        }
        # EMA5 > EMA9 > EMA33
        indicators = {
            "ema5": [100.0] * 35 + [104.5],
            "ema9": [100.0] * 35 + [103.0],
            "ema33": [100.0] * 35 + [101.0]
        }
        sig = strat.evaluate("INFY", "1m", candles, indicators)
        self.assertIsNotNone(sig)
        self.assertEqual(sig["signal"], "BUY")
        self.assertEqual(sig["symbol"], "INFY")
        self.assertGreater(sig["target"], sig["entry_price"])
        self.assertLess(sig["stop_loss"], sig["entry_price"])

    def test_duplicate_signal_prevention(self):
        candles = {
            "close": [100.0] * 35 + [105.0],
            "high": [102.0] * 35 + [106.0],
            "low": [98.0] * 35 + [104.0],
            "timestamp": ["10:30"] * 36
        }
        indicators = {
            "ema5": [100.0] * 35 + [104.5],
            "ema9": [100.0] * 35 + [103.0],
            "ema33": [100.0] * 35 + [101.0],
            "supertrend_direction": [1] * 36,
            "supertrend": [99.0] * 36,
            "vwap": [102.0] * 36
        }
        # First evaluation generates signal
        sig1 = self.manager.evaluate_completed_candle("INFY", "1m", candles, indicators, strategy_mode="EMA_CHANNEL")
        self.assertIsNotNone(sig1)

        # Second evaluation on the identical candle timestamp is suppressed as duplicate
        sig2 = self.manager.evaluate_completed_candle("INFY", "1m", candles, indicators, strategy_mode="EMA_CHANNEL")
        self.assertIsNone(sig2)

    def test_confluence_requires_direction_agreement(self):
        conf = ConfluenceStrategy({"min_confluence_score": 60.0})
        # If no indicators match, returns None
        candles = {"close": [100.0] * 36, "high": [101.0] * 36, "low": [99.0] * 36}
        indicators = {}
        sig = conf.evaluate("TCS", "1m", candles, indicators)
        self.assertIsNone(sig)

if __name__ == "__main__":
    unittest.main()

