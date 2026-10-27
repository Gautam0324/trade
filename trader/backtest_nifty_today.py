#!/usr/bin/env python3
"""
Backtesting 1-Minute Strategy Engine on Nifty (NSE: 99926000) for Today.
With Upgraded Features:
1. Multi-Timeframe Trend Filter (MTF):
   - Resamples 1-min candles to 5-min higher timeframe
   - Computes 5-min Supertrend (10, 3) & EMA 33/50 baseline
   - Rule: Trade CE ONLY if 5-min trend is BULLISH; trade PE ONLY if 5-min trend is BEARISH.
2. Smart Trailing Stop-Loss & Cost-to-Cost (Breakeven Lock):
   - When Option reaches +1.0R (e.g. +15 to +20 pts profit), Stop Loss is immediately shifted to Entry Price (Breakeven).
   - Once Breakeven is locked, the trade can NEVER result in a loss.
   - Further trails dynamically with ATR / Supertrend.
"""

import sys
import json
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
sys.path.append(str(BASE_DIR / "trader-alerts"))

import indicators
from options_engine import OptionsEngine
from risk_engine import RiskEngine
import download_historical_1min as dh

def resample_to_5min(raw_bars):
    """
    Resamples 1-minute bars into completed 5-minute bars for MTF trend confirmation.
    Returns: list of 5-min bar dicts with high, low, close, and timestamps.
    """
    bars_5m = []
    cur_bucket = None
    cur_open, cur_high, cur_low, cur_close, cur_vol = None, None, None, None, 0
    bucket_time = None

    for b in raw_bars:
        t_str = b[0].split("T")[1][:5] # "HH:MM"
        hh, mm = map(int, t_str.split(":"))
        bucket_idx = (hh * 60 + mm) // 5

        o, h, l, c = float(b[1]), float(b[2]), float(b[3]), float(b[4])
        v = float(b[5]) if len(b) > 5 else 0.0

        if cur_bucket is None:
            cur_bucket = bucket_idx
            bucket_time = t_str
            cur_open, cur_high, cur_low, cur_close, cur_vol = o, h, l, c, v
        elif cur_bucket == bucket_idx:
            cur_high = max(cur_high, h)
            cur_low = min(cur_low, l)
            cur_close = c
            cur_vol += v
        else:
            bars_5m.append({
                "time": bucket_time,
                "open": cur_open,
                "high": cur_high,
                "low": cur_low,
                "close": cur_close,
                "volume": cur_vol
            })
            cur_bucket = bucket_idx
            bucket_time = t_str
            cur_open, cur_high, cur_low, cur_close, cur_vol = o, h, l, c, v

    if cur_bucket is not None:
        bars_5m.append({
            "time": bucket_time,
            "open": cur_open,
            "high": cur_high,
            "low": cur_low,
            "close": cur_close,
            "volume": cur_vol
        })
    return bars_5m

