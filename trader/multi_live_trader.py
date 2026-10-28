#!/usr/bin/env python3
"""
Multi-Symbol Live Market Scanner & Paper Trader.
Tracks 5 Top Active Symbols:
1. TCS (Token: 11536)
2. JINDALSTEL (JPL / Jindal Steel - Token: 6733)
3. RELIANCE (Token: 2885)
4. INFY (Token: 1594)
5. HDFCBANK (Token: 1333)

Strategy: TradingView "Multi-EMA + Supertrend + MACD Combo" (1-minute Timeframe)
- Two-Stage Execution:
  * Stage 1: Signal Candle Telegram alert + CSV entry
  * Stage 2: Breakout Confirmation candle close before Paper Trade entry
  * Timeout cancellation logged to CSV if no breakout within 2 minutes
- Safe rate-limiting to prevent SmartAPI 429 errors
"""

import os
import sys
import json
import time
import datetime
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
sys.path.append(str(BASE_DIR / "trader-alerts"))

import indicators
from risk_engine import RiskEngine
from strategy_engine import StrategyEngine
from regime import detect_market_regime
import telegram_bot
import download_historical_1min as dh

WATCHLIST = [
    # Top Liquid NSE Stocks
    {"symbol": "JPL", "token": "766830", "exchange": "NSE", "name": "JPL (Newly Listed)"},
    {"symbol": "TCS", "token": "11536", "exchange": "NSE", "name": "Tata Consultancy Services"},
    {"symbol": "RELIANCE", "token": "2885", "exchange": "NSE", "name": "Reliance Industries"},
    {"symbol": "INFY", "token": "1594", "exchange": "NSE", "name": "Infosys"},
    {"symbol": "HDFCBANK", "token": "1333", "exchange": "NSE", "name": "HDFC Bank"},
    # High-Volume BSE Stocks
    {"symbol": "RELIANCE_BSE", "token": "500325", "exchange": "BSE", "name": "Reliance Industries (BSE)"},
    {"symbol": "TCS_BSE", "token": "532540", "exchange": "BSE", "name": "TCS (BSE)"},
    {"symbol": "HDFCBANK_BSE", "token": "500180", "exchange": "BSE", "name": "HDFC Bank (BSE)"}
]

LEDGER_PATH = BASE_DIR / "trader" / "ledger.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"
SIGNALS_CSV_PATH = BASE_DIR / "trader" / "signals_audit.csv"
DB_PATH = BASE_DIR / "trader" / "ticks_market_data.db"

def init_tick_db():
    """Initializes high-performance SQLite database for raw tick & candle storage."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            token TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            volume REAL
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sym_time ON ticks (symbol, timestamp)")
    conn.commit()
    conn.close()

def store_tick(timestamp, symbol, token, o, h, l, c, v):
    """Saves every incoming tick / 1m candle directly into SQLite database for future backtesting."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ticks (timestamp, symbol, token, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, symbol, token, o, h, l, c, v))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] {e}")

def init_signals_csv():
    if not os.path.exists(SIGNALS_CSV_PATH):
        with open(SIGNALS_CSV_PATH, "w", encoding="utf-8") as f:
            f.write("timestamp,signal_type,asset,option_contract,spot_price,trigger_level,status,reason,final_trade_taken\n")

def log_signal_to_csv(signal_type, asset, contract, spot_price, trigger_level, status, reason, trade_taken=False):
    try:
        init_signals_csv()
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        with open(SIGNALS_CSV_PATH, "a", encoding="utf-8") as f:
            f.write(f'"{ts}","{signal_type}","{asset}","{contract}",{spot_price:.2f},{trigger_level:.2f},"{status}","{reason}",{trade_taken}\n')
    except Exception as e:
        print(f"[CSV LOG ERROR] {e}")

LOCK_PATH = BASE_DIR / "trader" / "multi_trader.lock"

def acquire_instance_lock():
    """Ensures only ONE instance of the trading bot runs at any time."""
    pid = os.getpid()
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, "r") as f:
                old_pid = int(f.read().strip())
            # Check if old process is still alive on Windows
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, old_pid)
            if h_proc:
                kernel32.CloseHandle(h_proc)
                print(f"[FATAL] Another bot instance is already running with PID {old_pid}! Exiting to prevent duplicate trades.")
                sys.exit(0)
        except Exception:
            pass  # Lock is stale or unreadable

    with open(LOCK_PATH, "w") as f:
        f.write(str(pid))

