#!/usr/bin/env python3
"""
Diagnostic Script for Phase 1.5 - F&O Strategy Failure Attribution.
Processes all 163 F&O trades from ledger_fno.json and orders_lifecycle.db.
"""

import json
import sqlite3
import datetime
import math
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LEDGER_PATH = BASE_DIR / "trader" / "ledger_fno.json"
DB_PATH = BASE_DIR / "trader" / "orders_lifecycle.db"

def parse_time(dt_str, tm_str):
    if not tm_str:
        return None
    tm_clean = tm_str.replace("IST", "").strip()
    if len(tm_clean) == 5: # HH:MM
        tm_clean += ":00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.datetime.strptime(f"{dt_str} {tm_clean}", fmt)
        except Exception:
            pass
    return None

def run_analysis():
    with open(LEDGER_PATH, "r", encoding="utf-8") as f:
        ledger = json.load(f)
    trades = ledger.get("trade_history", [])

    fallback_trades = []
    live_trades = []

    for t in trades:
        src = t.get("quote_source")
        verified = t.get("verified_market_data", False)
        # Classify as live if verified quote from live Angel One API
        if verified and src in ("ANGEL_MARKET_DATA", "ANGEL_LTP_API", "ANGEL_FULL_API", "ANGEL_CANDLE_CLOSE"):
            live_trades.append(t)
        else:
            fallback_trades.append(t)

    print(f"Total Trades: {len(trades)}")
    print(f"Fallback Trades: {len(fallback_trades)}")
    print(f"Live Trades: {len(live_trades)}")

    def compute_stats(t_list, label):
        n = len(t_list)
        if n == 0:
            return {}
        wins = [t for t in t_list if float(t.get("pnl", 0)) > 0]
        losses = [t for t in t_list if float(t.get("pnl", 0)) < 0]
        be = [t for t in t_list if float(t.get("pnl", 0)) == 0]

        win_rate = (len(wins) / n) * 100
        gross_profit = sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in wins)
        gross_loss = abs(sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in losses))
        net_pnl = sum(float(t.get("pnl", 0)) for t in t_list)
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (gross_profit if gross_profit > 0 else 1.0)
        
        avg_win = (sum(float(t.get("pnl", 0)) for t in wins) / len(wins)) if wins else 0.0
        avg_loss = (abs(sum(float(t.get("pnl", 0)) for t in losses)) / len(losses)) if losses else 0.0
        expectancy = ((len(wins)/n) * avg_win) - ((len(losses)/n) * avg_loss)

        # Drawdown calculation over cumulative curve
        cum = 0.0
        peak = 0.0
        max_dd = 0.0
        for t in t_list:
            cum += float(t.get("pnl", 0))
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

        # Reasons
        sl_hits = [t for t in t_list if "Stop-Loss Hit" in t.get("reason", "") or "Stop Loss" in t.get("reason", "")]
        tgt_hits = [t for t in t_list if "Target Hit" in t.get("reason", "")]
        st_reversals = [t for t in t_list if "Supertrend Reversal" in t.get("reason", "")]
        eod_exits = [t for t in t_list if "EOD" in t.get("reason", "") or "Squareoff" in t.get("reason", "") or "Manual" in t.get("reason", "")]

        sl_rate = (len(sl_hits) / n) * 100
        tgt_rate = (len(tgt_hits) / n) * 100

        # Holding duration
        durations = []
        for t in t_list:
            d_in = parse_time(t.get("date"), t.get("entry_time"))
            d_out = parse_time(t.get("exit_date", t.get("date")), t.get("exit_time"))
            if d_in and d_out and d_out >= d_in:
                durations.append((d_out - d_in).total_seconds() / 60.0) # minutes

        avg_dur = sum(durations) / len(durations) if durations else 0.0

        # Fees & Slippage
        total_fees = sum(float(t.get("fees", 0.0)) for t in t_list)
        total_slippage = sum(float(t.get("slippage_applied", 0.0)) * int(t.get("qty", 1)) for t in t_list)

        # MFE / MAE approximations based on peak_price and entry/exit
        # For long option: MFE = (peak_price - entry_price) / entry_price
        # MAE = min(0, (exit_price - entry_price) / entry_price)
        mfes = []
        maes = []
        for t in t_list:
            ep = float(t.get("entry_price", 0.0))
            xp = float(t.get("exit_price", 0.0))
            pk = float(t.get("peak_price", max(ep, xp)))
            if ep > 0:
                mfe_pct = max(0.0, (pk - ep) / ep) * 100.0
                mae_pct = min(0.0, (min(ep, xp) - ep) / ep) * 100.0
                mfes.append(mfe_pct)
                maes.append(mae_pct)

        avg_mfe = sum(mfes) / len(mfes) if mfes else 0.0
        avg_mae = sum(maes) / len(maes) if maes else 0.0

        return {
            "label": label,
            "total_trades": n,
            "wins": len(wins),
            "losses": len(losses),
            "be": len(be),
            "win_rate": round(win_rate, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "profit_factor": round(profit_factor, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "max_drawdown": round(max_dd, 2),
            "sl_hits": len(sl_hits),
            "sl_rate": round(sl_rate, 2),
            "tgt_hits": len(tgt_hits),
            "tgt_rate": round(tgt_rate, 2),
            "st_reversals": len(st_reversals),
            "eod_exits": len(eod_exits),
            "avg_duration_min": round(avg_dur, 2),
            "total_fees": round(total_fees, 2),
            "total_slippage": round(total_slippage, 2),
            "avg_mfe_pct": round(avg_mfe, 2),
            "avg_mae_pct": round(avg_mae, 2)
        }

    fb_stats = compute_stats(fallback_trades, "Fallback/Synthetic (Nov 2 - Nov 11)")
    live_stats = compute_stats(live_trades, "Live Pricing (Nov 12 - Nov 16)")

    print(json.dumps({"fallback": fb_stats, "live": live_stats}, indent=2))

    # Stop Loss timing breakdown on live trades
    print("\n=== STOP LOSS DURATION BREAKDOWN (LIVE TRADES) ===")
    sl_durations = []
    for t in live_trades:
        if "Stop-Loss Hit" in t.get("reason", "") or "Stop Loss" in t.get("reason", ""):
            d_in = parse_time(t.get("date"), t.get("entry_time"))
            d_out = parse_time(t.get("exit_date", t.get("date")), t.get("exit_time"))
            if d_in and d_out:
                mins = (d_out - d_in).total_seconds() / 60.0
                sl_durations.append((t, mins))

    total_sl = len(sl_durations)
    print(f"Total Stop Loss Hits in Live Trades: {total_sl}")
    within_1m = sum(1 for _, m in sl_durations if m <= 1.0)
    within_3m = sum(1 for _, m in sl_durations if m <= 3.0)
    within_5m = sum(1 for _, m in sl_durations if m <= 5.0)
    within_15m = sum(1 for _, m in sl_durations if m <= 15.0)
    over_15m = sum(1 for _, m in sl_durations if m > 15.0)

    print(f"<= 1 min : {within_1m}/{total_sl} ({within_1m/total_sl*100:.1f}%)" if total_sl else "")
    print(f"<= 3 mins: {within_3m}/{total_sl} ({within_3m/total_sl*100:.1f}%)" if total_sl else "")
    print(f"<= 5 mins: {within_5m}/{total_sl} ({within_5m/total_sl*100:.1f}%)" if total_sl else "")
    print(f"<= 15 mins: {within_15m}/{total_sl} ({within_15m/total_sl*100:.1f}%)" if total_sl else "")
    print(f"> 15 mins: {over_15m}/{total_sl} ({over_15m/total_sl*100:.1f}%)" if total_sl else "")

    for t, m in sl_durations:
        print(f"  SL Hit: {t.get('symbol')} | Entry: {t.get('entry_time')} | Exit: {t.get('exit_time')} | Dur: {m:.1f}m | Ep: {t.get('entry_price')} -> Xp: {t.get('exit_price')} | PnL: {t.get('pnl')}")

if __name__ == "__main__":
    run_analysis()