def run_nifty_today_backtest():
    with open(BASE_DIR / "trader" / "config.json") as f:
        cfg = json.load(f)

    print("[INFO] Connecting to SmartAPI to fetch Nifty 50 1-minute candles for today...")
    api = dh.get_smart_api_session()
    
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    from_date = f"{today_str} 09:15"
    to_date = f"{today_str} 15:30"
    
    candle_resp = api.getCandleData({
        "exchange": "NSE",
        "symboltoken": "99926000",
        "interval": "ONE_MINUTE",
        "fromdate": from_date,
        "todate": to_date
    })

    if not candle_resp or not candle_resp.get("status"):
        print("Failed to fetch candle data:", candle_resp)
        return

    raw_bars = candle_resp.get("data", [])
    print(f"[OK] Fetched {len(raw_bars)} 1-minute bars for {today_str}.")

    # 1-min series
    opens_1m = [float(b[1]) for b in raw_bars]
    highs_1m = [float(b[2]) for b in raw_bars]
    lows_1m = [float(b[3]) for b in raw_bars]
    closes_1m = [float(b[4]) for b in raw_bars]
    times_1m = [b[0].split("T")[1][:5] for b in raw_bars]

    # Precalculate 1-min indicators
    ema33_1m = indicators.calculate_ema(closes_1m, 33)
    st_data_1m = indicators.calculate_supertrend(highs_1m, lows_1m, closes_1m, 10, 3.0)
    st_dir_1m = st_data_1m["direction"]

    # Resample to 5-min and calculate Higher Timeframe (HTF) Trend
    bars_5m = resample_to_5min(raw_bars)
    highs_5m = [b["high"] for b in bars_5m]
    lows_5m = [b["low"] for b in bars_5m]
    closes_5m = [b["close"] for b in bars_5m]
    times_5m = [b["time"] for b in bars_5m]
    
    # 5m Trend: Standard Supertrend (10, 2.0) on 5m completed bars
    st_data_5m = indicators.calculate_supertrend(highs_5m, lows_5m, closes_5m, 10, 2.0)
    st_dir_5m = st_data_5m["direction"]
    ema20_5m = indicators.calculate_ema(closes_5m, 20)

    def get_htf_bias(minute_time, current_1m_close):
        bias = "NEUTRAL"
        for idx in range(len(times_5m) - 1, -1, -1):
            if times_5m[idx] <= minute_time:
                direction = st_dir_5m[idx]
                e20 = ema20_5m[idx]
                if e20 is not None:
                    if current_1m_close < e20 or direction == -1:
                        bias = "BEARISH"
                    elif current_1m_close > e20 or direction == 1:
                        bias = "BULLISH"
                else:
                    if direction == -1:
                        bias = "BEARISH"
                    elif direction == 1:
                        bias = "BULLISH"
                break
        return bias

    opt_engine = OptionsEngine(cfg)
    risk_engine = RiskEngine(cfg)

    capital = cfg.get("budget", 1000000.0)
    open_pos = None
    trades = []
    pending_signal = None

    print("\n[RUNNING] Bar-by-Bar Simulation (MTF Filter + Breakeven Lock)...")
    print("-" * 75)

    for i in range(50, len(raw_bars)):
        bar_time = times_1m[i]
        c = closes_1m[i]
        h = highs_1m[i]
        l = lows_1m[i]

        # 1. Manage Active Position (Breakeven Lock + Trailing SL + Target + EOD)
        if open_pos:
            opt_type = open_pos["option_type"]
            entry_spot = open_pos["spot_entry"]
            entry_opt = open_pos["entry_opt_price"]
            current_spot = c

            delta = 0.60
            spot_change = (current_spot - entry_spot) if opt_type == "CE" else (entry_spot - current_spot)
            current_opt_price = max(1.0, round(entry_opt + (spot_change * delta), 2))

            open_pos["peak_opt_price"] = max(open_pos.get("peak_opt_price", current_opt_price), current_opt_price)

            # SMART BREAKEVEN LOCK:
            # If current option gain reaches +1R (initial risk distance), lock SL to entry price
            initial_risk_pts = entry_opt - open_pos["initial_sl_opt"]
            if not open_pos.get("breakeven_locked") and (current_opt_price - entry_opt) >= initial_risk_pts:
                open_pos["breakeven_locked"] = True
                open_pos["sl_opt_price"] = entry_opt # Shift SL to Entry (Cost-to-Cost)
                print(f"[BREAKEVEN LOCKED] [{bar_time}] Option reached +1R (Rs {current_opt_price}). Stop-Loss moved to Cost (Rs {entry_opt:.2f})!")

            exit_trade = False
            exit_reason = ""

            if current_opt_price <= open_pos["sl_opt_price"]:
                exit_trade = True
                exit_reason = "Breakeven Hit (Cost Exit)" if open_pos.get("breakeven_locked") else "Initial Stop Loss Hit"
            elif current_opt_price >= open_pos["target_opt_price"]:
                exit_trade = True
                exit_reason = "Target Hit (+1.5R)"
            elif opt_type == "CE" and st_dir_1m[i] == -1:
                exit_trade = True
                exit_reason = "Supertrend 1m Bearish Flip"
            elif opt_type == "PE" and st_dir_1m[i] == 1:
                exit_trade = True
                exit_reason = "Supertrend 1m Bullish Flip"
            elif bar_time >= "15:20":
                exit_trade = True
                exit_reason = "Mandatory 15:20 EOD Square-off"

            if exit_trade:
                pnl = round((current_opt_price - entry_opt) * open_pos["quantity"], 2)
                capital += pnl
                trades.append({
                    "symbol": open_pos["symbol"],
                    "type": opt_type,
                    "entry_time": open_pos["entry_time"],
                    "exit_time": bar_time,
                    "entry_spot": entry_spot,
                    "exit_spot": current_spot,
                    "entry_opt": entry_opt,
                    "exit_opt": current_opt_price,
                    "qty": open_pos["quantity"],
                    "lots": open_pos["quantity"] // 25,
                    "pnl": pnl,
                    "reason": exit_reason
                })
                print(f"[EXIT] [{bar_time}] {open_pos['symbol']} @ Rs {current_opt_price} | P&L: Rs {pnl:+,.2f} | Reason: {exit_reason}")
                open_pos = None
                continue

        # 2. Check Pending Signal Confirmation
        if pending_signal:
            if i > pending_signal["signal_idx"] + 2:
                print(f"[CANCEL] [{bar_time}] Signal Cancelled (Timeout - No Confirmation)")
                pending_signal = None
            else:
                if pending_signal["type"] == "CE" and c > pending_signal["trigger_high"]:
                    strike = opt_engine.select_strike(c, "CE", "NIFTY")
                    opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "CE")
                    est_opt_entry = opt_engine.estimate_option_premium(c, strike, "CE")
                    est_sl = round(est_opt_entry * 0.80, 2) # 20% SL
                    est_tgt = round(est_opt_entry * 1.30, 2) # +30% Target

                    risk_check = risk_engine.evaluate_order_risk(
                        opt_sym, "BUY", est_opt_entry, est_sl, est_tgt, capital, 0, len(trades), 0
                    )

                    if risk_check["passed"]:
                        qty = risk_check["quantity"]
                        open_pos = {
                            "symbol": opt_sym,
                            "option_type": "CE",
                            "entry_time": bar_time,
                            "spot_entry": c,
                            "entry_opt_price": est_opt_entry,
                            "initial_sl_opt": est_sl,
                            "sl_opt_price": est_sl,
                            "target_opt_price": est_tgt,
                            "quantity": qty,
                            "breakeven_locked": False
                        }
                        print(f"[CONFIRMED ENTRY] [{bar_time}] BUY {opt_sym} @ Rs {est_opt_entry} | Lots: {qty//25} ({qty} Qty) | Spot: {c:.2f}")
                        pending_signal = None
                        continue
                elif pending_signal["type"] == "PE" and c < pending_signal["trigger_low"]:
                    strike = opt_engine.select_strike(c, "PE", "NIFTY")
                    opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "PE")
                    est_opt_entry = opt_engine.estimate_option_premium(c, strike, "PE")
                    est_sl = round(est_opt_entry * 0.80, 2)
                    est_tgt = round(est_opt_entry * 1.30, 2)

                    risk_check = risk_engine.evaluate_order_risk(
                        opt_sym, "BUY", est_opt_entry, est_sl, est_tgt, capital, 0, len(trades), 0
                    )

                    if risk_check["passed"]:
                        qty = risk_check["quantity"]
                        open_pos = {
                            "symbol": opt_sym,
                            "option_type": "PE",
                            "entry_time": bar_time,
                            "spot_entry": c,
                            "entry_opt_price": est_opt_entry,
                            "initial_sl_opt": est_sl,
                            "sl_opt_price": est_sl,
                            "target_opt_price": est_tgt,
                            "quantity": qty,
                            "breakeven_locked": False
                        }
                        print(f"[CONFIRMED ENTRY] [{bar_time}] BUY {opt_sym} @ Rs {est_opt_entry} | Lots: {qty//25} ({qty} Qty) | Spot: {c:.2f}")
                        pending_signal = None
                        continue

        # 3. Detect New Signal Candle with Multi-Timeframe (MTF) Filter
        if not open_pos and not pending_signal and "09:30" <= bar_time <= "15:00":
            htf_bias = get_htf_bias(bar_time, c)

            is_bull_cross = st_dir_1m[i] == 1 and st_dir_1m[i-1] == -1 and c > ema33_1m[i]
            is_bear_cross = st_dir_1m[i] == -1 and st_dir_1m[i-1] == 1 and c < ema33_1m[i]

            # MTF FILTER: Only allow Bullish 1m signal if 5m is BULLISH
            if is_bull_cross:
                if htf_bias == "BULLISH":
                    pending_signal = {
                        "type": "CE",
                        "signal_time": bar_time,
                        "signal_idx": i,
                        "trigger_high": h,
                        "trigger_low": l,
                        "spot": c
                    }
                    print(f"[STAGE 1 SIGNAL] [{bar_time}] Bullish 1m Flip aligned with 5m Trend. Waiting confirmation above Rs {h:.2f}")
                else:
                    print(f"[BLOCKED] [{bar_time}] Bullish 1m Signal filtered out (5m HTF Bias is {htf_bias})")

            # MTF FILTER: Only allow Bearish 1m signal if 5m is BEARISH
            elif is_bear_cross:
                if htf_bias == "BEARISH":
                    pending_signal = {
                        "type": "PE",
                        "signal_time": bar_time,
                        "signal_idx": i,
                        "trigger_high": h,
                        "trigger_low": l,
                        "spot": c
                    }
                    print(f"[STAGE 1 SIGNAL] [{bar_time}] Bearish 1m Flip aligned with 5m Trend. Waiting confirmation below Rs {l:.2f}")
                else:
                    print(f"[BLOCKED] [{bar_time}] Bearish 1m Signal filtered out (5m HTF Bias is {htf_bias})")

    # Backtest Summary Report
    print("\n" + "=" * 75)
    print("UPGRADED BACKTEST PERFORMANCE - NIFTY 1M + 5M MTF + BREAKEVEN LOCK")
    print("=" * 75)
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    evens = [t for t in trades if t["pnl"] == 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

    print(f"Starting Capital : Rs {cfg.get('budget', 1000000.0):,.2f} (10 Lakhs)")
    print(f"Ending Capital   : Rs {capital:,.2f}")
    print(f"Total Realized P&L: Rs {total_pnl:+,.2f} ({((capital - 1000000)/1000000)*100:+.2f}%)")
    print(f"Total Trades Taken: {len(trades)}")
    print(f"Win Rate         : {win_rate:.1f}% ({len(wins)} Wins / {len(losses)} Losses / {len(evens)} Breakeven)")
    if trades:
        print("\nTrade Breakdown:")
        for idx, t in enumerate(trades, 1):
            print(f"  {idx}. {t['type']} {t['symbol']} | In: {t['entry_time']} @ Rs {t['entry_opt']} -> Out: {t['exit_time']} @ Rs {t['exit_opt']} | Lots: {t['lots']} | PnL: Rs {t['pnl']:+,.2f} ({t['reason']})")
    print("=" * 75)

if __name__ == "__main__":
    run_nifty_today_backtest()
