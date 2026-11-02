#!/usr/bin/env python3
"""
Day-Wise Historical Backtest Runner with Complete JSON Audit Export
Evaluates past 4 trading sessions: 2026-10-28, 2026-10-29, 2026-10-30, 2026-11-02
Exports full detailed JSON to trader/backtest_detailed_4days.json
"""

import os
import sys
import io
import json
import pandas as pd
from pathlib import Path

# Force UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TRADER_DIR = Path(__file__).resolve().parent
BASE_DIR = TRADER_DIR.parent
HIST_DIR = BASE_DIR / "historical_data"
JSON_OUT_PATH = TRADER_DIR / "backtest_detailed_4days.json"
sys.path.insert(0, str(TRADER_DIR))

from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from cost_model import CostModel

STOCKS = [
    "RELIANCE", "TCS", "INFY", "SBIN",
    "ICICIBANK", "BAJFINANCE", "TATASTEEL", "HDFCBANK"
]

DAYS = ["2026-10-28", "2026-10-29", "2026-10-30", "2026-11-02"]

def calc_holding_minutes(t_entry, t_exit):
    try:
        e_h, e_m = map(int, t_entry.split(":"))
        x_h, x_m = map(int, t_exit.split(":"))
        return (x_h * 60 + x_m) - (e_h * 60 + e_m)
    except Exception:
        return 0