def update_ledger(action, record):
    try:
        ledger = {}
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                ledger = json.load(f)

        starting_cap = 1000000.0
        ledger["starting_capital"] = starting_cap
        open_pos_list = ledger.get("open_positions", [])

        if action == "ENTRY":
            # Avoid duplicate ENTRY if symbol is already in open_positions
            if any(p.get("symbol") == record["symbol"] for p in open_pos_list):
                return
            open_pos_list.append({
                "id": f"POS-{len(ledger.get('trade_history', [])) + len(open_pos_list) + 1}",
                "symbol": record["symbol"],
                "side": record["side"],
                "qty": record["quantity"],
                "entry_price": record["entry_price"],
                "sl_price": record["sl_price"],
                "target_price": record["target_price"],
                "entry_time": record["entry_time"],
                "amount": round(record["quantity"] * record["entry_price"], 2)
            })
        elif action == "EXIT":
            # Remove from open_positions and add to trade_history
            open_pos_list = [p for p in open_pos_list if p.get("symbol") != record["symbol"]]
            ledger["trades"] = [p for p in ledger.get("trades", []) if p.get("symbol") != record["symbol"]]
            th = ledger.get("trade_history", [])
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")

            # Duplicate protection: don't record if exactly this exit for this symbol and entry_time already exists
            already_recorded = any(
                t.get("symbol") == record["symbol"] and 
                t.get("entry_time") == record["entry_time"] and
                t.get("date") == today_str
                for t in th
            )
            if already_recorded:
                print(f"[LEDGER] Exit for {record['symbol']} entry at {record['entry_time']} already recorded. Skipping duplicate.")
                return

            th.append({
                "symbol": record["symbol"],
                "side": record["side"],
                "qty": record["qty"],
                "entry_price": record["entry_price"],
                "exit_price": record["exit_price"],
                "entry_time": record["entry_time"],
                "exit_time": record["exit_time"],
                "pnl": record["pnl"],
                "reason": record["reason"],
                "date": today_str,
                "exit_date": today_str
            })
            ledger["trade_history"] = th
            ledger["realized_pnl"] = round(ledger.get("realized_pnl", 0.0) + record["pnl"], 2)

        ledger["open_positions"] = open_pos_list
        deployed = sum(float(p.get("amount", p.get("qty", 0) * p.get("entry_price", 0))) for p in open_pos_list)
        ledger["deployed_cash"] = round(deployed, 2)
        ledger["free_cash"] = round(starting_cap - deployed + ledger.get("realized_pnl", 0.0), 2)
        ledger["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(ledger, f, indent=2)
    except Exception as e:
        print(f"[LEDGER ERROR] {e}")

def run_multi_symbol_trader():
    acquire_instance_lock()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    capital = float(cfg.get("budget", 1000000.0))
    risk_engine = RiskEngine(cfg)
    strategy_engine = StrategyEngine(cfg)

    print("=" * 75)
    print("[START] MULTI-SYMBOL LIVE PAPER TRADER")
    print(f"[CONFIG] Capital: Rs {capital:,.2f} | Max Pos: 40% | Risk Cap: 1%")
    print("Watchlist: " + ", ".join([w["symbol"] for w in WATCHLIST]))
    print("=" * 75)

    init_tick_db()
    api = dh.get_smart_api_session()

    telegram_bot.send_telegram_message(
        "🚀 <b>Multi-Stock Day Trader Bot LIVE!</b>\n"
        f"📋 <b>Watchlist (5 Stocks):</b> <code>JPL (Newly Listed), TCS, RELIANCE, INFY, HDFCBANK</code>\n"
        f"💰 <b>Paper Capital:</b> ₹{capital:,.2f}\n"
        "⚡ <b>Timeframe:</b> 1-Minute | 2-Stage Confirmation Active\n"
        "💾 <b>Tick Storage:</b> Every live candle auto-saved into SQLite DB for backtesting.\n"
        "📝 Real-time CSV Audit & Telegram Alerts Connected."
    )

    # Load existing open positions from ledger.json so active trades are remembered across bot restarts
    open_positions = {}
    if os.path.exists(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                saved_ledger = json.load(f)
                for p in saved_ledger.get("open_positions", []):
                    open_positions[p["symbol"]] = {
                        "symbol": p["symbol"],
                        "side": p.get("side", "BUY"),
                        "entry_time": p.get("entry_time", "N/A"),
                        "entry_price": float(p["entry_price"]),
                        "sl_price": float(p.get("sl_price", p["entry_price"] * 0.99)),
                        "target_price": float(p.get("target_price", p["entry_price"] * 1.01)),
                        "quantity": int(p["qty"])
                    }
            if open_positions:
                print(f"[RECOVERY] Restored {len(open_positions)} active position(s) from ledger: {list(open_positions.keys())}")
        except Exception as e:
            print(f"[RECOVERY ERROR] {e}")

    pending_signals = {}
    last_processed_times = {w["symbol"]: None for w in WATCHLIST}
    trades_today = []

    while True:
        try:
            now = datetime.datetime.now()
            current_time_str = now.strftime("%H:%M")

            if current_time_str < "09:15":
                time.sleep(30)
                continue
            elif current_time_str >= "15:25":
                print(f"[MARKET CLOSED] End of session: {current_time_str}")
                break

            today_str = now.strftime("%Y-%m-%d")

            for item in WATCHLIST:
                sym = item["symbol"]
                token = item["token"]
                exch = item.get("exchange", "NSE")

                try:
                    resp = api.getCandleData({
                        "exchange": exch,
                        "symboltoken": token,
                        "interval": "ONE_MINUTE",
                        "fromdate": f"{today_str} 09:15",
                        "todate": f"{today_str} {current_time_str}"
                    })

                    if not resp or not resp.get("status") or not resp.get("data"):
                        time.sleep(1.5)
                        continue

                    raw_bars = resp["data"]
                    if len(raw_bars) < 5:
                        time.sleep(1.5)
                        continue

                    latest_bar = raw_bars[-1]
                    bar_time = latest_bar[0].split("T")[1][:5]

                    if bar_time == last_processed_times[sym]:
                        time.sleep(1.5)
                        continue

                    last_processed_times[sym] = bar_time
                    closes = [float(b[4]) for b in raw_bars]
                    highs = [float(b[2]) for b in raw_bars]
                    lows = [float(b[3]) for b in raw_bars]
                    opens = [float(b[1]) for b in raw_bars]
                    volumes = [float(b[5]) if len(b) > 5 else 0.0 for b in raw_bars]
                    c = closes[-1]
                    h = highs[-1]
                    l = lows[-1]
                    o = opens[-1]
                    v = volumes[-1]

                    # Save every single tick/candle into SQLite database for future backtesting
                    store_tick(latest_bar[0], sym, token, o, h, l, c, v)

                    print(f"[{bar_time}] {sym:10s} Close: Rs {c:8.2f} | H: {h:8.2f} | L: {l:8.2f} [SAVED TO DB]")

                    # Calculate Indicators
                    ema33 = indicators.calculate_ema(closes, 33)[-1]
                    st_data = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
                    st_dir = st_data["direction"][-1]
                    st_dir_prev = st_data["direction"][-2] if len(st_data["direction"]) > 1 else st_dir
                    macd_data = indicators.calculate_macd(closes, 12, 26, 9)
                    macd_hist = macd_data["hist"][-1]

                    # 1. Manage Open Position for this Symbol (Long & Short)
                    if sym in open_positions:
                        pos = open_positions[sym]
                        entry_p = pos["entry_price"]
                        current_p = c
                        side = pos["side"]

                        exit_trade = False
                        exit_price = current_p
                        exit_reason = ""

                        if side == "BUY":
                            # Target Touch: If candle High reached or crossed target price
                            if h >= pos["target_price"]:
                                exit_trade = True
                                exit_price = pos["target_price"]
                                exit_reason = "Target Hit (+1.5R - Wick Touch)"
                            # Stop-Loss Touch: If candle Low broke below SL price
                            elif l <= pos["sl_price"]:
                                exit_trade = True
                                exit_price = pos["sl_price"]
                                exit_reason = "Stop Loss Hit (Wick Touch)"
                            elif st_dir == -1:
                                exit_trade = True
                                exit_price = current_p
                                exit_reason = "Supertrend Bearish Reversal"

                        elif side == "SELL":
                            # Target Touch for Short: If candle Low reached or crossed target price
                            if l <= pos["target_price"]:
                                exit_trade = True
                                exit_price = pos["target_price"]
                                exit_reason = "Short Target Hit (+1.5R - Wick Touch)"
                            # Stop-Loss Touch for Short: If candle High reached or crossed SL price
                            elif h >= pos["sl_price"]:
                                exit_trade = True
                                exit_price = pos["sl_price"]
                                exit_reason = "Short Stop Loss Hit (Wick Touch)"
                            elif st_dir == 1:
                                exit_trade = True
                                exit_price = current_p
                                exit_reason = "Supertrend Bullish Reversal"

                        if exit_trade:
                            pnl = round((exit_price - entry_p) * pos["quantity"], 2) if side == "BUY" else round((entry_p - exit_price) * pos["quantity"], 2)
                            capital += pnl
                            trade_rec = {
                                "symbol": sym,
                                "side": side,
                                "entry_time": pos["entry_time"],
                                "exit_time": bar_time,
                                "entry_price": entry_p,
                                "exit_price": exit_price,
                                "qty": pos["quantity"],
                                "pnl": pnl,
                                "reason": exit_reason
                            }
                            trades_today.append(trade_rec)
                            update_ledger("EXIT", trade_rec)

                            telegram_bot.send_exit_alert({
                                "symbol": sym,
                                "qty": pos["quantity"],
                                "entry_price": entry_p,
                                "exit_price": exit_price,
                                "peak_price": max(entry_p, exit_price),
                                "reason": exit_reason,
                                "free_cash": capital,
                                "realized_pnl": sum(t["pnl"] for t in trades_today),
                                "currency": "INR",
                                "exchange": "NSE"
                            })
                            print(f"🎯 [EXIT EXECUTED] {side} {sym} @ Rs {exit_price} | PnL: Rs {pnl:+,.2f} | Reason: {exit_reason}")
                            del open_positions[sym]
                            continue

                    # 2. Check Pending Signal Confirmation for this Symbol (BUY or SELL)
                    # NOTE: Always evaluate the incoming completed confirmation candle FIRST
                    # before expiring or timing out the signal!
                    if sym in pending_signals:
                        ps = pending_signals[sym]
                        sig_time = ps.get("signal_time")
                        current_candle_count = len(raw_bars)

                        is_confirmed = False
                        if ps["side"] == "BUY" and c > ps["trigger_level"]:
                            is_confirmed = True
                        elif ps["side"] == "SELL" and c < ps["trigger_level"]:
                            is_confirmed = True

                        if is_confirmed and bar_time != sig_time:
                            # Confirmed by a distinct subsequent completed candle
                            side = ps["side"]
                            est_sl = ps["stop_loss"]
                            risk_pts = abs(c - est_sl)
                            est_tgt = round(c + (risk_pts * 1.5) if side == "BUY" else c - (risk_pts * 1.5), 2)
                            risk_check = risk_engine.evaluate_order_risk(
                                sym, side, c, est_sl, est_tgt, capital, 0, len(trades_today), len(open_positions)
                            )
                            if risk_check["passed"]:
                                qty = risk_check["quantity"]
                                open_positions[sym] = {
                                    "symbol": sym, "side": side, "entry_time": bar_time,
                                    "entry_price": c, "sl_price": est_sl, "target_price": est_tgt, "quantity": qty
                                }
                                log_signal_to_csv(side, sym, sym, c, ps["trigger_level"], "CONFIRMED_EXECUTED", f"{side} {qty} shares @ Rs {c}", trade_taken=True)
                                telegram_bot.send_trade_alert({
                                    "symbol": sym, "side": side, "qty": qty, "entry": c, "stop": est_sl, "target": est_tgt,
                                    "currency": "INR", "exchange": exch, "free_cash": capital - (qty * c) if side == "BUY" else capital,
                                    "strategy": f"{ps.get('strategy_name', 'Unified Engine')} ({ps.get('setup_tag', 'Breakout')})"
                                })
                                update_ledger("ENTRY", open_positions[sym])
                                print(f"🚨 [{bar_time}] ENTRY EXECUTED: {side} {sym} @ Rs {c:.2f} | Qty: {qty} | Strategy: {ps.get('strategy_name')}")
                                del pending_signals[sym]
                                continue
                            else:
                                reject_reason = f"Risk Engine Blocked: {'; '.join(risk_check.get('reasons', []))}"
                                print(f"🛡️ [{bar_time}] {side} {sym} Blocked by Risk Gate: {reject_reason}")
                                log_signal_to_csv(side, sym, sym, c, ps["trigger_level"], "REJECTED_BY_RISK", reject_reason, trade_taken=False)
                                del pending_signals[sym]
                                continue

                        # If not confirmed, check if confirmation window has genuinely elapsed.
                        # For window of 2 candles: if signal at idx 10 (bar count was 11),
                        # candle 1 arrives at count 12, candle 2 arrives at count 13.
                        # Expiry occurs strictly when candle count > signal_bar_count + window
                        sig_bar_count = ps.get("signal_idx", 0) + 1
                        window_candles = int(cfg.get("confirmation_window_candles", 2))
                        if current_candle_count > sig_bar_count + window_candles:
                            cancel_msg = f"Price did not break trigger level within confirmation window ({window_candles} bars elapsed)"
                            print(f"⚪ [{bar_time}] {ps['side']} {sym} Signal Cancelled ({cancel_msg})")
                            log_signal_to_csv(
                                ps["side"], sym, sym, ps["spot"], ps["trigger_level"],
                                "CANCELLED_NO_CONFIRMATION", cancel_msg, trade_taken=False
                            )
                            telegram_bot.send_signal_cancelled_alert({
                                "symbol": f"{sym} ({ps['side']})",
                                "exchange": exch,
                                "reason": cancel_msg,
                                "trigger_level": ps["trigger_level"],
                                "candle_time": bar_time
                            })
                            del pending_signals[sym]

                    # 3. Detect New Signal Candle via Unified Strategy Engine
                    if sym not in open_positions and sym not in pending_signals and "09:30" <= bar_time <= "15:00":
                        bars_dict = {
                            "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes
                        }
                        sig = strategy_engine.evaluate(sym, bars_dict, exchange=exch)

                        if sig and sig.get("direction") in ("LONG", "SHORT"):
                            side = "BUY" if sig["direction"] == "LONG" else "SELL"
                            trigger = sig.get("entry_trigger", h if side == "BUY" else l)
                            sl_level = sig.get("stop_loss", l if side == "BUY" else h)
                            strat_title = sig.get("strategy_name", "UNIFIED_STRATEGY")
                            desc = sig.get("setup_description", "Setup Triggered")

                            pending_signals[sym] = {
                                "side": side,
                                "signal_time": bar_time,
                                "signal_idx": len(raw_bars) - 1,
                                "trigger_level": trigger,
                                "stop_loss": sl_level,
                                "spot": c,
                                "strategy_name": strat_title,
                                "setup_tag": strat_title
                            }
                            log_signal_to_csv(side, sym, sym, c, trigger, "SIGNAL_GENERATED", f"{strat_title} ({desc})", trade_taken=False)
                            telegram_bot.send_signal_alert({
                                "symbol": sym, "exchange": exch, "side": f"{side} ({strat_title})",
                                "spot_price": c, "trigger_level": trigger, "stop_loss": sl_level,
                                "strategy_name": strat_title, "candle_time": bar_time
                            })
                            emoji = "🟢" if side == "BUY" else "🟡"
                            print(f"{emoji} [{bar_time}] STAGE 1 SIGNAL: {sym} {strat_title} ({side}) @ Rs {c:.2f}. Waiting confirmation beyond Rs {trigger:.2f}")

                except Exception as inner_e:
                    pass
                time.sleep(1.8) # Respect SmartAPI rate limit (~1 req / 1.5s)

            time.sleep(4)

        except Exception as e:
            print(f"[MAIN LOOP ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_multi_symbol_trader()
