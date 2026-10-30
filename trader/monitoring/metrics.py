"""
Metrics Tracker for Day Trader Pro V2.
Records tick processing latencies, candle completions, and execution throughput.
"""

from typing import Dict, Any, List
import time
import threading

class MetricsTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self.tick_count: int = 0
        self.candle_completed_count: int = 0
        self.signals_generated_count: int = 0
        self.orders_executed_count: int = 0
        self.errors_count: int = 0
        self._latencies_ms: List[float] = []

    def record_tick_processed(self, latency_ms: float):
        with self._lock:
            self.tick_count += 1
            self._latencies_ms.append(latency_ms)
            if len(self._latencies_ms) > 1000:
                self._latencies_ms.pop(0)

    def record_candle_completed(self):
        with self._lock:
            self.candle_completed_count += 1

    def record_signal_generated(self):
        with self._lock:
            self.signals_generated_count += 1

    def record_order_executed(self):
        with self._lock:
            self.orders_executed_count += 1

    def record_error(self):
        with self._lock:
            self.errors_count += 1

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            avg_lat = (sum(self._latencies_ms) / len(self._latencies_ms)) if self._latencies_ms else 0.0
            p95_lat = sorted(self._latencies_ms)[int(len(self._latencies_ms) * 0.95)] if len(self._latencies_ms) >= 20 else avg_lat

            return {
                "total_ticks": self.tick_count,
                "candles_completed": self.candle_completed_count,
                "signals_generated": self.signals_generated_count,
                "orders_executed": self.orders_executed_count,
                "errors_count": self.errors_count,
                "avg_processing_latency_ms": round(avg_lat, 2),
                "p95_latency_ms": round(p95_lat, 2)
            }

