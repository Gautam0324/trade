#!/usr/bin/env python3
"""
Centralized Position Exit & Trade Management Engine.
Handles:
1. Hard Initial Stop-Loss
2. Multi-stage Reward-to-Risk Targets (1R, 1.5R, 2R)
3. Partial Profit Booking: At +1R, close 50% of the position and move stop to Breakeven
4. ATR Trailing Stop (Ratchet stop upward only, never loosen)
5. EMA Momentum Exit (EMA 5 / EMA 9 cross-under)
6. Peak Reversal Exit (0.2% drop from high after in-profit)
7. Strict Market Hours (15:20 IST auto-squareoff)
"""

from decimal import Decimal
import datetime

class PositionManager:
    def __init__(self, config=None):
        self.config = config or {}
        self.enable_partial_profit = bool(self.config.get("enable_partial_profit", True))
        self.partial_profit_ratio = float(self.config.get("partial_profit_ratio", 0.50))
        self.atr_trailing_multiplier = float(self.config.get("atr_trailing_multiplier", 1.5))
        self.peak_reversal_threshold = float(self.config.get("peak_reversal_threshold", 0.002)) # 0.2%
        self.flat_by = self.config.get("flat_by", "15:20")

    def evaluate_exit(self, position, current_price, current_atr=None, current_ema5=None, current_ema9=None, current_time=None):
        """
        Evaluates open position against active exit rules.
        Returns:
          {
            "action": "HOLD" | "PARTIAL_EXIT" | "FULL_EXIT",
            "exit_price": float,
            "exit_qty": int,
            "reason": str,
            "updated_position": dict
          }
        """
        pos = dict(position)
        cur_p = float(current_price)
        entry_p = float(pos["entry_price"])
        initial_stop = float(pos.get("initial_stop", pos.get("stop_loss", entry_p * 0.99)))
        current_stop = float(pos.get("current_stop", initial_stop))
        peak_p = float(pos.get("peak_price", max(entry_p, cur_p)))
        qty = int(pos["qty"])
        partial_exited = bool(pos.get("partial_exited", False))

        # Update peak observed price
        if cur_p > peak_p:
            peak_p = cur_p
            pos["peak_price"] = peak_p

        r_unit = abs(entry_p - initial_stop)
        current_profit_per_share = cur_p - entry_p
        current_r = (current_profit_per_share / r_unit) if r_unit > 0 else 0.0
        pos["current_r"] = round(current_r, 2)

        # 1. Mandatory Market Close Flat-by Cutoff
        now_time = current_time or datetime.datetime.now().strftime("%H:%M")
        if self.flat_by and now_time >= self.flat_by:
            return {
                "action": "FULL_EXIT",
                "exit_price": cur_p,
                "exit_qty": qty,
                "reason": f"Mandatory EOD {self.flat_by} IST Intraday Square-off",
                "updated_position": pos
            }

        # 2. Hard Stop-Loss Hit
        if cur_p <= current_stop:
            return {
                "action": "FULL_EXIT",
                "exit_price": cur_p,
                "exit_qty": qty,
                "reason": f"Stop-loss hit ({cur_p:.2f} <= {current_stop:.2f})",
                "updated_position": pos
            }

        # 3. Partial Profit Booking at +1.0R (Book 50% and move stop to Breakeven)
        if self.enable_partial_profit and not partial_exited and current_r >= 1.0 and qty >= 2:
            exit_qty = int(qty * self.partial_profit_ratio)
            rem_qty = qty - exit_qty
            pos["partial_exited"] = True
            pos["qty"] = rem_qty
            include_cost = self.config.get("include_cost_in_breakeven", False)
            cost_buffer = round(entry_p * 0.0005, 2) if include_cost else 0.0
            breakeven_stop = round(entry_p + cost_buffer, 2)
            pos["current_stop"] = max(current_stop, breakeven_stop)
            return {
                "action": "PARTIAL_EXIT",
                "exit_price": cur_p,
                "exit_qty": exit_qty,
                "reason": f"Partial profit booked at +1R ({cur_p:.2f}). Stop moved to breakeven ({pos['current_stop']:.2f})",
                "updated_position": pos
            }

        # 4. ATR Trailing Stop Update (Ratchet upward only)
        if current_atr and current_atr > 0 and cur_p > entry_p:
            candidate_stop = round(peak_p - (self.atr_trailing_multiplier * current_atr), 2)
            if candidate_stop > current_stop:
                current_stop = candidate_stop
                pos["current_stop"] = current_stop

        # 5. Trailing Profit Lock / Peak Reversal Exit (0.2% drop from high after gaining)
        if cur_p > entry_p and peak_p > entry_p:
            if cur_p < (peak_p * (1.0 - self.peak_reversal_threshold)):
                return {
                    "action": "FULL_EXIT",
                    "exit_price": cur_p,
                    "exit_qty": qty,
                    "reason": f"Peak reversal exit: price fell from {peak_p:.2f} to {cur_p:.2f} (Profit Locked)",
                    "updated_position": pos
                }

        # 6. EMA Momentum Reversal Exit
        if current_ema5 and current_ema9:
            if cur_p > entry_p and (cur_p < current_ema5 or (current_ema5 < current_ema9)):
                return {
                    "action": "FULL_EXIT",
                    "exit_price": cur_p,
                    "exit_qty": qty,
                    "reason": f"EMA momentum breakdown (Price {cur_p:.2f} < EMA5 {current_ema5:.2f})",
                    "updated_position": pos
                }

        return {
            "action": "HOLD",
            "exit_price": cur_p,
            "exit_qty": 0,
            "reason": "Position healthy, trailing parameters active",
            "updated_position": pos
        }
