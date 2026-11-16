import json
import math
import glob
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime, time

BASE_DIR = Path("d:/Trade-agent/day-trader-skill")
LEDGER_FNO = BASE_DIR / "trader" / "ledger_fno.json"
HISTORICAL_DIR = BASE_DIR / "historical_data"

def run_phase1_reproduction():
    with open(LEDGER_FNO, "r") as f:
        ledger = json.load(f)
    
    trades = ledger.get("trade_history", [])
    
    fallback_trades = []
    live_trades = []
    
    for t in trades:
        entry_p = float(t.get("entry_price", 0))
        is_synthetic = (
            entry_p in [220.0, 85.0] or 
            t.get("audit_status") == "SYNTHETIC_FALLBACK_PRICE" or
            not t.get("verified_market_data", False)
        )
        if is_synthetic:
            fallback_trades.append(t)
        else:
            live_trades.append(t)
            
    def compute_metrics(trade_list, name="Dataset"):
        if not trade_list:
            return {"name": name, "count": 0}
            
        total_trades = len(trade_list)
        wins = [t for t in trade_list if float(t.get("pnl", 0)) > 0]
        losses = [t for t in trade_list if float(t.get("pnl", 0)) < 0]
        breakevens = [t for t in trade_list if float(t.get("pnl", 0)) == 0]
        
        n_wins = len(wins)
        n_losses = len(losses)
        win_rate = (n_wins / total_trades) * 100.0 if total_trades > 0 else 0.0
        
        gross_profit = sum(float(t.get("gross_pnl", 0)) for t in wins)
        gross_loss = abs(sum(float(t.get("gross_pnl", 0)) for t in losses))
        
        net_pnls = [float(t.get("pnl", 0)) for t in trade_list]
        net_pnl = sum(net_pnls)
        
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        
        avg_win = (sum(float(t.get("pnl", 0)) for t in wins) / n_wins) if n_wins > 0 else 0.0
        avg_loss = (sum(float(t.get("pnl", 0)) for t in losses) / n_losses) if n_losses > 0 else 0.0
        
        expectancy = (win_rate/100.0 * avg_win) + ((1.0 - win_rate/100.0) * avg_loss)
        
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for p in net_pnls:
            cum_pnl += p
            if cum_pnl > peak:
                peak = cum_pnl
            dd = peak - cum_pnl
            if dd > max_dd:
                max_dd = dd
                
        sl_hits = 0
        tgt_hits = 0
        supertrend_exits = 0
        manual_exits = 0
        
        durations = []
        for t in trade_list:
            reason = str(t.get("reason", "")).lower()
            if "stop-loss" in reason or "sl" in reason or "stop" in reason:
                sl_hits += 1
            elif "target" in reason or "tgt" in reason:
                tgt_hits += 1
            elif "supertrend" in reason:
                supertrend_exits += 1
            else:
                manual_exits += 1
            
            # Duration parse if available
            try:
                e_t = t.get("entry_time", "")
                x_t = t.get("exit_time", "")
                if ":" in e_t and ":" in x_t:
                    # extract HH:MM
                    e_parts = e_t.split(":")[:2]
                    x_parts = x_t.split()[1].split(":")[:2] if " " in x_t else x_t.split(":")[:2]
                    e_m = int(e_parts[0])*60 + int(e_parts[1])
                    x_m = int(x_parts[0])*60 + int(x_parts[1])
                    if x_m >= e_m:
                        durations.append(x_m - e_m)
            except Exception:
                pass
                
        avg_duration = (sum(durations) / len(durations)) if durations else 0.0
        sl_rate = (sl_hits / total_trades) * 100.0
        tgt_rate = (tgt_hits / total_trades) * 100.0
        
        total_fees = sum(float(t.get("fees", 0)) for t in trade_list)
        total_slippage = sum(float(t.get("slippage_applied", 0)) * float(t.get("qty", 0)) for t in trade_list)
        
        return {
            "name": name,
            "total_trades": total_trades,
            "wins": n_wins,
            "losses": n_losses,
            "breakevens": len(breakevens),
            "win_rate": round(win_rate, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "net_pnl": round(net_pnl, 2),
            "profit_factor": round(profit_factor, 2) if not math.isinf(profit_factor) else "inf",
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "max_drawdown": round(max_dd, 2),
            "sl_hits": sl_hits,
            "sl_rate": round(sl_rate, 2),
            "tgt_hits": tgt_hits,
            "tgt_rate": round(tgt_rate, 2),
            "supertrend_exits": supertrend_exits,
            "manual_exits": manual_exits,
            "avg_holding_duration_mins": round(avg_duration, 1),
            "total_fees": round(total_fees, 2),
            "total_slippage": round(total_slippage, 2)
        }

    fb_metrics = compute_metrics(fallback_trades, "Fallback/Synthetic Pricing")
    live_metrics = compute_metrics(live_trades, "Live Verified Market Quotes")
    all_metrics = compute_metrics(trades, "Combined Complete Dataset")
    
    return fb_metrics, live_metrics, all_metrics, live_trades

def run_adx_midday_study():
    files = list(HISTORICAL_DIR.glob("*_4days.csv"))
    
    results = {}
    aggregated = {
        "total_bars": 0,
        "adx_ge_20": 0,
        "adx_ge_25": 0,
        "adx_ge_30": 0,
        "adx_lt_20_sideways": 0,
        "morning_bars": 0,
        "midday_bars": 0,
        "afternoon_bars": 0,
        "midday_adx_ge_25": 0,
        "midday_adx_lt_20": 0,
        "atr_expansion_ge_1_1": 0,
        "atr_contraction_le_0_9": 0,
        "high_vol_count": 0
    }
    
    for fpath in files:
        symbol = fpath.stem.replace("_4days", "")
        df = pd.read_csv(fpath)
        if 'timestamp' in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'])
        elif 'datetime' in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        else:
            continue
            
        df = df.sort_values('datetime').reset_index(drop=True)
        
        high = df['high']
        low = df['low']
        close = df['close']
        
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr14 = tr.rolling(14).mean()
        atr_sma20 = atr14.rolling(20).mean()
        atr_ratio = atr14 / (atr_sma20 + 1e-9)
        
        # Calculate ADX(14)
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        
        plus_di = 100 * (pd.Series(plus_dm).rolling(14).mean() / (atr14 + 1e-9))
        minus_di = 100 * (pd.Series(minus_dm).rolling(14).mean() / (atr14 + 1e-9))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx14 = dx.rolling(14).mean()
        
        df['atr14'] = atr14
        df['atr_ratio'] = atr_ratio
        df['adx14'] = adx14
        
        df['time'] = df['datetime'].dt.time
        morn_start = time(9, 15)
        morn_end = time(11, 30)
        mid_start = time(11, 30)
        mid_end = time(13, 45)
        aft_start = time(13, 45)
        aft_end = time(15, 30)
        
        df['is_morning'] = df['time'].apply(lambda t: morn_start <= t < mid_start)
        df['is_midday'] = df['time'].apply(lambda t: mid_start <= t < aft_start)
        df['is_afternoon'] = df['time'].apply(lambda t: aft_start <= t <= aft_end)
        
        df_valid = df.dropna().copy()
        n_bars = len(df_valid)
        if n_bars == 0:
            continue
            
        sym_res = {
            "bars": n_bars,
            "adx_ge_20": int((df_valid['adx14'] >= 20).sum()),
            "adx_ge_25": int((df_valid['adx14'] >= 25).sum()),
            "adx_ge_30": int((df_valid['adx14'] >= 30).sum()),
            "adx_lt_20": int((df_valid['adx14'] < 20).sum()),
            "midday_bars": int(df_valid['is_midday'].sum()),
            "midday_adx_ge_25": int(((df_valid['is_midday']) & (df_valid['adx14'] >= 25)).sum()),
            "midday_adx_lt_20": int(((df_valid['is_midday']) & (df_valid['adx14'] < 20)).sum()),
            "atr_expansion": int((df_valid['atr_ratio'] >= 1.15).sum()),
            "atr_contraction": int((df_valid['atr_ratio'] <= 0.85).sum()),
        }
        results[symbol] = sym_res
        
        aggregated["total_bars"] += n_bars
        aggregated["adx_ge_20"] += sym_res["adx_ge_20"]
        aggregated["adx_ge_25"] += sym_res["adx_ge_25"]
        aggregated["adx_ge_30"] += sym_res["adx_ge_30"]
        aggregated["adx_lt_20_sideways"] += sym_res["adx_lt_20"]
        aggregated["morning_bars"] += int(df_valid['is_morning'].sum())
        aggregated["midday_bars"] += sym_res["midday_bars"]
        aggregated["afternoon_bars"] += int(df_valid['is_afternoon'].sum())
        aggregated["midday_adx_ge_25"] += sym_res["midday_adx_ge_25"]
        aggregated["midday_adx_lt_20"] += sym_res["midday_adx_lt_20"]
        aggregated["atr_expansion_ge_1_1"] += sym_res["atr_expansion"]
        aggregated["atr_contraction_le_0_9"] += sym_res["atr_contraction"]
        
    return results, aggregated

if __name__ == "__main__":
    fb, live, comb, live_trades = run_phase1_reproduction()
    sym_res, agg = run_adx_midday_study()
    
    out = {
        "fallback_metrics": fb,
        "live_metrics": live,
        "combined_metrics": comb,
        "aggregated_adx_study": agg,
        "symbols_tested": list(sym_res.keys())
    }
    print(json.dumps(out, indent=2))
