#!/usr/bin/env python3
"""
Reproducible Strategy Comparison & Validation Engine for Indian Equities.
Features:
- Replaces synthetic/random price generation with genuine historical 1-minute OHLCV CSV data
- Strict chronological forward data partition:
    * Training Period (60%)
    * Validation Period (20%)
    * Untouched Out-Of-Sample Test Period (20%)
- Baseline vs Candidate comparative variants:
    1. Baseline: EMA_CHANNEL
    2. Candidate 1: Baseline + Regime Filter (TRENDING_UP only)
    3. Candidate 2: Baseline + Trend Confirmation (TREND_MOMENTUM)
    4. Candidate 3: Value/Mean Reversion (VWAP_PULLBACK)
    5. Candidate 4: Multi-Strategy Confluence (CONFLUENCE)
- Comprehensive institutional metrics:
    * Total trades, Wins, Losses, Win Rate %
    * Gross P&L, Statutory Fees & Taxes, Slippage, Net P&L
    * Profit Factor, Maximum Drawdown %, Average R, Expectancy per trade
"""

import os
import sys
import argparse
import datetime
from pathlib import Path

TRADER_DIR = Path(__file__).resolve().parent
BASE_DIR = TRADER_DIR.parent
sys.path.insert(0, str(TRADER_DIR))

from backtest import BacktestEngine
from cost_model import CostModel

def slice_bars(bars_dict, start_idx, end_idx):
    return {
        "timestamp": bars_dict["timestamp"][start_idx:end_idx],
        "open": bars_dict["open"][start_idx:end_idx],
        "high": bars_dict["high"][start_idx:end_idx],
        "low": bars_dict["low"][start_idx:end_idx],
        "close": bars_dict["close"][start_idx:end_idx],
        "volume": bars_dict["volume"][start_idx:end_idx]
    }

def run_comparison(csv_file=None, max_bars=1500, starting_capital=1000000.0):
    if not csv_file:
        csv_file = str(BASE_DIR / "historical_data" / "3MINDIA_1min.csv")

    sym = Path(csv_file).stem.replace("_1min", "")
    print("=" * 105)
    print(f"REPRODUCIBLE STRATEGY COMPARISON & CHRONOLOGICAL OUT-OF-SAMPLE VALIDATION")
    print(f"Dataset: {sym} | Source: {csv_file}")
    print(f"Capital: Rs {starting_capital:,.2f} | Max Bars Evaluated: {max_bars}")
    print("=" * 105)

    engine = BacktestEngine(starting_capital=starting_capital)
    bars = engine.load_historical_csv(csv_file, max_bars=max_bars)
    n_bars = len(bars["close"])

    # Chronological Split (60% Train, 20% Val, 20% Test)
    train_end = int(n_bars * 0.60)
    val_end = int(n_bars * 0.80)

    train_bars = slice_bars(bars, 0, train_end)
    val_bars = slice_bars(bars, train_end, val_end)
    test_bars = slice_bars(bars, val_end, n_bars)

    splits = {
        "Training (60%)": (train_bars, f"{train_bars['timestamp'][0]} to {train_bars['timestamp'][-1]}"),
        "Validation (20%)": (val_bars, f"{val_bars['timestamp'][0]} to {val_bars['timestamp'][-1]}"),
        "Out-Of-Sample Test (20%)": (test_bars, f"{test_bars['timestamp'][0]} to {test_bars['timestamp'][-1]}"),
        "Full Dataset": (bars, f"{bars['timestamp'][0]} to {bars['timestamp'][-1]}")
    }

    variants = [
        ("EMA_CHANNEL", "Baseline EMA Channel"),
        ("TREND_MOMENTUM", "Trend + Momentum Confirmation"),
        ("VWAP_PULLBACK", "VWAP Dynamic Pullback"),
        ("CONFLUENCE", "Multi-Strategy Confluence")
    ]

    summary_matrix = []

    for split_name, (split_data, date_range) in splits.items():
        print(f"\n--- Period: {split_name} ({len(split_data['close'])} bars | {date_range}) ---")
        print(f"{'Strategy Variant':<32} | {'Trades':<6} | {'Win%':<6} | {'Gross PnL':<12} | {'Fees':<9} | {'Net PnL':<12} | {'PF':<5} | {'Max DD':<7} | {'Avg R'}")
        print("-" * 115)

        for strat_key, strat_desc in variants:
            res = engine.run(split_data, strategy_name=strat_key, symbol=sym)
            if "error" in res:
                print(f"{strat_desc:<32} | Insufficient bars ({res['error']})")
                continue

            net_pnl = res["net_pnl"]
            gross_pnl = res["gross_pnl"]
            fees = res["total_costs"]
            wr = res["win_rate_pct"]
            pf = res["profit_factor"]
            dd = res["max_drawdown_pct"]
            trades = res["total_trades"]
            avg_r = res["average_r"]

            summary_matrix.append({
                "period": split_name,
                "strategy": strat_key,
                "description": strat_desc,
                "bars": len(split_data["close"]),
                "date_range": date_range,
                "trades": trades,
                "win_rate": wr,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "net_pnl": net_pnl,
                "profit_factor": pf,
                "max_drawdown": dd,
                "average_r": avg_r
            })

            gross_str = f"Rs {gross_pnl:+,.1f}"
            fees_str = f"Rs {fees:,.1f}"
            net_str = f"Rs {net_pnl:+,.1f}"
            print(f"{strat_desc:<32} | {trades:<6} | {wr:>5.1f}% | {gross_str:>12} | {fees_str:>9} | {net_str:>12} | {pf:>5.2f} | {dd:>5.2f}% | {avg_r:>5.2f}")

    print("\n" + "=" * 105)
    print("COMPARISON COMPLETE. Zero synthetic data used.")
    print("=" * 105)
    return summary_matrix

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reproducible Strategy Comparison Engine")
    parser.add_argument("--csv", type=str, default=str(BASE_DIR / "historical_data" / "3MINDIA_1min.csv"))
    parser.add_argument("--max_bars", type=int, default=1500)
    args = parser.parse_args()

    run_comparison(csv_file=args.csv, max_bars=args.max_bars)
