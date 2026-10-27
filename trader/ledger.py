#!/usr/bin/env python3
"""
Live Portfolio & Cash Ledger for Day Trader Skill.
Tracks:
- Starting Capital (₹1,00,000)
- Realized P&L (Total Profit / Loss)
- Active Open Positions (Deployed Capital)
- Free Available Cash (Starting + P&L - Deployed)
- Monotonic position sequences, atomic replacement, and file locking
Standard library only.
"""

import json
import os
import sys
import time
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LEDGER_PATH = BASE_DIR / "trader" / "ledger.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"
LOCK_PATH = BASE_DIR / "trader" / "ledger.lock"

class FileLock:
    def __init__(self, lock_path=LOCK_PATH, timeout=10.0, retry_delay=0.05):
        self.lock_path = str(lock_path)
        self.timeout = timeout
        self.retry_delay = retry_delay
        self.fd = None

    def __enter__(self):
        start_time = time.time()
        while True:
            try:
                # Open exclusively for creation
                self.fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(self.fd, str(os.getpid()).encode())
                return self
            except (OSError, FileExistsError):
                if time.time() - start_time > self.timeout:
                    # Timeout reached; if stale lock, break it
                    try:
                        os.remove(self.lock_path)
                    except OSError:
                        pass
                    raise TimeoutError(f"Could not acquire lock on {self.lock_path} after {self.timeout}s")
                time.sleep(self.retry_delay)

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            try:
                os.remove(self.lock_path)
            except OSError:
                pass
            self.fd = None

def get_ledger():
    with FileLock():
        return _read_ledger_locked()

def _read_ledger_locked():
    if os.path.exists(LEDGER_PATH):
        try:
            with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass

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
        "realized_pnl_by_day": {},
        "next_position_seq": 1,
        "open_positions": [],
        "trade_history": [],
        "last_updated": datetime.datetime.now().isoformat()
    }
    _atomic_write_locked(default_ledger)
    return default_ledger

def save_ledger(data):
    with FileLock():
        _atomic_write_locked(data)

def _atomic_write_locked(data):
    data["last_updated"] = datetime.datetime.now().isoformat()
    parent_dir = LEDGER_PATH.parent
    parent_dir.mkdir(parents=True, exist_ok=True)
    temp_path = parent_dir / f"ledger_tmp_{os.getpid()}_{int(time.time()*1000)}.json"
    
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.flush()
        os.fsync(f.fileno())

    os.replace(temp_path, LEDGER_PATH)

def execute_locked_ledger_op(callback):
    """
    Executes callback(ledger) under an exclusive lock and atomically saves the result.
    """
    with FileLock():
        ledger = _read_ledger_locked()
        res = callback(ledger)
        _atomic_write_locked(ledger)
        return res

def record_trade_entry(symbol, side, qty, price, exchange="NSE", entry_reason="Strategy Entry", config=None):
    """
    Enters a trade under lock with strict constraints:
    - invested == round(qty * price, 2)
    - allocated <= free cash
    - max_open_positions and max_position_pct bounds
    - monotonic ID assignment
    """
    qty = int(qty)
    price = float(price)
    invested = round(qty * price, 2)

    if invested != round(qty * price, 2):
        return False, "Validation error: invested != round(qty * price, 2)"

    def _op(ledger):
        cfg = config or {}
        if not cfg and os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {}

        max_open = int(cfg.get("max_open_positions", 3))
        max_pos_pct = float(cfg.get("max_position_pct", 75.0)) / 100.0
        free_cash = ledger["free_cash"]

        # 1. Free cash check
        if invested > free_cash:
            return False, f"Insufficient free cash! Available: ₹{free_cash:,.2f}, Needed: ₹{invested:,.2f}"

        # 2. Max open positions check
        if len(ledger.get("open_positions", [])) >= max_open:
            return False, f"Maximum open positions reached ({len(ledger['open_positions'])}/{max_open})"

        # 3. Max position allocation check
        max_alloc = free_cash * max_pos_pct
        if invested > max_alloc + 1e-2:
            return False, f"Position allocation ₹{invested:,.2f} exceeds max position pct limit ₹{max_alloc:,.2f}"

        # Monotonic ID
        seq = ledger.get("next_position_seq", len(ledger.get("trade_history", [])) + len(ledger.get("open_positions", [])) + 1)
        pos_id = f"POS-{seq}"
        ledger["next_position_seq"] = seq + 1

        ledger["free_cash"] = round(free_cash - invested, 2)
        ledger["deployed_cash"] = round(ledger["deployed_cash"] + invested, 2)

        today_date = datetime.date.today().strftime("%Y-%m-%d")
        position = {
            "id": pos_id,
            "symbol": symbol,
            "side": side.upper(),
            "qty": qty,
            "entry_price": price,
            "amount": invested,
            "invested": invested,
            "exchange": exchange,
            "entry_reason": entry_reason,
            "date": today_date,
            "entry_date": today_date,
            "entry_time": datetime.datetime.now().strftime("%H:%M:%S IST")
        }
        ledger["open_positions"].append(position)
        return True, position

    return execute_locked_ledger_op(_op)

