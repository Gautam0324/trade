#!/usr/bin/env python3
"""
Unit Tests for V3 Trend Pullback Strategy (Phase 6 Deliverable)
"""

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))
sys.path.insert(0, str(BASE_DIR / "trader" / "strategies"))

from v3_trend_pullback import V3TrendPullbackStrategy

class TestV3TrendPullbackStrategy(unittest.TestCase):
    def setUp(self):
        self.strat = V3TrendPullbackStrategy({"adx_minimum": 20.0, "atr_multiplier": 1.5})

    def test_candle_aggregation(self):
        # 10 1-minute bars should aggregate into 2 5-minute bars
        one_min = {
            "timestamp": [f"10:0{i}" for i in range(10)],
            "open": [100.0 + i for i in range(10)],
            "high": [105.0 + i for i in range(10)],
            "low": [95.0 + i for i in range(10)],
            "close": [101.0 + i for i in range(10)],
            "volume": [1000] * 10
        }
        bars_5m = self.strat.aggregate_candles(one_min, target_minutes=5)
        self.assertEqual(len(bars_5m["close"]), 2)
        self.assertEqual(bars_5m["open"][0], 100.0)
        self.assertEqual(bars_5m["close"][0], 105.0)
        self.assertEqual(bars_5m["volume"][0], 5000)

    def test_sideways_market_rejection_low_adx(self):
        # Flat prices -> low ADX
        bars_15m = {
            "close": [100.0 + (i % 2) for i in range(40)],
            "high": [102.0 for _ in range(40)],
            "low": [98.0 for _ in range(40)],
            "open": [100.0 for _ in range(40)]
        }
        res = self.strat.evaluate_15m_trend(bars_15m)
        self.assertEqual(res["direction"], "SIDEWAYS")
        self.assertIn("Choppy / Sideways", res["reason"])

    def test_15m_bullish_trend_detection(self):
        # Strong steady uptrend
        closes = [100.0 + (i * 2.0) for i in range(40)]
        highs = [c + 1.5 for c in closes]
        lows = [c - 1.0 for c in closes]
        bars_15m = {"close": closes, "high": highs, "low": lows}

        res = self.strat.evaluate_15m_trend(bars_15m)
        self.assertEqual(res["direction"], "BULLISH")
        self.assertGreater(res["adx"], 20.0)

    def test_15m_bearish_trend_detection(self):
        # Strong steady downtrend
        closes = [200.0 - (i * 2.0) for i in range(40)]
        highs = [c + 1.0 for c in closes]
        lows = [c - 1.5 for c in closes]
        bars_15m = {"close": closes, "high": highs, "low": lows}

        res = self.strat.evaluate_15m_trend(bars_15m)
        self.assertEqual(res["direction"], "BEARISH")
        self.assertGreater(res["adx"], 20.0)

    def test_full_bullish_pullback_signal(self):
        # Construct realistic trend with a gentle pullback to EMA9
        closes = [100.0]
        for i in range(1, 25):
            # Realistic zigzag upward drift to keep RSI around 55-60
            closes.append(closes[-1] + (0.8 if i % 3 != 0 else -0.3))
        # Now 3 pullback bars down towards EMA9
        for _ in range(3):
            closes.append(closes[-1] - 0.6)
        # Reversal candle bouncing off EMA9
        last_low = closes[-1] - 0.5
        reversal_close = closes[-1] + 1.2
        closes.append(reversal_close)

        opens = [c - 0.2 for c in closes]
        opens[-1] = last_low + 0.1 # Bullish candle opening low
        highs = [max(o, c) + 0.3 for o, c in zip(opens, closes)]
        lows = [min(o, c) - 0.3 for o, c in zip(opens, closes)]
        lows[-1] = last_low # Touches EMA
        volumes = [2000] * len(closes)

        bars_5m = {"close": closes, "open": opens, "high": highs, "low": lows, "volume": volumes}
        htf_trend = {"direction": "BULLISH", "adx": 28.0}

        sig = self.strat.evaluate_5m_pullback("RELIANCE", bars_5m, htf_trend)
        self.assertEqual(sig["direction"], "LONG")
        self.assertEqual(sig["strategy_name"], "V3_TREND_PULLBACK")
        self.assertGreater(sig["target_price"], sig["entry_price"])
        self.assertLess(sig["stop_loss"], sig["entry_price"])
        self.assertEqual(sig["reward_risk"], 2.0)

if __name__ == "__main__":
    unittest.main()
