"""
Unit tests for Backtesting Engine, Chronological Splitter, and Performance Metrics.
"""

import unittest
from trader.backtesting.data_splitter import ChronologicalDataSplitter
from trader.backtesting.performance_metrics import PerformanceMetrics
from trader.backtesting.backtest_engine import BacktestEngine

class TestBacktesting(unittest.TestCase):
    def test_chronological_data_splitter(self):
        candles = [{"timestamp": f"{10 + i // 60:02d}:{i % 60:02d}", "close": 100 + i} for i in range(100)]
        train, val, test = ChronologicalDataSplitter.split(candles, 0.6, 0.2, 0.2)
        self.assertEqual(len(train), 60)
        self.assertEqual(len(val), 20)
        self.assertEqual(len(test), 20)
        # Ensure chronological ordering preserved
        self.assertEqual(train[0]["timestamp"], "10:00")
        self.assertEqual(train[-1]["timestamp"], "10:59")
        self.assertEqual(val[0]["timestamp"], "11:00")
        self.assertEqual(test[0]["timestamp"], "11:20")

    def test_performance_metrics_math(self):
        trades = [
            {"pnl": 1000.0, "gross_pnl": 1200.0, "fees": 200.0},
            {"pnl": -500.0, "gross_pnl": -400.0, "fees": 100.0},
            {"pnl": 1500.0, "gross_pnl": 1700.0, "fees": 200.0}
        ]
        m = PerformanceMetrics.calculate(trades, initial_capital=100000.0)
        self.assertEqual(m["total_trades"], 3)
        self.assertEqual(m["wins"], 2)
        self.assertEqual(m["losses"], 1)
        self.assertAlmostEqual(m["win_rate"], 66.67, places=1)
        self.assertAlmostEqual(m["net_pnl"], 2000.0)
        # Gross wins = 2500, Gross losses = 500 -> Profit Factor = 5.0
        self.assertAlmostEqual(m["profit_factor"], 5.0)

    def test_backtest_engine_execution(self):
        engine = BacktestEngine(initial_capital=100000.0)
        # Generate dummy 50 candles
        candles = []
        for i in range(50):
            candles.append({
                "timestamp": f"2026-10-30 10:{i:02d}",
                "open": 100.0 + (i * 0.1),
                "high": 101.0 + (i * 0.1),
                "low": 99.0 + (i * 0.1),
                "close": 100.5 + (i * 0.1),
                "volume": 1000.0
            })
        res = engine.run_backtest("INFY", candles, strategy_name="EMA_CHANNEL")
        self.assertIn("trades", res)
        self.assertIn("metrics", res)
        self.assertIsInstance(res["trades"], list)

if __name__ == "__main__":
    unittest.main()