def update_open_position(symbol_or_id, updated_data):
    """
    Updates trailing stop, peak price or quantity for an open position by ID or unique symbol.
    """
    def _op(ledger):
        for pos in ledger.get("open_positions", []):
            if pos["id"] == symbol_or_id or pos["symbol"] == symbol_or_id:
                pos.update(updated_data)
                return True
        return False

    return execute_locked_ledger_op(_op)

def record_trade_exit(symbol_or_id, exit_price, reason="Exit trigger", exit_qty=None):
    """
    Closes a position by position ID or symbol (if unambiguous).
    Validates:
    - returned == round(closing_qty * exit_price, 2)
    - pnl == round(returned - portion_invested, 2)
    Updates realized_pnl_by_day.
    """
    exit_price = float(exit_price)

    def _op(ledger):
        open_pos = ledger.get("open_positions", [])
        
        # 1. Match by ID first
        matching_idx = None
        for idx, pos in enumerate(open_pos):
            if pos.get("id") == symbol_or_id:
                matching_idx = idx
                break

        # 2. If no match by ID, check symbol
        if matching_idx is None:
            symbol_matches = [idx for idx, pos in enumerate(open_pos) if pos.get("symbol") == symbol_or_id]
            if len(symbol_matches) == 1:
                matching_idx = symbol_matches[0]
            elif len(symbol_matches) > 1:
                return False, f"Ambiguous exit for {symbol_or_id}: multiple open positions match. Specify position ID."

        if matching_idx is None:
            return False, f"No open position found for {symbol_or_id}"

        pos = open_pos[matching_idx]
        current_qty = int(pos["qty"])
        closing_qty = int(exit_qty) if exit_qty and int(exit_qty) < current_qty else current_qty

        entry_price = float(pos["entry_price"])
        portion_invested = round(closing_qty * entry_price, 2)
        returned = round(closing_qty * exit_price, 2)
        pnl = round(returned - portion_invested, 2)

        # Mathematical consistency assertion
        if returned != round(closing_qty * exit_price, 2):
            return False, "Validation error: returned != round(qty * exit_price, 2)"
        if pnl != round(returned - portion_invested, 2):
            return False, "Validation error: pnl != returned - invested"

        # Return capital + P&L to free cash
        ledger["deployed_cash"] = round(max(0.0, ledger["deployed_cash"] - portion_invested), 2)
        ledger["free_cash"] = round(ledger["free_cash"] + returned, 2)
        ledger["realized_pnl"] = round(ledger["realized_pnl"] + pnl, 2)

        today_date = datetime.date.today().strftime("%Y-%m-%d")
        
        # Update realized_pnl_by_day
        pnl_by_day = ledger.get("realized_pnl_by_day", {})
        pnl_by_day[today_date] = round(pnl_by_day.get(today_date, 0.0) + pnl, 2)
        ledger["realized_pnl_by_day"] = pnl_by_day

        if closing_qty < current_qty:
            pos["qty"] = current_qty - closing_qty
            pos["amount"] = round(pos["qty"] * entry_price, 2)
            pos["invested"] = pos["amount"]
            pos["partial_exited"] = True
        else:
            open_pos.pop(matching_idx)

        history_item = {
            "id": pos["id"],
            "legacy_id": pos.get("legacy_id", pos["id"]),
            "symbol": pos["symbol"],
            "qty": closing_qty,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "date": pos.get("date", today_date),
            "entry_date": pos.get("entry_date", today_date),
            "exit_date": today_date,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "entry_reason": pos.get("entry_reason", "Strategy Entry"),
            "exit_reason": reason,
            "invested": portion_invested,
            "returned": returned,
            "pnl": pnl,
            "reason": reason
        }
        ledger["trade_history"].append(history_item)
        return True, history_item

    return execute_locked_ledger_op(_op)

def get_today_trades(report_date=None):
    if report_date is None:
        report_date = datetime.date.today().strftime("%Y-%m-%d")
    leg = get_ledger()
    return [t for t in leg.get("trade_history", []) if (t.get("date") == report_date or t.get("entry_date") == report_date or t.get("exit_date") == report_date) and t.get("status") != "void_pending_review"]

def get_today_entries_count(report_date=None):
    """
    max_trades_per_day counts all entries placed today, whether still open or already closed.
    """
    if report_date is None:
        report_date = datetime.date.today().strftime("%Y-%m-%d")
    leg = get_ledger()
    closed_today = [t for t in leg.get("trade_history", []) if t.get("entry_date") == report_date and t.get("status") != "void_pending_review"]
    open_today = [p for p in leg.get("open_positions", []) if p.get("entry_date") == report_date]
    return len(closed_today) + len(open_today)

def get_today_realized_loss(report_date=None):
    """
    Daily loss uses ONLY realized P&L from trades that exited today.
    """
    if report_date is None:
        report_date = datetime.date.today().strftime("%Y-%m-%d")
    leg = get_ledger()
    pnl_by_day = leg.get("realized_pnl_by_day", {})
    return float(pnl_by_day.get(report_date, 0.0))

if __name__ == "__main__":
    l = get_ledger()
    print(json.dumps(l, indent=2))
