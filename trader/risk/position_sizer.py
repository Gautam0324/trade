"""
Position Sizer for Day Trader Pro V2.
Calculates risk-adjusted position sizes based on capital, stop-loss distance, and allocation constraints.
"""

from typing import Dict, Any, Tuple
import math

class PositionSizer:
    def __init__(
        self,
        default_risk_pct: float = 1.0,
        max_position_pct: float = 0.35,
        min_order_val: float = 5000.0,
        max_order_val: float = 400000.0
    ):
        self.default_risk_pct = default_risk_pct
        self.max_position_pct = max_position_pct
        self.min_order_val = min_order_val
        self.max_order_val = max_order_val

    def calculate_quantity(
        self,
        capital: float,
        entry_price: float,
        stop_loss: float,
        risk_pct: float = 1.0
    ) -> Tuple[int, Dict[str, Any]]:
        if capital <= 0 or entry_price <= 0:
            return 0, {"reason": "Non-positive capital or entry price"}

        risk_amount = capital * (risk_pct / 100.0)
        per_share_risk = abs(entry_price - stop_loss)

        if per_share_risk <= 0:
            return 0, {"reason": "Zero or negative stop loss distance"}

        # Quantity from risk
        raw_qty = risk_amount / per_share_risk

        # Quantity from max position size (35% of capital)
        max_capital_allowed = min(capital * self.max_position_pct, self.max_order_val)
        cap_qty = max_capital_allowed / entry_price

        final_qty = int(math.floor(min(raw_qty, cap_qty)))

        if final_qty <= 0:
            return 0, {"reason": "Calculated quantity is zero"}

        order_val = final_qty * entry_price
        if order_val < self.min_order_val and capital >= self.min_order_val:
            # Allow at least minimum order if capital permits
            final_qty = max(1, int(math.floor(self.min_order_val / entry_price)))

        return final_qty, {
            "quantity": final_qty,
            "order_value": round(final_qty * entry_price, 2),
            "risk_amount": round(risk_amount, 2),
            "max_loss": round(final_qty * per_share_risk, 2)
        }

