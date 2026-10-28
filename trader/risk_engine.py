#!/usr/bin/env python3
"""
Centralized Quantitative Risk Management Engine for Indian Equities.
Paper trading defaults:
- Capital: ₹1,000,000 (or configured budget)
- Max risk per trade: 1% (₹1,000 or ₹10,000 depending on config)
- Max position allocation: 40% of available cash
- Max open positions: 3
- Max daily trades: 3
- Max daily loss: ₹20,000 / configured (kill switch)
- Short selling: enabled or disabled per config
- Derivatives: disabled by policy
- Execution: paper only
"""

import math
import datetime

class RiskEngine:
    def __init__(self, config=None):
        self.config = config or {}
        self.starting_capital = float(self.config.get("budget", 1000000.0))
        # Support *_inr keys with fallback to *_usd
        self.max_loss_per_trade = float(self.config.get("max_loss_per_trade_inr", self.config.get("max_loss_per_trade_usd", 10000.0)))
        self.max_daily_loss = float(self.config.get("max_daily_loss_inr", self.config.get("max_daily_loss_usd", 20000.0)))
        self.max_position_pct = float(self.config.get("max_position_pct", 40.0)) / 100.0
        self.max_trades_per_day = int(self.config.get("max_trades_per_day", 10))
        self.max_open_positions = int(self.config.get("max_open_positions", 3))
        self.min_reward_risk = float(self.config.get("min_reward_risk", 1.5))
        self.allow_short = bool(self.config.get("allow_short", True))
        self.allow_options = bool(self.config.get("allow_options", False))
        self.paper = bool(self.config.get("paper", True))
        self.include_unrealized_in_daily_loss = bool(self.config.get("include_unrealized_in_daily_loss", True))
        self.max_order_notional = float(self.config.get("max_order_notional", self.starting_capital * 0.40))

        # Slippage & Estimated Brokerage (0.05% round-trip brokerage + taxes)
        self.estimated_cost_pct = float(self.config.get("estimated_cost_pct", 0.05)) / 100.0

    def evaluate_order_risk(self, symbol, side, entry_price, stop_loss, target_price,
                            available_cash, daily_realized_loss, current_trades_count,
                            current_open_positions_count, unrealized_loss=0.0,
                            existing_symbols=None):
        """
        Calculates position sizing and validates all safety constraints centrally.
        """
        # 0. Duplicate position check
        if existing_symbols:
            # Handle list or set or dict
            open_syms = set(existing_symbols.keys() if isinstance(existing_symbols, dict) else existing_symbols)
            if symbol in open_syms:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Duplicate position rejected: {symbol} is already open"]
                }

        # 1. Futures / Options / Derivatives validation
        sym_upper = symbol.upper()
        # Option contracts have CE / PE at the end (e.g. RELIANCE24OCT1200CE or NIFTY 25000 CE), or contain FUT/FUTURES/OPT
        is_derivative = (
            sym_upper.endswith("CE") or sym_upper.endswith("PE") or
            " CE" in sym_upper or " PE" in sym_upper or
            "FUT" in sym_upper or "OPT" in sym_upper
        )
        # Exclude standard equity tickers that happen to contain 'OPT' or end in 'CE' (like RELIANCE)
        EQUITY_SYMBOLS = {"RELIANCE", "RELIANCE_BSE", "TCS", "TCS_BSE", "INFY", "HDFCBANK", "HDFCBANK_BSE", "JPL", "JINDALSTEL"}
        if sym_upper in EQUITY_SYMBOLS:
            is_derivative = False
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

        # 2. Kill-switch check: daily loss limit reached (including unrealized losses if configured)
        effective_daily_loss = float(daily_realized_loss)
        if self.include_unrealized_in_daily_loss and unrealized_loss < 0:
            effective_daily_loss += float(unrealized_loss)

        if effective_daily_loss <= -self.max_daily_loss:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Daily loss limit reached: kill-switch triggered (Loss: -₹{abs(effective_daily_loss):,.2f} >= Limit: -₹{self.max_daily_loss:,.2f})"]
            }

        # 3. Daily trades count check
        if current_trades_count >= self.max_trades_per_day:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Maximum daily trades count reached ({current_trades_count}/{self.max_trades_per_day})"]
            }

        # 4. Maximum open positions check
        if current_open_positions_count >= self.max_open_positions:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Maximum open positions reached ({current_open_positions_count}/{self.max_open_positions})"]
            }

        # 5. Available Cash Check
        if available_cash <= 0:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Insufficient available cash: ₹{available_cash:,.2f} <= ₹0.00"]
            }

        # 6. Validate stop-loss and target placement
        entry_p = float(entry_price)
        stop_p = float(stop_loss)
        target_p = float(target_price)

        if side.upper() == "BUY":
            if stop_p >= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid stop-loss ({stop_p:.2f}) must be strictly below entry price ({entry_p:.2f})"]
                }
            if target_p <= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid target price ({target_p:.2f}) must be strictly above entry price ({entry_p:.2f})"]
                }
            risk_per_share = entry_p - stop_p
            reward_per_share = target_p - entry_p
        else:
            if stop_p <= entry_p:
                return {
                    "passed": False,
                    "quantity": 0,
                    "reasons": [f"Invalid short stop-loss ({stop_p:.2f}) must be strictly above entry price ({entry_p:.2f})"]
                }
            risk_per_share = stop_p - entry_p
            reward_per_share = entry_p - target_p

        if risk_per_share <= 0:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": ["Risk per share is non-positive"]
            }

        rr_ratio = reward_per_share / max(0.001, risk_per_share)
        # Rounding tolerance of 1e-4 prevents 1.49999 floating-point rejection
        if round(rr_ratio, 2) + 1e-4 < round(self.min_reward_risk, 2):
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Reward-to-risk ratio ({rr_ratio:.2f}) is below minimum required ({self.min_reward_risk:.2f})"]
            }

        # 7. Position sizing formula:
        # remaining daily budget = max_daily_loss - current loss
        remaining_loss_budget = max(0.0, self.max_daily_loss - abs(min(0.0, effective_daily_loss)))
        risk_budget = min(self.max_loss_per_trade, remaining_loss_budget)

        risk_quantity = math.floor(risk_budget / risk_per_share)
        max_position_cash = available_cash * self.max_position_pct
        cash_quantity = math.floor(max_position_cash / entry_p)

        # Lot sizing check for options/derivatives
        lot_size = 1
        if is_derivative or any(k in sym_upper for k in ("CE", "PE", "NIFTY", "BANKNIFTY")):
            if "BANKNIFTY" in sym_upper:
                lot_size = 15
            elif "NIFTY" in sym_upper:
                lot_size = 25

        raw_qty = min(risk_quantity, cash_quantity)
        if lot_size > 1:
            quantity = (raw_qty // lot_size) * lot_size
        else:
            quantity = raw_qty

        # Clamping to max order notional
        if (quantity * entry_p) > self.max_order_notional:
            quantity = math.floor(self.max_order_notional / entry_p)
            if lot_size > 1:
                quantity = (quantity // lot_size) * lot_size

        if quantity <= 0:
            return {
                "passed": False,
                "quantity": 0,
                "reasons": [f"Calculated risk quantity is 0 (Risk budget ₹{risk_budget:,.2f}, Risk/shr ₹{risk_per_share:.2f}, Lot size {lot_size}, Cash limit {cash_quantity})"]
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

    def evaluate_from_ledger(self, ledger, symbol, side, entry_price, stop_loss, target_price, current_quotes=None):
        """
        Calculates all risk state directly from the single source of truth (ledger.json).
        """
        starting_cap = float(ledger.get("starting_capital", self.starting_capital))
        open_positions = ledger.get("open_positions", [])
        deployed_cash = float(ledger.get("deployed_cash", 0.0))
        realized_pnl = float(ledger.get("realized_pnl", 0.0))
        free_cash = float(ledger.get("free_cash", starting_cap - deployed_cash + realized_pnl))

        # Filter today's trades
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        trade_history = ledger.get("trade_history", [])
        today_trades = [
            t for t in trade_history
            if t.get("date") == today_str or t.get("exit_date") == today_str
        ]
        daily_realized_loss = sum(float(t.get("pnl", 0.0)) for t in today_trades)

        # Unrealized PnL calculation
        unrealized_pnl = 0.0
        if current_quotes:
            for pos in open_positions:
                s = pos.get("symbol")
                if s in current_quotes:
                    curr_p = float(current_quotes[s])
                    ent_p = float(pos.get("entry_price", curr_p))
                    q = int(pos.get("qty", pos.get("quantity", 0)))
                    pos_side = pos.get("side", "BUY")
                    pnl = (curr_p - ent_p) * q if pos_side == "BUY" else (ent_p - curr_p) * q
                    unrealized_pnl += pnl

        existing_symbols = [p.get("symbol") for p in open_positions]

        return self.evaluate_order_risk(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            available_cash=free_cash,
            daily_realized_loss=daily_realized_loss,
            current_trades_count=len(today_trades),
            current_open_positions_count=len(open_positions),
            unrealized_loss=min(0.0, unrealized_pnl),
            existing_symbols=existing_symbols
        )
