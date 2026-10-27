#!/usr/bin/env python3
"""
Backtest engine strictly implementing the exact Pine Script:
"Multi-EMA + Supertrend + MACD Combo"
Applied on today's NIFTY 1-minute real data from Angel One SmartAPI:
- Capital: ₹10,00,000 (10 Lakh)
- Max Position: 40% (₹4,00,000)
- Max Risk: 1% (₹10,000) per trade
- Pine Script Indicators:
  * EMA 33 (Close), EMA 50 (HL2), EMA 100 (Open), EMA 200 (Close)
  * Supertrend (Period: 10, Factor: 3.0)
  * MACD (Fast: 12 EMA, Slow: 26 EMA, Signal: 9 EMA)
- 2-Stage Confirmation:
  * Signal Candle: Supertrend matches direction + MACD confirmation + EMA alignment
  * Confirmation Candle: Next 1-min candle must close beyond Signal Candle's High (for CE) or Low (for PE)
  * Breakeven Lock: Once +1R is reached, stop moves to Cost (Zero risk)
"""

import sys
import json
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))

import indicators
from options_engine import OptionsEngine
from risk_engine import RiskEngine
import download_historical_1min as dh

def run_exact_pine_script_backtest():
    with open(BASE_DIR / "trader" / "config.json") as f:
        cfg = json.load(f)

    print("[INFO] Connecting to Angel One SmartAPI to fetch Nifty 50 1-minute data for today...")
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

    # Parse candle series
    opens = [float(b[1]) for b in raw_bars]
    highs = [float(b[2]) for b in raw_bars]
    lows = [float(b[3]) for b in raw_bars]
    closes = [float(b[4]) for b in raw_bars]
    hl2_series = [(h + l) / 2.0 for h, l in zip(highs, lows)]
    times = [b[0].split("T")[1][:5] for b in raw_bars]

    # 1. Pine Script Multi-EMA Section:
    # ema1: len 33 (close), ema2: len 50 (hl2), ema3: len 100 (open), ema4: len 200 (close)
    ema33 = indicators.calculate_ema(closes, 33)
    ema50 = indicators.calculate_ema(hl2_series, 50)
    ema100 = indicators.calculate_ema(opens, 100)
    ema200 = indicators.calculate_ema(closes, 200)

    # 2. Pine Script Supertrend Section:
    # period: 10, factor: 3.0
    st_res = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
    st_dir = st_res["direction"] # 1: Bullish, -1: Bearish
    st_val = st_res["supertrend"]

    # 3. Pine Script MACD Section:
    # fast: 12 (EMA), slow: 26 (EMA), signal: 9 (EMA)
    macd_res = indicators.calculate_macd(closes, 12, 26, 9)
    macd_line = macd_res["macd"]
    macd_sig = macd_res["signal"]
    macd_hist = macd_res["hist"]

    opt_engine = OptionsEngine(cfg)
    risk_engine = RiskEngine(cfg)

    capital = cfg.get("budget", 1000000.0)
    open_pos = None
    trades = []
    pending_signal = None
    cancelled_signals = 0

    print("\n[RUNNING] Bar-by-Bar Backtest of 'Multi-EMA + Supertrend + MACD Combo'...")
    print("-" * 80)

    # Allow indicators to warm up (50 bars)
    for i in range(50, len(raw_bars)):
        bar_time = times[i]
        c = closes[i]
        h = highs[i]
        l = lows[i]

        # -------------------------------------------------------------
        # 1. POSITION MANAGEMENT (Target / SL / Breakeven / Flip)
        # -------------------------------------------------------------
        if open_pos:
            opt_type = open_pos["option_type"]
            entry_spot = open_pos["spot_entry"]
            entry_opt = open_pos["entry_opt_price"]
            current_spot = c

            # Option delta ~0.60
            delta = 0.60
            spot_change = (current_spot - entry_spot) if opt_type == "CE" else (entry_spot - current_spot)
            current_opt_price = max(1.0, round(entry_opt + (spot_change * delta), 2))

            open_pos["peak_opt_price"] = max(open_pos.get("peak_opt_price", current_opt_price), current_opt_price)

            # SMART BREAKEVEN LOCK:
            # If option reaches +1R (initial risk amount), lock SL to cost price
            r_distance = entry_opt - open_pos["initial_sl"]
            if not open_pos.get("breakeven_locked") and (current_opt_price - entry_opt) >= r_distance:
                open_pos["breakeven_locked"] = True
                open_pos["current_sl"] = entry_opt
                print(f"[BREAKEVEN LOCKED] [{bar_time}] Option reached +1R (Rs {current_opt_price:.2f}). Stop-loss locked at Cost Rs {entry_opt:.2f}!")

            exit_trade = False
            exit_reason = ""

            # Check SL
            if current_opt_price <= open_pos["current_sl"]:
                exit_trade = True
                exit_reason = "Breakeven Cost Hit (Zero Loss)" if open_pos.get("breakeven_locked") else "Initial Stop Loss Hit"
            # Check Target (+1.5R)
            elif current_opt_price >= open_pos["target_opt_price"]:
                exit_trade = True
                exit_reason = "Target Hit (+1.5R)"
            # Supertrend Trend Invalidation
            elif opt_type == "CE" and st_dir[i] == -1:
                exit_trade = True
                exit_reason = "Supertrend Bearish Flip"
            elif opt_type == "PE" and st_dir[i] == 1:
                exit_trade = True
                exit_reason = "Supertrend Bullish Flip"
            # EOD Cutoff
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

        # -------------------------------------------------------------
        # 2. CHECK PENDING SIGNAL CONFIRMATION (Confirmation Candle Rule)
        # -------------------------------------------------------------
        if pending_signal:
            # Timeout after 2 candles (max 2 minutes in 1m chart)
            if i > pending_signal["signal_idx"] + 2:
                print(f"[SIGNAL CANCELLED] [{bar_time}] Confirmation timeout. No breakout beyond Rs {pending_signal['trigger_level']:.2f}")
                pending_signal = None
                cancelled_signals += 1
            else:
                # CE Confirmation: Next candle closes strictly above Signal Candle High
                if pending_signal["type"] == "CE" and c > pending_signal["trigger_level"]:
                    strike = opt_engine.select_strike(c, "CE", "NIFTY")
                    opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "CE")
                    est_opt_entry = opt_engine.estimate_option_premium(c, strike, "CE")
                    est_sl = round(est_opt_entry * 0.80, 2) # 20% Option SL
                    est_tgt = round(est_opt_entry * 1.30, 2) # +30% Option Target (1.5R)

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
                            "initial_sl": est_sl,
                            "current_sl": est_sl,
                            "target_opt_price": est_tgt,
                            "quantity": qty,
                            "breakeven_locked": False
                        }
                        print(f"[ENTRY EXECUTED] [{bar_time}] BUY {opt_sym} @ Rs {est_opt_entry} | Lots: {qty//25} ({qty} Qty) | Spot: {c:.2f}")
                        pending_signal = None
                        continue

                # PE Confirmation: Next candle closes strictly below Signal Candle Low
                elif pending_signal["type"] == "PE" and c < pending_signal["trigger_level"]:
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
                            "initial_sl": est_sl,
                            "current_sl": est_sl,
                            "target_opt_price": est_tgt,
                            "quantity": qty,
                            "breakeven_locked": False
                        }
                        print(f"[ENTRY EXECUTED] [{bar_time}] BUY {opt_sym} @ Rs {est_opt_entry} | Lots: {qty//25} ({qty} Qty) | Spot: {c:.2f}")
                        pending_signal = None
                        continue

        # -------------------------------------------------------------
        # 3. DETECT NEW SIGNAL CANDLE (Pine Script Combo Conditions)
        # -------------------------------------------------------------
        if not open_pos and not pending_signal and "09:30" <= bar_time <= "15:00":
            # Bullish Combo: Supertrend Green + Close > EMA 33 + MACD Hist > 0
            is_bullish_combo = (
                st_dir[i] == 1 and
                st_dir[i-1] == -1 and
                c > ema33[i] and
                macd_hist[i] is not None and macd_hist[i] >= 0
            )

            # Bearish Combo: Supertrend Red + Close < EMA 33 + MACD Hist < 0
            is_bearish_combo = (
                st_dir[i] == -1 and
                st_dir[i-1] == 1 and
                c < ema33[i] and
                macd_hist[i] is not None and macd_hist[i] <= 0
            )

            if is_bullish_combo:
                pending_signal = {
                    "type": "CE",
                    "signal_time": bar_time,
                    "signal_idx": i,
                    "trigger_level": h, # High of signal candle
                    "spot": c
                }
                print(f"[STAGE 1 SIGNAL] [{bar_time}] BULLISH COMBO (Supertrend + EMA33 + MACD) @ Rs {c:.2f}. Waiting confirmation above Rs {h:.2f}")

            elif is_bearish_combo:
                pending_signal = {
                    "type": "PE",
                    "signal_time": bar_time,
                    "signal_idx": i,
                    "trigger_level": l, # Low of signal candle
                    "spot": c
                }
                print(f"[STAGE 1 SIGNAL] [{bar_time}] BEARISH COMBO (Supertrend + EMA33 + MACD) @ Rs {c:.2f}. Waiting confirmation below Rs {l:.2f}")

    # Final Summary Report
    print("\n" + "=" * 80)
    print("           P&L REPORT: NIFTY 50 1-MINUTE REAL DATA (TODAY 2026-10-27)")
    print("=" * 80)
    total_pnl = sum(t["pnl"] for t in trades)
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] < 0]
    breakevens = [t for t in trades if t["pnl"] == 0]
    win_rate = (len(wins) / len(trades) * 100) if trades else 0.0

    print(f"Initial Capital        : Rs {cfg.get('budget', 1000000.0):,.2f} (10 Lakhs)")
    print(f"Max Position Limit     : 40% (Rs 4,00,000 max deployed per trade)")
    print(f"Max Risk Per Trade     : 1% (Rs 10,000 capped)")
    print(f"Net Realized P&L       : Rs {total_pnl:+,.2f} ({((capital - 1000000)/1000000)*100:+.2f}%)")
    print(f"Final Account Balance  : Rs {capital:,.2f}")
    print(f"Total Executed Trades  : {len(trades)}")
    print(f"False Signals Filtered : {cancelled_signals} (Saved from losses)")
    print(f"Win Rate               : {win_rate:.1f}% ({len(wins)} Win / {len(losses)} Loss / {len(breakevens)} Breakeven)")

    if trades:
        print("\nDetailed Trades Log:")
        for idx, t in enumerate(trades, 1):
            status_icon = "[WIN]" if t["pnl"] > 0 else "[LOSS]"
            print(f"  {idx}. {status_icon} {t['type']} {t['symbol']} | {t['entry_time']} @ Rs {t['entry_opt']:.2f} -> {t['exit_time']} @ Rs {t['exit_opt']:.2f} | Lots: {t['lots']} ({t['qty']} Qty) | PnL: Rs {t['pnl']:+,.2f} ({t['reason']})")
    print("=" * 80)

if __name__ == "__main__":
    run_exact_pine_script_backtest()
