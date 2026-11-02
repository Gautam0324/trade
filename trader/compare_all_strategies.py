#!/usr/bin/env python3
"""
Multi-Strategy Benchmark Comparison Runner (Phase 8 Deliverable).
Compares 5 strategies on identical 4-day 1-minute historical data (28-Oct to 02-Nov):
1. EMA_CHANNEL
2. TREND_MOMENTUM
3. VWAP_PULLBACK
4. CONFLUENCE
5. V3_TREND_PULLBACK
"""

import sys
import io
import json
import math
import pandas as pd
from pathlib import Path

# UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TRADER_DIR = Path(__file__).resolve().parent
BASE_DIR = TRADER_DIR.parent
HIST_DIR = BASE_DIR / "historical_data"
sys.path.insert(0, str(TRADER_DIR))
sys.path.insert(0, str(TRADER_DIR / "strategies"))

from strategy_engine import StrategyEngine
from v3_trend_pullback import V3TrendPullbackStrategy
from cost_model import CostModel
from accounting_engine import AccountingEngine

STOCKS = ["RELIANCE", "TCS", "INFY", "SBIN", "ICICIBANK", "BAJFINANCE", "TATASTEEL", "HDFCBANK"]
DAYS = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]

def evaluate_stock_strategy(df, symbol, strat_name, strat_engine, cost_model, v3_engine=None):
    trades = []
    days = sorted(df['date'].unique())
    is_v3 = (strat_name == "V3_TREND_PULLBACK")

    for day in days:
        day_indices = df[df['date'] == day].index.tolist()
        if not day_indices or len(day_indices) < 25:
            continue

        end_idx = day_indices[-1]
        # Use full history up to today for indicators warm-up (essential for 15m HTF EMA50/200 & ADX)
        sub_df = df.iloc[:end_idx+1].reset_index(drop=True)
        first_today_idx = day_indices[0]

        pos = None
        daily_trades = 0

        for i in range(first_today_idx, len(sub_df)):
            row = sub_df.iloc[i]
            ts = str(row['timestamp'])
            time_str = ts.split('T')[1][:5] if 'T' in ts else ts.split(' ')[1][:5]
            cur_c = float(row['close'])
            cur_h = float(row['high'])
            cur_l = float(row['low'])
            cur_o = float(row['open'])

            # 1. Exit Management
            if pos:
                side = pos['side']
                sl = pos['sl']
                tgt = pos['tgt']
                qty = pos['qty']
                entry_p = pos['entry_p']

                exit_p = None
                reason = None

                if side == 'BUY':
                    if cur_l <= sl:
                        exit_p = cur_o if cur_o < sl else sl
                        reason = "Stop Loss"
                    elif cur_h >= tgt:
                        exit_p = cur_o if cur_o > tgt else tgt
                        reason = "Target Hit"
                    elif time_str >= '15:20':
                        exit_p = cur_c
                        reason = "EOD 15:20"
                else:
                    if cur_h >= sl:
                        exit_p = cur_o if cur_o > sl else sl
                        reason = "Stop Loss"
                    elif cur_l <= tgt:
                        exit_p = cur_o if cur_o < tgt else tgt
                        reason = "Target Hit"
                    elif time_str >= '15:20':
                        exit_p = cur_c
                        reason = "EOD 15:20"

                if exit_p is not None:
                    costs = cost_model.calculate_roundtrip_cost(side, entry_p, exit_p, qty)
                    trades.append({
                        "date": day, "symbol": symbol, "side": side,
                        "entry_p": entry_p, "exit_p": exit_p, "qty": qty,
                        "gross": costs['gross_pnl'], "fees": costs['total_costs'],
                        "net": costs['net_pnl'], "reason": reason
                    })
                    pos = None

            # 2. Entry Management (max 3 trades per day per stock for fair comparison)
            if not pos and time_str < '15:10' and daily_trades < 3:
                sliced = {
                    'timestamp': sub_df['timestamp'].iloc[:i+1].tolist(),
                    'open': sub_df['open'].iloc[:i+1].tolist(),
                    'high': sub_df['high'].iloc[:i+1].tolist(),
                    'low': sub_df['low'].iloc[:i+1].tolist(),
                    'close': sub_df['close'].iloc[:i+1].tolist(),
                    'volume': sub_df['volume'].iloc[:i+1].tolist()
                }

                if is_v3:
                    sig = v3_engine.evaluate_1m_stream(symbol, sliced)
                else:
                    sig = strat_engine.evaluate(symbol, sliced)

                if sig and sig.get("direction") in ("LONG", "SHORT"):
                    side = "BUY" if sig["direction"] == "LONG" else "SELL"
                    sl_p = sig.get("stop_loss", cur_l if side == "BUY" else cur_h)
                    tgt_p = sig.get("target_price", cur_c * 1.015 if side == "BUY" else cur_c * 0.985)

                    risk_pts = abs(cur_c - sl_p)
                    if risk_pts < (cur_c * 0.001):
                        risk_pts = cur_c * 0.005
                        sl_p = round(cur_c - risk_pts if side == "BUY" else cur_c + risk_pts, 2)
                        tgt_p = round(cur_c + (2.0 * risk_pts) if side == "BUY" else cur_c - (2.0 * risk_pts), 2)

                    # 0.5% risk on ₹10L = ₹5,000 risk per trade
                    target_risk = 5000.0
                    raw_qty = max(1, math.floor(target_risk / risk_pts))
                    # Cap by 40% capital allocation (₹400,000)
                    qty = min(raw_qty, max(1, math.floor(400000.0 / cur_c)))

                    pos = {"side": side, "entry_p": cur_c, "sl": sl_p, "tgt": tgt_p, "qty": qty}
                    daily_trades += 1

    return trades

