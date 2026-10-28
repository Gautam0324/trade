#!/usr/bin/env python3
"""
Telegram notification module for Day Trader Skill.
Sends formatted alerts, trade proposals, fills, and daily P&L updates.
Standard library only (urllib.request).
"""

import datetime
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRETS_PATH = BASE_DIR / "trader" / "secrets.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"

def get_telegram_creds():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if os.path.exists(SECRETS_PATH):
        try:
            with open(SECRETS_PATH, "r", encoding="utf-8") as f:
                sec = json.load(f)
                token = token or sec.get("telegram_bot_token")
                chat_id = chat_id or sec.get("telegram_chat_id")
        except Exception:
            pass

    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                token = token or cfg.get("telegram_bot_token")
                chat_id = chat_id or cfg.get("telegram_chat_id")
        except Exception:
            pass
            
    return token, chat_id

def send_telegram_message(text, parse_mode="HTML"):
    token, chat_id = get_telegram_creds()
    if not token or not chat_id:
        print("[TELEGRAM] Missing bot token or chat ID. Update trader/config.json or set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        return False, "Missing credentials"
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "User-Agent": "DayTraderSkill/2.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            res_data = json.loads(resp.read().decode())
            if res_data.get("ok"):
                print("[TELEGRAM] Message delivered successfully.")
                return True, "Delivered"
            return False, res_data.get("description", "Unknown error")
    except Exception as e:
        print(f"[TELEGRAM] Error sending message: {e}")
        return False, str(e)

def send_signal_alert(signal_data):
    """
    Stage 1: Sends a Telegram alert immediately when a SIGNAL CANDLE completes.
    Notifies that setup criteria are met and watching for Confirmation Candle.
    """
    sym = signal_data.get("symbol", "N/A")
    side = signal_data.get("side", "BUY").upper()
    spot_price = float(signal_data.get("spot_price", signal_data.get("entry_reference_price", 0.0)))
    trigger_level = float(signal_data.get("trigger_level", signal_data.get("candle_high", spot_price)))
    sl_level = float(signal_data.get("stop_loss", signal_data.get("candle_low", spot_price * 0.99)))
    strategy = signal_data.get("strategy_name", "CONFLUENCE")
    opt_contract = signal_data.get("option_contract", "")
    candle_time = signal_data.get("candle_time") or datetime.datetime.now().strftime("%I:%M %p IST")
    exchange = signal_data.get("exchange", "NSE")

    side_emoji = "🟢" if "BUY" in side else "🔴"
    opt_line = f"🎯 <b>Watchlist Option:</b> <code>{opt_contract}</code>\n" if opt_contract else ""

    msg = (
        f"🟡 <b>[STAGE 1: SIGNAL FORMED] {side_emoji} {sym}</b>\n\n"
        f"📌 <b>Stock Symbol:</b> <code>{sym}</code> ({exchange})\n"
        f"🧭 <b>Action:</b> <b>{side}</b>\n"
        f"🧠 <b>Strategy:</b> <code>{strategy}</code>\n"
        f"🕒 <b>Candle Time:</b> <code>{candle_time}</code>\n"
        f"📍 <b>Signal Spot Close:</b> ₹{spot_price:,.2f}\n"
        f"{opt_line}"
        f"----------------------------------------\n"
        f"👀 <b>Confirmation Trigger:</b>\n"
        f"  • Waiting for candle to cross: <b>₹{trigger_level:,.2f}</b>\n"
        f"  • SL Benchmark Level: <b>₹{sl_level:,.2f}</b>\n\n"
        f"⏳ <i>NO ENTRY YET. Waiting for confirmation breakout before placing order!</i>"
    )
    return send_telegram_message(msg)

def send_signal_cancelled_alert(cancel_data):
    """
    Sends an alert when a signal candle does not receive confirmation
    within the allowed window or if price invalidates the setup.
    """
    sym = cancel_data.get("symbol", "N/A")
    reason = cancel_data.get("reason", "Price failed to cross trigger level / Timed out")
    trigger_level = float(cancel_data.get("trigger_level", 0.0))
    candle_time = cancel_data.get("candle_time") or datetime.datetime.now().strftime("%I:%M %p IST")
    exchange = cancel_data.get("exchange", "NSE")

    msg = (
        f"⚪ <b>[SIGNAL CANCELLED] ❌ {sym}</b>\n\n"
        f"📌 <b>Stock Symbol:</b> <code>{sym}</code> ({exchange})\n"
        f"🕒 <b>Time:</b> <code>{candle_time}</code>\n"
        f"⚠️ <b>Reason:</b> {reason}\n"
        f"🎯 <b>Unmet Trigger Level:</b> ₹{trigger_level:,.2f}\n"
        f"----------------------------------------\n"
        f"🛡️ <i>No trade taken. Capital 100% safe! Waiting for fresh setup.</i>"
    )
    return send_telegram_message(msg)

def send_trade_alert(trade_data):
    """
    Format and send a trade alert to Telegram with clean emojis and key levels,
    including the actual deployed capital amount and expected profit.
    """
    sym = trade_data.get("symbol", "N/A")
    side = trade_data.get("side", "BUY").upper()
    qty_num = int(trade_data.get("qty", "0"))
    entry = float(trade_data.get("entry", 0.0))
    stop = float(trade_data.get("stop", 0.0))
    target = float(trade_data.get("target", 0.0))
    
    currency = trade_data.get("currency", "INR")
    sym_icon = "₹" if currency == "INR" else "$"
    exchange = trade_data.get("exchange", "NSE")

    # Actual deployed investment amount
    total_amount = round(qty_num * entry, 2)
    max_loss = round(qty_num * abs(entry - stop), 2)
    expected_profit = round(qty_num * abs(target - entry), 2)

    # Indicator metrics
    ema_info = trade_data.get("ema_info")
    ema_block = ""
    if ema_info:
        ema_block = (
            f"📈 <b>EMA Indicator Status:</b>\n"
            f"  • EMA 5 (Fast): {sym_icon}{ema_info.get('ema5', 0):,.2f}\n"
            f"  • EMA 9 (Signal): {sym_icon}{ema_info.get('ema9', 0):,.2f}\n"
            f"  • EMA 33 High (Band Top): {sym_icon}{ema_info.get('ema33_high', 0):,.2f}\n"
            f"  • EMA 33 Low (Band Base): {sym_icon}{ema_info.get('ema33_low', 0):,.2f}\n"
            f"  • Trend: {'🟢 Bullish Breakout (Above 33 High)' if ema_info.get('is_above_33h') else '⚪ Channel Rebound'}\n\n"
        )

    # Live Balance Breakdown
    free_cash = trade_data.get("free_cash")
    balance_line = ""
    if free_cash is not None:
        balance_line = f"💳 <b>Remaining Available Balance:</b> <b>{sym_icon}{free_cash:,.2f}</b>\n"

    # Entry Date & Time (IST)
    entry_datetime = trade_data.get("entry_time") or datetime.datetime.now().strftime("%d-%b-%Y  |  %I:%M:%S %p IST")

    strategy_name = trade_data.get("strategy", "EMA_CHANNEL")

    msg = (
        f"🚨 <b>DAY TRADER ALERT: {side} {sym} ({exchange})</b>\n"
        f"🧠 <b>Strategy:</b> <code>{strategy_name}</code>\n\n"
        f"📅 <b>Entry Date & Time:</b> <code>{entry_datetime}</code>\n"
        f"💰 <b>Total Trade Amount:</b> {sym_icon}{total_amount:,.2f}\n"
        f"📦 <b>Quantity:</b> {qty_num} Shares\n\n"
        f"🎯 <b>Entry Price:</b> {sym_icon}{entry:,.2f}\n"
        f"🛑 <b>Stop-Loss:</b> {sym_icon}{stop:,.2f}  (-{sym_icon}{max_loss:,.2f})\n"
        f"🏁 <b>Target (1.5R):</b> {sym_icon}{target:,.2f}  (+{sym_icon}{expected_profit:,.2f})\n\n"
        f"{ema_block}"
        f"⚠️ <b>Max Risk:</b> {sym_icon}{max_loss:,.2f} (Capped at 1% of budget)\n"
        f"⏰ <b>Intraday Auto Square-Off:</b> 15:20 IST\n"
        f"----------------------------------------\n"
        f"{balance_line}"
        f"🛡️ <i>Risk Check: PASSED. Capital deducted from available cash.</i>"
    )
    return send_telegram_message(msg)

def send_exit_alert(exit_data):
    """
    Format and send an immediate profit booking / trailing exit alert
    when price dips below EMA5 / trailing peak, showing updated available cash.
    """
    sym = exit_data.get("symbol", "N/A")
    qty = int(exit_data.get("qty", 0))
    entry_price = float(exit_data.get("entry_price", 0.0))
    peak_price = float(exit_data.get("peak_price", entry_price))
    exit_price = float(exit_data.get("exit_price", 0.0))
    reason = exit_data.get("reason", "Price dipped below EMA5 / trailing high")
    currency = exit_data.get("currency", "INR")
    sym_icon = "₹" if currency == "INR" else "$"
    exchange = exit_data.get("exchange", "NSE")

    # Profit calculations
    profit_per_share = round(exit_price - entry_price, 2)
    total_pnl = round(profit_per_share * qty, 2)
    pnl_pct = round((profit_per_share / entry_price) * 100, 2) if entry_price > 0 else 0.0

    if total_pnl > 0:
        pnl_badge = f"🟢 PROFIT: +{sym_icon}{total_pnl:,.2f} (+{pnl_pct}%)"
        header_title = "⚡ <b>TRADE EXITED: PROFIT BOOKED!</b> ⚡"
        footer_text = "✅ <i>Profit locked in. Position closed and capital released.</i>"
    elif total_pnl < 0:
        pnl_badge = f"🔴 LOSS: -{sym_icon}{abs(total_pnl):,.2f} ({pnl_pct}%)"
        header_title = "🛑 <b>TRADE EXITED: LOSS CUT / STOP-LOSS HIT</b> 🛑"
        footer_text = "🛡️ <i>Strict risk control enforced. Capital protected for next setup.</i>"
    else:
        pnl_badge = f"⚪ BREAKEVEN: ₹0.00 (0.00%)"
        header_title = "🔄 <b>TRADE EXITED: BREAKEVEN / FLAT SQUARE-OFF</b> 🔄"
        footer_text = "⚖️ <i>Position closed flat. Full capital released without loss.</i>"

    free_cash = exit_data.get("free_cash")
    realized_pnl = exit_data.get("realized_pnl")
    balance_block = ""
    if free_cash is not None:
        balance_block = (
            f"----------------------------------------\n"
            f"💳 <b>Updated Available Balance:</b> <b>{sym_icon}{free_cash:,.2f}</b>\n"
            f"📊 <b>Total Realized P&L:</b> <b>{'+' if (realized_pnl or 0) >= 0 else ''}{sym_icon}{(realized_pnl or 0):,.2f}</b>\n"
        )

    msg = (
        f"{header_title}\n\n"
        f"🏷️ <b>Stock:</b> <code>{sym}</code> ({exchange})\n"
        f"📦 <b>Quantity:</b> {qty} Shares\n"
        f"💵 <b>Result:</b> <b>{pnl_badge}</b>\n\n"
        f"📍 <b>Buy Entry Price:</b> {sym_icon}{entry_price:,.2f}\n"
        f"🚀 <b>Peak Reached:</b> {sym_icon}{peak_price:,.2f}\n"
        f"🏁 <b>Exit Price:</b> {sym_icon}{exit_price:,.2f}\n\n"
        f"🔍 <b>Trigger Reason:</b> {reason}\n"
        f"💰 <b>Cash Credited Back:</b> {sym_icon}{(qty * exit_price):,.2f}\n\n"
        f"{balance_block}"
        f"{footer_text}"
    )
    return send_telegram_message(msg)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text = " ".join(sys.argv[1:])
        send_telegram_message(text)
    else:
        # Test notification
        token, chat_id = get_telegram_creds()
        if token and chat_id:
            send_telegram_message("🤖 <b>Day Trader Bot Connected!</b>\nTelegram alerting is active for BSE/NSE trades in ₹ INR.")
        else:
            print("Usage: python telegram_bot.py 'Your message here'")
