#!/usr/bin/env python3
"""
Runs comprehensive backtest comparing all strategies against the baseline EMA strategy.
"""

import math
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from backtest import BacktestEngine

def run_comparison():
    import random
    random.seed(42)
    p = 1000.0
    prices = [p]
    highs = [p + 2.0]
    lows = [p - 2.0]
    for _ in range(250):
        change = random.gauss(0.6, 2.5)  # Natural Indian market intraday drift with fluctuations
        p = max(500.0, p + change)
        prices.append(p)
        highs.append(p + abs(random.gauss(2.5, 0.8)))
        lows.append(p - abs(random.gauss(2.5, 0.8)))

    closes = list(prices)
    volumes = [int(abs(random.gauss(100000, 25000))) for _ in range(len(prices))]
    bars = {'open': prices, 'high': highs, 'low': lows, 'close': closes, 'volume': volumes}

    bt = BacktestEngine(starting_capital=100000.0, cost_pct=0.0005)
    strategies = [
        'BREAKOUT_RETEST',
        'TREND_PULLBACK_RECLAIM',
        'FAILED_BREAKDOWN',
        'EMA_CHANNEL',
        'VWAP_PULLBACK',
        'ORB',
        'TREND_MOMENTUM',
        'CONFLUENCE'
    ]
    or_high = max(highs[:3])
    or_low = min(lows[:3])

    results = []
    print("\n" + "="*85)
    print("BACKTEST COMPARATIVE PERFORMANCE MATRIX (100,000 INR Starting Capital, 0.05% Costs)")
    print("="*85)
    print(f"{'Strategy':<16} | {'Trades':<6} | {'Win Rate':<8} | {'Net PnL (INR)':<14} | {'PF':<6} | {'Max DD':<7} | {'Avg R'}")
    print("-" * 85)

    for s in strategies:
        res = bt.run_strategy(bars, strategy_name=s, or_high=or_high, or_low=or_low)
        results.append(res)
        pnl_str = f"Rs. {res['net_pnl']:+,.2f}"
        print(f"{s:<16} | {res['total_trades']:<6} | {res['win_rate_pct']:>6.1f}% | {pnl_str:>14} | {res['profit_factor']:>5.2f} | {res['max_drawdown_pct']:>5.2f}% | {res['average_r']:>5.2f}R")

    print("="*85)
    return results

if __name__ == "__main__":
    run_comparison()
