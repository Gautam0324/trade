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

def fetch_candles(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=5d&interval=5m"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DayTraderDaemon/2.0)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode())
            res = data["chart"]["result"][0]
            quotes = res["indicators"]["quote"][0]
            return quotes
    except Exception as e:
        print(f"[DAEMON] Error fetching {symbol}: {e}")
        return None

def monitor_cycle():
    leg = ledger.get_ledger()
    free_cash = leg["free_cash"]
    open_positions = leg.get("open_positions", [])

    print(f"\n[SCAN] Starting cycle. Free Balance: Rs. {free_cash:,.2f} | Active Positions: {len(open_positions)}")

    # 1. First, check open positions for EMA5 Exit / Trailing Profit-Lock
    for pos in list(open_positions):
        sym = pos["symbol"]
        quotes = fetch_candles(sym)
        if not quotes:
            continue

        closes = [c for c in quotes["close"] if c is not None]
        if not closes:
            continue

        cur_price = closes[-1]
        entry_price = pos["entry_price"]
        peak_price = pos.get("peak_price", max(entry_price, cur_price))
        if cur_price > peak_price:
            peak_price = cur_price
            pos["peak_price"] = peak_price

        ema_eval = ema_strategy.evaluate_ema_setup(quotes)
        ema5 = ema_eval.get("ema5", cur_price)
        ema9 = ema_eval.get("ema9", cur_price)

        exit_check = ema_strategy.check_trailing_profit_exit(entry_price, peak_price, cur_price, ema5, ema9)
        if exit_check["should_exit"]:
            print(f"[EXIT] {sym} triggered exit: {exit_check['reason']}")
            ok, exit_item = ledger.record_trade_exit(sym, cur_price, exit_check["reason"])
            if ok:
                updated_leg = ledger.get_ledger()
                exit_payload = {
                    "symbol": sym,
                    "qty": pos["qty"],
                    "entry_price": entry_price,
                    "peak_price": peak_price,
                    "exit_price": cur_price,
                    "reason": exit_check["reason"],
                    "currency": "INR",
                    "exchange": pos.get("exchange", "NSE"),
                    "free_cash": updated_leg["free_cash"],
                    "realized_pnl": updated_leg["realized_pnl"]
                }
                telegram_bot.send_exit_alert(exit_payload)

    # 2. Check for New Entries if cash is available and open positions < 3, ONLY before 15:20 IST
    cur_time = datetime.datetime.now().strftime("%H:%M")
    if cur_time >= "15:20":
        return

    updated_leg = ledger.get_ledger()
    if updated_leg["free_cash"] >= 10000 and len(updated_leg["open_positions"]) < 3:
        for sym in WATCHLIST:
            # Skip if already holding
            if any(p["symbol"] == sym for p in updated_leg["open_positions"]):
                continue

            quotes = fetch_candles(sym)
            if not quotes:
                continue

            ema_eval = ema_strategy.evaluate_ema_setup(quotes)
            if ema_eval.get("signal") == "BUY":
                cur_price = ema_eval["close"]
                stop_price = ema_eval["ema33_low"]
                risk_per_share = max(0.50, round(cur_price - stop_price, 2))

                # Position sizing for ₹1,000 risk capped at available cash
                max_risk = 1000.0
                qty = max(1, int(max_risk / risk_per_share))
                cost = qty * cur_price

                # Cap to available cash
                if cost > (updated_leg["free_cash"] * 0.75):
                    qty = max(1, int((updated_leg["free_cash"] * 0.75) / cur_price))
                    cost = qty * cur_price

                if cost <= updated_leg["free_cash"]:
                    ok, pos = ledger.record_trade_entry(sym, "BUY", qty, cur_price, "BSE" if ".BO" in sym else "NSE")
                    if ok:
                        latest_leg = ledger.get_ledger()
                        trade_payload = {
                            "symbol": sym,
                            "side": "BUY",
                            "qty": str(qty),
                            "entry": cur_price,
                            "stop": stop_price,
                            "target": round(cur_price + (risk_per_share * 1.0), 2),
                            "currency": "INR",
                            "exchange": "BSE" if ".BO" in sym else "NSE",
                            "ema_info": ema_eval,
                            "free_cash": latest_leg["free_cash"]
                        }
                        telegram_bot.send_trade_alert(trade_payload)
                        break  # Take one trade per scan tick

if __name__ == "__main__":
    print("[DAEMON] Continuous Auto-Trader started (EMA5/9/33 Strategy with Trailing Profit-Lock).")
    print("[DAEMON] Scanning every 15 seconds for live market breakouts and trailing exits...")
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
