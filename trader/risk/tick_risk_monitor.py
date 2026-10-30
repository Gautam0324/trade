"""
Tick-Level Risk Monitor for Day Trader Pro V2.
Monitors stop-loss and targets on every valid tick in sub-milliseconds without running the strategy engine.
"""

from typing import Dict, Any, Optional, Callable, List
import threading

class TickRiskMonitor:
    def __init__(self, on_exit_triggered: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.on_exit_triggered = on_exit_triggered
        self._lock = threading.RLock()
        # Open positions being monitored: {symbol: position_dict}
        self._monitored_positions: Dict[str, Dict[str, Any]] = {}
        # Track positions that have already triggered exit to avoid duplicate exit callbacks
        self._exited_position_ids = set()

    def register_position(self, pos: Dict[str, Any]):
        symbol = pos.get("symbol", "").upper()
        if not symbol:
            return
        with self._lock:
            self._monitored_positions[symbol] = dict(pos)

    def unregister_position(self, symbol: str):
        with self._lock:
            self._monitored_positions.pop(symbol.upper(), None)

    def evaluate_tick(self, tick: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        symbol = tick.get("symbol", "").upper()
        ltp = float(tick.get("ltp", 0.0))
        if ltp <= 0:
            return None

        with self._lock:
            pos = self._monitored_positions.get(symbol)
            if not pos:
                return None

            pos_id = pos.get("id") or pos.get("position_id")
            if pos_id in self._exited_position_ids:
                return None

            side = pos.get("side", "BUY").upper()
            sl = float(pos.get("sl_price") or pos.get("stop_loss", 0.0))
            tgt = float(pos.get("target_price") or pos.get("target", 0.0))

            stop_hit = False
            target_hit = False

            if side == "BUY":
                if sl > 0 and ltp <= sl:
                    stop_hit = True
                if tgt > 0 and ltp >= tgt:
                    target_hit = True
            elif side == "SELL":
                if sl > 0 and ltp >= sl:
                    stop_hit = True
                if tgt > 0 and ltp <= tgt:
                    target_hit = True

            # If both triggered (gap opening through both stop and target), prioritize conservative stop
            exit_event = None
            if stop_hit:
                exit_event = {
                    "position_id": pos_id,
                    "symbol": symbol,
                    "side": side,
                    "exit_price": ltp,
                    "exit_reason": "TICK_STOP_LOSS_HIT",
                    "trigger_type": "STOP_LOSS",
                    "sl_level": sl,
                    "timestamp": tick.get("timestamp")
                }
            elif target_hit:
                exit_event = {
                    "position_id": pos_id,
                    "symbol": symbol,
                    "side": side,
                    "exit_price": ltp,
                    "exit_reason": "TICK_TARGET_HIT",
                    "trigger_type": "TARGET",
                    "target_level": tgt,
                    "timestamp": tick.get("timestamp")
                }

            if exit_event:
                if pos_id:
                    self._exited_position_ids.add(pos_id)
                self._monitored_positions.pop(symbol, None)

                if self.on_exit_triggered:
                    try:
                        self.on_exit_triggered(exit_event)
                    except Exception as e:
                        print(f"[TICK EXIT CALLBACK ERROR] {symbol}: {e}")

                return exit_event

        return None

