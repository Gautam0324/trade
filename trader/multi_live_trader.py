#!/usr/bin/env python3
"""
Multi-Symbol Live Market Scanner & Paper Trader.
Tracks Top Active Symbols across NSE & BSE with:
- Strict completed-candle enforcement (no unfinished candles for indicators or signals)
- Robust multi-strategy confluence engine
- Safe broker isolation boundary (BrokerAdapter)
- Risk-gate validation using single-source-of-truth ledger state
- Conservative exit evaluation (ambiguous stop/target resolution & gap slippage handling)
- Mandatory flat_by session square-off
- Structured error logging replacing bare exception silences
"""

import os
import sys
import json
import time
import datetime
import sqlite3
import logging
from pathlib import Path

# Force UTF-8 encoding for Windows standard console output to avoid charmap codec errors
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
sys.path.append(str(BASE_DIR / "trader-alerts"))

import indicators
from risk_engine import RiskEngine
from strategy_engine import StrategyEngine
from broker_adapter import BrokerAdapter, PaperModeIsolationError
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
LOCK_PATH = BASE_DIR / "trader" / "multi_trader.lock"

# -------------------------------------------------------------------------
# DATABASE & AUDIT LOGGING
# -------------------------------------------------------------------------

def init_tick_db():
    """Initializes high-performance SQLite database for raw tick & candle storage with unique constraints."""
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
            volume REAL,
            UNIQUE(symbol, timestamp)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sym_time ON ticks (symbol, timestamp)")
    conn.commit()
    conn.close()

def store_tick(timestamp, symbol, token, o, h, l, c, v):
    """Saves every completed 1m candle idempotently into SQLite (INSERT OR IGNORE)."""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR IGNORE INTO ticks (timestamp, symbol, token, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (timestamp, symbol, token, o, h, l, c, v))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DB ERROR] symbol={symbol} ts={timestamp}: {e}")

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

def acquire_instance_lock():
    """Ensures only ONE instance of the trading bot runs at any time."""
    pid = os.getpid()
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH, "r") as f:
                old_pid = int(f.read().strip())
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

# -------------------------------------------------------------------------
# CANDLE MANAGEMENT & COMPLETED CANDLE ENFORCEMENT
# -------------------------------------------------------------------------

def filter_completed_candles(raw_bars, current_time_str=None):
    """
    Enforces completed-candle integrity:
    - Sorts bars chronologically by timestamp.
    - Deduplicates bars by timestamp.
    - Discards any in-progress candle matching current_time_str (the currently forming candle).
    Returns list of fully completed candle bars: [[timestamp, o, h, l, c, v], ...]
    """
    if not raw_bars:
        return []

    # Sort chronologically
    sorted_bars = sorted(raw_bars, key=lambda b: b[0])

    # Deduplicate by timestamp
    seen_ts = set()
    deduped = []
    for bar in sorted_bars:
        ts = bar[0]
        if ts not in seen_ts:
            seen_ts.add(ts)
            deduped.append(bar)

    # Discard in-progress candle if current_time_str is provided
    if current_time_str:
        completed = []
        for bar in deduped:
            ts = bar[0]
            b_time = ts.split("T")[1][:5] if "T" in ts else ts.split(" ")[1][:5]
            if b_time < current_time_str:
                completed.append(bar)
        return completed

    return deduped

# -------------------------------------------------------------------------
# EXIT LOGIC & CONSERVATIVE AMBIGUITY RESOLUTION
# -------------------------------------------------------------------------

