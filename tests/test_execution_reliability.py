#!/usr/bin/env python3
"""
Unit and Integration Tests for Phase 3.2: Execution Reliability & Persistence Hardening.
Verifies all 12 mandatory Phase 3.2 requirements:
1. Pending order recovery after restart.
2. Duplicate submission after restart.
3. Simultaneous cash/F&O ledger writes.
4. Lost-update prevention.
5. Duplicate exit requests.
6. Retry exhaustion.
7. Stale candle rejection.
8. Invalid quote timestamps.
9. Quote-source classification.
10. Orphaned order reconciliation.
11. Partial fill recovery.
12. Paper-only execution after restart.
"""

import unittest
from unittest.mock import MagicMock, patch
import json
import time
import os
import tempfile
import datetime
import threading
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
sys.path.insert(0, str(TRADER_DIR))

import fno_trader
from order_manager import OrderManager, OrderState
from ledger_transaction import (
    FileLock,
    locked_ledger_transaction,
    generate_order_id,
    generate_fill_id,
    read_ledger_locked
)

class MockBrokerStatus:
    def __init__(self, status="COMPLETE", fill_price=105.0, fill_qty=65):
        self.status = status
        self.fill_price = fill_price
        self.fill_qty = fill_qty

    def get_order_status(self, order_id):
        return {
            "status": self.status,
            "fill_price": self.fill_price,
            "fill_qty": self.fill_qty
        }

