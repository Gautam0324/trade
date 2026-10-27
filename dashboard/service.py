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
    Checks if trader-scanner/daemon.py is currently running.
    """
    global daemon_process
    if daemon_process is not None:
        poll = daemon_process.poll()
        if poll is None:
            return {"status": "RUNNING", "pid": daemon_process.pid, "managed": True}
        else:
            daemon_process = None

    # Check via OS process list
    try:
        if os.name == "nt":
            cmd = 'powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \\"Name = \'python.exe\'\\" | Where-Object { $_.CommandLine -like \'*daemon.py*\' } | Select-Object -ExpandProperty ProcessId"'
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL).strip()
            if out and out.split()[0].isdigit():
                return {"status": "RUNNING", "pid": int(out.split()[0]), "managed": False}
        else:
            cmd = "pgrep -f 'trader-scanner/daemon.py'"
            out = subprocess.check_output(cmd, shell=True, text=True, stderr=subprocess.DEVNULL)
            if out.strip():
                return {"status": "RUNNING", "pid": int(out.strip().split()[0]), "managed": False}
    except Exception:
        pass
    return {"status": "STOPPED", "pid": None, "managed": False}

def fetch_live_quote_data(symbol):
    now_ts = time.time()
    if symbol in quote_cache:
        cached = quote_cache[symbol]
        if (now_ts - cached["timestamp"]) < QUOTE_CACHE_TTL:
            return cached["data"]

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

    # Indicator calculation
    import ema_strategy
    ema_eval = ema_strategy.evaluate_ema_setup(quotes)

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
        "signal": ema_eval.get("signal", "HOLD"),
        "ema5": ema_eval.get("ema5", cur_p),
        "ema9": ema_eval.get("ema9", cur_p),
        "ema33_high": ema_eval.get("ema33_high", cur_p),
        "ema33_low": ema_eval.get("ema33_low", cur_p),
        "last_updated": get_ist_now().strftime("%H:%M:%S IST")
    }

    quote_cache[symbol] = {
        "timestamp": now_ts,
        "data": data
    }
    return data

def compute_dashboard_metrics():
    """
    Computes all mathematical KPIs using the single source of truth: ledger.json
    """
    ledger = read_json_file(TRADER_DIR / "ledger.json", default={})
    config = read_json_file(TRADER_DIR / "config.json", default={})

    starting_cap = float(ledger.get("starting_capital", config.get("budget", 100000.0)))
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
        invested = float(p.get("amount", round(qty * entry_p, 2)))
        
        quote = fetch_live_quote_data(sym)
        cur_p = quote["price"] if quote else entry_p
        
        pos_pnl = round((cur_p - entry_p) * qty, 2)
        pos_pnl_pct = round((pos_pnl / invested) * 100, 2) if invested > 0 else 0.0
        unrealized_pnl += pos_pnl

        enriched_positions.append({
            **p,
            "current_price": cur_p,
            "unrealized_pnl": pos_pnl,
            "unrealized_pnl_pct": pos_pnl_pct,
            "market_value": round(qty * cur_p, 2),
            "last_quote_time": quote["last_updated"] if quote else "N/A"
        })

    unrealized_pnl = round(unrealized_pnl, 2)
    total_net_pnl = round(realized_pnl + unrealized_pnl, 2)
    total_portfolio_val = round(free_cash + deployed_cash + unrealized_pnl, 2)
    return_pct = round((total_net_pnl / starting_cap) * 100, 2) if starting_cap > 0 else 0.0

    # Trade statistics
    closed_trades = [t for t in trade_history if "pnl" in t]
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

    max_daily_loss = float(config.get("max_daily_loss_usd", 1000.0))
    daily_loss_utilization = round((abs(min(0.0, realized_pnl)) / max_daily_loss) * 100, 1) if max_daily_loss > 0 else 0.0

    today_str = get_ist_now().strftime("%Y-%m-%d")
    today_trades = [t for t in trade_history if t.get("date") == today_str or t.get("exit_date") == today_str]
    today_realized_pnl = sum(float(t.get("pnl", 0.0)) for t in today_trades)

    return {
        "starting_capital": starting_cap,
        "free_cash": free_cash,
        "deployed_cash": deployed_cash,
        "total_portfolio_value": total_portfolio_val,
        "realized_pnl": realized_pnl,
        "today_realized_pnl": round(today_realized_pnl, 2),
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
        "enriched_positions": enriched_positions
    }

def get_equity_curve_data():
    """
    Builds equity trajectory over time using trade history.
    """
    ledger = read_json_file(TRADER_DIR / "ledger.json", default={})
    starting_cap = float(ledger.get("starting_capital", 100000.0))
    trade_history = ledger.get("trade_history", [])

    points = []
    points.append({
        "time": "09:15:00",
        "equity": starting_cap,
        "cumulative_pnl": 0.0,
        "trade_id": "START"
    })

    current_cap = starting_cap
    cumulative_pnl = 0.0

    for idx, t in enumerate(trade_history):
        pnl = float(t.get("pnl", 0.0))
        cumulative_pnl = round(cumulative_pnl + pnl, 2)
        current_cap = round(starting_cap + cumulative_pnl, 2)
        exit_time = t.get("exit_time", f"Trade {idx+1}").replace(" IST", "")

        points.append({
            "time": exit_time,
            "equity": current_cap,
            "cumulative_pnl": cumulative_pnl,
            "trade_id": t.get("id", f"POS-{idx+1}"),
            "symbol": t.get("symbol", ""),
            "trade_pnl": pnl
        })

    return points

def get_strategy_performance():
    """
    Separates actual trading performance by entry strategy.
    """
    ledger = read_json_file(TRADER_DIR / "ledger.json", default={})
    trade_history = ledger.get("trade_history", [])

    strat_map = {}
    for t in trade_history:
        reason = t.get("entry_reason") or t.get("reason", "EMA 5 > EMA 9 Channel Breakout")
        # Extract Strategy Name
        strat = "EMA_CHANNEL"
        if "VWAP" in reason:
            strat = "VWAP_PULLBACK"
        elif "ORB" in reason or "Opening" in reason:
            strat = "ORB"
        elif "TREND" in reason or "Momentum" in reason:
            strat = "TREND_MOMENTUM"
        elif "CONFLUENCE" in reason:
            strat = "CONFLUENCE"
        elif "NIFTY" in reason or "Manual" in reason:
            strat = "MANUAL_INDEX"

        if strat not in strat_map:
            strat_map[strat] = {
                "name": strat,
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
        result.append({
            "strategy": s,
            "trades": cnt,
            "wins": data["wins"],
            "losses": data["losses"],
            "win_rate": win_rate,
            "profit_factor": pf,
            "net_pnl": round(data["net_pnl"], 2),
            "gross_profit": round(data["gross_profit"], 2),
            "gross_loss": round(data["gross_loss"], 2)
        })

    return result

def get_risk_audit():
    """
    Evaluates current risk metrics, limit breaches, and health warnings.
    """
    config = read_json_file(TRADER_DIR / "config.json", default={})
    ledger = read_json_file(TRADER_DIR / "ledger.json", default={})

    budget = float(config.get("budget", 100000.0))
    max_daily_loss = float(config.get("max_daily_loss_usd", 1000.0))
    max_trades_per_day = int(config.get("max_trades_per_day", 3))
    max_open_positions = int(config.get("max_open_positions", 3))
    max_pos_pct = float(config.get("max_position_pct", 75))

    today_str = get_ist_now().strftime("%Y-%m-%d")
    today_trades = [t for t in ledger.get("trade_history", []) if t.get("date") == today_str or t.get("exit_date") == today_str]
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

    # 4. Stop-loss coverage validation
    for p in open_positions:
        if not p.get("initial_stop") and not p.get("stop_loss"):
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
    """
    safe_cfg = dict(cfg)
    if "telegram_bot_token" in safe_cfg:
        token = str(safe_cfg["telegram_bot_token"])
        if len(token) > 8:
            safe_cfg["telegram_bot_token"] = token[:4] + "••••••••" + token[-4:]
        else:
            safe_cfg["telegram_bot_token"] = "••••••••"
    if "sec_user_agent" in safe_cfg:
        safe_cfg["sec_user_agent"] = "Configured (Protected)"
    return safe_cfg