def main():
    print("=" * 85)
    print("MULTI-STRATEGY BENCHMARK COMPARISON (4 TRADING SESSIONS: 28-OCT TO 02-NOV)")
    print("Universe: 8 Watchlist Stocks | Sizing: 0.5% Risk Sizing | Model: Exact Indian Costs")
    print("=" * 85)

    cost_model = CostModel({})
    v3_engine = V3TrendPullbackStrategy({"adx_minimum": 20.0, "atr_multiplier": 1.5})

    # Load cached CSV data
    stock_dfs = {}
    for s in STOCKS:
        csv_p = HIST_DIR / f"{s}_4days.csv"
        if csv_p.exists():
            df = pd.read_csv(csv_p)
            df['date'] = df['timestamp'].apply(lambda x: str(x).split('T')[0] if 'T' in str(x) else str(x).split(' ')[0])
            stock_dfs[s] = df

    strategies_to_test = [
        "EMA_CHANNEL",
        "TREND_MOMENTUM",
        "VWAP_PULLBACK",
        "CONFLUENCE",
        "V3_TREND_PULLBACK"
    ]

    results = []

    for strat_name in strategies_to_test:
        strat_engine = StrategyEngine({"strategy_mode": strat_name}) if strat_name != "V3_TREND_PULLBACK" else None
        all_strat_trades = []

        for sym in STOCKS:
            df = stock_dfs.get(sym)
            if df is None:
                continue
            trades = evaluate_stock_strategy(df, sym, strat_name, strat_engine, cost_model, v3_engine)
            all_strat_trades.extend(trades)

        total_t = len(all_strat_trades)
        wins = sum(1 for t in all_strat_trades if t['net'] > 0)
        losses = sum(1 for t in all_strat_trades if t['net'] <= 0)
        wr = round(wins / total_t * 100, 1) if total_t > 0 else 0.0
        gross = round(sum(t['gross'] for t in all_strat_trades), 2)
        fees = round(sum(t['fees'] for t in all_strat_trades), 2)
        net = round(sum(t['net'] for t in all_strat_trades), 2)

        gross_wins = sum(t['gross'] for t in all_strat_trades if t['gross'] > 0)
        gross_losses = abs(sum(t['gross'] for t in all_strat_trades if t['gross'] < 0))
        pf = round(gross_wins / gross_losses, 2) if gross_losses > 0 else 999.0
        expectancy = round(net / total_t, 2) if total_t > 0 else 0.0

        results.append({
            "strategy": strat_name,
            "trades": total_t,
            "wins": wins,
            "losses": losses,
            "win_rate": wr,
            "gross_pnl": gross,
            "fees": fees,
            "net_pnl": net,
            "profit_factor": pf,
            "expectancy": expectancy
        })

    print(f"{'Strategy Name':<20} {'Trades':<8} {'W / L':<10} {'Win %':<8} {'Gross P&L':<14} {'Fees':<12} {'Net P&L':<14} {'PF':<6} {'Expectancy':<10}")
    print("-" * 105)
    for r in results:
        print(f"{r['strategy']:<20} {r['trades']:<8} {r['wins']}/{r['losses']:<8} {r['win_rate']:>5.1f}%   ₹{r['gross_pnl']:>10,.2f}  ₹{r['fees']:>8,.2f}  ₹{r['net_pnl']:>10,.2f}   {r['profit_factor']:<6} ₹{r['expectancy']:>6,.2f}")
    print("=" * 105)

    # Save benchmark JSON
    with open(TRADER_DIR / "strategy_benchmark_comparison.json", "w", encoding="utf-8") as bf:
        json.dump(results, bf, indent=2)

if __name__ == "__main__":
    main()
