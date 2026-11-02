#!/usr/bin/env python3
"""
Unit Tests for Authoritative Accounting Engine (Phase 2 Deliverable)
"""

import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from accounting_engine import AccountingEngine

class TestAccountingEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AccountingEngine()

    def test_canonical_symbol(self):
        self.assertEqual(self.engine.canonical_symbol("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(self.engine.canonical_symbol("icicibank.bo"), "ICICIBANK")
        self.assertEqual(self.engine.canonical_symbol("TATASTEEL-EQ"), "TATASTEEL")
        self.assertEqual(self.engine.canonical_symbol("  tcs  "), "TCS")

    def test_cash_buy_trade_costs(self):
        # 100 shares bought @ 1000, sold @ 1050
        res = self.engine.calculate_trade_costs("EQUITY", "BUY", 1000.0, 1050.0, 100, "NSE")
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["gross_pnl"], 5000.0)
        self.assertGreater(res["brokerage"], 0.0)
        self.assertLessEqual(res["brokerage"], 40.0) # Cap at ₹20 per leg
        self.assertGreater(res["stt"], 0.0) # STT on sell leg
        self.assertGreater(res["exchange_charges"], 0.0)
        self.assertGreater(res["gst"], 0.0)
        self.assertGreater(res["stamp_duty"], 0.0) # Stamp duty on buy leg
        self.assertGreater(res["slippage"], 0.0)
        self.assertEqual(res["total_costs"], round(res["total_statutory"] + res["slippage"], 2))
        self.assertEqual(res["net_pnl"], round(5000.0 - res["total_costs"], 2))

    def test_cash_sell_trade_costs(self):
        # Short 100 shares @ 1050, bought back @ 1000 (Win)
        res = self.engine.calculate_trade_costs("EQUITY", "SELL", 1050.0, 1000.0, 100, "NSE")
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["gross_pnl"], 5000.0)
        self.assertGreater(res["net_pnl"], 0.0)

    def test_options_trade_costs(self):
        # 1 lot NIFTY (65 qty) bought @ 100, sold @ 130
        res = self.engine.calculate_trade_costs("OPTIONS", "BUY", 100.0, 130.0, 65, "NSE")
        self.assertEqual(res["status"], "VERIFIED")
        self.assertEqual(res["gross_pnl"], 1950.0)
        self.assertEqual(res["brokerage"], 4.48) # 65*100*0.0003=1.95, 65*130*0.0003=2.535 -> 4.48
        self.assertGreater(res["stt"], 0.0)
        self.assertGreater(res["total_costs"], 0.0)
        self.assertLess(res["net_pnl"], 1950.0)

    def test_incomplete_cost_handling(self):
        res = self.engine.calculate_trade_costs("EQUITY", "BUY", 0.0, 1050.0, 100)
        self.assertEqual(res["status"], "COST_DATA_INCOMPLETE")
        self.assertEqual(res["net_pnl"], 0.0)

    def test_completed_trade_record_creation(self):
        rec = self.engine.create_completed_trade_record(
            trade_id="TRD-001",
            position_id="POS-001",
            account_id="gautam",
            strategy_id="V3_TREND_PULLBACK",
            instrument="RELIANCE.NS",
            segment="EQUITY",
            side="BUY",
            quantity=100,
            entry_timestamp="2026-11-02 10:00:00",
            exit_timestamp="2026-11-02 11:30:00",
            entry_price=1200.0,
            exit_price=1220.0,
            exit_reason="Target Hit (+1.5R)"
        )
        self.assertEqual(rec["instrument"], "RELIANCE")
        self.assertEqual(rec["symbol"], "RELIANCE")
        self.assertEqual(rec["gross_pnl"], 2000.0)
        self.assertIn("brokerage", rec)
        self.assertIn("stt", rec)
        self.assertIn("slippage", rec)
        self.assertEqual(rec["accounting_status"], "VERIFIED")
        self.assertEqual(rec["date"], "2026-11-02")

    def test_account_reconciliation(self):
        ledger = {
            "realized_pnl": 1000.0,
            "free_cash": 101000.0,
            "open_positions": [],
            "trade_history": [
                {"id": "T1", "net_pnl": 600.0, "date": "2026-11-02"},
                {"id": "T2", "net_pnl": 400.0, "date": "2026-11-02"}
            ]
        }
        rec = self.engine.reconcile_account(ledger, starting_capital=100000.0)
        self.assertTrue(rec["is_reconciled"])
        self.assertEqual(rec["calculated_realized_pnl"], 1000.0)
        self.assertEqual(rec["calculated_free_cash"], 101000.0)
        self.assertEqual(rec["pnl_by_date"]["2026-11-02"], 1000.0)

if __name__ == "__main__":
    unittest.main()
