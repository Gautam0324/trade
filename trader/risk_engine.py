#!/usr/bin/env python3
"""
Centralized Quantitative Risk Management Engine for Indian Equities.
Paper trading defaults:
- Capital: ₹100,000
- Max risk per trade: 1% (₹1,000)
- Max position allocation: 75% of available cash
- Max open positions: 3
- Max daily trades: 3
- Max daily loss: ₹1,000 (kill switch)
- Short selling: disabled
- Options/leverage: disabled
- Execution: paper only
"""

from decimal import Decimal, ROUND_FLOOR
import math

class RiskEngine:
    def __init__(self, config=None):
        cfg = config or {}
        self.starting_capital = float(cfg.get("budget", 100000.0))
        # Support *_inr keys with fallback to *_usd
        self.max_loss_per_trade = float(cfg.get("max_loss_per_trade_inr", cfg.get("max_loss_per_trade_usd", 1000.0)))
        self.max_daily_loss = float(cfg.get("max_daily_loss_inr", cfg.get("max_daily_loss_usd", 1000.0)))
        self.max_position_pct = float(cfg.get("max_position_pct", 75.0)) / 100.0
        self.max_trades_per_day = int(cfg.get("max_trades_per_day", 3))
        self.max_open_positions = int(cfg.get("max_open_positions", 3))
        self.min_reward_risk = float(cfg.get("min_reward_risk", 1.0))
        self.allow_short = bool(cfg.get("allow_short", False))
        self.allow_options = bool(cfg.get("allow_options", False))
        self.paper = bool(cfg.get("paper", True))

        # Slippage & Estimated Brokerage (0.05% round-trip brokerage + taxes)
        self.estimated_cost_pct = float(cfg.get("estimated_cost_pct", 0.05)) / 100.0

    def evaluate_order_risk(self, symbol, side, entry_price, stop_loss, target_price,
                            available_cash, daily_realized_loss, current_trades_count,
                            current_open_positions_count):
        """
        Calculates position sizing and validates safety constraints.
        """
        reasons = []

        # 0. Futures / Options / Derivatives validation
        sym_upper = symbol.upper()
        is_derivative = any(term in sym_upper for term in ("FUT", "OPT", "FUTURES", "INDEX FUTURES", "CE", "PE", "NIFTY 50 (INDEX FUTURES)"))
        if is_derivative and not self.allow_options:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Derivatives trading is disabled for {symbol} (allow_options is false)"]
            }

        if side.upper() != "BUY" and not self.allow_short:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": ["Short selling disabled in configuration"]
            }

        # 1. Kill-switch check: daily loss limit reached
        if daily_realized_loss <= -self.max_daily_loss:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Daily loss limit reached (-₹{abs(daily_realized_loss):,.2f} >= -₹{self.max_daily_loss:,.2f})"]
            }

        # 2. Daily trades count check
        if current_trades_count >= self.max_trades_per_day:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Maximum daily trades count reached ({current_trades_count}/{self.max_trades_per_day})"]
            }

        # 3. Maximum open positions check
        if current_open_positions_count >= self.max_open_positions:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Maximum open positions reached ({current_open_positions_count}/{self.max_open_positions})"]
            }

        # 4. Validate stop-loss and target placement
        entry_p = float(entry_price)
        stop_p = float(stop_loss)
        target_p = float(target_price)

        if side.upper() == "BUY":
            if stop_p >= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid stop-loss ({stop_p}) must be strictly below entry price ({entry_p})"]
                }
            if target_p <= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid target price ({target_p}) must be strictly above entry price ({entry_p})"]
                }
            risk_per_share = entry_p - stop_p
            reward_per_share = target_p - entry_p
        else:
            if stop_p <= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid short stop-loss ({stop_p}) must be strictly above entry price ({entry_p})"]
                }
            risk_per_share = stop_p - entry_p
            reward_per_share = entry_p - target_p

        if risk_per_share <= 0:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": ["Risk per share is non-positive"]
            }

        rr_ratio = reward_per_share / risk_per_share
        if round(rr_ratio, 2) < round(self.min_reward_risk, 2):
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Reward-to-risk ratio ({rr_ratio:.2f}) is below minimum required ({self.min_reward_risk:.2f})"]
            }

        # 5. Position sizing formula:
        # remaining daily budget = max_daily_loss - current loss
        remaining_loss_budget = max(0.0, self.max_daily_loss - abs(min(0.0, daily_realized_loss)))
        risk_budget = min(self.max_loss_per_trade, remaining_loss_budget)

        risk_quantity = math.floor(risk_budget / risk_per_share)
        max_position_cash = available_cash * self.max_position_pct
        cash_quantity = math.floor(max_position_cash / entry_p)

        quantity = min(risk_quantity, cash_quantity)

        if quantity <= 0:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Calculated risk quantity is 0 (Risk budget ₹{risk_budget:,.2f}, Risk/shr ₹{risk_per_share:.2f}, Cash limit {cash_quantity})"]
            }

        allocated_capital = round(quantity * entry_p, 2)
        total_risk = round(quantity * risk_per_share, 2)

        return {
            "passed": True,
            "quantity": quantity,
            "allocated_capital": allocated_capital,
            "planned_risk": total_risk,
            "risk_per_share": round(risk_per_share, 2),
            "reward_risk": round(rr_ratio, 2),
            "reasons": ["Risk and position sizing checks passed"]
        }