def evaluate_day_for_stock(df, day, symbol, strat_eng, cost_model, trade_counter_ref, starting_capital=1000000.0):
    day_indices = df[df['date'] == day].index.tolist()
    if not day_indices or len(day_indices) < 20:
        return []

    # Include up to 60 bars before market open for indicator warm-up
    start_idx = max(0, day_indices[0] - 60)
    end_idx = day_indices[-1]

    sub_df = df.iloc[start_idx:end_idx+1].reset_index(drop=True)
    first_today_idx = day_indices[0] - start_idx

    risk_eng = RiskEngine({"budget": starting_capital})
    cash = starting_capital
    daily_trades = 0
    trades = []
    pos = None

    for i in range(first_today_idx, len(sub_df)):
        row = sub_df.iloc[i]
        ts = str(row['timestamp'])
        time_str = ts.split('T')[1][:5] if 'T' in ts else ts.split(' ')[1][:5]
        cur_c = float(row['close'])
        cur_h = float(row['high'])
        cur_l = float(row['low'])
        cur_o = float(row['open'])

        # 1. Open Position Exit Management
        if pos is not None:
            side = pos['side']
            sl = pos['sl']
            tgt = pos['tgt']
            qty = pos['qty']
            entry_p = pos['entry_p']

            exit_p = None
            exit_reason = None

            if side == 'BUY':
                if cur_l <= sl:
                    exit_p = cur_o if cur_o < sl else sl
                    exit_reason = f"🛑 Stop-Loss Hit (Trigger: Low touched ₹{cur_l:.2f} <= SL ₹{sl:.2f})"
                elif cur_h >= tgt:
                    exit_p = cur_o if cur_o > tgt else tgt
                    exit_reason = f"🎯 Target Hit (+1.5R) (Trigger: High reached ₹{cur_h:.2f} >= Target ₹{tgt:.2f})"
                elif time_str >= '15:20':
                    exit_p = cur_c
                    exit_reason = "⏰ EOD Mandatory Square-Off (15:20 IST reached, Intraday Flat)"
            else: # SELL
                if cur_h >= sl:
                    exit_p = cur_o if cur_o > sl else sl
                    exit_reason = f"🛑 Stop-Loss Hit (Trigger: High touched ₹{cur_h:.2f} >= SL ₹{sl:.2f})"
                elif cur_l <= tgt:
                    exit_p = cur_o if cur_o < tgt else tgt
                    exit_reason = f"🎯 Target Hit (+1.5R) (Trigger: Low dropped ₹{cur_l:.2f} <= Target ₹{tgt:.2f})"
                elif time_str >= '15:20':
                    exit_p = cur_c
                    exit_reason = "⏰ EOD Mandatory Square-Off (15:20 IST reached, Intraday Flat)"

            if exit_p is not None:
                costs = cost_model.calculate_roundtrip_cost(side, entry_p, exit_p, qty)
                entry_costs = costs.get("entry_costs", {})
                exit_costs = costs.get("exit_costs", {})
                
                total_fees = costs['total_costs']
                fee_breakdown = {
                    "brokerage": round(entry_costs.get("brokerage", 0.0) + exit_costs.get("brokerage", 0.0), 2),
                    "stt": round(entry_costs.get("stt", 0.0) + exit_costs.get("stt", 0.0), 2),
                    "exchange_txn": round(entry_costs.get("exchange_txn", 0.0) + exit_costs.get("exchange_txn", 0.0), 2),
                    "gst": round(entry_costs.get("gst", 0.0) + exit_costs.get("gst", 0.0), 2),
                    "stamp_duty": round(entry_costs.get("stamp_duty", 0.0) + exit_costs.get("stamp_duty", 0.0), 2),
                    "slippage": round(entry_costs.get("slippage", 0.0) + exit_costs.get("slippage", 0.0), 2),
                    "total_costs": total_fees
                }

                trade_counter_ref[0] += 1
                t_num = trade_counter_ref[0]
                risk_pts = abs(entry_p - sl)
                r_mult = round((exit_p - entry_p) / risk_pts, 2) if side == 'BUY' else round((entry_p - exit_p) / risk_pts, 2)
                holding_mins = calc_holding_minutes(pos['entry_time'], time_str)

                trades.append({
                    "id": f"BT-{t_num:03d}-{symbol}-{day.replace('-', '')}",
                    "trade_number": t_num,
                    "date": day,
                    "symbol": symbol,
                    "side": side,
                    "quantity": qty,
                    "entry_time": pos['entry_time'],
                    "entry_price": entry_p,
                    "exit_time": time_str,
                    "exit_price": exit_p,
                    "stop_loss": sl,
                    "target_price": tgt,
                    "holding_time_minutes": holding_mins,
                    "gross_pnl": costs['gross_pnl'],
                    "fees": total_fees,
                    "net_pnl": costs['net_pnl'],
                    "r_multiple": r_mult,
                    "pnl_status": "WIN" if costs['net_pnl'] > 0 else "LOSS",
                    "entry_reason": pos['entry_reason'],
                    "exit_reason": exit_reason,
                    "setup_description": pos['setup_description'],
                    "indicators_at_entry": pos['indicators'],
                    "fees_breakdown": fee_breakdown
                })
                cash += (qty * exit_p if side == 'BUY' else qty * (2 * entry_p - exit_p)) - total_fees
                pos = None

        # 2. New Entry Evaluation
        if pos is None and time_str < '15:10' and daily_trades < 6:
            sliced = {
                'timestamp': sub_df['timestamp'].iloc[:i+1].tolist(),
                'open': sub_df['open'].iloc[:i+1].tolist(),
                'high': sub_df['high'].iloc[:i+1].tolist(),
                'low': sub_df['low'].iloc[:i+1].tolist(),
                'close': sub_df['close'].iloc[:i+1].tolist(),
                'volume': sub_df['volume'].iloc[:i+1].tolist()
            }
            sig = strat_eng.evaluate(symbol, sliced)
            if sig and sig.get('direction') in ('LONG', 'SHORT'):
                side = 'BUY' if sig['direction'] == 'LONG' else 'SELL'
                sl_p = sig.get('stop_loss', cur_l if side == 'BUY' else cur_h)
                tgt_p = sig.get('target_price', cur_c * 1.015 if side == 'BUY' else cur_c * 0.985)

                if abs(cur_c - sl_p) < (cur_c * 0.001):
                    sl_p = round(cur_c * 0.995 if side == 'BUY' else cur_c * 1.005, 2)
                if abs(cur_c - tgt_p) < (cur_c * 0.002):
                    tgt_p = round(cur_c * 1.010 if side == 'BUY' else cur_c * 0.990, 2)

                r_eval = risk_eng.evaluate_order_risk(symbol, side, cur_c, sl_p, tgt_p, cash, 0.0, daily_trades, 0)
                if r_eval['passed'] and r_eval['quantity'] > 0:
                    qty = r_eval['quantity']
                    
                    # Detailed entry reason
                    ind_vals = sig.get("indicator_values", {})
                    st_val = ind_vals.get("supertrend", "N/A")
                    ema5 = ind_vals.get("ema5", "N/A")
                    ema9 = ind_vals.get("ema9", "N/A")
                    ema33 = ind_vals.get("ema33_high" if side == 'BUY' else "ema33_low", "N/A")
                    vwap = ind_vals.get("vwap", "N/A")
                    rsi = ind_vals.get("rsi", "N/A")
                    confl_score = sig.get("confluence_score", "N/A")

                    entry_reason = (
                        f"CONFLUENCE Setup: Supertrend {'Bullish (+1)' if side == 'BUY' else 'Bearish (-1)'} (val={st_val}), "
                        f"EMA 5 ({ema5}) {'>' if side == 'BUY' else '<'} EMA 9 ({ema9}) {'>' if side == 'BUY' else '<'} EMA 33 ({ema33}), "
                        f"Price (₹{cur_c:.2f}) {'>' if side == 'BUY' else '<'} VWAP ({vwap}), "
                        f"RSI={rsi}, Confluence Score={confl_score}"
                    )

                    pos = {
                        'side': side,
                        'entry_p': cur_c,
                        'sl': sl_p,
                        'tgt': tgt_p,
                        'qty': qty,
                        'entry_time': time_str,
                        'entry_reason': entry_reason,
                        'setup_description': sig.get("setup_description", "Confluence Signal"),
                        'indicators': ind_vals
                    }
                    daily_trades += 1
                    cash -= (qty * cur_c)

    return trades