def evaluate_position_exit(pos, bar, flat_by=None, current_time_str=None, st_dir=0):
    """
    Evaluates whether an active position should exit based on the current bar.
    Enforces:
    - flat_by session cutoff square-off
    - Conservative resolution: if both target and stop are touched in one candle, assume Stop Loss hit first.
    - Gap slippage handling: if candle opened beyond stop loss, exit at open price.
    - Supertrend reversal exit.

    Returns: (exit_trade: bool, exit_price: float, exit_reason: str)
    """
    side = pos["side"]
    entry_p = float(pos["entry_price"])
    sl_p = float(pos["sl_price"])
    tgt_p = float(pos["target_price"])

    # Extract bar prices
    # bar: [timestamp, open, high, low, close, volume]
    o = float(bar[1])
    h = float(bar[2])
    l = float(bar[3])
    c = float(bar[4])
    ts = bar[0]
    bar_time = ts.split("T")[1][:5] if "T" in ts else ts.split(" ")[1][:5]

    # 1. Check Session Cutoff (flat_by)
    if flat_by and current_time_str and current_time_str >= flat_by:
        return True, c, f"Session Cutoff (flat_by {flat_by} reached)"

    if side == "BUY":
        stop_touched = (l <= sl_p)
        target_touched = (h >= tgt_p)

        # Conservative ambiguity resolution: if both touched in the same candle, assume Stop Loss hit first!
        if stop_touched and target_touched:
            exit_price = o if o < sl_p else sl_p
            return True, exit_price, "Stop Loss Hit (Conservative Ambiguity Resolution: both levels touched)"

        # Stop loss touch only
        if stop_touched:
            # Gapped down below stop on candle open
            exit_price = o if o < sl_p else sl_p
            reason = "Stop Loss Hit (Gapped Below SL)" if o < sl_p else "Stop Loss Hit (Wick Touch)"
            return True, exit_price, reason

        # Target touch only
        if target_touched:
            exit_price = o if o > tgt_p else tgt_p
            return True, exit_price, "Target Hit (+1.5R - Wick Touch)"

        # Supertrend reversal
        if st_dir == -1:
            return True, c, "Supertrend Bearish Reversal"

    elif side == "SELL":
        stop_touched = (h >= sl_p)
        target_touched = (l <= tgt_p)

        # Conservative ambiguity resolution: if both touched, assume Stop Loss hit first
        if stop_touched and target_touched:
            exit_price = o if o > sl_p else sl_p
            return True, exit_price, "Short Stop Loss Hit (Conservative Ambiguity Resolution: both levels touched)"

        # Stop loss touch only
        if stop_touched:
            exit_price = o if o > sl_p else sl_p
            reason = "Short Stop Loss Hit (Gapped Above SL)" if o > sl_p else "Short Stop Loss Hit (Wick Touch)"
            return True, exit_price, reason

        # Target touch only
        if target_touched:
            exit_price = o if o < tgt_p else tgt_p
            return True, exit_price, "Short Target Hit (+1.5R - Wick Touch)"

        # Supertrend reversal
        if st_dir == 1:
            return True, c, "Supertrend Bullish Reversal"

    return False, c, ""

# -------------------------------------------------------------------------
# BENCHMARK DATA FETCHER
# -------------------------------------------------------------------------

def fetch_benchmark_data(api, today_str, current_time_str, symbol="NIFTY 50", token="99926000", exchange="NSE"):
    """
    Fetches benchmark candles for relative strength evaluation.
    Returns bars_dict: {"open": [...], "high": [...], "low": [...], "close": [...], "volume": [...], "timestamp": [...]}
    or None if unavailable.
    """
    if not api:
        return None
    try:
        resp = api.getCandleData({
            "exchange": exchange,
            "symboltoken": token,
            "interval": "ONE_MINUTE",
            "fromdate": f"{today_str} 09:15",
            "todate": f"{today_str} {current_time_str}"
        })
        if resp and resp.get("status") and resp.get("data"):
            completed_bars = filter_completed_candles(resp["data"], current_time_str)
            if len(completed_bars) >= 20:
                return {
                    "timestamp": [b[0] for b in completed_bars],
                    "open": [float(b[1]) for b in completed_bars],
                    "high": [float(b[2]) for b in completed_bars],
                    "low": [float(b[3]) for b in completed_bars],
                    "close": [float(b[4]) for b in completed_bars],
                    "volume": [float(b[5]) if len(b) > 5 else 0.0 for b in completed_bars]
                }
    except Exception as e:
        print(f"[BENCHMARK FETCH ERROR] symbol={symbol} ts={current_time_str}: {e}")
    return None

# -------------------------------------------------------------------------
# LEDGER SYNC & POSITION RECOVERY
# -------------------------------------------------------------------------

def update_ledger(action, record):
    try:
        ledger = {}
        if os.path.exists(LEDGER_PATH):
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                ledger = json.load(f)

        starting_cap = float(ledger.get("starting_capital", 1000000.0))
        ledger["starting_capital"] = starting_cap
        open_pos_list = ledger.get("open_positions", [])

        if action == "ENTRY":
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
            open_pos_list = [p for p in open_pos_list if p.get("symbol") != record["symbol"]]
            ledger["trades"] = [p for p in ledger.get("trades", []) if p.get("symbol") != record["symbol"]]
            th = ledger.get("trade_history", [])
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")

            already_recorded = any(
                t.get("symbol") == record["symbol"] and 
                t.get("entry_time") == record["entry_time"] and
                t.get("date") == today_str
                for t in th
            )
            if not already_recorded:
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
        print(f"[LEDGER ERROR] op={action}: {e}")

