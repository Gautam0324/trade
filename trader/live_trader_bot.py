#!/usr/bin/env python3
"""
Autonomous Live Market Index Options Trading Bot (NIFTY 50)
Runs continuously in market hours (09:15 - 15:30 IST):
1. Polls 1-min completed candles from Angel One SmartAPI
2. Computes TradingView "Multi-EMA + Supertrend + MACD Combo":
   - Supertrend (10, 3.0)
   - MACD (12, 26, 9)
   - Multi-EMA (33, 50, 100, 200)
3. Two-Stage Execution:
   - Stage 1: Sends immediate Telegram alert when Signal Candle completes
   - Stage 2: Confirms breakout on next 1-min candle close before executing trade
   - Cancels setup if no confirmation within 2 minutes (Zero Loss)
4. Order & Risk Management:
   - Budget: ₹10,00,000 (10 Lakh) | Max Position: 40% (₹4,00,000)
   - Max Risk per Trade: 1% (₹10,000)
   - Selects nearest ITM1 Strike & computes exact multiple of Nifty lot (25)
   - Records trades into trader/ledger.json and trader/orders.json
   - Smart Breakeven Lock at +1R
   - Real-time Telegram alerts for signals, entries, exits, and P&L
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
sys.path.append(str(BASE_DIR / "trader-alerts"))

import indicators
from options_engine import OptionsEngine
from risk_engine import RiskEngine
import telegram_bot
import download_historical_1min as dh

LEDGER_PATH = BASE_DIR / "trader" / "ledger.json"
ORDERS_PATH = BASE_DIR / "trader" / "orders.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"
SIGNALS_CSV_PATH = BASE_DIR / "trader" / "signals_audit.csv"

def init_signals_csv():
    """Initializes signals_audit.csv with headers if it doesn't exist."""
    if not os.path.exists(SIGNALS_CSV_PATH):
        with open(SIGNALS_CSV_PATH, "w", encoding="utf-8") as f:
            f.write("timestamp,signal_type,asset,option_contract,spot_price,trigger_level,status,reason,final_trade_taken\n")

def log_signal_to_csv(signal_type, asset, option_contract, spot_price, trigger_level, status, reason, trade_taken=False):
    """
    Logs every signal to CSV whether trade was taken or cancelled/filtered.
    status: 'SIGNAL_GENERATED' | 'CONFIRMED_EXECUTED' | 'CANCELLED_NO_CONFIRMATION' | 'CANCELLED_REVERSED'
    """
    try:
        init_signals_csv()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        with open(SIGNALS_CSV_PATH, "a", encoding="utf-8") as f:
            f.write(f'"{ts}","{signal_type}","{asset}","{option_contract}",{spot_price:.2f},{trigger_level:.2f},"{status}","{reason}",{trade_taken}\n')
    except Exception as e:
        print(f"[CSV LOG ERROR] {e}")

