#!/usr/bin/env python3
import unittest
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import ledger
from exit_engine import PositionManager
from risk_engine import RiskEngine

class TestExitAndRiskRules(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_ledger_path = ledger.LEDGER_PATH
        self.orig_lock_path = ledger.LOCK_PATH
        
        self.test_ledger_path = Path(self.temp_dir.name) / "ledger.json"
        self.test_lock_path = Path(self.temp_dir.name) / "ledger.lock"
        ledger.LEDGER_PATH = self.test_ledger_path
        ledger.LOCK_PATH = self.test_lock_path

        init_data = {
            "starting_capital": 100000.0,
            "currency": "INR",
            "free_cash": 100000.0,
            "deployed_cash": 0.0,
            "realized_pnl": 0.0,
            "realized_pnl_by_day": {},
            "next_position_seq": 1,
            "open_positions": [],
            "trade_history": []
        }
        ledger.save_ledger(init_data)

    def tearDown(self):
        ledger.LEDGER_PATH = self.orig_ledger_path
        ledger.LOCK_PATH = self.orig_lock_path
        self.temp_dir.cleanup()

    def test_eod_squareoff_with_quote_gives_correct_pnl(self):
        # Open position entered at 1200
        ok, pos = ledger.record_trade_entry("TEST.NS", "BUY", 10, 1200.0)
        self.assertTrue(ok)
        
        # Square off at quote 1250
        ok_exit, item = ledger.record_trade_exit(pos["id"], 1250.0, reason="Intraday EOD 15:20 Auto-Squareoff")
        self.assertTrue(ok_exit)
        self.assertEqual(item["entry_price"], 1200.0)
        self.assertEqual(item["exit_price"], 1250.0)
        self.assertEqual(item["pnl"], 500.0)
        self.assertGreater(item["pnl"], 0.0)

    def test_futures_rejected_when_allow_options_is_false(self):
        risk_eng = RiskEngine({"allow_options": False})
        res = risk_eng.evaluate_order_risk(
            "NIFTY 50 (Index Futures)", "BUY", 22000.0, 21900.0, 22200.0,
            available_cash=50000.0, daily_realized_loss=0.0,
            current_trades_count=0, current_open_positions_count=0
        )
        self.assertFalse(res["passed"])
        self.assertIn("Derivatives trading is disabled", res["reasons"][0])

    def test_all_readme_exit_rules(self):
        pos_mgr = PositionManager({"flat_by": "15:20", "enable_partial_profit": True, "partial_profit_ratio": 0.5})
        pos = {
            "id": "POS-1",
            "symbol": "TEST.NS",
            "entry_price": 100.0,
            "initial_stop": 90.0,
            "current_stop": 90.0,
            "qty": 10,
            "peak_price": 100.0,
            "partial_exited": False
        }

        # 1. Hard initial stop hit
        res_stop = pos_mgr.evaluate_exit(pos, current_price=89.0, current_time="10:00")
        self.assertEqual(res_stop["action"], "FULL_EXIT")
        self.assertIn("Stop-loss hit", res_stop["reason"])

        # 2. +1R Partial Profit Booking (Price 110.0)
        res_partial = pos_mgr.evaluate_exit(pos, current_price=110.0, current_time="10:00")
        self.assertEqual(res_partial["action"], "PARTIAL_EXIT")
        self.assertEqual(res_partial["exit_qty"], 5)
        # 3. Breakeven move
        self.assertEqual(res_partial["updated_position"]["current_stop"], 100.0)

        # 4. ATR Trail
        updated_pos = res_partial["updated_position"]
        res_atr = pos_mgr.evaluate_exit(updated_pos, current_price=115.0, current_atr=2.0, current_time="10:00")
        # Peak = 115, stop = 115 - 1.5 * 2 = 112
        self.assertEqual(res_atr["updated_position"]["current_stop"], 112.0)

        # 5. Peak Reversal (0.2% drop from peak 120 -> below 119.76)
        updated_pos["peak_price"] = 120.0
        res_reversal = pos_mgr.evaluate_exit(updated_pos, current_price=119.5, current_time="10:00")
        self.assertEqual(res_reversal["action"], "FULL_EXIT")
        self.assertIn("Peak reversal exit", res_reversal["reason"])

        # 6. EOD Squareoff at 15:20
        res_eod = pos_mgr.evaluate_exit(pos, current_price=105.0, current_time="15:20")
        self.assertEqual(res_eod["action"], "FULL_EXIT")
        self.assertIn("15:20 IST", res_eod["reason"])

if __name__ == "__main__":
    unittest.main()
