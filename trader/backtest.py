#!/usr/bin/env python3
"""
Credible Historical Backtesting Engine for Indian Equities.
Features:
- Loads and validates genuine NSE 1-minute OHLCV historical CSV data
- Eliminates look-ahead bias via strict sequential bar slicing
- Conservative execution assumptions:
  * Gap handling: fill at candle open if gapped beyond stop level
  * Stop/Target ambiguity: stop-loss hit first if both levels touched in the same bar
- Precise statutory & regulatory transaction costs via CostModel
- Supports both Long and Short execution
- Generates institutional performance metrics & exports trade logs
"""

import os
import sys
import argparse
import datetime
import pandas as pd
from pathlib import Path

TRADER_DIR = Path(__file__).resolve().parent
BASE_DIR = TRADER_DIR.parent
sys.path.insert(0, str(TRADER_DIR))

import indicators
from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from cost_model import CostModel

class BacktestEngine:
    def __init__(self, starting_capital=1000000.0, config=None):
        self.config = config or {}
        self.starting_capital = starting_capital
        self.cost_model = CostModel(self.config)

    @staticmethod
    def load_historical_csv(filepath, max_bars=None):
        """
        Loads and validates genuine 1-minute historical OHLCV data from CSV.
        """
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Historical data file not found: {filepath}")

        df = pd.read_csv(filepath)
        if max_bars and len(df) > max_bars * 2:
            df = df.iloc[-(max_bars * 2):].reset_index(drop=True)

        required_cols = {"timestamp", "open", "high", "low", "close"}
        if not required_cols.issubset(set(df.columns)):
            raise ValueError(f"CSV missing required columns {required_cols}. Found: {df.columns.tolist()}")

        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["high"] = pd.to_numeric(df["high"], errors="coerce")
        df["low"] = pd.to_numeric(df["low"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(1000.0)
        else:
            df["volume"] = 1000.0

        # Drop invalid rows
        df = df.dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)

        # Validate OHLC logic
        df = df[(df["high"] >= df["low"]) & (df["high"] >= df["open"]) & (df["high"] >= df["close"]) & (df["low"] <= df["open"]) & (df["low"] <= df["close"])]

        if max_bars and len(df) > max_bars:
            df = df.iloc[-max_bars:].reset_index(drop=True)

        return {
            "timestamp": df["timestamp"].tolist(),
            "open": df["open"].tolist(),
            "high": df["high"].tolist(),
            "low": df["low"].tolist(),
            "close": df["close"].tolist(),
            "volume": df["volume"].tolist()
        }

    def run_strategy(self, bars_dict, strategy_name="EMA_CHANNEL", symbol="EQUITY"):
        """Alias for backward compatibility with existing tests."""
        return self.run(bars_dict, strategy_name=strategy_name, symbol=symbol)

    def run(self, bars_dict, strategy_name="EMA_CHANNEL", symbol="EQUITY"):
        """
        Executes strictly sequential event-driven backtest.
        """
        closes = bars_dict["close"]
        highs = bars_dict["high"]
        lows = bars_dict["low"]
        opens = bars_dict["open"]
        volumes = bars_dict.get("volume", [1000.0] * len(closes))
        timestamps = bars_dict.get("timestamp", [f"T-{i}" for i in range(len(closes))])

        n_bars = len(closes)
        if n_bars < 40:
            return {"error": "Insufficient bars for backtest (minimum 40 bars required)"}

        cfg = dict(self.config)
        cfg["strategy_mode"] = strategy_name
        strat_eng = StrategyEngine(cfg)
        risk_eng = RiskEngine({"budget": self.starting_capital})

        cash = self.starting_capital
        peak_equity = self.starting_capital
        max_drawdown = 0.0

        open_position = None
        trade_log = []

        # Sequential bar-by-bar evaluation (zero lookahead)
        for i in range(35, n_bars):
            cur_time = timestamps[i]
            cur_o = opens[i]
            cur_h = highs[i]
            cur_l = lows[i]
            cur_c = closes[i]

            # 1. Manage Open Position Exits First
            if open_position is not None:
                side = open_position["side"]
                entry_p = open_position["entry_price"]
                sl_p = open_position["stop_loss"]
                tgt_p = open_position["target_price"]
                qty = open_position["qty"]

                exit_trade = False
                exit_price = cur_c
                exit_reason = ""

                if side == "BUY":
                    stop_touched = (cur_l <= sl_p)
                    target_touched = (cur_h >= tgt_p)

                    # Ambiguity resolution: conservative assumption assumes Stop Loss hit first
                    if stop_touched and target_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o < sl_p else sl_p
                        exit_reason = "Stop Loss Hit (Ambiguous Candle)"
                    elif stop_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o < sl_p else sl_p
                        exit_reason = "Stop Loss Hit (Gapped)" if cur_o < sl_p else "Stop Loss Hit"
                    elif target_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o > tgt_p else tgt_p
                        exit_reason = "Target Hit (+1.5R)"

                elif side == "SELL":
                    stop_touched = (cur_h >= sl_p)
                    target_touched = (cur_l <= tgt_p)

                    if stop_touched and target_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o > sl_p else sl_p
                        exit_reason = "Short Stop Loss Hit (Ambiguous Candle)"
                    elif stop_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o > sl_p else sl_p
                        exit_reason = "Short Stop Loss Hit (Gapped)" if cur_o > sl_p else "Short Stop Loss Hit"
                    elif target_touched:
                        exit_trade = True
                        exit_price = cur_o if cur_o < tgt_p else tgt_p
                        exit_reason = "Short Target Hit (+1.5R)"

                if exit_trade:
                    # Calculate exact costs via CostModel
                    cost_info = self.cost_model.calculate_roundtrip_cost(side, entry_p, exit_price, qty)
                    gross_pnl = cost_info["gross_pnl"]
                    fees = cost_info["total_costs"]
                    net_pnl = cost_info["net_pnl"]

                    cash += (qty * exit_price if side == "BUY" else qty * (2 * entry_p - exit_price)) - fees

                    risk_pts = abs(entry_p - sl_p)
                    r_mult = round((exit_price - entry_p) / risk_pts, 2) if side == "BUY" else round((entry_p - exit_price) / risk_pts, 2)

                    trade_log.append({
                        "symbol": symbol,
                        "side": side,
                        "entry_time": open_position["entry_time"],
                        "exit_time": cur_time,
                        "entry_price": entry_p,
                        "exit_price": exit_price,
                        "qty": qty,
                        "gross_pnl": gross_pnl,
                        "fees": fees,
                        "net_pnl": net_pnl,
                        "reason": exit_reason,
                        "r_multiple": r_mult
                    })
                    open_position = None

            # Track equity curve & drawdown
            current_equity = cash + (open_position["qty"] * cur_c if open_position else 0.0)
            if current_equity > peak_equity:
                peak_equity = current_equity
            dd = (peak_equity - current_equity) / peak_equity if peak_equity > 0 else 0.0
            if dd > max_drawdown:
                max_drawdown = dd

            # 2. Check for New Entry if flat
            if open_position is None:
                start_idx = max(0, i - 250)
                sliced_bars = {
                    "timestamp": timestamps[start_idx:i+1],
                    "open": opens[start_idx:i+1],
                    "high": highs[start_idx:i+1],
                    "low": lows[start_idx:i+1],
                    "close": closes[start_idx:i+1],
                    "volume": volumes[start_idx:i+1]
                }
                sig = strat_eng.evaluate(symbol, sliced_bars)

                if sig and sig.get("direction") in ("LONG", "SHORT"):
                    side = "BUY" if sig["direction"] == "LONG" else "SELL"
                    entry_p = cur_c
                    sl_p = sig.get("stop_loss", cur_l if side == "BUY" else cur_h)
                    tgt_p = sig.get("target_price", round(cur_c + 1.5 * abs(cur_c - sl_p), 2) if side == "BUY" else round(cur_c - 1.5 * abs(cur_c - sl_p), 2))

                    risk_eval = risk_eng.evaluate_order_risk(
                        symbol=symbol,
                        side=side,
                        entry_price=entry_p,
                        stop_loss=sl_p,
                        target_price=tgt_p,
                        available_cash=cash,
                        daily_realized_loss=min(0.0, cash - self.starting_capital),
                        current_trades_count=len(trade_log),
                        current_open_positions_count=0
                    )

                    if risk_eval["passed"] and risk_eval["quantity"] > 0:
                        qty = risk_eval["quantity"]
                        cost_outlay = qty * entry_p
                        cash -= cost_outlay
                        open_position = {
                            "symbol": symbol,
                            "side": side,
                            "entry_time": cur_time,
                            "entry_price": entry_p,
                            "stop_loss": sl_p,
                            "target_price": tgt_p,
                            "qty": qty,
                            "strategy": sig.get("strategy_name", strategy_name)
                        }

        # Square off remaining open position at backtest conclusion
        if open_position is not None:
            side = open_position["side"]
            entry_p = open_position["entry_price"]
            exit_price = closes[-1]
            qty = open_position["qty"]
            cost_info = self.cost_model.calculate_roundtrip_cost(side, entry_p, exit_price, qty)
            gross_pnl = cost_info["gross_pnl"]
            fees = cost_info["total_costs"]
            net_pnl = cost_info["net_pnl"]

            cash += (qty * exit_price if side == "BUY" else qty * (2 * entry_p - exit_price)) - fees
            trade_log.append({
                "symbol": symbol,
                "side": side,
                "entry_time": open_position["entry_time"],
                "exit_time": timestamps[-1],
                "entry_price": entry_p,
                "exit_price": exit_price,
                "qty": qty,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "net_pnl": net_pnl,
                "reason": "Backtest Final Close",
                "r_multiple": 0.0
            })
            open_position = None

        # Statistics computation
        wins = [t for t in trade_log if t["net_pnl"] > 0]
        losses = [t for t in trade_log if t["net_pnl"] < 0]
        be = [t for t in trade_log if t["net_pnl"] == 0]
        gross_profit = sum(t["gross_pnl"] for t in wins)
        gross_loss = abs(sum(t["gross_pnl"] for t in losses))
        total_fees = sum(t["fees"] for t in trade_log)
        net_profit = sum(t["net_pnl"] for t in trade_log)

        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (99.0 if gross_profit > 0 else 1.0)
        win_rate = round((len(wins) / len(trade_log)) * 100, 1) if trade_log else 0.0
        avg_r = round(sum(t["r_multiple"] for t in trade_log) / len(trade_log), 2) if trade_log else 0.0

        return {
            "symbol": symbol,
            "strategy": strategy_name,
            "starting_capital": self.starting_capital,
            "ending_capital": round(cash, 2),
            "gross_pnl": round(gross_profit - gross_loss, 2),
            "total_costs": round(total_fees, 2),
            "net_pnl": round(net_profit, 2),
            "roi_pct": round((net_profit / self.starting_capital) * 100, 2),
            "total_trades": len(trade_log),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(be),
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown_pct": round(max_drawdown * 100, 2),
            "average_r": avg_r,
            "trades": trade_log
        }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Institutional Backtester for Day Trader Pro")
    parser.add_argument("--csv", type=str, default=str(BASE_DIR / "historical_data" / "3MINDIA_1min.csv"), help="Path to 1-minute historical CSV")
    parser.add_argument("--strategy", type=str, default="EMA_CHANNEL", help="Strategy to test")
    parser.add_argument("--max_bars", type=int, default=2000, help="Max bars to evaluate")
    args = parser.parse_args()

    engine = BacktestEngine(starting_capital=1000000.0)
    print(f"Loading historical CSV: {args.csv}...")
    bars = engine.load_historical_csv(args.csv, max_bars=args.max_bars)
    sym = Path(args.csv).stem.replace("_1min", "")
    print(f"Running backtest for {sym} using strategy {args.strategy} over {len(bars['close'])} completed bars...")
    res = engine.run(bars, strategy_name=args.strategy, symbol=sym)

    print("\n" + "=" * 60)
    print(f"BACKTEST RESULTS: {sym} | Strategy: {args.strategy}")
    print("=" * 60)
    print(f"Starting Capital:     Rs {res['starting_capital']:,.2f}")
    print(f"Ending Capital:       Rs {res['ending_capital']:,.2f}")
    print(f"Gross P&L:            Rs {res['gross_pnl']:+,.2f}")
    print(f"Total Fees & Taxes:   Rs {res['total_costs']:,.2f}")
    print(f"Net Realized P&L:     Rs {res['net_pnl']:+,.2f} ({res['roi_pct']:+.2f}%)")
    print(f"Total Trades:         {res['total_trades']} (Wins: {res['wins']} | Losses: {res['losses']} | BE: {res['breakeven']})")
    print(f"Win Rate:             {res['win_rate_pct']}%")
    print(f"Profit Factor:        {res['profit_factor']}")
    print(f"Max Drawdown:         {res['max_drawdown_pct']}%")
    print(f"Average R-Multiple:   {res['average_r']}")
    print("=" * 60)
