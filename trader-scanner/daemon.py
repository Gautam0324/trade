#!/usr/bin/env python3
"""
Continuous Automated Day Trader Daemon for Indian Markets (NSE / BSE).
- Scans top liquid Indian stocks continuously during trading hours (09:15 - 15:30 IST).
- Strategy: EMA 5, 9, 33 High, 33 Low Breakout.
- Sizing: Within ₹100,000 budget & ₹1,000 max risk per trade.
- Tracks Free Available Cash: Minus on buy, adds on profit exit.
- Dispatches instant Telegram alerts with Remaining Available Balance.
- Trailing Profit Lock: Exits immediately when price dips below EMA 5 / peak.
"""

import datetime
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader-alerts"))
sys.path.insert(0, str(BASE_DIR / "trader-scanner"))
sys.path.insert(0, str(BASE_DIR / "trader"))

import telegram_bot
import ema_strategy
import ledger

from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from exit_engine import PositionManager

# Load unified configuration
CONFIG_PATH = BASE_DIR / "trader" / "config.json"
app_config = {}
if os.path.exists(CONFIG_PATH):
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            app_config = json.load(f)
    except Exception:
        pass

strategy_eng = StrategyEngine(app_config)
risk_eng = RiskEngine(app_config)
position_mgr = PositionManager(app_config)

def load_watchlist():
    """
    Loads stock codes dynamically from stocks.csv.
    Appends .NS for NSE ticker quotes and prioritizes active liquid stocks.
    """
    stocks_csv_path = BASE_DIR / "stocks.csv"
    symbols = []
    if os.path.exists(stocks_csv_path):
        try:
            import csv
            with open(stocks_csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    sym = row.get("SYMBOL", "").strip()
                    series = row.get("SERIES", "").strip()
                    # Focus on EQ (standard equity) series
                    if sym and series in ("EQ", ""):
                        symbols.append(f"{sym}.NS")
        except Exception as e:
            print(f"[DAEMON] Error reading stocks.csv: {e}")

    if not symbols:
        symbols = [
            "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS",
            "ICICIBANK.NS", "TATAMOTORS.NS", "SBIN.NS", "TCS.BO"
        ]
    return symbols

WATCHLIST = load_watchlist()
print(f"[DAEMON] Loaded {len(WATCHLIST)} stock codes from stocks.csv for trading.")

# Cooldown tracker to prevent rapid churn on the same symbol (e.g. 5 minutes)
COOLDOWN_MINUTES = int(app_config.get("cooldown_minutes", 5))
last_exit_timestamps = {}

def fetch_candles(symbol):
    tf = app_config.get("candle_timeframe", "5m")
    rng = app_config.get("candle_range", "5d")
    sym_query = symbol
    if symbol in ("NIFTY 50", "NIFTY"):
        sym_query = "^NSEI"
    elif symbol in ("BANKNIFTY", "NIFTY BANK"):
        sym_query = "^NSEBANK"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym_query)}?range={rng}&interval={tf}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DayTraderDaemon/2.0)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
            res = data["chart"]["result"][0]
            quotes = res["indicators"]["quote"][0]
            raw_o = quotes.get("open", [])
            raw_h = quotes.get("high", [])
            raw_l = quotes.get("low", [])
            raw_c = quotes.get("close", [])
            raw_v = quotes.get("volume", [])
            
            # Filter out null bars from market pauses/session gaps
            clean_o, clean_h, clean_l, clean_c, clean_v = [], [], [], [], []
            for i in range(len(raw_c)):
                o = raw_o[i] if i < len(raw_o) else None
                h = raw_h[i] if i < len(raw_h) else None
                l = raw_l[i] if i < len(raw_l) else None
                c = raw_c[i]
                v = raw_v[i] if i < len(raw_v) else 0
                if None not in (o, h, l, c):
                    clean_o.append(float(o))
                    clean_h.append(float(h))
                    clean_l.append(float(l))
                    clean_c.append(float(c))
                    clean_v.append(float(v) if v is not None else 0.0)

            return {
                "open": clean_o,
                "high": clean_h,
                "low": clean_l,
                "close": clean_c,
                "volume": clean_v
            }
    except Exception as e:
        print(f"[DAEMON] Error fetching {symbol}: {e}")
        return None

