#!/usr/bin/env python3
"""
Unit tests for Institutional Risk Engine & Order Limits (risk_engine.py).
Verifies:
- Daily max orders limit (e.g. 20 orders/day)
- Portfolio exposure cap (e.g. 80% of starting capital)
- Cash buffer reserve enforcement (e.g. 10% unallocated)
- Max order notional clamping (e.g. max ₹400,000 per order)
- Duplicate base symbol rejection (e.g. RELIANCE vs RELIANCE_BSE)
"""

import unittest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from risk_engine import RiskEngine

class TestRiskLimits(unittest.TestCase):
    def setUp(self):
        self.risk = RiskEngine({
            "budget": 1000000.0,
            "max_loss_per_trade_inr": 10000.0,
            "max_daily_loss_inr": 20000.0,
            "max_orders_per_day": 20,
            "max_trades_per_day": 10,
            "cash_buffer_pct": 10.0,
            "max_portfolio_exposure_pct": 80.0,
            "max_order_notional": 400000.0,
            "max_open_positions": 3
        })

    def test_max_orders_per_day_limit(self):
        # 19 orders placed today -> pass
        res1 = self.risk.evaluate_order_risk(
            "TCS", "BUY", 3000.0, 2970.0, 3060.0,
            available_cash=500000.0, daily_realized_loss=0.0,
            current_trades_count=5, current_open_positions_count=1,
            today_orders_count=19
        )
        self.assertTrue(res1["passed"])

        # 20 orders placed today -> rejected
        res2 = self.risk.evaluate_order_risk(
            "TCS", "BUY", 3000.0, 2970.0, 3060.0,
            available_cash=500000.0, daily_realized_loss=0.0,
            current_trades_count=5, current_open_positions_count=1,
            today_orders_count=20
        )
        self.assertFalse(res2["passed"])
        self.assertIn("Maximum daily orders count reached", res2["reasons"][0])

    def test_cash_buffer_enforcement(self):
        # Required buffer = 10% of 1,000,000 = ₹100,000
        # If available cash is ₹95,000, order must be blocked
        res = self.risk.evaluate_order_risk(
            "INFY", "BUY", 1500.0, 1485.0, 1530.0,
            available_cash=95000.0, daily_realized_loss=0.0,
            current_trades_count=0, current_open_positions_count=0
        )
        self.assertFalse(res["passed"])
        self.assertIn("Cash buffer constraint breached", res["reasons"][0])

    def test_max_portfolio_exposure(self):
        # Starting capital = 1,000,000; max exposure 80% = ₹800,000
        # If ₹750,000 is already deployed, only ₹50,000 new exposure allowed
        res = self.risk.evaluate_order_risk(
            "HDFCBANK", "BUY", 1000.0, 990.0, 1020.0,
            available_cash=250000.0, daily_realized_loss=0.0,
            current_trades_count=1, current_open_positions_count=1,
            deployed_cash=750000.0
        )
        self.assertTrue(res["passed"])
        # Position value should not exceed remaining ₹50,000 exposure
        self.assertLessEqual(res["quantity"] * 1000.0, 50000.0)

    def test_max_order_notional_clamping(self):
        # Max order notional = ₹400,000
        # Even with ample cash and wide budget, order value must not exceed ₹400,000
        res = self.risk.evaluate_order_risk(
            "RELIANCE", "BUY", 1000.0, 999.0, 1010.0,
            available_cash=900000.0, daily_realized_loss=0.0,
            current_trades_count=0, current_open_positions_count=0
        )
        self.assertTrue(res["passed"])
        self.assertLessEqual(res["quantity"] * 1000.0, 400000.0)

    def test_duplicate_base_symbol_rejection(self):
        # Open position in RELIANCE should block order in RELIANCE_BSE
        res = self.risk.evaluate_order_risk(
            "RELIANCE_BSE", "BUY", 1200.0, 1180.0, 1240.0,
            available_cash=500000.0, daily_realized_loss=0.0,
            current_trades_count=1, current_open_positions_count=1,
            existing_symbols=["RELIANCE"]
        )
        self.assertFalse(res["passed"])
        self.assertIn("Duplicate position rejected", res["reasons"][0])

if __name__ == "__main__":
    unittest.main()