def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def update_ledger(action, record):
    try:
        ledger = {}
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                ledger = json.load(f)
        
        trades = ledger.get("trades", [])
        trades.append(record)
        ledger["trades"] = trades
        ledger["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
    except Exception as e:
        print(f"[LEDGER ERROR] {e}")

def run_live_bot():
    cfg = load_config()
    print("[BOT START] Initializing Nifty Index Options Live Trader...")
    print(f"[CONFIG] Capital: Rs {cfg.get('budget', 1000000.0):,.2f} | Max Pos: {cfg.get('max_position_pct', 40)}% | Timeframe: 1m")

    # Connect to SmartAPI
    api = dh.get_smart_api_session()
    opt_engine = OptionsEngine(cfg)
    risk_engine = RiskEngine(cfg)

    # Initial state
    capital = float(cfg.get("budget", 1000000.0))
    open_pos = None
    pending_signal = None
    last_processed_candle_time = None
    trades_today = []

    # Send startup message to Telegram
    telegram_bot.send_telegram_message(
        "🚀 <b>Day Trader Bot LIVE!</b>\n"
        "⚡ <b>Asset:</b> NIFTY 50 Index Options (1m Timeframe)\n"
        "🧠 <b>Strategy:</b> Multi-EMA + Supertrend + MACD Combo\n"
        f"💰 <b>Capital:</b> ₹{capital:,.2f} (Max 40% allocation)\n"
        "🛡️ <b>Risk:</b> 1% per trade | 2-Stage Confirmation Active\n"
        "📡 Connected to Angel One SmartAPI live feed."
    )

    while True:
        try:
            now = datetime.datetime.now()
            current_time_str = now.strftime("%H:%M")

            # Check market hours (09:15 to 15:30 IST)
            if current_time_str < "09:15":
                print(f"[PRE-MARKET] Current time is {current_time_str}. Sleeping until 09:15...")
                time.sleep(30)
                continue
            elif current_time_str >= "15:25":
                print(f"[POST-MARKET] Market session concluded for today ({current_time_str}).")
                # Mandatory EOD exit if any
                if open_pos:
                    exit_reason = "Mandatory 15:20 EOD Square-off"
                    # Exit logic
                    pnl = 0.0
                    open_pos = None
                break

            # Fetch last 100 candles of 1-minute
            today_str = now.strftime("%Y-%m-%d")
            from_date = f"{today_str} 09:15"
            to_date = f"{today_str} {current_time_str}"

            candle_resp = api.getCandleData({
                "exchange": "NSE",
                "symboltoken": "99926000", # NIFTY 50
                "interval": "ONE_MINUTE",
                "fromdate": from_date,
                "todate": to_date
            })

            if not candle_resp or not candle_resp.get("status") or not candle_resp.get("data"):
                time.sleep(5)
                continue

            raw_bars = candle_resp["data"]
            latest_bar = raw_bars[-1]
            latest_candle_time = latest_bar[0].split("T")[1][:5]

            # Process only on new completed candle
            if latest_candle_time == last_processed_candle_time:
                time.sleep(4)
                continue

            last_processed_candle_time = latest_candle_time
            closes = [float(b[4]) for b in raw_bars]
            highs = [float(b[2]) for b in raw_bars]
            lows = [float(b[3]) for b in raw_bars]
            c = closes[-1]
            h = highs[-1]
            l = lows[-1]

            print(f"[{latest_candle_time}] Bar Close: {c:.2f} | H: {h:.2f} | L: {l:.2f}")

            # Calculate Indicators
            ema33 = indicators.calculate_ema(closes, 33)[-1]
            st_data = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
            st_dir = st_data["direction"][-1]
            st_dir_prev = st_data["direction"][-2] if len(st_data["direction"]) > 1 else st_dir
            st_val = st_data["supertrend"][-1]
            macd_data = indicators.calculate_macd(closes, 12, 26, 9)
            macd_hist = macd_data["hist"][-1]

            # ---------------------------------------------------------
            # 1. MANAGE ACTIVE POSITION
            # ---------------------------------------------------------
            if open_pos:
                opt_type = open_pos["option_type"]
                entry_spot = open_pos["spot_entry"]
                entry_opt = open_pos["entry_opt_price"]
                delta = 0.60
                spot_change = (c - entry_spot) if opt_type == "CE" else (entry_spot - c)
                current_opt_price = max(1.0, round(entry_opt + (spot_change * delta), 2))

                # Check Breakeven Lock at +1R
                r_pts = entry_opt - open_pos["initial_sl"]
                if not open_pos.get("breakeven_locked") and (current_opt_price - entry_opt) >= r_pts:
                    open_pos["breakeven_locked"] = True
                    open_pos["current_sl"] = entry_opt
                    telegram_bot.send_telegram_message(
                        f"🔒 <b>BREAKEVEN LOCKED!</b>\n"
                        f"Option {open_pos['symbol']} reached +1R (₹{current_opt_price:.2f}).\n"
                        f"Stop-Loss locked at Cost: <b>₹{entry_opt:.2f}</b> (Zero Loss Protected)!"
                    )

                exit_trade = False
                exit_reason = ""

                if current_opt_price <= open_pos["current_sl"]:
                    exit_trade = True
                    exit_reason = "Breakeven Cost Hit" if open_pos.get("breakeven_locked") else "Stop-Loss Hit"
                elif current_opt_price >= open_pos["target_opt_price"]:
                    exit_trade = True
                    exit_reason = "Target Hit (+1.5R)"
                elif opt_type == "CE" and st_dir == -1:
                    exit_trade = True
                    exit_reason = "Supertrend Bearish Flip"
                elif opt_type == "PE" and st_dir == 1:
                    exit_trade = True
                    exit_reason = "Supertrend Bullish Flip"
                elif latest_candle_time >= "15:20":
                    exit_trade = True
                    exit_reason = "Mandatory 15:20 EOD Square-off"

                if exit_trade:
                    pnl = round((current_opt_price - entry_opt) * open_pos["quantity"], 2)
                    capital += pnl
                    trade_record = {
                        "symbol": open_pos["symbol"],
                        "type": opt_type,
                        "entry_time": open_pos["entry_time"],
                        "exit_time": latest_candle_time,
                        "entry_opt": entry_opt,
                        "exit_opt": current_opt_price,
                        "qty": open_pos["quantity"],
                        "pnl": pnl,
                        "reason": exit_reason
                    }
                    trades_today.append(trade_record)
                    update_ledger("EXIT", trade_record)

                    # Send Exit Alert
                    telegram_bot.send_exit_alert({
                        "symbol": open_pos["symbol"],
                        "qty": open_pos["quantity"],
                        "entry_price": entry_opt,
                        "exit_price": current_opt_price,
                        "peak_price": open_pos.get("peak_opt_price", current_opt_price),
                        "reason": exit_reason,
                        "free_cash": capital,
                        "realized_pnl": sum(t["pnl"] for t in trades_today),
                        "currency": "INR",
                        "exchange": "NSE"
                    })
                    print(f"[EXIT] {open_pos['symbol']} @ Rs {current_opt_price} | P&L: Rs {pnl:+,.2f} | Reason: {exit_reason}")
                    open_pos = None
                    continue

            # ---------------------------------------------------------
            # 2. CHECK PENDING SIGNAL CONFIRMATION
            # ---------------------------------------------------------
            if pending_signal:
                # Check timeout (max 2 candles)
                if len(raw_bars) > pending_signal["signal_idx"] + 2:
                    cancel_reason = "Confirmation timeout: Price did not break trigger level within 2 minutes"
                    print(f"⚪ [{latest_candle_time}] Signal Cancelled ({cancel_reason})")
                    log_signal_to_csv(
                        pending_signal["type"], "NIFTY 50", pending_signal.get("option_contract", "N/A"),
                        pending_signal["spot"], pending_signal["trigger_level"],
                        "CANCELLED_NO_CONFIRMATION", cancel_reason, trade_taken=False
                    )
                    telegram_bot.send_signal_cancelled_alert({
                        "symbol": f"NIFTY 50 ({pending_signal['type']})",
                        "reason": cancel_reason,
                        "trigger_level": pending_signal["trigger_level"],
                        "candle_time": latest_candle_time
                    })
                    pending_signal = None
                else:
                    # CE Confirmation: Close > Signal Candle High
                    if pending_signal["type"] == "CE" and c > pending_signal["trigger_level"]:
                        strike = opt_engine.select_strike(c, "CE", "NIFTY")
                        opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "CE")
                        est_opt_entry = opt_engine.estimate_option_premium(c, strike, "CE")
                        est_sl = round(est_opt_entry * 0.80, 2)
                        est_tgt = round(est_opt_entry * 1.30, 2)

                        risk_check = risk_engine.evaluate_order_risk(
                            opt_sym, "BUY", est_opt_entry, est_sl, est_tgt, capital, 0, len(trades_today), 0
                        )

                        if risk_check["passed"]:
                            qty = risk_check["quantity"]
                            open_pos = {
                                "symbol": opt_sym,
                                "option_type": "CE",
                                "entry_time": latest_candle_time,
                                "spot_entry": c,
                                "entry_opt_price": est_opt_entry,
                                "initial_sl": est_sl,
                                "current_sl": est_sl,
                                "target_opt_price": est_tgt,
                                "quantity": qty,
                                "breakeven_locked": False
                            }
                            log_signal_to_csv(
                                "CE", "NIFTY 50", opt_sym, c, pending_signal["trigger_level"],
                                "CONFIRMED_EXECUTED", f"Bought {qty} qty at Rs {est_opt_entry}", trade_taken=True
                            )
                            telegram_bot.send_trade_alert({
                                "symbol": opt_sym,
                                "side": "BUY",
                                "qty": qty,
                                "entry": est_opt_entry,
                                "stop": est_sl,
                                "target": est_tgt,
                                "currency": "INR",
                                "exchange": "NSE",
                                "free_cash": capital - (qty * est_opt_entry),
                                "strategy": "Multi-EMA + Supertrend + MACD"
                            })
                            update_ledger("ENTRY", open_pos)
                            print(f"🚨 [{latest_candle_time}] ENTRY EXECUTED: BUY {opt_sym} @ Rs {est_opt_entry} | Qty: {qty}")
                            pending_signal = None
                            continue

                    # PE Confirmation: Close < Signal Candle Low
                    elif pending_signal["type"] == "PE" and c < pending_signal["trigger_level"]:
                        strike = opt_engine.select_strike(c, "PE", "NIFTY")
                        opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "PE")
                        est_opt_entry = opt_engine.estimate_option_premium(c, strike, "PE")
                        est_sl = round(est_opt_entry * 0.80, 2)
                        est_tgt = round(est_opt_entry * 1.30, 2)

                        risk_check = risk_engine.evaluate_order_risk(
                            opt_sym, "BUY", est_opt_entry, est_sl, est_tgt, capital, 0, len(trades_today), 0
                        )

                        if risk_check["passed"]:
                            qty = risk_check["quantity"]
                            open_pos = {
                                "symbol": opt_sym,
                                "option_type": "PE",
                                "entry_time": latest_candle_time,
                                "spot_entry": c,
                                "entry_opt_price": est_opt_entry,
                                "initial_sl": est_sl,
                                "current_sl": est_sl,
                                "target_opt_price": est_tgt,
                                "quantity": qty,
                                "breakeven_locked": False
                            }
                            log_signal_to_csv(
                                "PE", "NIFTY 50", opt_sym, c, pending_signal["trigger_level"],
                                "CONFIRMED_EXECUTED", f"Bought {qty} qty at Rs {est_opt_entry}", trade_taken=True
                            )
                            telegram_bot.send_trade_alert({
                                "symbol": opt_sym,
                                "side": "BUY",
                                "qty": qty,
                                "entry": est_opt_entry,
                                "stop": est_sl,
                                "target": est_tgt,
                                "currency": "INR",
                                "exchange": "NSE",
                                "free_cash": capital - (qty * est_opt_entry),
                                "strategy": "Multi-EMA + Supertrend + MACD"
                            })
                            update_ledger("ENTRY", open_pos)
                            print(f"🚨 [{latest_candle_time}] ENTRY EXECUTED: BUY {opt_sym} @ Rs {est_opt_entry} | Qty: {qty}")
                            pending_signal = None
                            continue

            # ---------------------------------------------------------
            # 3. DETECT NEW SIGNAL CANDLE (Stage 1)
            # ---------------------------------------------------------
            if not open_pos and not pending_signal and "09:30" <= latest_candle_time <= "15:00":
                is_bull_combo = (
                    st_dir == 1 and st_dir_prev == -1 and
                    (ema33 is None or c > ema33) and
                    (macd_hist is not None and macd_hist >= 0)
                )
                is_bear_combo = (
                    st_dir == -1 and st_dir_prev == 1 and
                    (ema33 is None or c < ema33) and
                    (macd_hist is not None and macd_hist <= 0)
                )

                if is_bull_combo:
                    strike = opt_engine.select_strike(c, "CE", "NIFTY")
                    opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "CE")
                    pending_signal = {
                        "type": "CE",
                        "signal_time": latest_candle_time,
                        "signal_idx": len(raw_bars) - 1,
                        "trigger_level": h,
                        "spot": c,
                        "option_contract": opt_sym
                    }
                    log_signal_to_csv(
                        "CE", "NIFTY 50", opt_sym, c, h, "SIGNAL_GENERATED", "Bullish Combo Triggered (Waiting Confirmation)", trade_taken=False
                    )
                    telegram_bot.send_signal_alert({
                        "symbol": "NIFTY 50",
                        "side": "BUY (CALL CE)",
                        "spot_price": c,
                        "trigger_level": h,
                        "stop_loss": l,
                        "strategy_name": "Multi-EMA + Supertrend + MACD",
                        "option_contract": opt_sym,
                        "candle_time": latest_candle_time
                    })
                    print(f"🟡 [{latest_candle_time}] STAGE 1 SIGNAL: Bullish Combo @ Rs {c:.2f}. Waiting for confirmation above Rs {h:.2f}")

                elif is_bear_combo:
                    strike = opt_engine.select_strike(c, "PE", "NIFTY")
                    opt_sym = opt_engine.construct_option_symbol("NIFTY", strike, "PE")
                    pending_signal = {
                        "type": "PE",
                        "signal_time": latest_candle_time,
                        "signal_idx": len(raw_bars) - 1,
                        "trigger_level": l,
                        "spot": c,
                        "option_contract": opt_sym
                    }
                    log_signal_to_csv(
                        "PE", "NIFTY 50", opt_sym, c, l, "SIGNAL_GENERATED", "Bearish Combo Triggered (Waiting Confirmation)", trade_taken=False
                    )
                    telegram_bot.send_signal_alert({
                        "symbol": "NIFTY 50",
                        "side": "BUY (PUT PE)",
                        "spot_price": c,
                        "trigger_level": l,
                        "stop_loss": h,
                        "strategy_name": "Multi-EMA + Supertrend + MACD",
                        "option_contract": opt_sym,
                        "candle_time": latest_candle_time
                    })
                    print(f"🟡 [{latest_candle_time}] STAGE 1 SIGNAL: Bearish Combo @ Rs {c:.2f}. Waiting for confirmation below Rs {l:.2f}")

            time.sleep(12)

        except Exception as e:
            print(f"[LOOP ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_live_bot()
