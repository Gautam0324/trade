#!/usr/bin/env python3
"""
Unit Tests for V3 Risk Engine (Phase 4 Deliverable)
"""

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from v3_risk_engine import V3RiskEngine

class TestV3RiskEngine(unittest.TestCase):
    def setUp(self):
        self.risk_eng = V3RiskEngine(account_id="gautam", starting_capital=1000000.0)

    def test_risk_based_position_sizing_cash(self):
        # Capital 10L, 0.5% risk = ₹5,000
        # Stock: Entry 1000, SL 980 (Risk per share = ₹20)
        # Expected Qty: 5000 / 20 = 250 shares
        qty, planned_risk, rejections = self.risk_eng.calculate_position_size(
            symbol="RELIANCE", entry_price=1000.0, stop_loss_price=980.0,
            current_equity=1000000.0, available_cash=500000.0, segment="EQUITY"
        )
        self.assertEqual(len(rejections), 0)
        self.assertEqual(qty, 250)
        self.assertEqual(planned_risk, 5000.0)

    def test_high_vs_low_priced_risk_balance(self):
        # 1. Stock A (Price ₹2000, SL 1960, diff ₹40) -> 5000 / 40 = 125 shares (Notional: ₹250k <= 400k cap)
        qty_a, risk_a, _ = self.risk_eng.calculate_position_size(
            "STOCKA", 2000.0, 1960.0, 1000000.0, 800000.0, "EQUITY"
        )
        # 2. TATASTEEL (Low price ₹100, SL 98, diff ₹2) -> 5000 / 2 = 2500 shares (Notional: ₹250k <= 400k cap)
        qty_b, risk_b, _ = self.risk_eng.calculate_position_size(
            "STOCKB", 100.0, 98.0, 1000000.0, 800000.0, "EQUITY"
        )
        self.assertEqual(qty_a, 125)
        self.assertEqual(qty_b, 2500)
        self.assertEqual(risk_a, 5000.0)
        self.assertEqual(risk_b, 5000.0)
        # Both stocks have IDENTICAL planned risk of ₹5,000!

    def test_daily_loss_limit_rejection(self):
        # 1.5% max daily loss on 10L = ₹15,000
        res = self.risk_eng.evaluate_account_risk(
            symbol="INFY", side="BUY", entry_price=1200.0, stop_loss=1190.0,
            current_equity=1000000.0, available_cash=500000.0,
            daily_realized_pnl=-16000.0, # Breached -15k
            today_completed_trades=2, consecutive_losses_count=1,
            current_open_positions=[]
        )
        self.assertFalse(res["passed"])
        self.assertTrue(any("Daily loss limit breached" in r for r in res["rejection_reasons"]))

    def test_consecutive_losses_kill_switch(self):
        res = self.risk_eng.evaluate_account_risk(
            symbol="INFY", side="BUY", entry_price=1200.0, stop_loss=1190.0,
            current_equity=1000000.0, available_cash=500000.0,
            daily_realized_pnl=-4000.0,
            today_completed_trades=2, consecutive_losses_count=2, # 2 losses in a row
            current_open_positions=[]
        )
        self.assertFalse(res["passed"])
        self.assertTrue(any("Consecutive loss" in r for r in res["rejection_reasons"]))

    def test_max_open_positions_rejection(self):
        dummy_positions = [
            {"symbol": "TCS", "entry_price": 2500, "sl_price": 2480, "qty": 100},
            {"symbol": "SBIN", "entry_price": 980, "sl_price": 970, "qty": 100},
            {"symbol": "RELIANCE", "entry_price": 1250, "sl_price": 1240, "qty": 100}
        ]
        res = self.risk_eng.evaluate_account_risk(
            symbol="INFY", side="BUY", entry_price=1200.0, stop_loss=1190.0,
            current_equity=1000000.0, available_cash=500000.0,
            daily_realized_pnl=0.0, today_completed_trades=1,
            consecutive_losses_count=0, current_open_positions=dummy_positions # 3 open
        )
        self.assertFalse(res["passed"])
        self.assertTrue(any("Max open positions" in r for r in res["rejection_reasons"]))

    def test_multi_account_isolation(self):
        # Account 1: Himanshu with 2 consecutive losses (blocked)
        eng_himanshu = V3RiskEngine(account_id="himanshu", starting_capital=1000000.0)
        res_himanshu = eng_himanshu.evaluate_account_risk(
            "SBIN", "BUY", 980.0, 970.0, 1000000.0, 500000.0, -2000.0, 2, 2, []
        )
        self.assertFalse(res_himanshu["passed"])

        # Account 2: Gautam with 0 losses (approved)
        eng_gautam = V3RiskEngine(account_id="gautam", starting_capital=1000000.0)
        res_gautam = eng_gautam.evaluate_account_risk(
            "SBIN", "BUY", 980.0, 970.0, 1000000.0, 500000.0, 0.0, 0, 0, []
        )
        self.assertTrue(res_gautam["passed"])
        self.assertGreater(res_gautam["quantity"], 0)

if __name__ == "__main__":
    unittest.main()
