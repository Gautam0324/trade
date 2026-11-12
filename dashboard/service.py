#!/usr/bin/env python3
"""
Dashboard Backend Service for Algo Trading Terminal.
Provides:
- Real-time bot daemon status & process management
- KPI computations (Realized/Unrealized P&L, Win Rate, Profit Factor, Drawdown, etc.)
- Historical equity curve & cumulative daily P&L
- Multi-strategy performance breakdown
- Risk limit compliance & alert center
- Safe daemon start/stop & pause/resume controls
- System health, audit logs, and configuration viewer
"""

import os
import sys
import json
import time
import math
import subprocess
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
REPORTS_DIR = TRADER_DIR / "reports"
LOGS_DIR = TRADER_DIR / "logs"
AUDIT_LOG_PATH = TRADER_DIR / "audit_log.json"
STATE_DIR = TRADER_DIR / "state"

sys.path.insert(0, str(BASE_DIR / "trader"))
sys.path.insert(0, str(BASE_DIR / "trader-scanner"))
sys.path.insert(0, str(BASE_DIR / "trader-alerts"))

# Cache live quotes in memory for 15 seconds to avoid rate limiting
quote_cache = {}
QUOTE_CACHE_TTL = 15

# Daemon process handle if started from dashboard
daemon_process = None

def get_ist_now():
    # IST is UTC + 5:30
    utc_now = datetime.datetime.now(datetime.timezone.utc)
    ist_tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return utc_now.astimezone(ist_tz)

def read_json_file(path, default=None):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default if default is not None else {}
    return default if default is not None else {}

def write_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def log_audit_event(action, details=None, status="SUCCESS"):
    events = read_json_file(AUDIT_LOG_PATH, default=[])
    if not isinstance(events, list):
        events = []
    event = {
        "timestamp": get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST"),
        "action": action,
        "details": details or {},
        "status": status
    }
    events.append(event)
    # Keep last 500 audit events
    if len(events) > 500:
        events = events[-500:]
    write_json_file(AUDIT_LOG_PATH, events)
    return event

def is_market_open():
    """
    NSE/BSE Trading Hours: Monday - Friday 09:15 to 15:30 IST.
    Pre-market: 09:00 to 09:15 IST.
    """
    now = get_ist_now()
    weekday = now.weekday() # 0 = Monday, 6 = Sunday
    if weekday >= 5:
        return "CLOSED (Weekend)"
    
    time_str = now.strftime("%H:%M")
    if "09:00" <= time_str < "09:15":
        return "PRE-MARKET"
    elif "09:15" <= time_str <= "15:30":
        return "OPEN"
    else:
        return "CLOSED"

