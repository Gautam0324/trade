"""
Connection Monitor for Day Trader Pro V2.
Tracks connection status, reconnection cycles, tick throughput, error counts, and feed health.
"""

from typing import Dict, Any, Optional
import time
from enum import Enum

class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    STALE = "STALE"
    ERROR = "ERROR"

class ConnectionMonitor:
    def __init__(self):
        self.state = ConnectionState.DISCONNECTED
        self.last_state_change = time.time()
        self.connected_at: Optional[float] = None
        self.disconnected_at: Optional[float] = None
        self.reconnect_count: int = 0
        self.total_ticks_received: int = 0
        self.valid_ticks_processed: int = 0
        self.invalid_ticks_rejected: int = 0
        self.duplicate_ticks_dropped: int = 0
        self.last_tick_time: Optional[float] = None
        self.last_error: Optional[str] = None

    def set_state(self, new_state: ConnectionState, reason: str = ""):
        if self.state != new_state:
            self.state = new_state
            self.last_state_change = time.time()
            if new_state == ConnectionState.CONNECTED:
                self.connected_at = time.time()
                self.last_error = None
            elif new_state in (ConnectionState.DISCONNECTED, ConnectionState.ERROR):
                self.disconnected_at = time.time()
                if reason:
                    self.last_error = reason
            elif new_state == ConnectionState.RECONNECTING:
                self.reconnect_count += 1

    def record_tick(self, is_valid: bool, is_duplicate: bool = False, error_reason: str = ""):
        self.total_ticks_received += 1
        self.last_tick_time = time.time()
        if is_duplicate:
            self.duplicate_ticks_dropped += 1
        elif is_valid:
            self.valid_ticks_processed += 1
        else:
            self.invalid_ticks_rejected += 1
            if error_reason:
                self.last_error = error_reason

    def is_healthy(self, max_feed_silence_secs: float = 30.0) -> bool:
        if self.state != ConnectionState.CONNECTED:
            return False
        if not self.last_tick_time:
            return False
        return (time.time() - self.last_tick_time) <= max_feed_silence_secs

    def get_status_dict(self) -> Dict[str, Any]:
        now = time.time()
        silence_sec = round(now - self.last_tick_time, 2) if self.last_tick_time else None
        uptime_sec = round(now - self.connected_at, 2) if (self.connected_at and self.state == ConnectionState.CONNECTED) else 0.0

        return {
            "status": self.state.value,
            "is_healthy": self.is_healthy(),
            "uptime_seconds": uptime_sec,
            "reconnect_count": self.reconnect_count,
            "total_ticks_received": self.total_ticks_received,
            "valid_ticks_processed": self.valid_ticks_processed,
            "invalid_ticks_rejected": self.invalid_ticks_rejected,
            "duplicate_ticks_dropped": self.duplicate_ticks_dropped,
            "feed_silence_seconds": silence_sec,
            "last_error": self.last_error
        }

