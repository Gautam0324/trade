#!/usr/bin/env python3
"""
Unit Tests for EOD State Machine & Lifecycle (Phase 3 Deliverable)
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from eod_state_machine import EODStateMachine, EODState

class TestEODStateMachine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.state_file = Path(self.temp_dir) / "test_eod_state.json"
        self.sm = EODStateMachine(state_file=self.state_file, entry_cutoff="15:15", flat_by="15:20")

    def tearDown(self):
        if self.state_file.exists():
            os.remove(self.state_file)
        try:
            os.rmdir(self.temp_dir)
        except Exception:
            pass

    def test_normal_entry_approved(self):
        ok, msg = self.sm.can_open_new_position(current_time_str="11:30")
        self.assertTrue(ok)
        self.assertEqual(msg, "Approved")

    def test_entry_cutoff_rejection(self):
        ok, msg = self.sm.can_open_new_position(current_time_str="15:16")
        self.assertFalse(ok)
        self.assertIn("cutoff reached", msg)
        self.assertEqual(self.sm.state, EODState.EOD_LOCKED)

    def test_eod_complete_blocks_entries(self):
        self.sm.mark_eod_complete()
        self.assertEqual(self.sm.state, EODState.EOD_COMPLETE)
        ok, msg = self.sm.can_open_new_position(current_time_str="15:10")
        self.assertFalse(ok)
        self.assertIn("EOD_COMPLETE", msg)

    def test_square_off_trigger(self):
        self.assertFalse(self.sm.should_trigger_square_off("15:19"))
        self.assertTrue(self.sm.should_trigger_square_off("15:20"))
        self.assertTrue(self.sm.should_trigger_square_off("15:22"))

    def test_idempotent_closed_positions(self):
        pos_id = "POS-RELIANCE-12345"
        self.assertFalse(self.sm.is_position_already_closed(pos_id))
        self.sm.mark_position_closed(pos_id)
        self.assertTrue(self.sm.is_position_already_closed(pos_id))
        # Mark again
        self.sm.mark_position_closed(pos_id)
        self.assertTrue(self.sm.is_position_already_closed(pos_id))

    def test_restart_recovery_persistence(self):
        self.sm.mark_position_closed("POS-TCS-001")
        self.sm.mark_eod_complete()

        # Re-instantiate from the same persistent file (simulating bot restart)
        sm2 = EODStateMachine(state_file=self.state_file, entry_cutoff="15:15", flat_by="15:20")
        self.assertEqual(sm2.state, EODState.EOD_COMPLETE)
        self.assertTrue(sm2.is_position_already_closed("POS-TCS-001"))
        ok, _ = sm2.can_open_new_position("15:10")
        self.assertFalse(ok)

if __name__ == "__main__":
    unittest.main()

