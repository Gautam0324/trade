#!/usr/bin/env python3
"""
EOD Execution State Machine & Lifecycle Manager for Day Trader Pro.
Prevents duplicate entries, enforces cutoff times, guarantees idempotent square-offs,
and persists session state across process restarts.

States:
- READY: Normal trading session active, evaluating signals and orders.
- ENTRY_PENDING: Order submitted, awaiting fill confirmation.
- OPEN: Position active with protective stop & target monitoring.
- EXIT_PENDING: Exit order submitted, awaiting simulated fill confirmation.
- CLOSED: Position confirmed closed.
- EOD_LOCKED: Past entry cutoff (default 15:15 / 15:20 IST). All new entries rejected.
- EOD_COMPLETE: All open positions confirmed squared off. No new trades permitted for session.
- HALTED: Emergency risk halt active.
"""

import os
import json
import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

class EODState:
    READY = "READY"
    ENTRY_PENDING = "ENTRY_PENDING"
    OPEN = "OPEN"
    EXIT_PENDING = "EXIT_PENDING"
    CLOSED = "CLOSED"
    EOD_LOCKED = "EOD_LOCKED"
    EOD_COMPLETE = "EOD_COMPLETE"
    HALTED = "HALTED"

class EODStateMachine:
    def __init__(self, state_file: Optional[Path] = None, entry_cutoff: str = "15:15", flat_by: str = "15:20"):
        self.state_file = state_file or (Path(__file__).resolve().parent / "eod_session_state.json")
        self.entry_cutoff = entry_cutoff
        self.flat_by = flat_by
        self.session_date = self._get_ist_today()
        self.state = EODState.READY
        self.closed_position_ids = set()
        self.load_state()

    @staticmethod
    def _get_ist_today() -> str:
        # Uses local system or Asia/Kolkata
        return datetime.datetime.now().strftime("%Y-%m-%d")

    @staticmethod
    def _get_ist_time() -> str:
        return datetime.datetime.now().strftime("%H:%M")

    def load_state(self):
        """Loads persistent session state. Resets automatically on a new trading date."""
        if not self.state_file.exists():
            self._save_state()
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                d = json.load(f)
            stored_date = d.get("session_date")
            today = self._get_ist_today()

            if stored_date == today:
                self.state = d.get("state", EODState.READY)
                self.closed_position_ids = set(d.get("closed_position_ids", []))
            else:
                # New trading session -> reset
                self.session_date = today
                self.state = EODState.READY
                self.closed_position_ids = set()
                self._save_state()
        except Exception:
            self.state = EODState.READY
            self._save_state()

    def _save_state(self):
        try:
            data = {
                "session_date": self.session_date,
                "state": self.state,
                "entry_cutoff": self.entry_cutoff,
                "flat_by": self.flat_by,
                "closed_position_ids": list(self.closed_position_ids),
                "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[EOD STATE SAVE ERROR] {e}")

    def can_open_new_position(self, current_time_str: Optional[str] = None) -> Tuple[bool, str]:
        """
        Validates if a new position can be entered.
        Blocks entries if:
        - State is EOD_LOCKED, EOD_COMPLETE, or HALTED.
        - Current time is at or beyond entry cutoff.
        """
        cur_t = current_time_str or self._get_ist_time()

        if self.state in (EODState.EOD_LOCKED, EODState.EOD_COMPLETE):
            return False, f"Rejected: Session is in {self.state} state"

        if self.state == EODState.HALTED:
            return False, "Rejected: System execution is HALTED"

        if cur_t >= self.entry_cutoff:
            self.state = EODState.EOD_LOCKED
            self._save_state()
            return False, f"Rejected: Entry cutoff reached ({cur_t} >= {self.entry_cutoff})"

        return True, "Approved"

    def should_trigger_square_off(self, current_time_str: Optional[str] = None) -> bool:
        """Determines if mandatory EOD square-off must execute."""
        cur_t = current_time_str or self._get_ist_time()
        if cur_t >= self.flat_by:
            if self.state not in (EODState.EOD_LOCKED, EODState.EOD_COMPLETE):
                self.state = EODState.EOD_LOCKED
                self._save_state()
            return True
        return False

    def mark_position_closed(self, position_id: str):
        """Idempotently records position closure."""
        if position_id:
            self.closed_position_ids.add(str(position_id))
            self._save_state()

    def is_position_already_closed(self, position_id: str) -> bool:
        return str(position_id) in self.closed_position_ids

    def mark_eod_complete(self):
        """Transitions to EOD_COMPLETE once all open positions are confirmed closed."""
        self.state = EODState.EOD_COMPLETE
        self._save_state()
        print(f"[EOD STATE MACHINE] Session marked EOD_COMPLETE for {self.session_date}. All entries blocked.")

    def reset_session(self):
        """Manual reset helper for new test/session runs."""
        self.session_date = self._get_ist_today()
        self.state = EODState.READY
        self.closed_position_ids = set()
        self._save_state()

