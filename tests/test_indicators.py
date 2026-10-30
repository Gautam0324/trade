"""
Unit tests for Indicators and IndicatorEngine.
"""

import unittest
from trader.indicators import (
    calculate_ema,
    calculate_sma,
    calculate_macd,
    calculate_supertrend,
    calculate_vwap,
    calculate_rsi,
    calculate_highest_high,
    calculate_lowest_low,
    IndicatorEngine
)

class TestIndicators(unittest.TestCase):
    def test_sma_and_ema(self):
        data = [10.0, 11.0, 12.0, 13.0, 14.0]
        sma = calculate_sma(data, 3)
        self.assertEqual(len(sma), 5)
        self.assertIsNone(sma[0])
        self.assertIsNone(sma[1])
        self.assertAlmostEqual(sma[2], 11.0)
        self.assertAlmostEqual(sma[4], 13.0)

        ema = calculate_ema(data, 3)
        self.assertEqual(len(ema), 5)
        self.assertAlmostEqual(ema[2], 11.0)
        self.assertGreater(ema[4], 12.0)

    def test_insufficient_data(self):
        data = [10.0, 11.0]
        ema = calculate_ema(data, 5)
        self.assertEqual(ema, [None, None])

    def test_supertrend_direction(self):
        # Starts in range, then strong rally breaking upper band
        highs = [100.0] * 15 + [120.0, 130.0, 140.0, 150.0, 160.0]
        lows = [95.0] * 15 + [115.0, 125.0, 135.0, 145.0, 155.0]
        closes = [98.0] * 15 + [119.0, 129.0, 139.0, 149.0, 159.0]
        st = calculate_supertrend(highs, lows, closes, 10, 3.0)
        self.assertEqual(len(st["direction"]), 20)
        # After breaking out past upper band, direction flips to +1
        self.assertEqual(st["direction"][-1], 1)

    def test_vwap_calculation(self):
        highs = [10.0, 20.0]
        lows = [5.0, 15.0]
        closes = [8.0, 18.0]
        vols = [100.0, 200.0]
        vwap = calculate_vwap(highs, lows, closes, vols)
        # tp0 = (10+5+8)/3 = 7.6667
        # tp1 = (20+15+18)/3 = 17.6667
        # vwap1 = (7.6667*100 + 17.6667*200) / 300 = 14.3333
        self.assertAlmostEqual(vwap[0], 23.0 / 3.0, places=3)
        self.assertAlmostEqual(vwap[1], 14.3333, places=3)

    def test_highest_high_and_lowest_low(self):
        highs = [10.0] * 35
        highs[10] = 50.0
        hh = calculate_highest_high(highs, 33)
        self.assertEqual(len(hh), 35)
        self.assertEqual(hh[-1], 50.0)

    def test_indicator_engine(self):
        engine = IndicatorEngine()
        bars = {
            "timestamp": [f"10:{i:02d}" for i in range(40)],
            "high": [100.0 + i for i in range(40)],
            "low": [90.0 + i for i in range(40)],
            "close": [95.0 + i for i in range(40)],
            "volume": [1000.0 for _ in range(40)]
        }
        res = engine.calculate_all("INFY", bars)
        self.assertIn("ema5", res)
        self.assertIn("supertrend", res)
        self.assertIn("macd", res)
        self.assertIn("vwap", res)
        self.assertIsNotNone(res["latest"]["ema5"])

if __name__ == "__main__":
    unittest.main()
