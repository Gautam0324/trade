"""
Unit tests for TickRiskMonitor, DailyRiskState, and PositionSizer (V2).
"""

import unittest
import os
import tempfile
from trader.risk.tick_risk_monitor import TickRiskMonitor
from trader.risk.position_sizer import PositionSizer
from trader.risk.daily_risk_state import DailyRiskState

class TestRiskV2(unittest.TestCase):
    def setUp(self):
        self.exits = []
        self.monitor = TickRiskMonitor(on_exit_triggered=lambda e: self.exits.append(e))

    def test_tick_stop_loss_triggered(self):
        self.monitor.register_position({
            "id": "POS-001", "symbol": "RELIANCE", "side": "BUY",
            "entry_price": 1160.0, "sl_price": 1150.0, "target_price": 1180.0
        })

        # Tick 1: Inside range
        res1 = self.monitor.evaluate_tick({"symbol": "RELIANCE", "ltp": 1155.0})
        self.assertIsNone(res1)
        self.assertEqual(len(self.exits), 0)

        # Tick 2: Stop loss hit!
        res2 = self.monitor.evaluate_tick({"symbol": "RELIANCE", "ltp": 1149.50})
        self.assertIsNotNone(res2)
        self.assertEqual(res2["exit_reason"], "TICK_STOP_LOSS_HIT")
        self.assertEqual(len(self.exits), 1)

        # Subsequent ticks should not trigger duplicate exit
        res3 = self.monitor.evaluate_tick({"symbol": "RELIANCE", "ltp": 1148.0})
        self.assertIsNone(res3)
        self.assertEqual(len(self.exits), 1)

    def test_tick_target_triggered(self):
        self.monitor.register_position({
            "id": "POS-002", "symbol": "INFY", "side": "BUY",
            "entry_price": 1170.0, "sl_price": 1160.0, "target_price": 1185.0
        })

        res = self.monitor.evaluate_tick({"symbol": "INFY", "ltp": 1186.0})
        self.assertIsNotNone(res)
        self.assertEqual(res["exit_reason"], "TICK_TARGET_HIT")

    def test_daily_risk_state_persistence(self):
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            state1 = DailyRiskState(tmp_path)
            state1.record_trade_completion(-500.0)
            self.assertEqual(state1.get_summary()["realized_loss"], 500.0)

            # Simulate process restart
            state2 = DailyRiskState(tmp_path)
            self.assertEqual(state2.get_summary()["realized_loss"], 500.0)
            self.assertEqual(state2.get_summary()["trade_count"], 1)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_position_sizer(self):
        sizer = PositionSizer()
        qty, info = sizer.calculate_quantity(capital=100000.0, entry_price=1000.0, stop_loss=990.0, risk_pct=1.0)
        # Risk amount = 1000. Risk per share = 10. Qty = 35 shares due to 35% max capital constraint (35,000 / 1000 = 35)
        self.assertEqual(qty, 35)
        self.assertEqual(info["order_value"], 35000.0)

if __name__ == "__main__":
    unittest.main()

