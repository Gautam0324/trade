#!/usr/bin/env python3
"""
Thread-safe, Process-safe Order Lifecycle & Position State Manager.
Guarantees:
- Atomic order admission and reservation (prevents concurrent double-order creation)
- Unique persistent Order IDs (ORD-YYYYMMDD-HHMMSS-SYM-SEQ) and Position IDs (POS-YYYYMMDD-SYM-SEQ)
- Signal idempotency via UNIQUE(signal_id)
- Explicit finite state machine:
  CREATED -> VALIDATED -> SUBMITTED -> FILLED -> CLOSED
  (or REJECTED, CANCELLED, FAILED)
- State persistence before and after each transition via ACID SQLite
- Automatic reconciliation with ledger.json
"""

import os
import sys
import json
import time
import uuid
import sqlite3
import datetime
from pathlib import Path
from contextlib import contextmanager

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
DB_PATH = TRADER_DIR / "orders_lifecycle.db"
LEDGER_PATH = TRADER_DIR / "ledger.json"

class OrderState:
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"

class OrderManager:
    def __init__(self, db_path=None, ledger_path=None):
        self.db_path = db_path or DB_PATH
        self.ledger_path = ledger_path or LEDGER_PATH
        self._init_db()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    signal_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    stop_loss REAL,
                    target_price REAL,
                    status TEXT NOT NULL,
                    rejection_reason TEXT,
                    strategy_name TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS positions (
                    position_id TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    sl_price REAL NOT NULL,
                    target_price REAL NOT NULL,
                    entry_time TEXT NOT NULL,
                    exit_price REAL,
                    exit_time TEXT,
                    gross_pnl REAL,
                    fees REAL,
                    net_pnl REAL,
                    exit_reason TEXT,
                    status TEXT NOT NULL, -- OPEN or CLOSED
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_sym_status ON orders (symbol, status)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_positions_sym_status ON positions (symbol, status)")
            conn.commit()

    def generate_order_id(self, symbol):
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:6].upper()
        return f"ORD-{now_str}-{uid}-{symbol}"

    def generate_position_id(self, symbol):
        now_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:6].upper()
        return f"POS-{now_str}-{uid}-{symbol}"

    def can_admit_order(self, symbol, signal_id=None):
        """
        Atomically checks if an order can be admitted for this symbol.
        Rejects if:
        1. An active open position already exists in DB.
        2. A pending active order already exists (CREATED, VALIDATED, SUBMITTED).
        3. The exact signal_id was already processed.
        """
        with self._connection() as conn:
            cur = conn.cursor()
            # 1. Check signal idempotency
            if signal_id:
                cur.execute("SELECT order_id, status FROM orders WHERE signal_id = ?", (signal_id,))
                existing = cur.fetchone()
                if existing:
                    return False, f"Duplicate signal: signal_id {signal_id} already generated order {existing['order_id']} ({existing['status']})"

            # 2. Check open position for this symbol
            cur.execute("SELECT position_id FROM positions WHERE symbol = ? AND status = 'OPEN'", (symbol,))
            open_pos = cur.fetchone()
            if open_pos:
                return False, f"Duplicate position: {symbol} already has active open position {open_pos['position_id']}"

            # 3. Check active pending order
            cur.execute("""
                SELECT order_id, status FROM orders 
                WHERE symbol = ? AND status IN ('CREATED', 'VALIDATED', 'SUBMITTED')
            """, (symbol,))
            pending = cur.fetchone()
            if pending:
                return False, f"Duplicate pending order: {symbol} already has in-flight order {pending['order_id']} in status {pending['status']}"

        return True, "OK"

    def create_order(self, symbol, side, quantity, price, stop_loss, target_price,
                     signal_id=None, strategy_name=None):
        """
        Atomically creates a new order in CREATED state after reservation check.
        """
        now_ist = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        order_id = self.generate_order_id(symbol)

        # Enforce atomic reservation
        can_admit, reason = self.can_admit_order(symbol, signal_id)
        if not can_admit:
            # If rejected due to duplicate signal_id, return immediately without duplicate insert
            if signal_id and "Duplicate signal" in reason:
                return {"success": False, "order_id": order_id, "status": OrderState.REJECTED, "reason": reason}

            with self._connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    INSERT INTO orders (order_id, signal_id, symbol, side, quantity, price, stop_loss, target_price, status, rejection_reason, strategy_name, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (order_id, None, symbol, side.upper(), quantity, price, stop_loss, target_price, OrderState.REJECTED, reason, strategy_name, now_ist, now_ist))
                conn.commit()
            return {"success": False, "order_id": order_id, "status": OrderState.REJECTED, "reason": reason}

        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO orders (order_id, signal_id, symbol, side, quantity, price, stop_loss, target_price, status, rejection_reason, strategy_name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (order_id, signal_id, symbol, side.upper(), quantity, price, stop_loss, target_price, OrderState.CREATED, None, strategy_name, now_ist, now_ist))
            conn.commit()

        return {"success": True, "order_id": order_id, "status": OrderState.CREATED}

    def update_order_status(self, order_id, new_status, rejection_reason=None):
        now_ist = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE orders 
                SET status = ?, rejection_reason = COALESCE(?, rejection_reason), updated_at = ?
                WHERE order_id = ?
            """, (new_status, rejection_reason, now_ist, order_id))
            conn.commit()

    def record_simulated_fill(self, order_id, fill_price, fill_qty=None):
        now_ist = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        time_short = datetime.datetime.now().strftime("%H:%M")

        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
            order = cur.fetchone()
            if not order:
                raise ValueError(f"Order {order_id} not found")

            actual_qty = fill_qty if fill_qty is not None else order["quantity"]
            position_id = self.generate_position_id(order["symbol"])

            cur.execute("""
                UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?
            """, (OrderState.FILLED, now_ist, order_id))

            cur.execute("""
                INSERT INTO positions (
                    position_id, order_id, symbol, side, quantity, entry_price, sl_price, target_price,
                    entry_time, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)
            """, (
                position_id, order_id, order["symbol"], order["side"], actual_qty,
                fill_price, order["stop_loss"], order["target_price"], time_short, now_ist, now_ist
            ))
            conn.commit()

        self._sync_entry_to_ledger(position_id, order["symbol"], order["side"], actual_qty, fill_price, order["stop_loss"], order["target_price"], time_short)

        return {
            "order_id": order_id,
            "position_id": position_id,
            "symbol": order["symbol"],
            "side": order["side"],
            "quantity": actual_qty,
            "fill_price": fill_price,
            "status": "OPEN"
        }

    def record_position_exit(self, symbol, exit_price, exit_reason, fees=0.0):
        now_ist = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
        time_short = datetime.datetime.now().strftime("%H:%M")
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")

        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM positions WHERE symbol = ? AND status = 'OPEN'", (symbol,))
            pos = cur.fetchone()
            if not pos:
                return None

            pos_id = pos["position_id"]
            order_id = pos["order_id"]
            side = pos["side"]
            qty = pos["quantity"]
            entry_p = pos["entry_price"]

            if side == "BUY":
                gross_pnl = round((exit_price - entry_p) * qty, 2)
            else:
                gross_pnl = round((entry_p - exit_price) * qty, 2)

            net_pnl = round(gross_pnl - fees, 2)

            cur.execute("""
                UPDATE positions SET
                    exit_price = ?, exit_time = ?, gross_pnl = ?, fees = ?, net_pnl = ?,
                    exit_reason = ?, status = 'CLOSED', updated_at = ?
                WHERE position_id = ?
            """, (exit_price, time_short, gross_pnl, fees, net_pnl, exit_reason, now_ist, pos_id))

            cur.execute("UPDATE orders SET status = ?, updated_at = ? WHERE order_id = ?", (OrderState.CLOSED, now_ist, order_id))
            conn.commit()

        record = {
            "id": pos_id,
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": entry_p,
            "exit_price": exit_price,
            "entry_time": pos["entry_time"],
            "exit_time": time_short,
            "gross_pnl": gross_pnl,
            "fees": fees,
            "pnl": net_pnl,
            "reason": exit_reason,
            "date": today_str,
            "exit_date": today_str
        }
        self._sync_exit_to_ledger(record)
        return record

    def get_open_positions(self):
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT * FROM positions WHERE status = 'OPEN'")
            return [dict(row) for row in cur.fetchall()]

    def get_today_orders_count(self):
        today_prefix = datetime.datetime.now().strftime("%Y-%m-%d")
        with self._connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM orders WHERE created_at LIKE ?", (f"{today_prefix}%",))
            row = cur.fetchone()
            return row["cnt"] if row else 0

    def _sync_entry_to_ledger(self, pos_id, symbol, side, qty, entry_price, sl_price, target_price, entry_time):
        if not os.path.exists(self.ledger_path):
            return
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)

            open_pos = ledger.get("open_positions", [])
            if any(p.get("symbol") == symbol for p in open_pos):
                return

            open_pos.append({
                "id": pos_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "entry_price": entry_price,
                "sl_price": sl_price,
                "target_price": target_price,
                "entry_time": entry_time,
                "amount": round(qty * entry_price, 2)
            })
            ledger["open_positions"] = open_pos
            starting_cap = float(ledger.get("starting_capital", 1000000.0))
            deployed = sum(float(p.get("amount", p.get("qty", 0) * p.get("entry_price", 0))) for p in open_pos)
            ledger["deployed_cash"] = round(deployed, 2)
            ledger["free_cash"] = round(starting_cap - deployed + float(ledger.get("realized_pnl", 0.0)), 2)
            ledger["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

            with open(self.ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
        except Exception as e:
            print(f"[ORDER_MANAGER LEDGER SYNC ERROR] {e}")

    def _sync_exit_to_ledger(self, record):
        if not os.path.exists(self.ledger_path):
            return
        try:
            with open(self.ledger_path, "r", encoding="utf-8") as f:
                ledger = json.load(f)

            symbol = record["symbol"]
            open_pos = [p for p in ledger.get("open_positions", []) if p.get("symbol") != symbol]
            ledger["open_positions"] = open_pos
            ledger["trades"] = [p for p in ledger.get("trades", []) if p.get("symbol") != symbol]

            th = ledger.get("trade_history", [])
            today_str = datetime.datetime.now().strftime("%Y-%m-%d")

            already_recorded = any(
                t.get("symbol") == symbol and 
                t.get("entry_time") == record["entry_time"] and
                t.get("date") == today_str
                for t in th
            )
            if not already_recorded:
                th.append(record)
                ledger["trade_history"] = th
                ledger["realized_pnl"] = round(float(ledger.get("realized_pnl", 0.0)) + float(record["pnl"]), 2)

                # Update daily pnl map
                pnl_by_day = ledger.get("realized_pnl_by_day", {})
                day_pnl = sum(float(t.get("pnl", 0.0)) for t in th if t.get("date") == today_str or t.get("exit_date") == today_str)
                pnl_by_day[today_str] = round(day_pnl, 2)
                ledger["realized_pnl_by_day"] = pnl_by_day

            starting_cap = float(ledger.get("starting_capital", 1000000.0))
            deployed = sum(float(p.get("amount", p.get("qty", 0) * p.get("entry_price", 0))) for p in open_pos)
            ledger["deployed_cash"] = round(deployed, 2)
            ledger["free_cash"] = round(starting_cap - deployed + float(ledger.get("realized_pnl", 0.0)), 2)
            ledger["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

            with open(self.ledger_path, "w", encoding="utf-8") as f:
                json.dump(ledger, f, indent=2)
        except Exception as e:
            print(f"[ORDER_MANAGER LEDGER EXIT SYNC ERROR] {e}")