class TestExecutionReliability(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_orders.db"
        self.ledger_path = Path(self.temp_dir.name) / "test_ledger.json"
        
        # Initialize test ledger with starting capital
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            json.dump({
                "starting_capital": 100000.0,
                "free_cash": 100000.0,
                "deployed_cash": 0.0,
                "realized_pnl": 0.0,
                "open_positions": [],
                "trade_history": []
            }, f)

        self.om = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)
        self.nifty_contract = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "45001",
            "strike": 24200.0,
            "expiry": "26NOV2026",
            "lotsize": 65,
            "option_type": "CE",
            "underlying": "NIFTY"
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_1_pending_order_recovery_after_restart(self):
        """
        Requirement 1: Pending order recovery after process restart.
        Verifies:
        - Never assumes pending order was filled if execution state is uncertain.
        - Transitions to REQUIRES_OPERATOR_REVIEW when broker confirmation is absent.
        - If broker confirms fill, transitions to FILLED and creates position.
        """
        # Scenario A: Uncertain state without broker fill confirmation -> REQUIRES_OPERATOR_REVIEW
        order_res = self.om.create_order(
            symbol="NIFTY26NOV24200CE", side="BUY", quantity=65, price=100.0,
            stop_loss=95.0, target_price=115.0, signal_id="SIG-TEST-REC-1"
        )
        oid = order_res["order_id"]
        self.om.update_order_status(oid, OrderState.SUBMITTED)

        # Simulate process restart by instantiating new OrderManager
        restarted_om = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)
        recovered = restarted_om.recover_pending_orders(broker_adapter=None)

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0]["order_id"], oid)
        self.assertEqual(recovered[0]["status"], OrderState.REQUIRES_OPERATOR_REVIEW)

        # Check DB state
        with restarted_om._connection() as conn:
            row = conn.execute("SELECT status, rejection_reason FROM orders WHERE order_id = ?", (oid,)).fetchone()
            self.assertEqual(row["status"], OrderState.REQUIRES_OPERATOR_REVIEW)
            self.assertIn("requires operator review", row["rejection_reason"])

        # Scenario B: Broker confirms fill upon restart -> transitions to FILLED
        order_res2 = self.om.create_order(
            symbol="BANKNIFTY26NOV51500PE", side="BUY", quantity=30, price=200.0,
            stop_loss=190.0, target_price=230.0, signal_id="SIG-TEST-REC-2"
        )
        oid2 = order_res2["order_id"]
        self.om.update_order_status(oid2, OrderState.SUBMITTED)

        broker_mock = MockBrokerStatus(status="COMPLETE", fill_price=202.5, fill_qty=30)
        restarted_om2 = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)
        recovered2 = restarted_om2.recover_pending_orders(broker_adapter=broker_mock)

        filled_rec = [r for r in recovered2 if r["order_id"] == oid2]
        self.assertEqual(len(filled_rec), 1)
        self.assertEqual(filled_rec[0]["status"], OrderState.FILLED)

    def test_2_duplicate_submission_after_restart(self):
        """
        Requirement 2: Prevent duplicate submission after restart.
        Verifies that an order in REQUIRES_OPERATOR_REVIEW or with an existing signal_id
        blocks any duplicate submission attempt for the symbol.
        """
        signal_id = "SIG-UNIQUE-123"
        order_res = self.om.create_order(
            symbol="NIFTY26NOV24200CE", side="BUY", quantity=65, price=100.0,
            stop_loss=95.0, target_price=115.0, signal_id=signal_id
        )
        oid = order_res["order_id"]
        self.om.update_order_status(oid, OrderState.REQUIRES_OPERATOR_REVIEW)

        # Reboot
        rebooted_om = OrderManager(db_path=self.db_path, ledger_path=self.ledger_path)

        # Attempt to admit duplicate order with same signal
        can_admit_sig, reason_sig = rebooted_om.can_admit_order("NIFTY26NOV24200CE", signal_id=signal_id)
        self.assertFalse(can_admit_sig)
        self.assertIn("Duplicate signal", reason_sig)

        # Attempt to admit new order for the same symbol while previous is under review
        can_admit_sym, reason_sym = rebooted_om.can_admit_order("NIFTY26NOV24200CE", signal_id="SIG-NEW-456")
        self.assertFalse(can_admit_sym)
        self.assertIn("Duplicate pending order", reason_sym)

    def test_3_simultaneous_cash_and_fno_ledger_writes(self):
        """
        Requirement 3: Simultaneous cash and F&O ledger writes.
        Verifies that multiple concurrent threads updating the ledger under file lock
        complete without deadlocks, corruption, or write collision errors.
        """
        iterations = 20
        errors = []

        def cash_writer():
            for i in range(iterations):
                def _op(ledger):
                    th = ledger.setdefault("trade_history", [])
                    th.append({"id": f"CASH-{i}", "symbol": "RELIANCE", "pnl": 50.0})
                    ledger["realized_pnl"] = round(ledger.get("realized_pnl", 0.0) + 50.0, 2)
                try:
                    locked_ledger_transaction(self.ledger_path, _op)
                except Exception as e:
                    errors.append(f"Cash writer error: {e}")

        def fno_writer():
            for i in range(iterations):
                def _op(ledger):
                    th = ledger.setdefault("trade_history", [])
                    th.append({"id": f"FNO-{i}", "symbol": "NIFTY26NOV24200CE", "pnl": 100.0})
                    ledger["realized_pnl"] = round(ledger.get("realized_pnl", 0.0) + 100.0, 2)
                try:
                    locked_ledger_transaction(self.ledger_path, _op)
                except Exception as e:
                    errors.append(f"FNO writer error: {e}")

        t1 = threading.Thread(target=cash_writer)
        t2 = threading.Thread(target=fno_writer)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Concurrent write errors encountered: {errors}")

        # Verify final state
        final_ledger = read_ledger_locked(self.ledger_path)
        self.assertEqual(len(final_ledger["trade_history"]), iterations * 2)
        expected_pnl = (iterations * 50.0) + (iterations * 100.0)
        self.assertAlmostEqual(final_ledger["realized_pnl"], expected_pnl, places=2)

    def test_4_lost_update_prevention(self):
        """
        Requirement 4: Lost-update prevention.
        Verifies that atomic Read-Modify-Write within locked_ledger_transaction
        completely eliminates lost-update anomalies under concurrent balance deductions.
        """
        num_threads = 10
        deduction_per_thread = 1000.0
        initial_cash = 100000.0

        def deduct_cash():
            def _op(ledger):
                cur_free = float(ledger["free_cash"])
                time.sleep(0.01)  # Simulate compute/network latency within lock
                ledger["free_cash"] = round(cur_free - deduction_per_thread, 2)
            locked_ledger_transaction(self.ledger_path, _op)

        threads = [threading.Thread(target=deduct_cash) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        final_ledger = read_ledger_locked(self.ledger_path)
        expected_final = initial_cash - (num_threads * deduction_per_thread)
        self.assertAlmostEqual(final_ledger["free_cash"], expected_final, places=2)

    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_5_duplicate_exit_requests(self, mock_tg, mock_save):
        """
        Requirement 5: Duplicate exit requests.
        Verifies that an in-flight exit request blocks any concurrent duplicate exit attempts.
        """
        trader = fno_trader.FnoOptionsTrader(order_manager=self.om)
        opt_sym = "NIFTY26NOV24200CE"
        trader.open_positions[opt_sym] = {
            "id": "POS-1", "symbol": opt_sym, "token": "45001",
            "entry_price": 100.0, "qty": 65, "side": "BUY"
        }

        # Simulate that exit is currently in-flight
        trader.in_flight_exits.add(opt_sym)

        # Attempt duplicate exit
        res = trader.exit_position(opt_sym, "Duplicate Trigger", exit_price=110.0)
        self.assertFalse(res)
        # Position must remain OPEN
        self.assertIn(opt_sym, trader.open_positions)

    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_6_retry_exhaustion(self, mock_tg, mock_save):
        """
        Requirement 6: Retry exhaustion.
        Verifies that when market quote is continuously unavailable:
        - Bounded retries are executed up to max_retries.
        - Open position is preserved (NOT closed or dropped).
        - Operator alert is generated.
        - Successful exit is NEVER fabricated.
        """
        trader = fno_trader.FnoOptionsTrader(order_manager=self.om)
        opt_sym = "NIFTY26NOV24200CE"
        trader.open_positions[opt_sym] = {
            "id": "POS-1", "symbol": opt_sym, "token": "45001",
            "entry_price": 100.0, "qty": 65, "side": "BUY"
        }

        # Mock quote fetch to return None (failure)
        trader.fetch_option_quote = MagicMock(return_value=None)

        initial_completed = len(trader.completed_trades)
        # Execute exit with 3 retries and 0 backoff for fast testing
        success = trader.exit_position(opt_sym, "Stop-Loss Hit", exit_price=None, max_retries=3, backoff_base=0.0)

        self.assertFalse(success)
        self.assertEqual(trader.fetch_option_quote.call_count, 3)
        # Position must remain preserved in open_positions
        self.assertIn(opt_sym, trader.open_positions)
        self.assertEqual(len(trader.completed_trades), initial_completed)

        # Operator alert must be dispatched
        mock_tg.assert_called()
        alert_call_arg = mock_tg.call_args[0][0]
        self.assertIn("EXIT RETRY EXHAUSTED", alert_call_arg)
        self.assertIn(opt_sym, alert_call_arg)

    def test_7_stale_candle_rejection(self):
        """
        Requirement 7: Stale candle rejection.
        Verifies that candle close quotes older than 180s are strictly rejected.
        """
        api = MagicMock()
        now_dt = datetime.datetime.now()
        # Bar from 10 minutes ago
        stale_time = (now_dt - datetime.timedelta(minutes=10)).strftime("%H:%M")
        today_str = now_dt.strftime("%Y-%m-%d")

        api.getLtpData.return_value = {"status": False}
        api.getMarketData.return_value = {"status": False}
        api.getCandleData.return_value = {
            "status": True,
            "data": [[f"{today_str}T{stale_time}:00+05:30", 100, 105, 95, 102.0, 500]]
        }

        quote = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertIsNone(quote)

    def test_8_invalid_quote_timestamps(self):
        """
        Requirement 8: Invalid and malformed quote timestamps.
        Verifies that malformed or non-parseable timestamps in broker responses are rejected.
        """
        api = MagicMock()
        # Case A: Corrupt gibberish timestamp
        api.getLtpData.return_value = {
            "status": True,
            "data": {
                "ltp": 150.0, "bid": 149.0, "ask": 151.0,
                "feed_time": "INVALID_TIMESTAMP_GIBBERISH"
            }
        }
        api.getMarketData.return_value = {"status": False}
        api.getCandleData.return_value = {"status": False}

        quote = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertIsNone(quote)

        # Case B: Future clock skew timestamp (> 60s ahead)
        future_time = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
        api.getLtpData.return_value = {
            "status": True,
            "data": {
                "ltp": 150.0, "bid": 149.0, "ask": 151.0,
                "feed_time": future_time
            }
        }
        quote_future = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertIsNone(quote_future)

    def test_9_quote_source_classification(self):
        """
        Requirement 9: Quote-source classification and price provenance.
        Verifies distinct classification:
        - Real-time LTP: is_realtime_quote=True, LIVE_QUOTE
        - Market depth: is_realtime_quote=True, LIVE_QUOTE
        - Candle: is_realtime_quote=False, HISTORICAL_CANDLE
        - Full price_provenance payload attached
        """
        api = MagicMock()

        # 1. Real-time LTP
        api.getLtpData.return_value = {
            "status": True,
            "data": {"ltp": 155.0, "bid": 154.0, "ask": 156.0}
        }
        q_ltp = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertTrue(q_ltp["is_realtime_quote"])
        self.assertEqual(q_ltp["quote_type"], "LIVE_QUOTE")
        self.assertEqual(q_ltp["quote_source"], "ANGEL_LTP_API")
        self.assertIn("price_provenance", q_ltp)

        # 2. Market Depth
        api.getLtpData.return_value = {"status": False}
        api.getMarketData.return_value = {
            "status": True,
            "data": {
                "fetched": [{
                    "ltp": 160.0,
                    "depth": {"buy": [{"price": 159.0}], "sell": [{"price": 161.0}]}
                }]
            }
        }
        q_depth = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertTrue(q_depth["is_realtime_quote"])
        self.assertEqual(q_depth["quote_type"], "LIVE_QUOTE")
        self.assertEqual(q_depth["quote_source"], "ANGEL_MARKET_DATA")

        # 3. Candle Close (1 minute ago)
        api.getMarketData.return_value = {"status": False}
        one_min_ago = (datetime.datetime.now() - datetime.timedelta(minutes=1)).strftime("%H:%M")
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        api.getCandleData.return_value = {
            "status": True,
            "data": [[f"{today_str}T{one_min_ago}:00+05:30", 100, 105, 95, 150.0, 500]]
        }
        q_candle = fno_trader.get_option_market_quote(api, "OPT_SYM", "45001")
        self.assertFalse(q_candle["is_realtime_quote"])
        self.assertEqual(q_candle["quote_type"], "HISTORICAL_CANDLE")
        self.assertEqual(q_candle["quote_source"], "ANGEL_CANDLE_CLOSE")

    def test_10_orphaned_order_reconciliation(self):
        """
        Requirement 10: Orphaned order reconciliation.
        Verifies that an order marked FILLED without an open position is reconciled.
        """
        order_res = self.om.create_order(
            symbol="NIFTY26NOV24200CE", side="BUY", quantity=65, price=100.0,
            stop_loss=95.0, target_price=115.0, signal_id="SIG-ORPHAN-1"
        )
        oid = order_res["order_id"]
        # Mark order FILLED directly in DB without creating position (orphaned fill)
        with self.om._connection() as conn:
            conn.execute("UPDATE orders SET status = 'FILLED' WHERE order_id = ?", (oid,))
            conn.commit()

        # Reconcile orphans
        reconciled = self.om.reconcile_orphaned_orders()
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0]["order_id"], oid)

        # Position should now exist in positions table
        open_positions = self.om.get_open_positions()
        self.assertTrue(any(p["order_id"] == oid for p in open_positions))

    def test_11_partial_fill_recovery(self):
        """
        Requirement 11: Partial fill recovery.
        Verifies:
        - Partial fill updates filled_quantity and transitions to PARTIALLY_FILLED.
        - Position tracks filled quantity.
        - Subsequent partial fill completing total quantity transitions to FILLED.
        """
        order_res = self.om.create_order(
            symbol="NIFTY26NOV24200CE", side="BUY", quantity=65, price=100.0,
            stop_loss=95.0, target_price=115.0, signal_id="SIG-PARTIAL-1"
        )
        oid = order_res["order_id"]

        # First partial fill: 30 shares
        res1 = self.om.record_partial_fill(oid, filled_qty=30, fill_price=101.0)
        self.assertEqual(res1["status"], OrderState.PARTIALLY_FILLED)
        self.assertEqual(res1["filled_quantity"], 30)
        self.assertEqual(res1["remaining_quantity"], 35)

        # Second partial fill: remaining 35 shares
        res2 = self.om.record_partial_fill(oid, filled_qty=35, fill_price=102.0)
        self.assertEqual(res2["status"], OrderState.FILLED)
        self.assertEqual(res2["filled_quantity"], 65)
        self.assertEqual(res2["remaining_quantity"], 0)

        # Verify average price on position: ((30*101) + (35*102)) / 65 = 101.54
        with self.om._connection() as conn:
            pos = conn.execute("SELECT quantity, entry_price FROM positions WHERE order_id = ?", (oid,)).fetchone()
            self.assertEqual(pos["quantity"], 65)
            self.assertAlmostEqual(pos["entry_price"], 101.54, places=2)

    def test_12_paper_only_execution_after_restart(self):
        """
        Requirement 12: Paper-only execution isolation after restart.
        Verifies that after process restart, paper mode invariant is intact
        and broker cannot place real Angel One orders.
        """
        trader = fno_trader.FnoOptionsTrader(order_manager=self.om)
        self.assertTrue(trader.config.get("paper", True))
        self.assertFalse(trader.broker_adapter.is_live())

if __name__ == "__main__":
    unittest.main()
