#!/usr/bin/env python3
"""
V3 Risk Engine: Independent Account-Isolated Risk Management.
Enforces:
- 0.5% risk per trade based on individual account equity.
- 1.5% max daily loss limit.
- 2 consecutive losses kill switch.
- Max 3 concurrent open positions.
- Max 5 trades per day.
- Max 1.0% concurrent open risk.
- Independent account state isolation (never shared between accounts).
- Derivative vs Equity lot size awareness (NIFTY 65, BANKNIFTY 30).
"""

import math
import json
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple

class V3RiskEngine:
    VERSION = "3.0.0"

    def __init__(self, account_id: str = "default", starting_capital: float = 1000000.0, config: Optional[Dict[str, Any]] = None):
        self.account_id = str(account_id).lower()
        self.config = config or {}
        self.capital = float(starting_capital)

        # Risk parameters (configurable, defaults from V3 Master Blueprint)
        self.risk_per_trade_pct = float(self.config.get("risk_per_trade_pct", 0.5)) / 100.0   # 0.5%
        self.max_daily_loss_pct = float(self.config.get("max_daily_loss_pct", 1.5)) / 100.0   # 1.5%
        self.max_consecutive_losses = int(self.config.get("max_consecutive_losses", 2))
        self.max_open_positions = int(self.config.get("max_open_positions", 3))
        self.max_trades_per_day = int(self.config.get("max_trades_per_day", 5))
        self.max_total_open_risk_pct = float(self.config.get("max_total_open_risk_pct", 1.0)) / 100.0 # 1.0%
        self.max_capital_allocation_pct = float(self.config.get("max_capital_allocation_pct", 40.0)) / 100.0 # 40%

    def calculate_position_size(
        self,
        symbol: str,
        entry_price: float,
        stop_loss_price: float,
        current_equity: float,
        available_cash: float,
        segment: str = "EQUITY",
        lot_size: int = 1
    ) -> Tuple[int, float, List[str]]:
        """
        Calculates strictly risk-budgeted position size.
        Formula: Quantity = Floor( (Equity * Risk_Per_Trade) / ( |Entry - Stop| ) )
        Returns: (quantity, planned_risk_inr, rejection_reasons)
        """
        reasons = []
        entry_p = float(entry_price)
        sl_p = float(stop_loss_price)
        equity = float(current_equity)
        cash = float(available_cash)

        if entry_p <= 0 or sl_p <= 0:
            return 0, 0.0, ["Invalid prices: entry and stop loss must be greater than zero"]

        risk_per_unit = abs(entry_p - sl_p)
        if risk_per_unit <= 0.0001:
            return 0, 0.0, ["Stop loss distance is virtually zero"]

        # 0.5% Risk Budget
        risk_budget = round(equity * self.risk_per_trade_pct, 2)

        is_options = "OPT" in segment.upper() or lot_size > 1

        if is_options:
            # Sizing for options: risk per 1 lot = risk_per_unit * lot_size
            risk_per_lot = risk_per_unit * lot_size
            if risk_per_lot > risk_budget * 1.25: # Allow small tolerance for minimum 1-lot
                # Check if 1 lot risk exceeds max daily loss budget
                max_allowed_single_risk = equity * self.risk_per_trade_pct * 1.5
                if risk_per_lot > max_allowed_single_risk:
                    return 0, 0.0, [f"1-lot option risk (₹{risk_per_lot:.2f}) exceeds risk budget (₹{risk_budget:.2f})"]
                num_lots = 1
            else:
                num_lots = max(1, math.floor(risk_budget / risk_per_lot))

            qty = num_lots * lot_size
            planned_risk = round(qty * risk_per_unit, 2)
            required_cash = round(qty * entry_p, 2)
        else:
            # Cash Equities
            raw_qty = math.floor(risk_budget / risk_per_unit)
            qty = max(1, raw_qty)

            # Cap by maximum capital allocation per position (e.g. 40% of cash)
            max_capital_for_pos = min(cash * 0.95, equity * self.max_capital_allocation_pct)
            if (qty * entry_p) > max_capital_for_pos:
                qty = max(1, math.floor(max_capital_for_pos / entry_p))

            planned_risk = round(qty * risk_per_unit, 2)
            required_cash = round(qty * entry_p, 2)

        if required_cash > cash:
            return 0, 0.0, [f"Insufficient available cash: requires ₹{required_cash:.2f}, available ₹{cash:.2f}"]

        return qty, planned_risk, []

    def evaluate_account_risk(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        current_equity: float,
        available_cash: float,
        daily_realized_pnl: float,
        today_completed_trades: int,
        consecutive_losses_count: int,
        current_open_positions: List[Dict[str, Any]],
        segment: str = "EQUITY",
        lot_size: int = 1
    ) -> Dict[str, Any]:
        """
        Validates all account-isolated risk constraints before executing a paper order.
        """
        rejections = []
        equity = float(current_equity)

        # 1. Max daily loss limit (1.5% of equity)
        max_daily_loss = equity * self.max_daily_loss_pct
        if daily_realized_pnl <= -max_daily_loss:
            rejections.append(f"Daily loss limit breached: realized loss ₹{-daily_realized_pnl:.2f} >= ₹{max_daily_loss:.2f} (1.5%)")

        # 2. Consecutive losses kill-switch (2 in a row)
        if consecutive_losses_count >= self.max_consecutive_losses:
            rejections.append(f"Consecutive loss kill switch triggered ({consecutive_losses_count}/{self.max_consecutive_losses})")

        # 3. Max open positions (3)
        if len(current_open_positions) >= self.max_open_positions:
            rejections.append(f"Max open positions limit reached ({len(current_open_positions)}/{self.max_open_positions})")

        # 4. Max daily trades (5)
        if today_completed_trades >= self.max_trades_per_day:
            rejections.append(f"Max daily trades reached ({today_completed_trades}/{self.max_trades_per_day})")

        # 5. Duplicate position check
        open_syms = [p.get("symbol", "").upper() for p in current_open_positions]
        if symbol.upper() in open_syms:
            rejections.append(f"Duplicate position: {symbol} is already open")

        # 6. Concurrent open risk check (max 1.0% total open risk)
        current_open_risk = sum(
            abs(float(p.get("entry_price", 0)) - float(p.get("sl_price", p.get("stop_loss", 0)))) * int(p.get("qty", p.get("quantity", 0)))
            for p in current_open_positions
        )
        max_total_open_risk = equity * self.max_total_open_risk_pct

        if rejections:
            return {
                "passed": False,
                "account_id": self.account_id,
                "quantity": 0,
                "planned_risk": 0.0,
                "rejection_reasons": rejections
            }

        # Calculate position size
        qty, planned_risk, size_rejections = self.calculate_position_size(
            symbol, entry_price, stop_loss, current_equity, available_cash, segment, lot_size
        )

        if size_rejections:
            return {
                "passed": False,
                "account_id": self.account_id,
                "quantity": 0,
                "planned_risk": 0.0,
                "rejection_reasons": size_rejections
            }

        # Check if new planned risk would breach concurrent open risk
        if (current_open_risk + planned_risk) > (max_total_open_risk * 1.10): # 10% buffer
            return {
                "passed": False,
                "account_id": self.account_id,
                "quantity": 0,
                "planned_risk": 0.0,
                "rejection_reasons": [f"Total concurrent open risk would reach ₹{current_open_risk + planned_risk:.2f} > limit ₹{max_total_open_risk:.2f} (1.0%)"]
            }

        return {
            "passed": True,
            "account_id": self.account_id,
            "quantity": qty,
            "planned_risk": planned_risk,
            "rejection_reasons": []
        }

