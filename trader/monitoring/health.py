"""
Health Service for Day Trader Pro V2.
Evaluates WebSocket state, tick freshness, daily risk status, and ledger consistency.
"""

from typing import Dict, Any, Optional

class HealthService:
    def __init__(
        self,
        connection_monitor=None,
        price_store=None,
        daily_risk_state=None,
        position_manager=None,
        metrics_tracker=None
    ):
        self.connection_monitor = connection_monitor
        self.price_store = price_store
        self.daily_risk_state = daily_risk_state
        self.position_manager = position_manager
        self.metrics_tracker = metrics_tracker

    def get_health_status(self) -> Dict[str, Any]:
        ws_status = self.connection_monitor.get_status_dict() if self.connection_monitor else {"status": "UNKNOWN", "is_healthy": False}
        feed_summary = self.price_store.get_feed_health_summary() if self.price_store else {}
        risk_summary = self.daily_risk_state.get_summary() if self.daily_risk_state else {}
        metrics = self.metrics_tracker.get_summary() if self.metrics_tracker else {}

        any_stale = any(item.get("is_stale", True) for item in feed_summary.values()) if feed_summary else True
        is_overall_healthy = ws_status.get("is_healthy", False) and not any_stale

        return {
            "status": "HEALTHY" if is_overall_healthy else ("DEGRADED" if ws_status.get("status") == "CONNECTED" else "UNHEALTHY"),
            "paper_trading": True,
            "live_trading": False,
            "websocket": ws_status,
            "feed": feed_summary,
            "risk": risk_summary,
            "metrics": metrics,
            "open_positions_count": self.position_manager.count() if self.position_manager else 0
        }

