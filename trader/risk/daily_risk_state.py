"""
Persistent Daily Risk State for Day Trader Pro V2.
Preserves daily trade counts, loss accumulators, and kill-switch status across process restarts.
"""

import os
import json
import datetime
import zoneinfo
from pathlib import Path
from typing import Dict, Any, Optional

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class DailyRiskState:
    def __init__(self, state_file_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.file_path = Path(state_file_path) if state_file_path else base_dir / "trader" / "daily_risk_state.json"
        self._state: Dict[str, Any] = {}
        self.load()

    def _today_str(self) -> str:
        return datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d")

    def load(self):
        today = self._today_str()
        if self.file_path.exists():
            try:
                with open(self.file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        self._state = data
                        return
            except Exception:
                pass

        # Initialize fresh state for new day
        self._state = {
            "date": today,
            "trade_count": 0,
            "order_count": 0,
            "realized_loss": 0.0,
            "realized_pnl": 0.0,
            "kill_switch_triggered": False,
            "kill_switch_reason": None
        }
        self.save()

    def save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self._state, f, indent=2)
        except Exception as e:
            print(f"[RISK STATE SAVE ERROR] {e}")

    def record_order(self):
        self.load()
        self._state["order_count"] += 1
        self.save()

    def record_trade_completion(self, pnl: float):
        self.load()
        self._state["trade_count"] += 1
        self._state["realized_pnl"] += pnl
        if pnl < 0:
            self._state["realized_loss"] += abs(pnl)
        self.save()

    def trigger_kill_switch(self, reason: str):
        self.load()
        self._state["kill_switch_triggered"] = True
        self._state["kill_switch_reason"] = reason
        self.save()

    def get_summary(self) -> Dict[str, Any]:
        self.load()
        return dict(self._state)
