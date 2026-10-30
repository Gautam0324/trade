"""
Trade Ledger for Day Trader Pro V2.
Authoritative single-source-of-truth JSON ledger persistence and trade history logger.
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import json
import threading

class TradeLedger:
    def __init__(self, ledger_path: Optional[str] = None):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.ledger_path = Path(ledger_path) if ledger_path else base_dir / "trader" / "ledger.json"
        self._lock = threading.RLock()
        self._ledger_data: Dict[str, Any] = {}
        self.load()

    def load(self):
        with self._lock:
            if self.ledger_path.exists() and self.ledger_path.stat().st_size > 0:
                try:
                    with open(self.ledger_path, "r", encoding="utf-8") as f:
                        self._ledger_data = json.load(f)
                        return
                except Exception as e:
                    print(f"[LEDGER LOAD ERROR] {e}")

            self._ledger_data = {
                "initial_capital": 1000000.0,
                "free_cash": 1000000.0,
                "open_positions": [],
                "trade_history": []
            }
            self.save()

    def save(self):
        with self._lock:
            try:
                with open(self.ledger_path, "w", encoding="utf-8") as f:
                    json.dump(self._ledger_data, f, indent=2)
            except Exception as e:
                print(f"[LEDGER SAVE ERROR] {e}")

    def record_closed_trade(self, trade_record: Dict[str, Any]):
        with self._lock:
            self.load()
            hist = self._ledger_data.setdefault("trade_history", [])
            # Deduplicate by id if present
            tid = trade_record.get("id")
            if not any(t.get("id") == tid for t in hist if tid):
                hist.append(trade_record)
                pnl = float(trade_record.get("pnl", 0.0))
                self._ledger_data["free_cash"] = round(float(self._ledger_data.get("free_cash", 1000000.0)) + pnl, 2)
                self.save()

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            self.load()
            return dict(self._ledger_data)
