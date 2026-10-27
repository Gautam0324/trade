#!/usr/bin/env python3
import unittest
import os
import sys
import multiprocessing
import tempfile
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import ledger

def _concurrent_entry(worker_id, ledger_path, result_queue):
    # Set ledger path
    ledger.LEDGER_PATH = Path(ledger_path)
    ledger.LOCK_PATH = Path(ledger_path).parent / "ledger.lock"
    ok, res = ledger.record_trade_entry(f"STOCK_{worker_id}", "BUY", 1, 100.0)
    result_queue.put((ok, res))

class TestLedgerIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orig_ledger_path = ledger.LEDGER_PATH
        self.orig_lock_path = ledger.LOCK_PATH
        
        self.test_ledger_path = Path(self.temp_dir.name) / "ledger.json"
        self.test_lock_path = Path(self.temp_dir.name) / "ledger.lock"
        ledger.LEDGER_PATH = self.test_ledger_path
        ledger.LOCK_PATH = self.test_lock_path

        # Init clean ledger
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

    def test_back_to_back_entries_get_unique_monotonic_ids(self):
        ok1, pos1 = ledger.record_trade_entry("SBIN.NS", "BUY", 10, 500.0)
        ok2, pos2 = ledger.record_trade_entry("INFY.NS", "BUY", 5, 1000.0)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.assertEqual(pos1["id"], "POS-1")
        self.assertEqual(pos2["id"], "POS-2")
        self.assertNotEqual(pos1["id"], pos2["id"])

    def test_refuse_entry_above_free_cash(self):
        # Free cash is 100,000; try order of 150,000
        ok, res = ledger.record_trade_entry("EXPENSIVE.NS", "BUY", 150, 1000.0)
        self.assertFalse(ok)
        self.assertIn("Insufficient free cash", res)

    def test_partial_and_full_exit_math(self):
        ok, pos = ledger.record_trade_entry("TCS.NS", "BUY", 10, 1000.0)
        self.assertTrue(ok)
        pos_id = pos["id"]

        # Partial exit: 5 qty @ 1100
        ok_p, p_item = ledger.record_trade_exit(pos_id, 1100.0, exit_qty=5)
        self.assertTrue(ok_p)
        self.assertEqual(p_item["invested"], 5000.0)
        self.assertEqual(p_item["returned"], 5500.0)
        self.assertEqual(p_item["pnl"], 500.0)

        # Remaining open position has qty = 5
        leg = ledger.get_ledger()
        self.assertEqual(len(leg["open_positions"]), 1)
        self.assertEqual(leg["open_positions"][0]["qty"], 5)

        # Full exit: remaining 5 qty @ 1200
        ok_f, f_item = ledger.record_trade_exit(pos_id, 1200.0)
        self.assertTrue(ok_f)
        self.assertEqual(f_item["invested"], 5000.0)
        self.assertEqual(f_item["returned"], 6000.0)
        self.assertEqual(f_item["pnl"], 1000.0)

        # No open positions left
        leg = ledger.get_ledger()
        self.assertEqual(len(leg["open_positions"]), 0)
        self.assertEqual(leg["realized_pnl"], 1500.0)

    def test_closing_by_id_and_ambiguous_symbol_rejection(self):
        # Two open positions with same symbol
        ok1, pos1 = ledger.record_trade_entry("RELIANCE.NS", "BUY", 5, 2000.0, config={"max_open_positions": 5})
        ok2, pos2 = ledger.record_trade_entry("RELIANCE.NS", "BUY", 5, 2100.0, config={"max_open_positions": 5})
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        # Exiting by symbol "RELIANCE.NS" should fail due to ambiguity
        ok_sym, err = ledger.record_trade_exit("RELIANCE.NS", 2200.0)
        self.assertFalse(ok_sym)
        self.assertIn("Ambiguous exit", err)

        # Exiting by specific ID succeeds
        ok_id, item = ledger.record_trade_exit(pos1["id"], 2200.0)
        self.assertTrue(ok_id)
        self.assertEqual(item["id"], pos1["id"])

    def test_two_processes_concurrent_writes_lose_no_updates(self):
        queue = multiprocessing.Queue()
        p1 = multiprocessing.Process(target=_concurrent_entry, args=(1, str(self.test_ledger_path), queue))
        p2 = multiprocessing.Process(target=_concurrent_entry, args=(2, str(self.test_ledger_path), queue))

        p1.start()
        p2.start()
        p1.join(timeout=10)
        p2.join(timeout=10)

        ok1, res1 = queue.get(timeout=2)
        ok2, res2 = queue.get(timeout=2)
        self.assertTrue(ok1)
        self.assertTrue(ok2)

        leg = ledger.get_ledger()
        self.assertEqual(len(leg["open_positions"]), 2)
        ids = {p["id"] for p in leg["open_positions"]}
        self.assertEqual(len(ids), 2)

if __name__ == "__main__":
    unittest.main()
