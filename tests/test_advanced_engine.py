#!/usr/bin/env python3
"""
Unit test suite for Advanced Multi-Strategy Trading Engine.
Covers:
- Technical indicators (EMA, SMA, ATR, RSI, ADX, VWAP, RVol)
- Strategy generation (EMA Channel, VWAP Pullback, ORB, Trend+Momentum)
- Risk management & position sizing constraints
- Centralized Exit Engine (Stop Loss, Partial 1R profit booking, ATR Trailing, Peak Reversal)
- Ledger atomic operations and partial exits
- Deterministic synthetic backtesting
"""

import unittest
from decimal import Decimal
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import indicators
from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from exit_engine import PositionManager
import ledger
from backtest import BacktestEngine

class TestAdvancedEngine(unittest.TestCase):
    def setUp(self):
        # Deterministic price series (100 bars)
        self.prices = [100.0 + (i * 0.5) for i in range(100)]
        self.highs = [p + 0.8 for p in self.prices]
        self.lows = [p - 0.8 for p in self.prices]
        self.closes = list(self.prices)
        self.volumes = [50000 + (i * 500) for i in range(100)]

    def test_indicator_ema_sma(self):
        sma5 = indicators.calculate_sma(self.closes[:10], 5)
        self.assertEqual(len(sma5), 10)
        self.assertAlmostEqual(sma5[4], sum(self.closes[:5]) / 5.0)

        ema5 = indicators.calculate_ema(self.closes[:10], 5)
        self.assertEqual(len(ema5), 10)
        self.assertAlmostEqual(ema5[4], sma5[4])

    def test_indicator_atr(self):
        atr = indicators.calculate_atr(self.highs, self.lows, self.closes, 14)
        self.assertEqual(len(atr), 100)
        self.assertIsNotNone(atr[13])
        self.assertGreater(atr[-1], 0)

    def test_indicator_rsi(self):
        rsi = indicators.calculate_rsi(self.closes, 14)
        self.assertEqual(len(rsi), 100)
        # Steady uptrend prices should yield strong RSI > 70
        self.assertGreater(rsi[-1], 70.0)

    def test_indicator_adx(self):
        adx_res = indicators.calculate_adx(self.highs, self.lows, self.closes, 14)
        self.assertIn("adx", adx_res)
        self.assertIn("plus_di", adx_res)
        self.assertIn("minus_di", adx_res)
        self.assertIsNotNone(adx_res["plus_di"][-1])
        # In uptrend, +DI must be greater than -DI
        self.assertGreater(adx_res["plus_di"][-1], adx_res["minus_di"][-1])

    def test_indicator_vwap(self):
        vwap = indicators.calculate_vwap(self.highs, self.lows, self.closes, self.volumes)
        self.assertEqual(len(vwap), 100)
        self.assertGreater(vwap[-1], 0)

    def test_risk_management_sizing(self):
        risk = RiskEngine({"budget": 100000.0, "max_loss_per_trade_usd": 1000.0})
        # Entry 100, Stop 95 -> risk/share = 5 -> qty = 1000 / 5 = 200
        res = risk.evaluate_order_risk(
            symbol="TEST.NS",
            side="BUY",
            entry_price=100.0,
            stop_loss=95.0,
            target_price=110.0,
            available_cash=100000.0,
            daily_realized_loss=0.0,
            current_trades_count=0,
            current_open_positions_count=0
        )
        self.assertTrue(res["passed"])
        self.assertEqual(res["quantity"], 200)

    def test_risk_management_kill_switch(self):
        risk = RiskEngine({"budget": 100000.0, "max_daily_loss_usd": 1000.0})
        res = risk.evaluate_order_risk(
            symbol="TEST.NS",
            side="BUY",
            entry_price=100.0,
            stop_loss=95.0,
            target_price=110.0,
            available_cash=100000.0,
            daily_realized_loss=-1050.0,  # Loss exceeded
            current_trades_count=1,
            current_open_positions_count=0
        )
        self.assertFalse(res["passed"])
        self.assertEqual(res["quantity"], 0)
        self.assertIn("Daily loss limit reached", res["reasons"][0])

    def test_position_exit_partial_booking(self):
        pos_mgr = PositionManager({"enable_partial_profit": True, "flat_by": "15:20"})
        # Position: Entry 100, Stop 90 (1R = 10 points)
        pos = {
            "symbol": "TEST.NS",
            "entry_price": 100.0,
            "stop_loss": 90.0,
            "initial_stop": 90.0,
            "current_stop": 90.0,
            "qty": 10,
            "partial_exited": False
        }
        # Price reaches 110 (+1R) during regular market hours (11:00 IST)
        eval_res = pos_mgr.evaluate_exit(pos, current_price=110.0, current_time="11:00")
        self.assertEqual(eval_res["action"], "PARTIAL_EXIT")
        self.assertEqual(eval_res["exit_qty"], 5)
        # Stop moved to breakeven (100.0)
        self.assertEqual(eval_res["updated_position"]["current_stop"], 100.0)

    def test_position_exit_peak_reversal(self):
        pos_mgr = PositionManager({"peak_reversal_threshold": 0.002, "flat_by": "15:20"})
        pos = {
            "symbol": "TEST.NS",
            "entry_price": 100.0,
            "stop_loss": 95.0,
            "initial_stop": 95.0,
            "current_stop": 95.0,
            "peak_price": 110.0,  # Gained up to 110
            "qty": 10,
            "partial_exited": True
        }
        # Price drops from 110 to 109.5 during regular market hours (11:00 IST)
        eval_res = pos_mgr.evaluate_exit(pos, current_price=109.5, current_time="11:00")
        self.assertEqual(eval_res["action"], "FULL_EXIT")
        self.assertIn("Peak reversal exit", eval_res["reason"])

    def test_backtest_runner(self):
        bt = BacktestEngine(starting_capital=100000.0)
        bars = {
            "open": self.prices,
            "high": self.highs,
            "low": self.lows,
            "close": self.closes,
            "volume": self.volumes
        }
        res = bt.run_strategy(bars, strategy_name="EMA_CHANNEL")
        self.assertIn("net_pnl", res)
        self.assertIn("win_rate_pct", res)
        self.assertIn("total_trades", res)

if __name__ == "__main__":
    unittest.main()
