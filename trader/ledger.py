#!/usr/bin/env python3
"""
Live Portfolio & Cash Ledger for Day Trader Skill.
Tracks:
- Starting Capital (₹1,00,000)
- Realized P&L (Total Profit / Loss)
- Active Open Positions (Deployed Capital)
- Free Available Cash (Starting + P&L - Deployed)
Standard library only.
"""

import json
import os
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LEDGER_PATH = BASE_DIR / "trader" / "ledger.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"

def get_ledger():
    if os.path.exists(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

    # Read starting budget from config
    initial_capital = 100000.0
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                initial_capital = float(cfg.get("budget", 100000.0))
        except Exception:
            pass

    default_ledger = {
        "starting_capital": initial_capital,
        "currency": "INR",
        "free_cash": initial_capital,
        "deployed_cash": 0.0,
        "realized_pnl": 0.0,
        "open_positions": [],
        "trade_history": [],
        "last_updated": datetime.datetime.now().isoformat()
    }
    save_ledger(default_ledger)
    return default_ledger

def save_ledger(data):
    data["last_updated"] = datetime.datetime.now().isoformat()
    os.makedirs(os.path.dirname(LEDGER_PATH), exist_ok=True)
    with open(LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def record_trade_entry(symbol, side, qty, price, exchange="NSE", entry_reason="EMA 5 > EMA 9 above EMA 33 Channel Breakout"):
    """
    Called when a trade is entered:
    Minus deployed money from free cash.
    """
    ledger = get_ledger()
    qty = int(qty)
    price = float(price)
    amount = round(qty * price, 2)

    if amount > ledger["free_cash"]:
        return False, f"Insufficient free cash! Available: ₹{ledger['free_cash']:,.2f}, Needed: ₹{amount:,.2f}"

    ledger["free_cash"] = round(ledger["free_cash"] - amount, 2)
    ledger["deployed_cash"] = round(ledger["deployed_cash"] + amount, 2)

    position = {
        "id": f"POS-{len(ledger['trade_history']) + 1}",
        "symbol": symbol,
        "side": side.upper(),
        "qty": qty,
        "entry_price": price,
        "amount": amount,
        "exchange": exchange,
        "entry_reason": entry_reason,
        "entry_time": datetime.datetime.now().strftime("%H:%M:%S IST")
    }
    ledger["open_positions"].append(position)
    save_ledger(ledger)
    return True, position

def record_trade_exit(symbol, exit_price, reason="Exit trigger"):
    """
    Called when a trade is closed:
    Returns deployed money + profit (or minus loss) back to free cash.
    """
    ledger = get_ledger()
    exit_price = float(exit_price)
    matching_idx = None

    for idx, pos in enumerate(ledger["open_positions"]):
        if pos["symbol"] == symbol:
            matching_idx = idx
            break

    if matching_idx is None:
        return False, f"No open position found for {symbol}"

    pos = ledger["open_positions"].pop(matching_idx)
    qty = pos["qty"]
    entry_price = pos["entry_price"]
    initial_amount = pos["amount"]
    entry_reason = pos.get("entry_reason", "EMA 5 > EMA 9 above EMA 33 High")

    # Sell revenue
    exit_amount = round(qty * exit_price, 2)
    pnl = round(exit_amount - initial_amount, 2)

    # Return capital + P&L to free cash
    ledger["deployed_cash"] = round(max(0.0, ledger["deployed_cash"] - initial_amount), 2)
    ledger["free_cash"] = round(ledger["free_cash"] + exit_amount, 2)
    ledger["realized_pnl"] = round(ledger["realized_pnl"] + pnl, 2)

    history_item = {
        "id": pos["id"],
        "symbol": symbol,
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "entry_time": pos["entry_time"],
        "exit_time": datetime.datetime.now().strftime("%H:%M:%S IST"),
        "entry_reason": entry_reason,
        "exit_reason": reason,
        "invested": initial_amount,
        "returned": exit_amount,
        "pnl": pnl,
        "reason": reason
    }
    ledger["trade_history"].append(history_item)
    save_ledger(ledger)
    return True, history_item

if __name__ == "__main__":
    l = get_ledger()
    print(json.dumps(l, indent=2))
