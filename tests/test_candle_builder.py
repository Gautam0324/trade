"""
Unit tests for CandleBuilder and CandleStore.
"""

import unittest
import datetime
import zoneinfo
from trader.market_data.candle_builder import CandleBuilder
from trader.market_data.candle_store import CandleStore

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class TestCandleBuilder(unittest.TestCase):
    def setUp(self):
        self.completed_candles = []
        self.builder = CandleBuilder(on_candle_completed=lambda c: self.completed_candles.append(c))
        self.store = CandleStore(db_path=":memory:")

    def test_candle_ohlc_and_boundary(self):
        # Base timestamp: 10:00:05
        base_dt = datetime.datetime(2026, 10, 30, 10, 0, 5, tzinfo=IST_TZ)
        base_ms = int(base_dt.timestamp() * 1000)

        # Tick 1: 10:00:05 @ 100.0, CumVol: 1000
        self.builder.process_tick({
            "symbol": "RELIANCE", "ltp": 100.0, "volume": 1000, "raw_exchange_timestamp": base_ms
        })

        # Tick 2: 10:00:20 @ 105.0 (High), CumVol: 1050
        self.builder.process_tick({
            "symbol": "RELIANCE", "ltp": 105.0, "volume": 1050, "raw_exchange_timestamp": base_ms + 15000
        })

        # Tick 3: 10:00:40 @ 98.0 (Low), CumVol: 1100
        self.builder.process_tick({
            "symbol": "RELIANCE", "ltp": 98.0, "volume": 1100, "raw_exchange_timestamp": base_ms + 35000
        })

        # Tick 4: 10:00:55 @ 102.0 (Close), CumVol: 1150
        self.builder.process_tick({
            "symbol": "RELIANCE", "ltp": 102.0, "volume": 1150, "raw_exchange_timestamp": base_ms + 50000
        })

        # Check forming candle
        forming_1m = self.builder.get_forming_candle("RELIANCE", "1m")
        self.assertIsNotNone(forming_1m)
        self.assertEqual(forming_1m["open"], 100.0)
        self.assertEqual(forming_1m["high"], 105.0)
        self.assertEqual(forming_1m["low"], 98.0)
        self.assertEqual(forming_1m["close"], 102.0)
        self.assertEqual(forming_1m["volume"], 150.0)  # 1150 - 1000

        # Tick 5 crosses 1-minute boundary: 10:01:05 @ 103.0
        boundary_ms = base_ms + 60000
        completed = self.builder.process_tick({
            "symbol": "RELIANCE", "ltp": 103.0, "volume": 1200, "raw_exchange_timestamp": boundary_ms
        })

        # Verify completed candle was emitted
        self.assertEqual(len(self.completed_candles), 1)
        c1m = self.completed_candles[0]
        self.assertEqual(c1m["timeframe"], "1m")
        self.assertEqual(c1m["open"], 100.0)
        self.assertEqual(c1m["high"], 105.0)
        self.assertEqual(c1m["low"], 98.0)
        self.assertEqual(c1m["close"], 102.0)
        self.assertEqual(c1m["status"], "completed")

        # Add to candle store and query
        self.store.add_candle(c1m)
        bars = self.store.get_bars_dict("RELIANCE", "1m")
        self.assertEqual(len(bars["close"]), 1)
        self.assertEqual(bars["close"][0], 102.0)

    def test_5m_candle_completion(self):
        # Start at 10:00:00
        start_dt = datetime.datetime(2026, 10, 30, 10, 0, 0, tzinfo=IST_TZ)
        start_ms = int(start_dt.timestamp() * 1000)

        # Simulate ticks across 5 minutes
        for minute in range(6):
            tick_ms = start_ms + (minute * 60 * 1000)
            self.builder.process_tick({
                "symbol": "TCS", "ltp": 2500.0 + minute, "volume": 1000 + (minute * 100),
                "raw_exchange_timestamp": tick_ms
            })

        # At minute 5, 5m candle should have crossed boundary and completed!
        c5m_list = [c for c in self.completed_candles if c["timeframe"] == "5m"]
        self.assertGreaterEqual(len(c5m_list), 1)
        self.assertEqual(c5m_list[0]["open"], 2500.0)
        self.assertEqual(c5m_list[0]["close"], 2504.0)

if __name__ == "__main__":
    unittest.main()

