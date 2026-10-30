"""
Unit tests for Paper Execution Engine, State Transitions, and Cost Accounting.
"""

import unittest
import os
import sys
from pathlib import Path
base_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(base_dir / "trader"))
sys.path.append(str(base_dir))
import tempfile
from trader.execution.paper_order_manager import PaperOrderManager, OrderLifecycleState
from trader.execution.position_manager import PositionManager
from trader.execution.trade_ledger import TradeLedger
from trader.execution.paper_engine import PaperExecutionEngine, PaperTradingIsolationError
from cost_model import CostModel

class TestPaperExecution(unittest.TestCase):
    def setUp(self):
        self.tmp_ledger = tempfile.NamedTemporaryFile(delete=False)
        self.tmp_ledger.close()
        self.ledger = TradeLedger(self.tmp_ledger.name)
        self.order_mgr = PaperOrderManager()
        self.pos_mgr = PositionManager()
        self.cost_model = CostModel()
        self.engine = PaperExecutionEngine(
            paper_order_manager=self.order_mgr,
            position_manager=self.pos_mgr,
            trade_ledger=self.ledger,
            cost_model=self.cost_model,
            slippage_pct=0.0002
        )

    def tearDown(self):
        if os.path.exists(self.tmp_ledger.name):
            os.remove(self.tmp_ledger.name)

    def test_paper_entry_and_exit_lifecycle(self):
        # 1. Execute Entry
        pos = self.engine.execute_paper_entry(
            symbol="INFY",
            side="BUY",
            quantity=100,
            price=1000.0,
            stop_loss=990.0,
            target_price=1020.0,
            signal_id="SIG-INFY-BUY-1",
            strategy_name="CONFLUENCE"
        )
        self.assertEqual(pos["symbol"], "INFY")
        self.assertEqual(self.pos_mgr.count(), 1)
        # Slippage on buy: 1000 * 1.0002 = 1000.20
        self.assertAlmostEqual(pos["entry_price"], 1000.20, places=2)

        # 2. Execute Exit with Profit
        closed = self.engine.execute_paper_exit(
            symbol="INFY",
            exit_price=1010.0,
            exit_reason="TICK_TARGET_HIT"
        )
        self.assertIsNotNone(closed)
        self.assertEqual(self.pos_mgr.count(), 0)
        self.assertEqual(closed["status"], "CLOSED")
        self.assertGreater(closed["gross_pnl"], 0)
        self.assertGreater(closed["fees"], 0)
        # Net PnL = Gross - Fees
        self.assertAlmostEqual(closed["pnl"], closed["gross_pnl"] - closed["fees"], places=1)

        # Verify ledger updated
        summary = self.ledger.get_summary()
        self.assertEqual(len(summary["trade_history"]), 1)

    def test_duplicate_exit_returns_none(self):
        self.engine.execute_paper_entry(
            symbol="TCS", side="BUY", quantity=10, price=2500.0,
            stop_loss=2480.0, target_price=2530.0, signal_id="SIG-TCS-1", strategy_name="EMA_CHANNEL"
        )
        exit1 = self.engine.execute_paper_exit("TCS", 2510.0, "TARGET_HIT")
        self.assertIsNotNone(exit1)

        # Second exit on closed position returns None safely
        exit2 = self.engine.execute_paper_exit("TCS", 2510.0, "TARGET_HIT")
        self.assertIsNone(exit2)

    def test_safety_guard_blocks_live_trading(self):
        os.environ["LIVE_TRADING"] = "true"
        try:
            with self.assertRaises(PaperTradingIsolationError):
                PaperExecutionEngine(self.order_mgr, self.pos_mgr, self.ledger)
        finally:
            os.environ["LIVE_TRADING"] = "false"

if __name__ == "__main__":
    unittest.main()