def monitor_cycle():
    leg = ledger.get_ledger()
    free_cash = leg["free_cash"]
    open_positions = leg.get("open_positions", [])

    print(f"\n[SCAN] Starting cycle. Free Balance: Rs. {free_cash:,.2f} | Active Positions: {len(open_positions)}")

    # 1. First, check open positions using Centralized PositionManager
    for pos in list(open_positions):
        sym = pos["symbol"]
        quotes = fetch_candles(sym)
        if not quotes:
            continue

        closes = [c for c in quotes["close"] if c is not None]
        highs = [h for h in quotes.get("high", []) if h is not None]
        lows = [l for l in quotes.get("low", []) if l is not None]
        opens = [o for o in quotes.get("open", []) if o is not None]
        if not closes:
            continue

        cur_price = closes[-1]
        ema_eval = ema_strategy.evaluate_ema_setup(quotes)
        ema5 = ema_eval.get("ema5", cur_price)
        ema9 = ema_eval.get("ema9", cur_price)

        exit_res = position_mgr.evaluate_exit(
            pos,
            current_price=cur_price,
            current_ema5=ema5,
            current_ema9=ema9
        )

        # Update trailing position metrics in ledger
        ledger.update_open_position(sym, exit_res["updated_position"])

        if exit_res["action"] in ("PARTIAL_EXIT", "FULL_EXIT"):
            exit_qty = exit_res["exit_qty"]
            print(f"[{exit_res['action']}] {sym}: {exit_res['reason']} (Qty: {exit_qty})")
            ok, exit_item = ledger.record_trade_exit(pos["id"], cur_price, exit_res["reason"], exit_qty=exit_qty)
            if ok:
                last_exit_timestamps[sym] = time.time()
                updated_leg = ledger.get_ledger()
                exit_payload = {
                    "symbol": sym,
                    "qty": exit_qty,
                    "entry_price": pos["entry_price"],
                    "peak_price": exit_res["updated_position"].get("peak_price", cur_price),
                    "exit_price": cur_price,
                    "reason": exit_res["reason"],
                    "currency": "INR",
                    "exchange": pos.get("exchange", "NSE"),
                    "free_cash": updated_leg["free_cash"],
                    "realized_pnl": updated_leg["realized_pnl"]
                }
                telegram_bot.send_exit_alert(exit_payload)

    # Reload config dynamically
    cfg = app_config
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass

    cur_time = datetime.datetime.now().strftime("%H:%M")
    no_entries_after = cfg.get("no_entries_after", "15:15")
    no_entries_before = cfg.get("no_entries_before", "09:30")
    if cur_time >= no_entries_after or cur_time < no_entries_before:
        return

    # 2. Check for New Entries only on 5-Minute Candle Close (00, 05, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55 min)
    now_dt = datetime.datetime.now()
    entry_close_only = cfg.get("entry_candle_close_only", True)
    if entry_close_only:
        # Check if we are within the first 60 seconds of a newly completed 5-minute bar
        minute_rem = now_dt.minute % 5
        # Example: at 10:15:00 to 10:15:59, minute_rem is 0
        if minute_rem != 0:
            return

    updated_leg = ledger.get_ledger()
    today_trades_count = ledger.get_today_entries_count()
    if today_trades_count >= risk_eng.max_trades_per_day:
        return

    if updated_leg["free_cash"] >= 10000 and len(updated_leg["open_positions"]) < risk_eng.max_open_positions:
        now_ts = time.time()
        for sym in WATCHLIST:
            # Skip if already holding
            if any(p["symbol"] == sym for p in updated_leg["open_positions"]):
                continue

            # Respect per-symbol cooldown period after exit
            last_exit = last_exit_timestamps.get(sym, 0)
            if (now_ts - last_exit) < (COOLDOWN_MINUTES * 60):
                continue

            quotes = fetch_candles(sym)
            if not quotes:
                continue

            # Validate completed candle bars
            import indicators
            is_valid, cleaned_quotes, val_err = indicators.validate_ohlcv_bars(quotes)
            if not is_valid:
                print(f"[DAEMON] Invalid bar data for {sym}: {val_err}")
                continue

            signal_payload = strategy_eng.evaluate_all(sym, cleaned_quotes)
            if signal_payload:
                # Log signal evaluation to SQLite DB
                try:
                    import db_v3
                    conn = db_v3.get_connection()
                    cur = conn.cursor()
                    cur.execute("""
                    INSERT INTO signals_audit (
                        timestamp, symbol, strategy_name, side, signal, confluence_score, accepted,
                        rejection_reasons, component_weights, indicator_values, entry_price, stop_loss, target_price,
                        risk_per_share, reward_risk_ratio
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                        sym,
                        signal_payload.get("strategy_name", "UNKNOWN"),
                        signal_payload.get("side", "BUY"),
                        signal_payload.get("signal", "NO_TRADE"),
                        signal_payload.get("confluence_score", 0.0),
                        1 if signal_payload.get("signal") == "BUY" else 0,
                        json.dumps(signal_payload.get("rejections", [])),
                        json.dumps(signal_payload.get("component_weights", {})),
                        json.dumps(signal_payload.get("indicator_values", {})),
                        signal_payload.get("entry_reference_price"),
                        signal_payload.get("stop_loss"),
                        signal_payload.get("target_price"),
                        signal_payload.get("risk_per_share"),
                        signal_payload.get("reward_risk_ratio")
                    ))
                    conn.commit()
                    conn.close()
                except Exception as dbe:
                    print(f"[DAEMON] DB log error: {dbe}")

            if signal_payload and signal_payload.get("signal") == "BUY":
                cur_price = signal_payload["entry_reference_price"]
                stop_price = signal_payload["stop_loss"]
                target_price = signal_payload["target_price"]

                daily_loss = ledger.get_today_realized_loss()
                risk_eval = risk_eng.evaluate_order_risk(
                    symbol=sym,
                    side="BUY",
                    entry_price=cur_price,
                    stop_loss=stop_price,
                    target_price=target_price,
                    available_cash=updated_leg["free_cash"],
                    daily_realized_loss=daily_loss,
                    current_trades_count=today_trades_count,
                    current_open_positions_count=len(updated_leg["open_positions"])
                )

                if risk_eval["passed"] and risk_eval["quantity"] > 0:
                    qty = risk_eval["quantity"]

                    ok, pos = ledger.record_trade_entry(
                        sym, "BUY", qty, cur_price,
                        "BSE" if ".BO" in sym else "NSE",
                        entry_reason=f"[{signal_payload['strategy_name']}] {signal_payload['signal_reason']}"
                    )
                    if ok:
                        latest_leg = ledger.get_ledger()
                        trade_payload = {
                            "symbol": sym,
                            "strategy": signal_payload["strategy_name"],
                            "side": "BUY",
                            "qty": str(qty),
                            "entry": cur_price,
                            "stop": stop_price,
                            "target": target_price,
                            "currency": "INR",
                            "exchange": "BSE" if ".BO" in sym else "NSE",
                            "ema_info": signal_payload.get("indicator_values", {}),
                            "free_cash": latest_leg["free_cash"]
                        }
                        telegram_bot.send_trade_alert(trade_payload)
                        break  # Take one trade per scan tick

DAEMON_LOCK_PATH = BASE_DIR / "trader" / "state" / "daemon.lock"

def acquire_daemon_lock():
    DAEMON_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    if os.path.exists(DAEMON_LOCK_PATH):
        try:
            with open(DAEMON_LOCK_PATH, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.isdigit():
                    pid = int(content)
                    if is_pid_alive(pid):
                        print(f"[DAEMON] Another daemon instance is already running with PID {pid}. Aborting startup.")
                        sys.exit(1)
        except Exception:
            pass

    with open(DAEMON_LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(os.getpid()))

def release_daemon_lock():
    try:
        if os.path.exists(DAEMON_LOCK_PATH):
            os.remove(DAEMON_LOCK_PATH)
    except Exception:
        pass

def is_pid_alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

if __name__ == "__main__":
    acquire_daemon_lock()
    import atexit
    atexit.register(release_daemon_lock)

    tf = app_config.get("candle_timeframe", "5m")
    print(f"[DAEMON] Continuous Auto-Trader started (PID: {os.getpid()}, Timeframe: {tf}, Strategy: {app_config.get('strategy_mode', 'EMA_CHANNEL')}).")
    print("[DAEMON] Scanning every 15 seconds with OHLC candle evaluation & cooldown protection...")
    import export_csv
    squareoff_done = False
    eod_done = False
    bhavcopy_updated = False

    while True:
        try:
            now = datetime.datetime.now()
            cur_time_str = now.strftime("%H:%M")

            # 1. At 15:20 to 15:25 IST: Close all remaining open positions at live quote (retry each tick)
            if "15:20" <= cur_time_str < "15:25":
                leg = ledger.get_ledger()
                open_pos = leg.get("open_positions", [])
                if open_pos:
                    print(f"[DAEMON {cur_time_str} IST] Auto-squaring off open positions with live quotes...")
                    for p in list(open_pos):
                        sym = p["symbol"]
                        quotes = fetch_candles(sym)
                        if quotes and quotes.get("close"):
                            closes = [c for c in quotes["close"] if c is not None]
                            if closes:
                                exit_price = float(closes[-1])
                                ok, exit_item = ledger.record_trade_exit(p["id"], exit_price, "Intraday EOD 15:20 Auto-Squareoff")
                                if ok:
                                    print(f"[DAEMON EOD] Closed {sym} @ ₹{exit_price:,.2f} P&L: ₹{exit_item['pnl']:,.2f}")
                            else:
                                print(f"[DAEMON EOD] Quote unavailable for {sym}, will retry next tick...")
                        else:
                            print(f"[DAEMON EOD] Quote unavailable for {sym}, will retry next tick...")

            # 2. At 15:25 IST: Final check, list any unclosed positions, generate CSV and send Telegram EOD summary (run once)
            if cur_time_str >= "15:25" and not eod_done:
                leg = ledger.get_ledger()
                unclosed = leg.get("open_positions", [])
                if unclosed:
                    print(f"[DAEMON 15:25 IST WARNING] Unclosed positions remaining due to missing quotes: {[p['symbol'] for p in unclosed]}")
                print("[DAEMON 15:25 IST] Generating Daily CSV Journal and sending Telegram report...")
                try:
                    csv_p, cnt, w, l, pnl, end_cash = export_csv.generate_daily_trade_csv()
                    export_csv.send_eod_telegram_summary(csv_p, cnt, w, l, pnl, end_cash)
                    eod_done = True
                except Exception as ex:
                    print(f"[DAEMON CSV ERROR] {ex}")

            # 3. At 16:00 IST: Fetch and update official Daily OHLC Bhavcopy for all stocks
            if cur_time_str >= "16:00" and not bhavcopy_updated:
                print("[DAEMON 16:00 IST] Updating daily OHLC Bhavcopy for all stocks...")
                try:
                    import update_daily_bhavcopy
                    ok = update_daily_bhavcopy.update_daily_ohlc()
                    if ok:
                        bhavcopy_updated = True
                except Exception as ex:
                    print(f"[DAEMON BHAVCOPY ERROR] {ex}")

            # Do NOT run monitor/new entries after 15:20 IST
            if cur_time_str < "15:20":
                monitor_cycle()
            else:
                if eod_done and (bhavcopy_updated or cur_time_str >= "16:15"):
                    print("[DAEMON] Market closed and 16:00 Daily OHLC updated. Trading halted.")
                    break
        except Exception as e:
            print(f"[DAEMON ERROR] {e}")
        time.sleep(15)
