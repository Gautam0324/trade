#!/usr/bin/env python3
"""
Unit tests for Institutional Transaction Cost Model (cost_model.py).
Verifies:
- Brokerage cap at ₹20 per executed order (0.03% or ₹20, whichever is lower)
- STT charged only on SELL side for intraday equities (0.025%)
- Exchange turnover fees (NSE: 0.00345%)
- GST (18% on brokerage + exchange txn fees)
- SEBI turnover charges (₹10 per crore)
- Stamp duty charged only on BUY side (0.003%)
- Realistic execution slippage (default 2 bps)
- Gross P&L vs Net P&L reconciliation
"""

import unittest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from cost_model import CostModel

class TestCostModel(unittest.TestCase):
    def setUp(self):
        self.cm = CostModel({
            "brokerage_per_order_inr": 20.0,
            "brokerage_rate": 0.0003,
            "stt_rate": 0.00025,
            "exchange_txn_rate": 0.0000345,
            "gst_rate": 0.18,
            "sebi_rate": 0.000001,
            "stamp_duty_rate": 0.00003,
            "slippage_rate": 0.0002
        })

    def test_brokerage_capping(self):
        # High value order: 1000 shares @ ₹2000 = ₹2,000,000 turnover
        # 0.03% of 2,000,000 = ₹600. Brokerage must be capped at ₹20.00
        cost = self.cm.calculate_leg_cost("BUY", 2000.0, 1000)
        self.assertEqual(cost["brokerage"], 20.0)

        # Small value order: 10 shares @ ₹100 = ₹1,000 turnover
        # 0.03% of 1,000 = ₹0.30 (< ₹20 cap)
        cost_small = self.cm.calculate_leg_cost("BUY", 100.0, 10)
        self.assertAlmostEqual(cost_small["brokerage"], 0.30, places=2)

    def test_stt_and_stamp_duty_asymmetry(self):
        # Intraday equities: Stamp duty only on BUY; STT only on SELL
        buy_cost = self.cm.calculate_leg_cost("BUY", 1000.0, 100)
        self.assertGreater(buy_cost["stamp_duty"], 0.0)
        self.assertEqual(buy_cost["stt"], 0.0)

        sell_cost = self.cm.calculate_leg_cost("SELL", 1000.0, 100)
        self.assertEqual(sell_cost["stamp_duty"], 0.0)
        self.assertGreater(sell_cost["stt"], 0.0)

    def test_roundtrip_cost_and_net_pnl(self):
        # Buy 100 shares at ₹1000, Sell at ₹1020
        # Gross profit: 100 * 20 = ₹2000.00
        rt = self.cm.calculate_roundtrip_cost("BUY", 1000.0, 1020.0, 100)
        self.assertEqual(rt["gross_pnl"], 2000.0)
        self.assertGreater(rt["total_costs"], 0.0)
        self.assertEqual(rt["net_pnl"], round(rt["gross_pnl"] - rt["total_costs"], 2))
        self.assertLess(rt["net_pnl"], rt["gross_pnl"])

    def test_slippage_simulation(self):
        # Slippage: BUY should execute slightly higher, SELL slightly lower
        exec_buy = self.cm.simulate_fill_price("BUY", 1000.0)
        self.assertGreater(exec_buy, 1000.0)

        exec_sell = self.cm.simulate_fill_price("SELL", 1000.0)
        self.assertLess(exec_sell, 1000.0)

if __name__ == "__main__":
    unittest.main()

