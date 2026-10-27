#!/usr/bin/env python3
import unittest
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import ledger
from risk_engine import RiskEngine

class TestTradeCapsAndDailyLoss(unittest.TestCase):
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
            "realized_pnl_by_day": {
                "2026-10-26": -500.0  # Loss from yesterday
            },
            "next_position_seq": 1,
            "open_positions": [],
            "trade_history": [
                {
                    "id": "POS-0",
                    "symbol": "PAST.NS",
                    "qty": 10,
                    "entry_price": 100.0,
                    "exit_price": 50.0,
                    "entry_date": "2026-10-26",
                    "exit_date": "2026-10-26",
                    "pnl": -500.0
                }
            ]
        }
        ledger.save_ledger(init_data)

    def tearDown(self):
        ledger.LEDGER_PATH = self.orig_ledger_path
        ledger.LOCK_PATH = self.orig_lock_path
        self.temp_dir.cleanup()

    def test_fourth_entry_on_same_day_is_blocked_even_while_open(self):
        # Place 3 entries today
        ok1, _ = ledger.record_trade_entry("SYM1.NS", "BUY", 1, 100.0)
        ok2, _ = ledger.record_trade_entry("SYM2.NS", "BUY", 1, 100.0)
        ok3, _ = ledger.record_trade_entry("SYM3.NS", "BUY", 1, 100.0)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertTrue(ok3)

        today_count = ledger.get_today_entries_count()
        self.assertEqual(today_count, 3)

        risk_eng = RiskEngine({"max_trades_per_day": 3})
        res = risk_eng.evaluate_order_risk(
            "SYM4.NS", "BUY", 100.0, 95.0, 110.0,
            available_cash=50000.0, daily_realized_loss=0.0,
            current_trades_count=today_count,
            current_open_positions_count=3
        )
        self.assertFalse(res["passed"])
        self.assertIn("Maximum daily trades count reached", res["reasons"][0])

    def test_losses_from_earlier_days_do_not_block_today(self):
        # Yesterday had -500 loss (exceeding today's 150 limit)
        # Today realized loss should be 0.0
        today_loss = ledger.get_today_realized_loss()
        self.assertEqual(today_loss, 0.0)

        risk_eng = RiskEngine({"max_daily_loss_inr": 150.0})
        res = risk_eng.evaluate_order_risk(
            "FRESH.NS", "BUY", 100.0, 95.0, 110.0,
            available_cash=50000.0, daily_realized_loss=today_loss,
            current_trades_count=0,
            current_open_positions_count=0
        )
        self.assertTrue(res["passed"])

if __name__ == "__main__":
    unittest.main()