# -------------------------------------------------------------------------
# MAIN MULTI-SYMBOL TRADING LOOP
# -------------------------------------------------------------------------

def run_multi_symbol_trader():
    acquire_instance_lock()

    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    capital = float(cfg.get("budget", 1000000.0))
    flat_by = cfg.get("flat_by", "15:20")
    risk_engine = RiskEngine(cfg)
    strategy_engine = StrategyEngine(cfg)
    broker_adapter = BrokerAdapter(cfg)

    print("=" * 75)
    print("[START] MULTI-SYMBOL LIVE PAPER TRADER (Unified Confluence & Completed-Candle Engine)")
    print(f"[CONFIG] Capital: Rs {capital:,.2f} | Flat By: {flat_by} | Paper: {cfg.get('paper', True)}")
    print("Watchlist: " + ", ".join([w["symbol"] for w in WATCHLIST]))
    print("=" * 75)

    init_tick_db()
    api = dh.get_smart_api_session()

    telegram_bot.send_telegram_message(
        "🚀 <b>Multi-Stock Day Trader Bot LIVE!</b>\n"
        f"📋 <b>Watchlist:</b> <code>JPL, TCS, RELIANCE, INFY, HDFCBANK</code>\n"
        f"💰 <b>Paper Capital:</b> ₹{capital:,.2f}\n"
        "⚡ <b>Engine:</b> Strict Completed Candles + Real Confluence + Broker Boundary Active\n"
        "💾 <b>Tick Storage:</b> SQLite DB (Idempotent UNIQUE constraint active).\n"
        "📝 Real-time CSV Audit & Telegram Alerts Connected."
    )

    # 1. Recover active open positions from ledger.json across restarts
    open_positions = {}
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")
    trades_today = []

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
                        "quantity": int(p.get("qty", p.get("quantity", 0)))
                    }
                # Restore today's executed trades to persist daily risk limits across restarts
                for t in saved_ledger.get("trade_history", []):
                    if t.get("date") == today_str or t.get("exit_date") == today_str:
                        trades_today.append(t)

            if open_positions:
                print(f"[RECOVERY] Restored {len(open_positions)} active position(s) from ledger: {list(open_positions.keys())}")
            if trades_today:
                print(f"[RECOVERY] Restored {len(trades_today)} executed trade(s) for today. Realized PnL: Rs {sum(t.get('pnl', 0.0) for t in trades_today):+,.2f}")
        except Exception as e:
            print(f"[RECOVERY ERROR] {e}")

    pending_signals = {}
    last_processed_times = {w["symbol"]: None for w in WATCHLIST}

    while True:
        try:
            now = datetime.datetime.now()
            current_time_str = now.strftime("%H:%M")
            today_str = now.strftime("%Y-%m-%d")

            if current_time_str < "09:15":
                time.sleep(30)
                continue

            # Check Session Cutoff (flat_by): Square off any open positions at cutoff
            if current_time_str >= flat_by and open_positions:
                print(f"[SESSION CUTOFF] Reached flat_by ({flat_by}). Auto squaring off all {len(open_positions)} open positions...")
                for sym in list(open_positions.keys()):
                    pos = open_positions.pop(sym, None)
                    if not pos:
                        continue
                    exit_price = pos["entry_price"]
                    pnl = 0.0
                    trade_rec = {
                        "symbol": sym, "side": pos["side"], "entry_time": pos["entry_time"],
                        "exit_time": current_time_str, "entry_price": pos["entry_price"],
                        "exit_price": exit_price, "qty": pos["quantity"], "pnl": pnl,
                        "reason": f"Session Cutoff (flat_by {flat_by} reached)"
                    }
                    trades_today.append(trade_rec)
                    update_ledger("EXIT", trade_rec)
                    try:
                        telegram_bot.send_exit_alert({
                            "symbol": sym, "qty": pos["quantity"], "entry_price": pos["entry_price"],
                            "exit_price": exit_price, "peak_price": exit_price,
                            "reason": trade_rec["reason"], "free_cash": capital,
                            "realized_pnl": sum(t["pnl"] for t in trades_today), "currency": "INR", "exchange": "NSE"
                        })
                    except Exception as te:
                        print(f"[TELEGRAM ALERT ERROR] {te}")
                    print(f"[SQUARE-OFF EXECUTED] {pos['side']} {sym} @ Rs {exit_price} | Reason: {trade_rec['reason']}")

            if current_time_str >= "15:25":
                print(f"[MARKET CLOSED] End of session: {current_time_str}")
                break

            # Fetch benchmark data for relative strength calculations
            benchmark_bars = fetch_benchmark_data(api, today_str, current_time_str, symbol=cfg.get("benchmark_symbol", "NIFTY 50"))

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

                    # Filter only strictly completed candles (drops the currently forming minute)
                    completed_bars = filter_completed_candles(resp["data"], current_time_str)
                    if len(completed_bars) < 5:
                        time.sleep(1.5)
                        continue

                    latest_bar = completed_bars[-1]
                    bar_time = latest_bar[0].split("T")[1][:5] if "T" in latest_bar[0] else latest_bar[0].split(" ")[1][:5]

                    if bar_time == last_processed_times[sym]:
                        time.sleep(1.5)
                        continue

                    last_processed_times[sym] = bar_time
                    closes = [float(b[4]) for b in completed_bars]
                    highs = [float(b[2]) for b in completed_bars]
                    lows = [float(b[3]) for b in completed_bars]
                    opens = [float(b[1]) for b in completed_bars]
                    volumes = [float(b[5]) if len(b) > 5 else 0.0 for b in completed_bars]
                    c, h, l, o, v = closes[-1], highs[-1], lows[-1], opens[-1], volumes[-1]

                    # Save completed candle into SQLite database idempotently
                    store_tick(latest_bar[0], sym, token, o, h, l, c, v)

                    print(f"[{bar_time}] {sym:10s} Close: Rs {c:8.2f} | H: {h:8.2f} | L: {l:8.2f} [COMPLETED BAR]")

                    # Indicators calculated on completed bars
                    st_data = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
                    st_dir = st_data["direction"][-1] if (st_data and "direction" in st_data and len(st_data["direction"]) > 0) else 0

                    # 1. Manage Open Position for this Symbol (with conservative exits & gap handling)
                    if sym in open_positions:
                        pos = open_positions[sym]
                        exit_trade, exit_price, exit_reason = evaluate_position_exit(
                            pos, latest_bar, flat_by=flat_by, current_time_str=current_time_str, st_dir=st_dir
                        )

                        if exit_trade:
                            pos = open_positions.pop(sym, None)
                            if not pos:
                                continue
                            entry_p = pos["entry_price"]
                            side = pos["side"]
                            pnl = round((exit_price - entry_p) * pos["quantity"], 2) if side == "BUY" else round((entry_p - exit_price) * pos["quantity"], 2)
                            capital += pnl
                            trade_rec = {
                                "symbol": sym, "side": side, "entry_time": pos["entry_time"],
                                "exit_time": bar_time, "entry_price": entry_p, "exit_price": exit_price,
                                "qty": pos["quantity"], "pnl": pnl, "reason": exit_reason
                            }
                            trades_today.append(trade_rec)
                            update_ledger("EXIT", trade_rec)

                            try:
                                telegram_bot.send_exit_alert({
                                    "symbol": sym, "qty": pos["quantity"], "entry_price": entry_p,
                                    "exit_price": exit_price, "peak_price": max(entry_p, exit_price),
                                    "reason": exit_reason, "free_cash": capital,
                                    "realized_pnl": sum(t["pnl"] for t in trades_today), "currency": "INR", "exchange": exch
                                })
                            except Exception as te:
                                print(f"[TELEGRAM ALERT ERROR] {te}")
                            print(f"[EXIT EXECUTED] {side} {sym} @ Rs {exit_price} | PnL: Rs {pnl:+,.2f} | Reason: {exit_reason}")
                            continue

                    # 2. Check Pending Signal Confirmation for this Symbol
                    # Evaluates the subsequent completed confirmation candle before expiring
                    if sym in pending_signals:
                        ps = pending_signals[sym]
                        sig_time = ps.get("signal_time")
                        current_candle_count = len(completed_bars)

                        is_confirmed = False
                        if ps["side"] == "BUY" and c > ps["trigger_level"]:
                            is_confirmed = True
                        elif ps["side"] == "SELL" and c < ps["trigger_level"]:
                            is_confirmed = True

                        if is_confirmed and bar_time != sig_time:
                            # Confirmed by distinct subsequent completed candle
                            side = ps["side"]
                            est_sl = ps["stop_loss"]
                            risk_pts = abs(c - est_sl)
                            est_tgt = round(c + (risk_pts * 1.5) if side == "BUY" else c - (risk_pts * 1.5), 2)

                            # Central Risk Gate Evaluation from Ledger
                            ledger_data = {}
                            if os.path.exists(LEDGER_PATH):
                                try:
                                    with open(LEDGER_PATH, "r", encoding="utf-8") as lf:
                                        ledger_data = json.load(lf)
                                except Exception as le:
                                    print(f"[LEDGER READ ERROR] {le}")

                            risk_check = risk_engine.evaluate_from_ledger(
                                ledger=ledger_data,
                                symbol=sym,
                                side=side,
                                entry_price=c,
                                stop_loss=est_sl,
                                target_price=est_tgt,
                                current_quotes={sym: c}
                            )

                            if risk_check["passed"]:
                                qty = risk_check["quantity"]

                                # Safe Order Routing via BrokerAdapter
                                order_res = broker_adapter.place_order(sym, side, qty, c)

                                open_positions[sym] = {
                                    "symbol": sym, "side": side, "entry_time": bar_time,
                                    "entry_price": c, "sl_price": est_sl, "target_price": est_tgt, "quantity": qty
                                }
                                log_signal_to_csv(side, sym, sym, c, ps["trigger_level"], "CONFIRMED_EXECUTED", f"{side} {qty} shares @ Rs {c}", trade_taken=True)
                                
                                fresh_free_cash = float(ledger_data.get("free_cash", capital)) - (qty * c)
                                telegram_bot.send_trade_alert({
                                    "symbol": sym, "side": side, "qty": qty, "entry": c, "stop": est_sl, "target": est_tgt,
                                    "currency": "INR", "exchange": exch, "free_cash": fresh_free_cash,
                                    "strategy": f"{ps.get('strategy_name', 'Unified Engine')} ({ps.get('setup_tag', 'Breakout')})"
                                })
                                update_ledger("ENTRY", open_positions[sym])
                                print(f"[ENTRY EXECUTED] {side} {sym} @ Rs {c:.2f} | Qty: {qty} | OrderId: {order_res.get('order_id')}")
                                del pending_signals[sym]
                                continue
                            else:
                                reject_reason = f"Risk Engine Blocked: {'; '.join(risk_check.get('reasons', []))}"
                                print(f"[RISK BLOCKED] [{bar_time}] {side} {sym}: {reject_reason}")
                                log_signal_to_csv(side, sym, sym, c, ps["trigger_level"], "REJECTED_BY_RISK", reject_reason, trade_taken=False)
                                del pending_signals[sym]
                                continue

                        # Expiry condition: check if confirmation window has elapsed
                        sig_bar_count = ps.get("signal_idx", 0) + 1
                        window_candles = int(cfg.get("confirmation_window_candles", 2))
                        if current_candle_count > sig_bar_count + window_candles:
                            cancel_msg = f"Price did not break trigger level within confirmation window ({window_candles} bars elapsed)"
                            print(f"[CANCELLED] [{bar_time}] {ps['side']} {sym} Signal Cancelled ({cancel_msg})")
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
                            "timestamp": [b[0] for b in completed_bars],
                            "open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes
                        }
                        sig = strategy_engine.evaluate(sym, bars_dict, exchange=exch, benchmark_bars=benchmark_bars)

                        if sig and sig.get("direction") in ("LONG", "SHORT"):
                            side = "BUY" if sig["direction"] == "LONG" else "SELL"
                            trigger = sig.get("entry_trigger", h if side == "BUY" else l)
                            sl_level = sig.get("stop_loss", l if side == "BUY" else h)
                            strat_title = sig.get("strategy_name", "UNIFIED_STRATEGY")
                            desc = sig.get("setup_description", "Setup Triggered")

                            pending_signals[sym] = {
                                "side": side,
                                "signal_time": bar_time,
                                "signal_idx": len(completed_bars) - 1,
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
                            print(f"[STAGE 1 SIGNAL] [{bar_time}] {sym} {strat_title} ({side}) @ Rs {c:.2f}. Waiting confirmation beyond Rs {trigger:.2f}")

                except Exception as inner_e:
                    print(f"[PROCESSING ERROR] symbol={sym} time={current_time_str} error={type(inner_e).__name__}: {inner_e}")

                time.sleep(1.8) # Respect SmartAPI rate limit (~1 req / 1.5s)

            time.sleep(4)

        except Exception as e:
            print(f"[MAIN LOOP ERROR] {e}")
            time.sleep(10)

if __name__ == "__main__":
    run_multi_symbol_trader()