def main():
    print("=" * 80)
    print("RUNNING COMPLETE DETAILED JSON EXPORT FOR 4-DAY BACKTEST")
    print("=" * 80)

    strat_eng = StrategyEngine({'strategy_mode': 'CONFLUENCE'})
    cost_model = CostModel({})

    stock_dfs = {}
    for sym in STOCKS:
        csv_p = HIST_DIR / f"{sym}_4days.csv"
        if csv_p.exists():
            df = pd.read_csv(csv_p)
            df['date'] = df['timestamp'].apply(lambda x: str(x).split('T')[0] if 'T' in str(x) else str(x).split(' ')[0])
            stock_dfs[sym] = df

    all_trades = []
    trade_counter = [0]
    day_summaries = []

    for day in DAYS:
        day_trades = []
        stock_stats = {}

        for sym in STOCKS:
            df = stock_dfs.get(sym)
            if df is None:
                continue
            t_list = evaluate_day_for_stock(df, day, sym, strat_eng, cost_model, trade_counter)
            if t_list:
                day_trades.extend(t_list)
                all_trades.extend(t_list)
                net = sum(t['net_pnl'] for t in t_list)
                wins = sum(1 for t in t_list if t['net_pnl'] > 0)
                losses = sum(1 for t in t_list if t['net_pnl'] <= 0)
                stock_stats[sym] = {
                    "count": len(t_list),
                    "wins": wins,
                    "losses": losses,
                    "net_pnl": round(net, 2),
                    "gross_pnl": round(sum(t['gross_pnl'] for t in t_list), 2),
                    "fees": round(sum(t['fees'] for t in t_list), 2)
                }

        total_t = len(day_trades)
        wins = sum(1 for t in day_trades if t['net_pnl'] > 0)
        losses = sum(1 for t in day_trades if t['net_pnl'] <= 0)
        wr = round(wins / total_t * 100, 1) if total_t > 0 else 0.0
        gross = round(sum(t['gross_pnl'] for t in day_trades), 2)
        fees = round(sum(t['fees'] for t in day_trades), 2)
        net = round(sum(t['net_pnl'] for t in day_trades), 2)

        day_summaries.append({
            "date": day,
            "trades_count": total_t,
            "wins": wins,
            "losses": losses,
            "win_rate_pct": wr,
            "gross_pnl": gross,
            "fees_and_taxes": fees,
            "net_pnl": net,
            "stock_performance": stock_stats
        })

    # Global stock aggregation
    stock_summaries = {}
    for sym in STOCKS:
        sym_trades = [t for t in all_trades if t["symbol"] == sym]
        if sym_trades:
            s_wins = sum(1 for t in sym_trades if t["net_pnl"] > 0)
            s_losses = sum(1 for t in sym_trades if t["net_pnl"] <= 0)
            s_gross = sum(t["gross_pnl"] for t in sym_trades)
            s_fees = sum(t["fees"] for t in sym_trades)
            s_net = sum(t["net_pnl"] for t in sym_trades)
            s_wr = round(s_wins / len(sym_trades) * 100, 1)
            stock_summaries[sym] = {
                "symbol": sym,
                "total_trades": len(sym_trades),
                "wins": s_wins,
                "losses": s_losses,
                "win_rate_pct": s_wr,
                "gross_pnl": round(s_gross, 2),
                "fees_and_taxes": round(s_fees, 2),
                "net_pnl": round(s_net, 2)
            }

    # Consolidated Metrics
    total_trades = len(all_trades)
    total_wins = sum(1 for t in all_trades if t['net_pnl'] > 0)
    total_losses = sum(1 for t in all_trades if t['net_pnl'] <= 0)
    total_wr = round(total_wins / total_trades * 100, 1) if total_trades else 0.0
    total_gross = round(sum(t['gross_pnl'] for t in all_trades), 2)
    total_fees = round(sum(t['fees'] for t in all_trades), 2)
    total_net = round(sum(t['net_pnl'] for t in all_trades), 2)

    gross_profit = sum(t['gross_pnl'] for t in all_trades if t['gross_pnl'] > 0)
    gross_loss = abs(sum(t['gross_pnl'] for t in all_trades if t['gross_pnl'] < 0))
    pf = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

    export_payload = {
        "metadata": {
            "title": "Day Trader Pro V2 -- Detailed 4-Day Historical Backtest",
            "strategy": "CONFLUENCE (Supertrend 10,3 + EMA 5/9/33 Channel + VWAP)",
            "timeframe": "1-minute completed bars",
            "sessions_evaluated": DAYS,
            "universe_symbols": STOCKS,
            "starting_capital_inr": 1000000.0,
            "total_trades_count": total_trades,
            "total_winning_trades": total_wins,
            "total_losing_trades": total_losses,
            "overall_win_rate_pct": total_wr,
            "profit_factor": pf,
            "total_gross_pnl": total_gross,
            "total_statutory_fees_and_taxes": total_fees,
            "total_net_pnl": total_net,
            "generated_at": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S IST")
        },
        "day_wise_summary": day_summaries,
        "stock_wise_summary": stock_summaries,
        "trades": all_trades
    }

    with open(JSON_OUT_PATH, "w", encoding="utf-8") as jf:
        json.dump(export_payload, jf, indent=2, ensure_ascii=False)

    print(f"Successfully generated detailed JSON with {total_trades} trades at:")
    print(f"  {JSON_OUT_PATH}")
    print(f"  File size: {os.path.getsize(JSON_OUT_PATH):,} bytes")

if __name__ == "__main__":
    main()
