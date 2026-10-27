#!/usr/bin/env python3
"""
ALGO DAY TRADER ENGINE V3.0 - Persistence Layer (SQLite Database).
Tables:
- bars: cached completed historical 5m OHLCV bars
- signals_audit: full record of evaluated signals (scores, weights, breakdown, accepted/rejected reasons)
- orders: paper orders placed
- fills: executed fills with slippage and transaction costs
- positions: open position state with trailing stops and partial exit flags
- trades: closed trade journal with net P&L and holding stats
- backtests: recorded backtest run configurations and performance summaries
- audit_events: timestamped operational, market-data, risk, and error events
"""

import sqlite3
import json
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "trader" / "trader_v3.db"

def get_connection(db_path=None):
    path = db_path or DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn

def init_db(db_path=None):
    conn = get_connection(db_path)
    cur = conn.cursor()

    # 1. Historical Bars
    cur.execute("""
    CREATE TABLE IF NOT EXISTS bars (
        symbol TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        open REAL NOT NULL,
        high REAL NOT NULL,
        low REAL NOT NULL,
        close REAL NOT NULL,
        volume REAL NOT NULL,
        is_completed INTEGER DEFAULT 1,
        PRIMARY KEY (symbol, timestamp)
    )
    """)

    # 2. Signals Audit (Full Confluence Breakdown)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS signals_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        strategy_name TEXT NOT NULL,
        side TEXT NOT NULL,
        signal TEXT NOT NULL,
        confluence_score REAL NOT NULL,
        accepted INTEGER NOT NULL,
        rejection_reasons TEXT,
        component_weights TEXT,
        indicator_values TEXT,
        entry_price REAL,
        stop_loss REAL,
        target_price REAL,
        risk_per_share REAL,
        reward_risk_ratio REAL
    )
    """)

    # 3. Orders Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        order_type TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        price REAL NOT NULL,
        status TEXT NOT NULL,
        reason TEXT
    )
    """)

    # 4. Fills Table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS fills (
        fill_id TEXT PRIMARY KEY,
        order_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        fill_price REAL NOT NULL,
        slippage REAL DEFAULT 0.0,
        costs REAL DEFAULT 0.0
    )
    """)

    # 5. Active Positions
    cur.execute("""
    CREATE TABLE IF NOT EXISTS positions (
        position_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        initial_stop REAL NOT NULL,
        current_stop REAL NOT NULL,
        target_price REAL NOT NULL,
        peak_price REAL NOT NULL,
        partial_exited INTEGER DEFAULT 0,
        entry_time TEXT NOT NULL,
        entry_date TEXT NOT NULL,
        r_unit REAL NOT NULL,
        strategy_name TEXT
    )
    """)

    # 6. Completed Trades History
    cur.execute("""
    CREATE TABLE IF NOT EXISTS trades (
        trade_id TEXT PRIMARY KEY,
        symbol TEXT NOT NULL,
        side TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        gross_pnl REAL NOT NULL,
        costs REAL NOT NULL,
        net_pnl REAL NOT NULL,
        entry_time TEXT NOT NULL,
        exit_time TEXT NOT NULL,
        date TEXT NOT NULL,
        exit_reason TEXT,
        r_multiple REAL
    )
    """)

    # 7. Audit Events
    cur.execute("""
    CREATE TABLE IF NOT EXISTS audit_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        status TEXT NOT NULL,
        details TEXT
    )
    """)

    # 8. Backtest Runs
    cur.execute("""
    CREATE TABLE IF NOT EXISTS backtests (
        run_id TEXT PRIMARY KEY,
        timestamp TEXT NOT NULL,
        strategy TEXT NOT NULL,
        start_date TEXT,
        end_date TEXT,
        initial_capital REAL,
        net_pnl REAL,
        win_rate REAL,
        profit_factor REAL,
        max_drawdown REAL,
        total_trades INTEGER,
        metrics_json TEXT
    )
    """)

    conn.commit()
    conn.close()

def log_audit_db(category, action, status="SUCCESS", details=None, db_path=None):
    conn = get_connection(db_path)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO audit_events (timestamp, category, action, status, details)
    VALUES (?, ?, ?, ?, ?)
    """, (
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
        category,
        action,
        status,
        json.dumps(details or {})
    ))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
    print("[DB] Initialized trader_v3.db successfully.")
