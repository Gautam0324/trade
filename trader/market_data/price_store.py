"""
Price Store for Day Trader Pro V2.
High-concurrency, thread-safe in-memory cache for validated ticks and latest prices.
"""

from typing import Dict, Any, Optional
import time
import threading
import datetime
import zoneinfo

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class PriceStore:
    def __init__(self, stale_threshold_secs: float = 15.0):
        self.stale_threshold_secs = stale_threshold_secs
        self._lock = threading.RLock()
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}
        self._latest_prices: Dict[str, float] = {}
        self._last_arrival_time: Dict[str, float] = {}

    def update_tick(self, tick: Dict[str, Any]):
        symbol = tick.get("symbol")
        if not symbol:
            return
        
        ltp = float(tick.get("ltp", 0.0))
        now = time.time()

        with self._lock:
            self._latest_ticks[symbol] = dict(tick)
            self._latest_prices[symbol] = ltp
            self._last_arrival_time[symbol] = now

    def get_latest_price(self, symbol: str) -> Optional[float]:
        with self._lock:
            return self._latest_prices.get(symbol.upper())

    def get_latest_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            tick = self._latest_ticks.get(symbol.upper())
            return dict(tick) if tick else None

    def get_all_latest_prices(self) -> Dict[str, float]:
        with self._lock:
            return dict(self._latest_prices)

    def get_last_tick_timestamp(self, symbol: str) -> Optional[str]:
        with self._lock:
            tick = self._latest_ticks.get(symbol.upper())
            return tick.get("timestamp") if tick else None

    def is_feed_stale(self, symbol: str, max_age_seconds: Optional[float] = None) -> bool:
        threshold = max_age_seconds or self.stale_threshold_secs
        with self._lock:
            last_time = self._last_arrival_time.get(symbol.upper())
            if not last_time:
                return True
            return (time.time() - last_time) > threshold

    def get_feed_health_summary(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            summary = {}
            for sym, last_time in self._last_arrival_time.items():
                age = round(now - last_time, 2)
                summary[sym] = {
                    "price": self._latest_prices.get(sym),
                    "age_seconds": age,
                    "is_stale": age > self.stale_threshold_secs,
                    "last_timestamp": self._latest_ticks.get(sym, {}).get("timestamp")
                }
            return summary

