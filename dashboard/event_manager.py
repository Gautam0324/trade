"""
Dashboard Event Manager for Day Trader Pro V2.
Thread-safe event dispatcher for incremental real-time WebSocket dashboard feeds.
"""

from typing import Dict, Any, List, Callable
import threading
import json

class DashboardEventManager:
    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: List[Callable[[str], None]] = []
        self._latest_events: Dict[str, Dict[str, Any]] = {}

    def subscribe(self, callback: Callable[[str], None]):
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[str], None]):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def broadcast(self, event_type: str, data: Dict[str, Any]):
        payload = {
            "type": event_type,
            **data
        }
        with self._lock:
            self._latest_events[event_type] = payload
            subs = list(self._subscribers)

        msg = json.dumps(payload)
        for sub in subs:
            try:
                sub(msg)
            except Exception:
                pass

    def emit_tick(self, symbol: str, ltp: float, timestamp: str):
        self.broadcast("market_tick", {"symbol": symbol, "ltp": ltp, "timestamp": timestamp})

    def emit_candle(self, symbol: str, timeframe: str, candle: Dict[str, Any]):
        self.broadcast("candle_update", {"symbol": symbol, "timeframe": timeframe, "candle": candle})

    def emit_signal(self, symbol: str, strategy: str, signal: str, reason: str):
        self.broadcast("strategy_signal", {"symbol": symbol, "strategy": strategy, "signal": signal, "reason": reason})

    def emit_trade(self, trade_id: str, symbol: str, status: str, pnl: float):
        self.broadcast("paper_trade_update", {"trade_id": trade_id, "symbol": symbol, "status": status, "pnl": pnl})

