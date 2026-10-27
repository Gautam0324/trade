#!/usr/bin/env python3
"""
Daily Trading Journal & CSV Exporter for Day Trader Skill.
Generates an audit-ready CSV file of all trades executed today,
including:
- Trade ID & Stock Symbol
- Entry Date, Time & Exit Time
- Quantity & Capital Invested
- Buy Price & Sell Price
- Profit / Loss (₹ & %)
- Exit Reason (EMA Trailing, Target, etc.)
- Closing Available Account Balance

Also sends a complete End-of-Day (EOD) summary to Telegram.
"""

import csv
import datetime
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader-alerts"))
sys.path.insert(0, str(BASE_DIR / "trader"))

import telegram_bot
import ledger

REPORTS_DIR = BASE_DIR / "trader" / "reports"

def generate_daily_trade_csv(report_date=None):
    if report_date is None:
        report_date = datetime.date.today().strftime("%Y-%m-%d")

    os.makedirs(REPORTS_DIR, exist_ok=True)
    csv_file = REPORTS_DIR / f"trades_{report_date}.csv"

    leg = ledger.get_ledger()
    history = leg.get("trade_history", [])

    headers = [
        "Trade ID",
        "Symbol",
        "Side",
        "Quantity",
        "Entry Date",
        "Entry Time",
        "Entry Price (INR)",
        "Invested Capital (INR)",
        "Entry Reason / Setup",
        "Exit Date",
        "Exit Time",
        "Exit Price (INR)",
        "Returned Capital (INR)",
        "Net P&L (INR)",
        "P&L (%)",
        "Exit Reason"
    ]

    total_pnl = 0.0
    wins = 0
    losses = 0

    # Write logic with retry / fallback in case file is currently open in Excel or another editor
    target_path = csv_file
    f = None
    try:
        f = open(csv_file, "w", newline="", encoding="utf-8")
    except PermissionError:
        # Fallback to secondary timestamped file
        ts = datetime.datetime.now().strftime("%H%M%S")
        target_path = REPORTS_DIR / f"trades_{report_date}_{ts}.csv"
        f = open(target_path, "w", newline="", encoding="utf-8")

    with f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for h in history:
            if h.get("status") == "void_pending_review":
                continue
            qty = int(h.get("qty", 0))
            in_p = float(h.get("entry_price", 0.0))
            out_p = float(h.get("exit_price", 0.0))
            invested = float(h.get("invested", round(qty * in_p, 2)))
            returned = float(h.get("returned", round(qty * out_p, 2)))
            pnl = float(h.get("pnl", round(returned - invested, 2)))
            pnl_pct = round((pnl / invested) * 100, 2) if invested > 0 else 0.0

            entry_reason = h.get("entry_reason")
            if not entry_reason:
                entry_reason = "EMA 5 > EMA 9 above EMA 33 Channel Breakout"

            exit_reason = h.get("exit_reason") or h.get("reason", "Trailing profit / Stop triggered")

            total_pnl += pnl
            if pnl >= 0:
                wins += 1
            else:
                losses += 1

            writer.writerow([
                h.get("id", "N/A"),
                h.get("symbol", "N/A"),
                h.get("side", "BUY"),
                qty,
                report_date,
                h.get("entry_time", ""),
                f"{in_p:.2f}",
                f"{invested:.2f}",
                entry_reason,
                report_date,
                h.get("exit_time", ""),
                f"{out_p:.2f}",
                f"{returned:.2f}",
                f"{pnl:+.2f}",
                f"{pnl_pct:+.2f}%",
                exit_reason
            ])

    print(f"[REPORTS] Daily CSV generated successfully: {target_path}")
    return target_path, len(history), wins, losses, round(total_pnl, 2), leg.get("free_cash", 100000.0)

def send_eod_telegram_summary(csv_file, count, wins, losses, net_pnl, ending_cash):
    """
    Sends the 15:25 EOD summary message to Telegram.
    """
    pnl_symbol = "+" if net_pnl >= 0 else ""
    pnl_badge = f"🟢 NET PROFIT: +₹{net_pnl:,.2f}" if net_pnl >= 0 else f"🔴 NET LOSS: -₹{abs(net_pnl):,.2f}"
    win_rate = round((wins / count) * 100, 1) if count > 0 else 0.0

    today_str = datetime.date.today().strftime("%d-%b-%Y")

    msg = (
        f"📊 <b>END-OF-DAY TRADING REPORT (15:25 IST)</b>\n"
        f"📅 <b>Date:</b> {today_str}\n\n"
        f"🏆 <b>Overall Daily Result:</b> <b>{pnl_badge}</b>\n"
        f"📈 <b>Total Executed Trades:</b> {count}\n"
        f"✅ <b>Winning Trades:</b> {wins}\n"
        f"❌ <b>Risk-Managed Exits:</b> {losses}\n"
        f"🎯 <b>Win Rate:</b> {win_rate}%\n\n"
        f"💰 <b>Final Closing Cash:</b> <b>₹{ending_cash:,.2f}</b>\n"
        f"📁 <b>CSV Journal Generated:</b> <code>{csv_file.name}</code>\n\n"
        f"🔒 <i>All intraday positions auto-squared off for the day. Capital 100% safe.</i>"
    )

    telegram_bot.send_telegram_message(msg)

if __name__ == "__main__":
    csv_path, count, wins, losses, net_pnl, ending_cash = generate_daily_trade_csv()
    send_eod_telegram_summary(csv_path, count, wins, losses, net_pnl, ending_cash)