def check_daemon_status():
    """
    Checks if trading bot is currently running.
    Checks multi_trader.lock and daemon.lock.
    """
    global daemon_process
    if daemon_process is not None:
        poll = daemon_process.poll()
        if poll is None:
            return {"status": "RUNNING", "pid": daemon_process.pid, "managed": True}
        else:
            daemon_process = None

    # 1. Check multi_live_trader lock
    multi_lock = TRADER_DIR / "multi_trader.lock"
    if os.path.exists(multi_lock):
        try:
            with open(multi_lock, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.isdigit():
                    pid = int(content)
                    if is_pid_running(pid):
                        return {"status": "RUNNING", "pid": pid, "managed": False, "bot_type": "Multi-Live Trader"}
        except (PermissionError, OSError):
            # File is actively locked by the running live trader process!
            return {"status": "RUNNING", "pid": "Active", "managed": False, "bot_type": "Multi-Live Trader"}
        except Exception:
            pass

    # 2. Check daemon.lock
    lock_path = STATE_DIR / "daemon.lock"
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content.isdigit():
                    pid = int(content)
                    if is_pid_running(pid):
                        return {"status": "RUNNING", "pid": pid, "managed": False, "bot_type": "Scanner Daemon"}
        except Exception:
            pass

    return {"status": "STOPPED", "pid": None, "managed": False}

def is_pid_running(pid):
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            err = ctypes.windll.kernel32.GetLastError()
            # ERROR_ACCESS_DENIED (5) means the process exists but permissions/session differ
            if err == 5:
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False

def fetch_live_quote_data(symbol):
    now_ts = time.time()
    clean_sym = symbol.replace(".NS", "").replace(".BO", "")

    if clean_sym in quote_cache:
        cached = quote_cache[clean_sym]
        if (now_ts - cached["timestamp"]) < QUOTE_CACHE_TTL:
            return cached["data"]

    # First check live ticks SQLite DB updated by multi_live_trader
    ticks_db = TRADER_DIR / "ticks_market_data.db"
    if os.path.exists(ticks_db):
        try:
            import sqlite3
            conn = sqlite3.connect(ticks_db, timeout=2)
            cur = conn.cursor()
            cur.execute("""
                SELECT open, high, low, close, volume, timestamp 
                FROM ticks 
                WHERE symbol = ? OR symbol = ? 
                ORDER BY id DESC LIMIT 2
            """, (clean_sym, symbol))
            rows = cur.fetchall()
            conn.close()

            if rows:
                latest = rows[0]
                prev = rows[1] if len(rows) > 1 else latest
                cur_p = round(float(latest[3]), 2)
                prev_p = round(float(prev[3]), 2)
                change = round(cur_p - prev_p, 2)
                change_pct = round((change / prev_p) * 100, 2) if prev_p > 0 else 0.0

                data = {
                    "symbol": symbol,
                    "price": cur_p,
                    "prev_close": prev_p,
                    "change": change,
                    "change_pct": change_pct,
                    "high": float(latest[1]),
                    "low": float(latest[2]),
                    "open": float(latest[0]),
                    "volume": float(latest[4]) if latest[4] else 0,
                    "signal": "HOLD",
                    "confluence_score": 50.0,
                    "confluence_weights": {},
                    "rejections": [],
                    "ema5": cur_p,
                    "ema9": cur_p,
                    "ema33_high": cur_p,
                    "ema33_low": cur_p,
                    "supertrend": None,
                    "rsi": 50.0,
                    "adx": 20.0,
                    "vwap": cur_p,
                    "rvol": 1.0,
                    "last_updated": get_ist_now().strftime("%H:%M:%S IST")
                }
                quote_cache[clean_sym] = {"timestamp": now_ts, "data": data}
                return data
        except Exception:
            pass

    # Fallback to daemon if ticks DB didn't have it
    try:
        import daemon
        quotes = daemon.fetch_candles(symbol)
        if not quotes or not quotes.get("close"):
            return None

        closes = [c for c in quotes["close"] if c is not None]
        highs = [h for h in quotes.get("high", []) if h is not None]
        lows = [l for l in quotes.get("low", []) if l is not None]
        opens = [o for o in quotes.get("open", []) if o is not None]
        volumes = [v for v in quotes.get("volume", []) if v is not None]

        if not closes:
            return None

        cur_p = round(float(closes[-1]), 2)
        prev_close = round(float(closes[-2]), 2) if len(closes) > 1 else cur_p
        change = round(cur_p - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close > 0 else 0.0

        data = {
            "symbol": symbol,
            "price": cur_p,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "high": max(highs) if highs else cur_p,
            "low": min(lows) if lows else cur_p,
            "open": opens[-1] if opens else cur_p,
            "volume": volumes[-1] if volumes else 0,
            "signal": "HOLD",
            "confluence_score": 50.0,
            "confluence_weights": {},
            "rejections": [],
            "ema5": cur_p,
            "ema9": cur_p,
            "ema33_high": cur_p,
            "ema33_low": cur_p,
            "supertrend": None,
            "rsi": 50.0,
            "adx": 20.0,
            "vwap": cur_p,
            "rvol": 1.0,
            "last_updated": get_ist_now().strftime("%H:%M:%S IST")
        }
        quote_cache[clean_sym] = {"timestamp": now_ts, "data": data}
        return data
    except Exception:
        return None

def compute_dashboard_metrics(account_id="gautam"):
    """
    Computes all mathematical KPIs using the single source of truth for the account:
    ledger.json (Gautam), ledger_himanshu.json (Himanshu), or ledger_prateek.json (Prateek).
    """
    acc_id = (account_id or "gautam").lower().strip()
    accounts_info = read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
    acc_meta = accounts_info.get("accounts", {}).get(acc_id, {})
    ledger_filename = acc_meta.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")

    ledger = read_json_file(TRADER_DIR / ledger_filename, default={})
    config = read_json_file(TRADER_DIR / "config.json", default={})

    starting_cap = float(acc_meta.get("capital", ledger.get("starting_capital", ledger.get("initial_capital", config.get("budget", 1000000.0)))))
    free_cash = float(ledger.get("free_cash", starting_cap))
    deployed_cash = float(ledger.get("deployed_cash", 0.0))
    realized_pnl = float(ledger.get("realized_pnl", 0.0))
    open_positions = ledger.get("open_positions", [])
    trade_history = ledger.get("trade_history", [])

    # Calculate live unrealized P&L on open positions
    unrealized_pnl = 0.0
    enriched_positions = []
    for p in open_positions:
        sym = p.get("symbol", "")
        qty = int(p.get("qty", 0))
        entry_p = float(p.get("entry_price", 0.0))
        invested = float(p.get("amount", p.get("invested", round(qty * entry_p, 2))))
        quote = None

        # Check if option contract
        is_option = p.get("option_type") or sym.endswith("CE") or sym.endswith("PE")
        if is_option:
            cur_p = float(p.get("current_price", 0.0))
            uname = p.get("underlying")
            if uname and p.get("entry_spot"):
                spot_sym = "NIFTY 50" if uname == "NIFTY" else ("BANKNIFTY" if uname == "BANKNIFTY" else uname)
                spot_q = fetch_live_quote_data(spot_sym)
                if spot_q and spot_q.get("price"):
                    spot_cur = float(spot_q["price"])
                    spot_change = spot_cur - float(p["entry_spot"])
                    delta = 0.50 if (p.get("option_type") == "CE" or sym.endswith("CE")) else -0.50
                    calc_p = max(1.0, round(entry_p + (spot_change * delta), 2))
                    cur_p = calc_p if cur_p <= 0 else cur_p
                    quote = {"last_updated": spot_q.get("last_updated", get_ist_now().strftime("%H:%M:%S IST"))}
            if cur_p <= 0:
                cur_p = entry_p
        else:
            quote = fetch_live_quote_data(sym)
            cur_p = quote["price"] if quote else float(p.get("current_price", entry_p))
        
        pos_pnl = round((cur_p - entry_p) * qty, 2)
        pos_pnl_pct = round((pos_pnl / invested) * 100, 2) if invested > 0 else 0.0
        unrealized_pnl += pos_pnl

        enriched_positions.append({
            **p,
            "amount": invested,
            "invested": invested,
            "current_price": cur_p,
            "current_stop": float(p.get("sl_price", p.get("current_stop", round(entry_p * 0.95, 2)))),
            "target_price": float(p.get("target_price", round(entry_p * 1.15, 2))),
            "peak_price": float(p.get("peak_price", max(cur_p, entry_p))),
            "unrealized_pnl": pos_pnl,
            "unrealized_pnl_pct": pos_pnl_pct,
            "market_value": round(qty * cur_p, 2),
            "last_quote_time": quote["last_updated"] if quote else get_ist_now().strftime("%H:%M:%S IST")
        })

    unrealized_pnl = round(unrealized_pnl, 2)
    total_net_pnl = round(realized_pnl + unrealized_pnl, 2)
    total_portfolio_val = round(free_cash + deployed_cash + unrealized_pnl, 2)
    return_pct = round((total_net_pnl / starting_cap) * 100, 2) if starting_cap > 0 else 0.0

    # Trade statistics
    closed_trades = [t for t in trade_history if "pnl" in t and t.get("status") != "void_pending_review"]
    wins = [t for t in closed_trades if float(t["pnl"]) > 0]
    losses = [t for t in closed_trades if float(t["pnl"]) < 0]
    breakeven = [t for t in closed_trades if float(t["pnl"]) == 0]

    win_count = len(wins)
    loss_count = len(losses)
    total_completed = len(closed_trades)
    win_rate = round((win_count / total_completed) * 100, 1) if total_completed > 0 else 0.0

    gross_profit = sum(float(t["pnl"]) for t in wins)
    gross_loss = abs(sum(float(t["pnl"]) for t in losses))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (round(gross_profit, 2) if gross_profit > 0 else 1.0)

    # Max Drawdown calculation over closed trade equity curve
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for t in closed_trades:
        cumulative += float(t.get("pnl", 0.0))
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd

    max_daily_loss = float(config.get("max_daily_loss_inr", config.get("max_daily_loss_usd", 1000.0)))
    daily_loss_utilization = round((abs(min(0.0, realized_pnl)) / max_daily_loss) * 100, 1) if max_daily_loss > 0 else 0.0

    today_str = get_ist_now().strftime("%Y-%m-%d")
    today_trades = [t for t in trade_history if (t.get("date") == today_str or t.get("exit_date") == today_str) and t.get("status") != "void_pending_review"]
    today_realized_pnl = sum(float(t.get("pnl", 0.0)) for t in today_trades)
    today_gross_pnl = sum(float(t.get("gross_pnl", t.get("pnl", 0.0))) for t in today_trades)
    today_fees = sum(float(t.get("fees", 0.0)) for t in today_trades)
    today_net_pnl = round(today_realized_pnl, 2)

    total_gross_pnl = sum(float(t.get("gross_pnl", t.get("pnl", 0.0))) for t in closed_trades)
    total_fees = sum(float(t.get("fees", 0.0)) for t in closed_trades)

    # Segment Breakdown: F&O vs Cash
    fno_trades = [t for t in closed_trades if t.get("symbol", "").endswith(("CE", "PE")) or "OPT" in t.get("id", "")]
    cash_trades = [t for t in closed_trades if t not in fno_trades]

    fno_wins = [t for t in fno_trades if float(t.get("pnl", 0)) > 0]
    cash_wins = [t for t in cash_trades if float(t.get("pnl", 0)) > 0]
    fno_losses = [t for t in fno_trades if float(t.get("pnl", 0)) < 0]
    cash_losses = [t for t in cash_trades if float(t.get("pnl", 0)) < 0]

    fno_gross = round(sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in fno_trades), 2)
    fno_fees = round(sum(float(t.get("fees", 0)) for t in fno_trades), 2)
    fno_net = round(sum(float(t.get("pnl", 0)) for t in fno_trades), 2)
    fno_win_rate = round((len(fno_wins) / len(fno_trades)) * 100, 1) if fno_trades else 0.0
    fno_pf = round(sum(float(t["pnl"]) for t in fno_wins) / abs(sum(float(t["pnl"]) for t in fno_losses)), 2) if fno_losses else (round(fno_gross, 2) if fno_gross > 0 else 1.0)

    cash_gross = round(sum(float(t.get("gross_pnl", t.get("pnl", 0))) for t in cash_trades), 2)
    cash_fees = round(sum(float(t.get("fees", 0)) for t in cash_trades), 2)
    cash_net = round(sum(float(t.get("pnl", 0)) for t in cash_trades), 2)
    cash_win_rate = round((len(cash_wins) / len(cash_trades)) * 100, 1) if cash_trades else 0.0
    cash_pf = round(sum(float(t["pnl"]) for t in cash_wins) / abs(sum(float(t["pnl"]) for t in cash_losses)), 2) if cash_losses else (round(cash_gross, 2) if cash_gross > 0 else 1.0)

    # Invariant Check: Starting Capital + Realized P&L == Free Cash + Deployed Cash
    inv_lhs = round(starting_cap + realized_pnl, 2)
    inv_rhs = round(free_cash + deployed_cash, 2)
    inv_diff = round(abs(inv_lhs - inv_rhs), 2)
    invariant_status = "PASS" if inv_diff <= 0.05 else "DISCREPANCY"

    # Analytics: Streaks, Expectancy, Best & Worst Instruments
    cur_win = 0
    cur_loss = 0
    max_win_streak = 0
    max_loss_streak = 0
    for t in closed_trades:
        p = float(t.get("pnl", 0))
        if p > 0:
            cur_win += 1
            cur_loss = 0
            if cur_win > max_win_streak: max_win_streak = cur_win
        elif p < 0:
            cur_loss += 1
            cur_win = 0
            if cur_loss > max_loss_streak: max_loss_streak = cur_loss

    avg_win = round(sum(float(t["pnl"]) for t in wins) / len(wins), 2) if wins else 0.0
    avg_loss = round(abs(sum(float(t["pnl"]) for t in losses)) / len(losses), 2) if losses else 0.0
    expectancy = round(((win_rate / 100.0) * avg_win) - (((100.0 - win_rate) / 100.0) * avg_loss), 2)

    # Symbol-wise Performance
    sym_map = {}
    for t in closed_trades:
        s = t.get("symbol", "UNKNOWN")
        p = float(t.get("pnl", 0))
        if s not in sym_map:
            sym_map[s] = {"symbol": s, "pnl": 0.0, "trades": 0, "wins": 0}
        sym_map[s]["pnl"] = round(sym_map[s]["pnl"] + p, 2)
        sym_map[s]["trades"] += 1
        if p > 0: sym_map[s]["wins"] += 1

    sorted_syms = sorted(sym_map.values(), key=lambda x: x["pnl"], reverse=True)
    best_instruments = sorted_syms[:5] if sorted_syms else []
    worst_instruments = sorted(sym_map.values(), key=lambda x: x["pnl"])[:5] if sorted_syms else []

    # Hourly Distribution
    hour_buckets = {f"{h:02d}:00-{h+1:02d}:00": {"hour": f"{h:02d}:00 - {h+1:02d}:00", "trades": 0, "pnl": 0.0, "wins": 0} for h in range(9, 16)}
    for t in closed_trades:
        time_str = t.get("exit_time") or t.get("entry_time") or ""
        if ":" in time_str:
            try:
                hh = int(time_str.split(":")[0][-2:])
                bucket_key = f"{hh:02d}:00-{hh+1:02d}:00"
                if bucket_key in hour_buckets:
                    hour_buckets[bucket_key]["trades"] += 1
                    p = float(t.get("pnl", 0))
                    hour_buckets[bucket_key]["pnl"] = round(hour_buckets[bucket_key]["pnl"] + p, 2)
                    if p > 0: hour_buckets[bucket_key]["wins"] += 1
            except Exception:
                pass

    # Reconcile check
    from ledger_utils import reconcile_ledger
    is_reconciled, reconcile_report, _ = reconcile_ledger(ledger)

    last_updated_str = ledger.get("last_updated", "N/A")

    return {
        "starting_capital": starting_cap,
        "free_cash": free_cash,
        "deployed_cash": deployed_cash,
        "total_portfolio_value": total_portfolio_val,
        "realized_pnl": realized_pnl,
        "today_realized_pnl": round(today_realized_pnl, 2),
        "today_gross_pnl": round(today_gross_pnl, 2),
        "today_fees": round(today_fees, 2),
        "today_net_pnl": round(today_net_pnl, 2),
        "total_gross_pnl": round(total_gross_pnl, 2),
        "total_fees": round(total_fees, 2),
        "unrealized_pnl": unrealized_pnl,
        "total_net_pnl": total_net_pnl,
        "return_pct": return_pct,
        "open_positions_count": len(open_positions),
        "today_trades_count": len(today_trades),
        "total_trades_count": total_completed,
        "wins_count": win_count,
        "losses_count": loss_count,
        "breakeven_count": len(breakeven),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": round(max_dd, 2),
        "max_daily_loss": max_daily_loss,
        "daily_loss_utilization_pct": daily_loss_utilization,
        "enriched_positions": enriched_positions,
        "segment_fno": {
            "gross_pnl": fno_gross,
            "fees": fno_fees,
            "net_pnl": fno_net,
            "trades_count": len(fno_trades),
            "wins_count": len(fno_wins),
            "losses_count": len(fno_losses),
            "win_rate": fno_win_rate,
            "profit_factor": fno_pf
        },
        "segment_cash": {
            "gross_pnl": cash_gross,
            "fees": cash_fees,
            "net_pnl": cash_net,
            "trades_count": len(cash_trades),
            "wins_count": len(cash_wins),
            "losses_count": len(cash_losses),
            "win_rate": cash_win_rate,
            "profit_factor": cash_pf
        },
        "accounting_invariant": {
            "status": invariant_status,
            "starting_capital": starting_cap,
            "realized_pnl": realized_pnl,
            "free_cash": free_cash,
            "deployed_cash": deployed_cash,
            "lhs": inv_lhs,
            "rhs": inv_rhs,
            "diff": inv_diff
        },
        "analytics": {
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "best_instruments": best_instruments,
            "worst_instruments": worst_instruments,
            "hourly_distribution": list(hour_buckets.values())
        },
        "is_reconciled": is_reconciled,
        "reconcile_discrepancies": reconcile_report.get("discrepancies", []),
        "last_updated": last_updated_str,
        "data_freshness": "FRESH" if is_reconciled else "WARNING_DISCREPANCY"
    }

def get_equity_curve_data(account_id="gautam", date_filter=None, as_payload=None):
    """
    Builds equity trajectory over time using trade history for the specified account.
    Supports:
    - date_filter="ALL": All trades across all dates (chronological)
    - date_filter="DAILY": Day-by-day P&L and closing equity comparison
    - date_filter="YYYY-MM-DD": Single day intraday trade-by-trade progression
    """
    acc_id = (account_id or "gautam").lower().strip()
    accounts_info = read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
    acc_meta = accounts_info.get("accounts", {}).get(acc_id, {})
    ledger_filename = acc_meta.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")

    ledger = read_json_file(TRADER_DIR / ledger_filename, default={})
    starting_cap = float(acc_meta.get("capital", ledger.get("starting_capital", ledger.get("initial_capital", 1000000.0))))
    trade_history = ledger.get("trade_history", [])

    # Extract all unique dates present in trade history (sorted descending for dropdown)
    all_dates = sorted(list(set(t.get("date") or t.get("exit_date") for t in trade_history if (t.get("date") or t.get("exit_date")))), reverse=True)

    filter_requested = date_filter is not None
    date_filter = (date_filter or "ALL").upper().strip()

    # Case 1: DAILY aggregated bar/line comparison
    if date_filter == "DAILY":
        asc_dates = sorted(list(set(t.get("date") or t.get("exit_date") for t in trade_history if (t.get("date") or t.get("exit_date")))))
        points = []
        cum_pnl = 0.0
        for d in asc_dates:
            day_trades = [t for t in trade_history if (t.get("date") == d or t.get("exit_date") == d)]
            day_pnl = round(sum(float(t.get("pnl", 0.0)) for t in day_trades), 2)
            cum_pnl = round(cum_pnl + day_pnl, 2)
            points.append({
                "time": d,
                "date": d,
                "equity": round(starting_cap + cum_pnl, 2),
                "cumulative_pnl": cum_pnl,
                "day_pnl": day_pnl,
                "trade_count": len(day_trades)
            })
        return {
            "view_mode": "DAILY",
            "points": points,
            "available_dates": all_dates,
            "starting_capital": starting_cap,
            "total_pnl": cum_pnl
        }

    # Case 2: Specific Date (e.g. 2026-11-09)
    matching_date = None
    for d in all_dates:
        if d.upper() == date_filter:
            matching_date = d
            break

    if matching_date:
        # Prior PnL from trades before matching_date
        prior_pnl = round(sum(float(t.get("pnl", 0.0)) for t in trade_history if (t.get("date") or t.get("exit_date", "")) < matching_date), 2)
        day_start_cap = round(starting_cap + prior_pnl, 2)
        day_trades = [t for t in trade_history if (t.get("date") == matching_date or t.get("exit_date") == matching_date)]

        points = [{
            "time": "09:15:00",
            "date": matching_date,
            "equity": day_start_cap,
            "cumulative_pnl": 0.0,
            "trade_id": "DAY_START",
            "symbol": "START",
            "trade_pnl": 0.0
        }]
        day_cum = 0.0
        for idx, t in enumerate(day_trades):
            pnl = float(t.get("pnl", 0.0))
            day_cum = round(day_cum + pnl, 2)
            cur_eq = round(day_start_cap + day_cum, 2)
            exit_time = t.get("exit_time", f"Trade {idx+1}").replace(" IST", "")
            points.append({
                "time": exit_time,
                "date": matching_date,
                "equity": cur_eq,
                "cumulative_pnl": day_cum,
                "trade_id": t.get("id", f"POS-{idx+1}"),
                "symbol": t.get("symbol", ""),
                "trade_pnl": pnl
            })
        return {
            "view_mode": "INTRADAY",
            "selected_date": matching_date,
            "points": points,
            "available_dates": all_dates,
            "starting_capital": day_start_cap,
            "day_pnl": day_cum
        }

    # Case 3: ALL Dates (default cumulative)
    points = [{
        "time": "START",
        "date": all_dates[-1] if all_dates else "START",
        "equity": starting_cap,
        "cumulative_pnl": 0.0,
        "trade_id": "START"
    }]
    cum_pnl = 0.0
    for idx, t in enumerate(trade_history):
        pnl = float(t.get("pnl", 0.0))
        cum_pnl = round(cum_pnl + pnl, 2)
        cur_cap = round(starting_cap + cum_pnl, 2)
        t_date = t.get("date") or t.get("exit_date") or ""
        t_time = t.get("exit_time", f"T{idx+1}").replace(" IST", "")
        label = f"{t_date} {t_time}".strip() if t_date else t_time
        points.append({
            "time": label,
            "date": t_date,
            "equity": cur_cap,
            "cumulative_pnl": cum_pnl,
            "trade_id": t.get("id", f"POS-{idx+1}"),
            "symbol": t.get("symbol", ""),
            "trade_pnl": pnl
        })

    # Return pure list for backwards compatibility unless as_payload is requested or explicit date_filter passed
    if as_payload is True or (as_payload is None and filter_requested):
        return {
            "view_mode": "ALL",
            "points": points,
            "available_dates": all_dates,
            "starting_capital": starting_cap,
            "total_pnl": cum_pnl
        }
    return points

def get_strategy_performance(account_id="gautam"):
    """
    Separates actual trading performance by entry strategy for the specified account.
    """
    acc_id = (account_id or "gautam").lower().strip()
    accounts_info = read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
    acc_meta = accounts_info.get("accounts", {}).get(acc_id, {})
    ledger_filename = acc_meta.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")

    ledger = read_json_file(TRADER_DIR / ledger_filename, default={})
    trade_history = ledger.get("trade_history", [])

    strat_map = {}
    for t in trade_history:
        reason = t.get("entry_reason") or t.get("reason", "EMA 5 > EMA 9 Channel Breakout")
        # Extract Strategy Name
        strat = "EMA_CHANNEL"
        tag = "MOMENTUM_PULLBACK"
        if "VWAP" in reason:
            strat = "VWAP_PULLBACK"
            tag = "MEAN_REVERSION"
        elif "ORB" in reason or "Opening" in reason:
            strat = "ORB"
            tag = "BREAKOUT"
        elif "TREND" in reason or "Momentum" in reason:
            strat = "TREND_MOMENTUM"
            tag = "TREND_FOLLOWING"
        elif "CONFLUENCE" in reason:
            strat = "CONFLUENCE"
            tag = "MULTI_TIMEFRAME"
        elif "V3" in reason or "V3_TREND_PULLBACK" in reason:
            strat = "V3_TREND_PULLBACK"
            tag = "QUANT_REGIME"
        elif "NIFTY" in reason or "Manual" in reason:
            strat = "FNO_CONFLUENCE"
            tag = "INDEX_DERIVATIVES"

        if strat not in strat_map:
            strat_map[strat] = {
                "name": strat,
                "tag": tag,
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "net_pnl": 0.0
            }

        pnl = float(t.get("pnl", 0.0))
        strat_map[strat]["trades"] += 1
        strat_map[strat]["net_pnl"] += pnl
        if pnl > 0:
            strat_map[strat]["wins"] += 1
            strat_map[strat]["gross_profit"] += pnl
        elif pnl < 0:
            strat_map[strat]["losses"] += 1
            strat_map[strat]["gross_loss"] += abs(pnl)

    result = []
    for s, data in strat_map.items():
        cnt = data["trades"]
        win_rate = round((data["wins"] / cnt) * 100, 1) if cnt > 0 else 0.0
        pf = round(data["gross_profit"] / data["gross_loss"], 2) if data["gross_loss"] > 0 else (round(data["gross_profit"], 2) if data["gross_profit"] > 0 else 1.0)
        avg_w = round(data["gross_profit"] / data["wins"], 2) if data["wins"] > 0 else 0.0
        avg_l = round(data["gross_loss"] / data["losses"], 2) if data["losses"] > 0 else 0.0
        exp = round(((win_rate / 100.0) * avg_w) - (((100.0 - win_rate) / 100.0) * avg_l), 2)
        result.append({
            "strategy": s,
            "tag": data["tag"],
            "trades": cnt,
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": win_rate,
            "profit_factor": pf,
            "avg_win": avg_w,
            "avg_loss": avg_l,
            "expectancy": exp,
            "net_pnl": round(data["net_pnl"], 2),
            "gross_profit": round(data["gross_profit"], 2),
            "gross_loss": round(data["gross_loss"], 2),
            "execution_type": "PAPER_VERIFIED"
        })

    return sorted(result, key=lambda x: x["net_pnl"], reverse=True)

def get_risk_audit(account_id="gautam"):
    """
    Evaluates current risk metrics, limit breaches, and health warnings.
    """
    acc_id = (account_id or "gautam").lower().strip()
    accounts_info = read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
    acc_meta = accounts_info.get("accounts", {}).get(acc_id, {})
    ledger_filename = acc_meta.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")

    config = read_json_file(TRADER_DIR / "config.json", default={})
    ledger = read_json_file(TRADER_DIR / ledger_filename, default={})

    budget = float(acc_meta.get("capital", config.get("budget", 100000.0)))
    max_daily_loss = float(config.get("max_daily_loss_inr", config.get("max_daily_loss_usd", 1000.0)))
    max_trades_per_day = int(config.get("max_trades_per_day", 3))
    max_open_positions = int(config.get("max_open_positions", 3))
    max_pos_pct = float(config.get("max_position_pct", 75))

    today_str = get_ist_now().strftime("%Y-%m-%d")
    today_trades = [t for t in ledger.get("trade_history", []) if (t.get("date") == today_str or t.get("exit_date") == today_str)]
    today_trades_count = len(today_trades)
    today_realized_loss = abs(min(0.0, sum(float(t.get("pnl", 0.0)) for t in today_trades)))
    open_positions = ledger.get("open_positions", [])

    alerts = []
    # 1. Daily Loss Check
    if today_realized_loss >= max_daily_loss:
        alerts.append({
            "severity": "CRITICAL",
            "type": "DAILY_LOSS_BREACH",
            "message": f"Daily loss limit hit: ₹{today_realized_loss:,.2f} >= ₹{max_daily_loss:,.2f}. Trading halted."
        })
    elif today_realized_loss >= (max_daily_loss * 0.75):
        alerts.append({
            "severity": "WARNING",
            "type": "DAILY_LOSS_WARNING",
            "message": f"Daily loss approaching limit (75% utilized): ₹{today_realized_loss:,.2f} / ₹{max_daily_loss:,.2f}."
        })

    # 2. Daily Trades Limit Check
    if today_trades_count >= max_trades_per_day:
        alerts.append({
            "severity": "INFO",
            "type": "MAX_TRADES_REACHED",
            "message": f"Today's trade quota reached ({today_trades_count}/{max_trades_per_day}). New entries halted."
        })

    # 3. Open Positions Limit
    if len(open_positions) >= max_open_positions:
        alerts.append({
            "severity": "WARNING",
            "type": "MAX_POSITIONS_HOLD",
            "message": f"Maximum concurrent positions open ({len(open_positions)}/{max_open_positions})."
        })

    # 4. Stop-loss coverage validation (supports sl_price, current_stop, initial_stop, stop_loss)
    for p in open_positions:
        sl = p.get("sl_price") or p.get("current_stop") or p.get("initial_stop") or p.get("stop_loss")
        if not sl or float(sl) <= 0:
            alerts.append({
                "severity": "CRITICAL",
                "type": "MISSING_STOP_LOSS",
                "message": f"Position {p.get('symbol')} is missing mandatory stop-loss!"
            })

    # 5. Market close square-off approaching
    now_ist = get_ist_now().strftime("%H:%M")
    flat_by = config.get("flat_by", "15:20")
    if now_ist >= "15:10" and open_positions:
        alerts.append({
            "severity": "WARNING",
            "type": "EOD_SQUAREOFF_APPROACHING",
            "message": f"Approaching {flat_by} IST square-off deadline with {len(open_positions)} open positions."
        })

    return {
        "budget": budget,
        "max_daily_loss": max_daily_loss,
        "max_trades_per_day": max_trades_per_day,
        "max_open_positions": max_open_positions,
        "max_position_pct": max_pos_pct,
        "realized_daily_loss": today_realized_loss,
        "current_trades_count": today_trades_count,
        "open_positions_count": len(open_positions),
        "flat_by_time": flat_by,
        "alerts": alerts
    }

def get_system_logs(filter_level=None, limit=100):
    """
    Parses and returns structured system events and log entries.
    """
    events = []
    # 1. Audit events
    audit_events = read_json_file(AUDIT_LOG_PATH, default=[])
    for ae in audit_events:
        events.append({
            "timestamp": ae.get("timestamp"),
            "level": "INFO" if ae.get("status") == "SUCCESS" else "ERROR",
            "component": "AUDIT",
            "message": f"{ae.get('action')}: {json.dumps(ae.get('details', {}))}"
        })

    # 2. Trade Ledger Events
    ledger = read_json_file(TRADER_DIR / "ledger.json", default={})
    for t in ledger.get("trade_history", []):
        events.append({
            "timestamp": t.get("exit_time", ""),
            "level": "INFO" if float(t.get("pnl", 0)) >= 0 else "WARNING",
            "component": "LEDGER_EXIT",
            "message": f"Closed {t.get('symbol')} Qty:{t.get('qty')} @ ₹{t.get('exit_price')} P&L: ₹{t.get('pnl')} ({t.get('reason')})"
        })

    # Sort descending by timestamp string
    events.sort(key=lambda x: str(x.get("timestamp")), reverse=True)
    if filter_level:
        events = [e for e in events if e.get("level") == filter_level]
    return events[:limit]

def sanitize_config(cfg):
    """
    Sanitizes configuration dictionary by masking Telegram bot token and secrets.
    Never returns raw tokens or secret credentials.
    """
    safe_cfg = dict(cfg)
    if "telegram_bot_token" in safe_cfg:
        token = str(safe_cfg["telegram_bot_token"])
        if len(token) > 8:
            safe_cfg["telegram_bot_token"] = token[:4] + "••••••••" + token[-4:]
        else:
            safe_cfg["telegram_bot_token"] = "••••••••"
    if "api_token" in safe_cfg:
        safe_cfg["api_token"] = "••••••••"
    if "sec_user_agent" in safe_cfg:
        safe_cfg["sec_user_agent"] = "Configured (Protected)"
    return safe_cfg

def reconcile_account_ledger(account_id="gautam"):
    """
    Performs one-click accounting reconciliation and auto-repair on account ledger.
    """
    acc_id = (account_id or "gautam").lower().strip()
    accounts_info = read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
    acc_meta = accounts_info.get("accounts", {}).get(acc_id, {})
    ledger_filename = acc_meta.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")
    ledger_path = TRADER_DIR / ledger_filename

    from ledger_utils import reconcile_ledger
    ledger_data = read_json_file(ledger_path, default={})
    is_valid, report, repaired = reconcile_ledger(ledger_data, auto_repair=True)
    if repaired:
        write_json_file(ledger_path, repaired)
        log_audit_event("RECONCILE_LEDGER", {"account": acc_id, "file": ledger_filename, "is_valid": is_valid})
    return {"status": "SUCCESS", "is_valid": is_valid, "report": report}

def get_fno_desk_data():
    """
    Returns options desk intelligence:
    - Spot prices for NIFTY 50 and BANKNIFTY
    - Dynamic ATM strike calculations
    - Active F&O open positions with lot sizes, greeks, and mark-to-market
    - Recent F&O trade metrics
    """
    from options_engine import OptionsEngine
    nifty_quote = fetch_live_quote_data("NIFTY 50")
    banknifty_quote = fetch_live_quote_data("BANKNIFTY")

    nifty_spot = nifty_quote["price"] if nifty_quote else 25250.0
    banknifty_spot = banknifty_quote["price"] if banknifty_quote else 60800.0

    opt_eng = OptionsEngine()
    nifty_atm = opt_eng.select_strike(nifty_spot, "CE", "NIFTY")
    banknifty_atm = opt_eng.select_strike(banknifty_spot, "CE", "BANKNIFTY")

    fno_ledger = read_json_file(TRADER_DIR / "ledger_fno.json", default={})
    fno_positions = fno_ledger.get("open_positions", [])
    fno_history = fno_ledger.get("trade_history", [])

    enriched_fno_pos = []
    for p in fno_positions:
        sym = p.get("symbol", "")
        entry_p = float(p.get("entry_price", 0.0))
        cur_p = float(p.get("current_price", entry_p))
        qty = int(p.get("qty", 25))
        invested = float(p.get("amount", p.get("invested", round(qty * entry_p, 2))))
        pnl = round((cur_p - entry_p) * qty, 2)
        pnl_pct = round((pnl / invested) * 100, 2) if invested > 0 else 0.0
        lot_size = OptionsEngine.get_lot_size(sym)
        enriched_fno_pos.append({
            **p,
            "invested": invested,
            "current_price": cur_p,
            "unrealized_pnl": pnl,
            "unrealized_pnl_pct": pnl_pct,
            "lot_size": lot_size,
            "lots": max(1, qty // lot_size) if lot_size > 0 else 1
        })

    return {
        "indices": {
            "NIFTY": {
                "spot": nifty_spot,
                "atm_strike": nifty_atm,
                "step": 50,
                "lot_size": 25,
                "ce_contract": f"NIFTY23NOV26{nifty_atm}CE",
                "pe_contract": f"NIFTY23NOV26{nifty_atm}PE",
                "last_updated": nifty_quote["last_updated"] if nifty_quote else "QUOTE UNAVAILABLE"
            },
            "BANKNIFTY": {
                "spot": banknifty_spot,
                "atm_strike": banknifty_atm,
                "step": 100,
                "lot_size": 15,
                "ce_contract": f"BANKNIFTY23NOV26{banknifty_atm}CE",
                "pe_contract": f"BANKNIFTY23NOV26{banknifty_atm}PE",
                "last_updated": banknifty_quote["last_updated"] if banknifty_quote else "QUOTE UNAVAILABLE"
            }
        },
        "open_positions": enriched_fno_pos,
        "starting_capital": float(fno_ledger.get("starting_capital", 200000.0)),
        "free_cash": float(fno_ledger.get("free_cash", 200000.0)),
        "deployed_cash": float(fno_ledger.get("deployed_cash", 0.0)),
        "realized_pnl": float(fno_ledger.get("realized_pnl", 0.0)),
        "total_trades": len(fno_history)
    }
