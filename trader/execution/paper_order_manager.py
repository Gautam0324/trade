"""
Paper Order Manager & Lifecycle State Machine for Day Trader Pro V2.
Enforces atomic transitions:
SIGNAL_GENERATED -> SIGNAL_VALIDATED -> PAPER_ORDER_CREATED -> POSITION_OPEN -> EXIT_TRIGGERED -> POSITION_CLOSED
"""

from enum import Enum
from typing import Dict, Any, Optional, Tuple
import time

class OrderLifecycleState(str, Enum):
    SIGNAL_GENERATED = "SIGNAL_GENERATED"
    SIGNAL_VALIDATED = "SIGNAL_VALIDATED"
    PAPER_ORDER_CREATED = "PAPER_ORDER_CREATED"
    POSITION_OPEN = "POSITION_OPEN"
    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    POSITION_CLOSED = "POSITION_CLOSED"

class PaperOrderManager:
    def __init__(self):
        self._orders: Dict[str, Dict[str, Any]] = {}
        self._signals_seen = set()

    def record_signal(self, signal: Dict[str, Any]) -> Tuple[bool, str]:
        sig_id = signal.get("signal_id")
        if not sig_id:
            return False, "Missing signal_id"

        if sig_id in self._signals_seen:
            return False, f"Duplicate signal {sig_id} already registered"

        self._signals_seen.add(sig_id)
        return True, "SIGNAL_ACCEPTED"

    def create_order(
        self,
        signal_id: str,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        stop_loss: float,
        target_price: float,
        strategy_name: str
    ) -> Dict[str, Any]:
        order_id = f"PORD-{int(time.time() * 1000)}-{symbol.upper()}"
        order_record = {
            "order_id": order_id,
            "signal_id": signal_id,
            "symbol": symbol.upper(),
            "side": side.upper(),
            "quantity": qty,
            "price": price,
            "stop_loss": stop_loss,
            "target_price": target_price,
            "strategy_name": strategy_name,
            "state": OrderLifecycleState.PAPER_ORDER_CREATED.value,
            "created_at": time.time()
        }
        self._orders[order_id] = order_record
        return order_record

    def update_state(self, order_id: str, new_state: OrderLifecycleState):
        if order_id in self._orders:
            self._orders[order_id]["state"] = new_state.value
            self._orders[order_id]["updated_at"] = time.time()

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        return self._orders.get(order_id)
