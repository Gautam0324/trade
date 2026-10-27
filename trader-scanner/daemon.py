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

WATCHLIST = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "TATAMOTORS.NS",
    "SBIN.NS",
    "TCS.BO"
]

# Cooldown tracker to prevent rapid churn on the same symbol (e.g. 5 minutes)
COOLDOWN_MINUTES = int(app_config.get("cooldown_minutes", 5))
last_exit_timestamps = {}

def fetch_candles(symbol):
    tf = app_config.get("candle_timeframe", "5m")
    rng = app_config.get("candle_range", "5d")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval={tf}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DayTraderDaemon/2.0)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
            res = data["chart"]["result"][0]
            quotes = res["indicators"]["quote"][0]
            # Ensure full OHLC data structure is present
            return {
                "open": quotes.get("open", []),
                "high": quotes.get("high", []),
                "low": quotes.get("low", []),
                "close": quotes.get("close", []),
                "volume": quotes.get("volume", [])
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
            ok, exit_item = ledger.record_trade_exit(sym, cur_price, exit_res["reason"], exit_qty=exit_qty)
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
    today_trades = ledger.get_today_trades()
    today_trades_count = len(today_trades)
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

            signal_payload = strategy_eng.evaluate_all(sym, quotes)
            if signal_payload and signal_payload.get("signal") == "BUY":
                cur_price = signal_payload["entry_reference_price"]
                stop_price = signal_payload["stop_loss"]
                target_price = signal_payload["target_price"]

                risk_eval = risk_eng.evaluate_order_risk(
                    symbol=sym,
                    side="BUY",
                    entry_price=cur_price,
                    stop_loss=stop_price,
                    target_price=target_price,
                    available_cash=updated_leg["free_cash"],
                    daily_realized_loss=min(0.0, updated_leg["realized_pnl"]),
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

if __name__ == "__main__":
    tf = app_config.get("candle_timeframe", "5m")
    print(f"[DAEMON] Continuous Auto-Trader started (Timeframe: {tf}, Strategy: {app_config.get('strategy_mode', 'EMA_CHANNEL')}).")
    print("[DAEMON] Scanning every 15 seconds with OHLC candle evaluation & cooldown protection...")
    import export_csv
    squareoff_done = False
    eod_done = False

    while True:
        try:
            now = datetime.datetime.now()
            cur_time_str = now.strftime("%H:%M")

            # 1. At 15:20 IST: Close all remaining open positions before market close (run once)
            if cur_time_str >= "15:20" and not squareoff_done:
                leg = ledger.get_ledger()
                if leg.get("open_positions"):
                    print("[DAEMON 15:20 IST] Auto-squaring off all open intraday positions...")
                    for p in list(leg["open_positions"]):
                        ledger.record_trade_exit(p["symbol"], p["entry_price"], "Intraday EOD 15:20 Auto-Squareoff")
                squareoff_done = True

            # 2. At 15:25 IST: Generate CSV and send Telegram EOD summary (run once)
            if cur_time_str >= "15:25" and not eod_done:
                print("[DAEMON 15:25 IST] Generating Daily CSV Journal and sending Telegram report...")
                try:
                    csv_p, cnt, w, l, pnl, end_cash = export_csv.generate_daily_trade_csv()
                    export_csv.send_eod_telegram_summary(csv_p, cnt, w, l, pnl, end_cash)
                    eod_done = True
                except Exception as ex:
                    print(f"[DAEMON CSV ERROR] {ex}")

            # Do NOT run monitor/new entries after 15:20 IST
            if cur_time_str < "15:20":
                monitor_cycle()
            else:
                if eod_done:
                    print("[DAEMON] Market closed for the day. Trading halted.")
                    break
        except Exception as e:
            print(f"[DAEMON ERROR] {e}")
        time.sleep(15)
