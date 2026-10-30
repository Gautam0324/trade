"""
Position Manager for Day Trader Pro V2.
Authoritative tracking of live open positions, mark-to-market P&L, and position lookup.
"""

from typing import Dict, Any, List, Optional
import threading

class PositionManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._positions: Dict[str, Dict[str, Any]] = {}

    def open_position(self, pos: Dict[str, Any]) -> str:
        symbol = pos["symbol"].upper()
        pos_id = pos.get("id") or f"POS-{symbol}-{int(pos.get('entry_time_epoch', 0))}"
        pos["id"] = pos_id
        with self._lock:
            self._positions[symbol] = dict(pos)
        return pos_id

    def close_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._positions.pop(symbol.upper(), None)

    def get_position(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            p = self._positions.get(symbol.upper())
            return dict(p) if p else None

    def get_all_positions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(p) for p in self._positions.values()]

    def count(self) -> int:
        with self._lock:
            return len(self._positions)

    def calculate_unrealized_pnl(self, current_prices: Dict[str, float]) -> Dict[str, float]:
        with self._lock:
            pnl_by_symbol = {}
            for sym, pos in self._positions.items():
                cur_price = current_prices.get(sym)
                if not cur_price:
                    pnl_by_symbol[sym] = 0.0
                    continue
                qty = pos.get("quantity") or pos.get("qty", 0)
                entry = pos.get("entry_price", 0.0)
                side = pos.get("side", "BUY").upper()
                if side == "BUY":
                    pnl_by_symbol[sym] = round((cur_price - entry) * qty, 2)
                else:
                    pnl_by_symbol[sym] = round((entry - cur_price) * qty, 2)
            return pnl_by_symbol

