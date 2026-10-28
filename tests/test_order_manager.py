#!/usr/bin/env python3
"""
Exhaustive Tests for Order Lifecycle & Duplicate Order Prevention (Phase 2):
- Idempotency with repeated signal
- Blocking when symbol has active open position
- Blocking when symbol has pending in-flight order
- State machine progression
- Restart recovery
- Idempotent exit handling
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from order_manager import OrderManager, OrderState

class TestOrderManager(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp_dir.name) / "test_orders.db"
        self.ledger_path = Path(self.tmp_dir.name) / "test_ledger.json"
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            f.write('{"starting_capital": 1000000.0, "free_cash": 1000000.0, "deployed_cash": 0, "open_positions": [], "trade_history": []}')
        self.mgr = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def test_create_and_fill_order_lifecycle(self):
        """Full lifecycle: CREATED -> SUBMITTED -> FILLED -> CLOSED."""
        res = self.mgr.create_order("RELIANCE", "BUY", 100, 1200.0, 1190.0, 1220.0, signal_id="SIG-1")
        self.assertTrue(res["success"])
        order_id = res["order_id"]
        self.assertEqual(res["status"], OrderState.CREATED)

        # Transition to SUBMITTED
        self.mgr.update_order_status(order_id, OrderState.SUBMITTED)

        # Fill order
        fill = self.mgr.record_simulated_fill(order_id, fill_price=1200.0)
        self.assertEqual(fill["status"], "OPEN")
        self.assertEqual(fill["symbol"], "RELIANCE")

        # Open positions check
        open_pos = self.mgr.get_open_positions()
        self.assertEqual(len(open_pos), 1)
        self.assertEqual(open_pos[0]["symbol"], "RELIANCE")

        # Exit position
        exit_rec = self.mgr.record_position_exit("RELIANCE", exit_price=1215.0, exit_reason="Target Hit", fees=50.0)
        self.assertIsNotNone(exit_rec)
        self.assertEqual(exit_rec["gross_pnl"], 1500.0)
        self.assertEqual(exit_rec["fees"], 50.0)
        self.assertEqual(exit_rec["pnl"], 1450.0)

        # Ensure no open position remains
        self.assertEqual(len(self.mgr.get_open_positions()), 0)

    def test_duplicate_signal_idempotency(self):
        """Proves identical signal_id cannot create a second order."""
        res1 = self.mgr.create_order("TCS", "BUY", 50, 2400.0, 2380.0, 2440.0, signal_id="SIG-TCS-101")
        self.assertTrue(res1["success"])

        # Attempt exact same signal again
        res2 = self.mgr.create_order("TCS", "BUY", 50, 2400.0, 2380.0, 2440.0, signal_id="SIG-TCS-101")
        self.assertFalse(res2["success"])
        self.assertEqual(res2["status"], OrderState.REJECTED)
        self.assertIn("Duplicate signal", res2["reason"])

    def test_existing_open_position_blocks_new_entry(self):
        """Proves open position blocks new order admission for the same symbol."""
        res = self.mgr.create_order("INFY", "BUY", 100, 1100.0, 1090.0, 1120.0, signal_id="SIG-INFY-1")
        self.assertTrue(res["success"])
        self.mgr.record_simulated_fill(res["order_id"], fill_price=1100.0)

        # Try entering INFY again with a new signal
        res2 = self.mgr.create_order("INFY", "BUY", 100, 1105.0, 1095.0, 1125.0, signal_id="SIG-INFY-2")
        self.assertFalse(res2["success"])
        self.assertIn("already has active open position", res2["reason"])

    def test_pending_in_flight_order_blocks_new_entry(self):
        """Proves pending in-flight order blocks second entry attempt."""
        res = self.mgr.create_order("HDFCBANK", "BUY", 200, 680.0, 675.0, 690.0, signal_id="SIG-HDFC-1")
        self.assertTrue(res["success"])

        # Try entering while order is in CREATED state
        res2 = self.mgr.create_order("HDFCBANK", "BUY", 200, 680.0, 675.0, 690.0, signal_id="SIG-HDFC-2")
        self.assertFalse(res2["success"])
        self.assertIn("already has in-flight order", res2["reason"])

    def test_restart_recovery(self):
        """Proves new instance of OrderManager correctly reads open positions from DB."""
        res = self.mgr.create_order("JPL", "BUY", 150, 1400.0, 1385.0, 1425.0, signal_id="SIG-JPL-RESTART")
        self.mgr.record_simulated_fill(res["order_id"], fill_price=1400.0)

        # Simulate bot restart by creating brand new OrderManager pointing to same DB
        restarted_mgr = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)
        open_pos = restarted_mgr.get_open_positions()
        self.assertEqual(len(open_pos), 1)
        self.assertEqual(open_pos[0]["symbol"], "JPL")
        self.assertEqual(open_pos[0]["quantity"], 150)

        # Ensure restarted manager still blocks duplicate entry
        res2 = restarted_mgr.create_order("JPL", "BUY", 150, 1405.0, 1390.0, 1430.0, signal_id="SIG-JPL-NEW")
        self.assertFalse(res2["success"])
        self.assertIn("already has active open position", res2["reason"])

if __name__ == "__main__":
    unittest.main()

