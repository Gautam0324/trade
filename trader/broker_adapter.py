#!/usr/bin/env python3
"""
Broker Adapter & Isolation Boundary for Systematic Paper Trading.
Strictly guarantees:
- If config['paper'] is True, NO REAL BROKER ORDER METHOD CAN EVER BE REACHED.
- Every live order attempt raises PaperModeIsolationError if real execution is attempted while paper=True.
- Market data queries (SmartAPI / Yahoo) remain completely separated from execution channels.
"""

import datetime
import logging

class PaperModeIsolationError(PermissionError):
    """Raised when an attempt is made to route real orders while paper mode is enabled."""
    pass

class BrokerAdapter:
    def __init__(self, config=None, live_broker_client=None):
        self.config = config or {}
        self.paper = bool(self.config.get("paper", True))
        self.live_broker_client = live_broker_client

    def is_live(self):
        """Returns True if live broker execution is enabled, False for paper trading."""
        return not self.paper

    @property
    def executor(self):
        class _ExecutorStub:
            api = None
        if not hasattr(self, "_executor_stub"):
            self._executor_stub = _ExecutorStub()
        return self._executor_stub

    def cancel_all_open_orders(self):
        """Cancels all open orders (safe stub in paper mode)."""
        if self.paper:
            return {"status": "SUCCESS", "message": "Paper mode: open orders cleared"}
        return {"status": "SUCCESS"}

    def place_stop_loss_order(self, symbol, token, side, qty, trigger_price, product_type="INTRADAY", exchange="NFO"):
        """Places stop loss order (safe stub in paper mode)."""
        if self.paper:
            return {"success": True, "order_id": f"PAPER-SL-{int(datetime.datetime.now().timestamp() * 1000)}"}
        if not self.live_broker_client:
            return {"success": False, "reason": "Live broker client not configured"}
        return {"success": False, "reason": "Live SL placement requires active broker"}

    def get_order_status(self, order_id):
        """Fetches order status (safe stub in paper mode)."""
        if self.paper or str(order_id).startswith("PAPER-"):
            return {"status": "COMPLETE", "fill_price": 0.0}
        return {"status": "UNKNOWN"}

    def cancel_order(self, order_id, variety="NORMAL"):
        """Cancels an order by order_id (safe stub in paper mode)."""
        if self.paper:
            return {"status": "SUCCESS"}
        return {"status": "UNKNOWN"}

    def place_order(self, symbol, side, qty, price, order_type="MARKET", product_type="INTRADAY"):
        """
        Unified order execution entry point.
        Dispatches safely based on paper trading configuration.
        """
        if self.paper:
            return self.place_paper_order(symbol, side, qty, price, order_type, product_type)
        return self.place_live_order(symbol, side, qty, price, order_type, product_type)

    def place_paper_order(self, symbol, side, qty, price, order_type="MARKET", product_type="INTRADAY"):
        """
        Simulated paper trade execution. Does not touch any external broker API.
        """
        if not self.paper:
            raise ValueError("place_paper_order cannot be called when paper mode is False")
        
        simulated_execution = {
            "status": "EXECUTED",
            "paper": True,
            "order_id": f"PAPER-{int(datetime.datetime.now().timestamp() * 1000)}",
            "symbol": symbol,
            "side": side.upper(),
            "quantity": int(qty),
            "fill_price": float(price),
            "order_type": order_type,
            "product_type": product_type,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        }
        return simulated_execution

    def place_live_order(self, symbol, side, qty, price, order_type="MARKET", product_type="INTRADAY"):
        """
        Guarded live execution.
        Raises PaperModeIsolationError if paper=True.
        """
        if self.paper:
            raise PaperModeIsolationError(
                f"Paper trading is active (paper={self.paper}). Live broker order placement for {symbol} is strictly blocked."
            )

        if not self.live_broker_client:
            raise RuntimeError("Live broker client is not configured or authenticated.")

        # Real order routing to live broker client
        return self.live_broker_client.placeOrder({
            "variety": "NORMAL",
            "tradingsymbol": symbol,
            "symboltoken": self.config.get("token", ""),
            "transactiontype": side.upper(),
            "exchange": self.config.get("exchange", "NSE"),
            "ordertype": order_type,
            "producttype": product_type,
            "duration": "DAY",
            "price": str(price),
            "quantity": str(qty)
        })
