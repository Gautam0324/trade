#!/usr/bin/env python3
"""
Backtesting Framework for Multi-Strategy Indian Equities Trading System.
Features:
- Deterministic event-driven simulation bar-by-bar
- Conservative fill assumption: no intrabar lookahead
- Realistic transaction costs: 0.05% brokerage/STT/turnover charges
- Tracks initial stop-loss, ATR trailing stop, +1R partial profit booking (50%)
- Compares each individual strategy (EMA_CHANNEL, VWAP_PULLBACK, ORB, TREND_MOMENTUM, CONFLUENCE)
- Comprehensive performance report:
  Net P&L, Win Rate, Profit Factor, Max Drawdown, Total Trades, Average R-multiple
"""

import datetime
import math
from pathlib import Path
import sys

TRADER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADER_DIR))

import indicators
from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from exit_engine import PositionManager

class BacktestEngine:
    def __init__(self, starting_capital=100000.0, cost_pct=0.0005):
        self.starting_capital = starting_capital
        self.cost_pct = cost_pct  # 0.05% round-trip transaction costs

    def run_strategy(self, bars_dict, strategy_name="EMA_CHANNEL", or_high=None, or_low=None):
        """
        Runs backtest over historical bar sequence for a single instrument.
        bars_dict: { 'open': [], 'high': [], 'low': [], 'close': [], 'volume': [] }
        """
        closes = bars_dict.get("close", [])
        highs = bars_dict.get("high", [])
        lows = bars_dict.get("low", [])
        opens = bars_dict.get("open", [])
        volumes = bars_dict.get("volume", [100000] * len(closes))

        n_bars = len(closes)
        if n_bars < 40:
            return {"error": "Insufficient bars for backtest"}

        cfg = {"strategy_mode": strategy_name}
        strat_eng = StrategyEngine(cfg)
        risk_eng = RiskEngine({"budget": self.starting_capital})
        pos_mgr = PositionManager({"flat_by": "15:20"})

        cash = self.starting_capital
        peak_equity = self.starting_capital
        max_drawdown = 0.0

        open_position = None
        trade_log = []

        # Precompute VWAP & ATR series
        vwap_series = indicators.calculate_vwap(highs, lows, closes, volumes)
        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        ema5_series = indicators.calculate_ema(closes, 5)
        ema9_series = indicators.calculate_ema(closes, 9)

        # Iterate bar by bar (no future lookahead)
        for i in range(35, n_bars):
            cur_open = opens[i]
            cur_high = highs[i]
            cur_low = lows[i]
            cur_close = closes[i]
            cur_atr = atr_series[i] or (cur_close * 0.01)
            cur_ema5 = ema5_series[i]
            cur_ema9 = ema9_series[i]

            # 1. Manage Active Open Position Exits first
            if open_position is not None:
                # Conservative fill check: Did low hit stop?
                cur_stop = open_position.get("current_stop", open_position.get("initial_stop", open_position.get("stop_loss")))
                if cur_low <= cur_stop:
                    # Stopped out at stop price (or gap down open)
                    fill_p = min(cur_open, cur_stop)
                    qty = open_position["qty"]
                    invested = qty * open_position["entry_price"]
                    revenue = qty * fill_p
                    cost = (invested + revenue) * self.cost_pct
                    net_pnl = (revenue - invested) - cost
                    cash += revenue - cost

                    trade_log.append({
                        "entry_price": open_position["entry_price"],
                        "exit_price": fill_p,
                        "qty": qty,
                        "pnl": net_pnl,
                        "reason": "Stop Loss Hit",
                        "r_multiple": (fill_p - open_position["entry_price"]) / open_position["risk_per_share"]
                    })
                    open_position = None
                else:
                    # Evaluate dynamic exits (Partial profit, ATR trailing, EMA drop)
                    exit_res = pos_mgr.evaluate_exit(
                        open_position,
                        current_price=cur_close,
                        current_atr=cur_atr,
                        current_ema5=cur_ema5,
                        current_ema9=cur_ema9
                    )
                    open_position = exit_res["updated_position"]

                    if exit_res["action"] == "PARTIAL_EXIT":
                        p_qty = exit_res["exit_qty"]
                        inv_portion = p_qty * open_position["entry_price"]
                        rev_portion = p_qty * cur_close
                        cost = (inv_portion + rev_portion) * self.cost_pct
                        net_pnl = (rev_portion - inv_portion) - cost
                        cash += rev_portion - cost

                        trade_log.append({
                            "entry_price": open_position["entry_price"],
                            "exit_price": cur_close,
                            "qty": p_qty,
                            "pnl": net_pnl,
                            "reason": exit_res["reason"],
                            "r_multiple": 1.0
                        })
                    elif exit_res["action"] == "FULL_EXIT":
                        f_qty = exit_res["exit_qty"]
                        inv_tot = f_qty * open_position["entry_price"]
                        rev_tot = f_qty * cur_close
                        cost = (inv_tot + rev_tot) * self.cost_pct
                        net_pnl = (rev_tot - inv_tot) - cost
                        cash += rev_tot - cost

                        trade_log.append({
                            "entry_price": open_position["entry_price"],
                            "exit_price": cur_close,
                            "qty": f_qty,
                            "pnl": net_pnl,
                            "reason": exit_res["reason"],
                            "r_multiple": (cur_close - open_position["entry_price"]) / max(0.01, open_position["risk_per_share"])
                        })
                        open_position = None

            # Track drawdown
            current_equity = cash + (open_position["qty"] * cur_close if open_position else 0.0)
            if current_equity > peak_equity:
                peak_equity = current_equity
            dd = (peak_equity - current_equity) / peak_equity
            if dd > max_drawdown:
                max_drawdown = dd

            # 2. Check for New Entry if flat
            if open_position is None:
                # Slice historical bars up to current candle (no lookahead)
                sliced_bars = {
                    "open": opens[:i+1],
                    "high": highs[:i+1],
                    "low": lows[:i+1],
                    "close": closes[:i+1],
                    "volume": volumes[:i+1]
                }
                signal_payload = strat_eng.evaluate_all(
                    symbol="TEST",
                    bars=sliced_bars,
                    or_high=or_high,
                    or_low=or_low,
                    vwap_series=vwap_series[:i+1]
                )

                if signal_payload and signal_payload.get("signal") == "BUY":
                    risk_eval = risk_eng.evaluate_order_risk(
                        symbol="TEST",
                        side="BUY",
                        entry_price=signal_payload["entry_reference_price"],
                        stop_loss=signal_payload["stop_loss"],
                        target_price=signal_payload["target_price"],
                        available_cash=cash,
                        daily_realized_loss=min(0.0, cash - self.starting_capital),
                        current_trades_count=len(trade_log),
                        current_open_positions_count=0
                    )

                    if risk_eval["passed"] and risk_eval["quantity"] > 0:
                        qty = risk_eval["quantity"]
                        cost = qty * signal_payload["entry_reference_price"]
                        cash -= cost
                        open_position = {
                            "symbol": "TEST",
                            "strategy": signal_payload["strategy_name"],
                            "qty": qty,
                            "entry_price": signal_payload["entry_reference_price"],
                            "initial_stop": signal_payload["stop_loss"],
                            "current_stop": signal_payload["stop_loss"],
                            "risk_per_share": signal_payload["risk_per_share"],
                            "target_price": signal_payload["target_price"],
                            "peak_price": signal_payload["entry_reference_price"]
                        }

        # Close any lingering position at final close
        if open_position is not None:
            f_qty = open_position["qty"]
            inv_tot = f_qty * open_position["entry_price"]
            rev_tot = f_qty * closes[-1]
            cost = (inv_tot + rev_tot) * self.cost_pct
            net_pnl = (rev_tot - inv_tot) - cost
            cash += rev_tot - cost
            trade_log.append({
                "entry_price": open_position["entry_price"],
                "exit_price": closes[-1],
                "qty": f_qty,
                "pnl": net_pnl,
                "reason": "Backtest Final Close",
                "r_multiple": (closes[-1] - open_position["entry_price"]) / max(0.01, open_position["risk_per_share"])
            })

        # Calculate performance statistics
        wins = [t for t in trade_log if t["pnl"] > 0]
        losses = [t for t in trade_log if t["pnl"] < 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 0.0)
        win_rate = round((len(wins) / len(trade_log)) * 100, 1) if trade_log else 0.0
        net_pnl = round(cash - self.starting_capital, 2)
        avg_r = round(sum(t["r_multiple"] for t in trade_log) / len(trade_log), 2) if trade_log else 0.0

        return {
            "strategy": strategy_name,
            "starting_capital": self.starting_capital,
            "ending_capital": round(cash, 2),
            "net_pnl": net_pnl,
            "roi_pct": round((net_pnl / self.starting_capital) * 100, 2),
            "total_trades": len(trade_log),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "average_r": avg_r,
            "trades": trade_log
        }
