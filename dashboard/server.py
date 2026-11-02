#!/usr/bin/env python3
"""
Production-Quality HTTP & REST API Server for Algo Trading Terminal.
Serves:
- Institutional Fintech Terminal UI
- Real-time endpoints for Overview KPIs, Equity Curve, Watchlist, Positions, Trade History,
  Strategy Performance, Risk Audit, Bot Process Management, Logs, and Configuration.
"""

import http.server
import json
import os
import sys
import subprocess
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
TRADER_DIR = BASE_DIR / "trader"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(TRADER_DIR))

import service

PORT = 8080

import secrets

# Generate a random API token at startup
DASHBOARD_API_TOKEN = os.environ.get("DASHBOARD_API_TOKEN") or secrets.token_hex(16)
print(f"\n[SECURITY] Dashboard API Token: {DASHBOARD_API_TOKEN}\n")

class PremiumDashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def validate_host_and_origin(self):
        """
        Validates Host and Origin against localhost, 127.0.0.1, or local Wi-Fi IP (10.10.0.199).
        """
        host = self.headers.get("Host", "").split(":")[0].lower()
        allowed = ("localhost", "127.0.0.1", "10.10.0.199", "0.0.0.0")
        if host not in allowed and not host.startswith("10.10.0.") and not host.startswith("192.168."):
            self.send_error(403, "Forbidden Host")
            return False

        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            if origin_host not in allowed and not origin_host.startswith("10.10.0.") and not origin_host.startswith("192.168."):
                self.send_error(403, "Forbidden Origin")
                return False
        return True

    def validate_api_token(self):
        """
        Requires DASHBOARD_API_TOKEN on every POST request (via X-API-Token or Authorization).
        """
        token = self.headers.get("X-API-Token")
        if not token:
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                token = auth[7:].strip()
        if not token or token != DASHBOARD_API_TOKEN:
            self.send_json({"error": "Unauthorized: Invalid or missing API token"}, status=401)
            return False
        return True

    def do_GET(self):
        if not self.validate_host_and_origin():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            self.send_json({
                "status": "HEALTHY",
                "paper_trading": True,
                "live_trading": False,
                "timestamp": service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
            })
            return

        if path.startswith("/api/"):
            try:
                self.handle_api_get(path, query)
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
        else:
            if path == "/" or not path or path == "/index.html":
                self.path = "/index.html"
            super().do_GET()

    def do_POST(self):
        if not self.validate_host_and_origin():
            return
        if not self.validate_api_token():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        if path.startswith("/api/"):
            try:
                self.handle_api_post(path, data)
            except Exception as e:
                self.send_json({"error": str(e)}, status=500)
        else:
            self.send_error(404, "Not Found")

    def do_OPTIONS(self):
        if not self.validate_host_and_origin():
            return
        self.send_response(200)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token")
        self.end_headers()

    def send_json(self, data, status=200):
        response_bytes = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token")
        self.end_headers()
        self.wfile.write(response_bytes)

    def handle_api_get(self, path, query):
        account = query.get("account", ["gautam"])[0].lower()
        accounts_data = service.read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
        acc_meta = accounts_data.get("accounts", {}).get(account, {})
        ledger_filename = acc_meta.get("ledger_file", f"ledger_{account}.json" if account != "gautam" else "ledger.json")

        if path == "/api/status" or path == "/api/overview":
            # Complete Overview Payload
            kpis = service.compute_dashboard_metrics(account_id=account)
            daemon_info = service.check_daemon_status()
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            market_status = service.is_market_open()
            ist_time = service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

            self.send_json({
                "account": account,
                "accounts": accounts_data.get("accounts", {}),
                "market_status": market_status,
                "trading_mode": "PAPER" if config.get("paper", True) else "LIVE",
                "ist_time": ist_time,
                "bot_status": daemon_info,
                "kpis": kpis,
                "config": service.sanitize_config(config),
                "api_token": DASHBOARD_API_TOKEN
            })

        elif path == "/api/system/status":
            daemon_info = service.check_daemon_status()
            ledger = service.read_json_file(TRADER_DIR / ledger_filename, default={})
            positions = ledger.get("open_positions", [])
            self.send_json({
                "account": account,
                "status": "HEALTHY" if daemon_info.get("running") else "DEGRADED",
                "daemon": daemon_info,
                "open_positions_count": len(positions),
                "timestamp": service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
            })

        elif path == "/api/accounts":
            acc_file = TRADER_DIR / "accounts.json"
            acc_data = service.read_json_file(acc_file, default={"accounts": {}})
            self.send_json(acc_data)

        elif path == "/api/equity_curve":
            points = service.get_equity_curve_data(account_id=account)
            self.send_json({"points": points, "account": account})

        elif path == "/api/watchlist":
            symbols = acc_meta.get("watchlist", [])
            if not symbols:
                config = service.read_json_file(TRADER_DIR / "config.json", default={})
                symbols = config.get("watchlist", [
                    "JPL",
                    "TCS",
                    "RELIANCE",
                    "INFY",
                    "HDFCBANK"
                ])
            quotes = []
            for s in symbols:
                q = service.fetch_live_quote_data(s)
                if q:
                    quotes.append(q)
            self.send_json({"symbols": quotes, "account": account})

        elif path == "/api/positions":
            kpis = service.compute_dashboard_metrics(account_id=account)
            self.send_json({
                "account": account,
                "open_positions": kpis["enriched_positions"],
                "total_unrealized_pnl": kpis["unrealized_pnl"],
                "total_deployed_cash": kpis["deployed_cash"]
            })

        elif path == "/api/trades":
            ledger = service.read_json_file(TRADER_DIR / ledger_filename, default={})
            history = ledger.get("trade_history", [])
            
            # Support symbol, strategy, and outcome filters
            symbol_filter = query.get("symbol", [None])[0]
            outcome_filter = query.get("outcome", [None])[0] # WIN, LOSS, BREAKEVEN

            filtered = []
            for t in reversed(history):
                pnl = float(t.get("pnl", 0.0))
                if symbol_filter and t.get("symbol") != symbol_filter:
                    continue
                if outcome_filter == "WIN" and pnl <= 0:
                    continue
                if outcome_filter == "LOSS" and pnl >= 0:
                    continue
                if outcome_filter == "BREAKEVEN" and pnl != 0:
                    continue
                filtered.append(t)

            self.send_json({
                "total_records": len(history),
                "filtered_count": len(filtered),
                "trades": filtered
            })

        elif path == "/api/strategies":
            stats = service.get_strategy_performance()
            self.send_json({"strategies": stats})

        elif path == "/api/risk":
            risk_data = service.get_risk_audit()
            self.send_json(risk_data)

        elif path == "/api/logs":
            lvl = query.get("level", [None])[0]
            logs = service.get_system_logs(filter_level=lvl)
            self.send_json({"logs": logs})

        elif path == "/api/config":
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            self.send_json({"config": service.sanitize_config(config)})

        elif path == "/api/signals":
            # Return evaluated signals, scores, weights and rejections
            import db_v3
            conn = db_v3.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals_audit ORDER BY id DESC LIMIT 50")
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            self.send_json({"signals": rows})

        elif path == "/api/backtest":
            from backtest import BacktestEngine
            import daemon
            symbol = query.get("symbol", ["RELIANCE.NS"])[0].upper()
            strat = query.get("strategy", ["CONFLUENCE"])[0].upper()
            quotes = daemon.fetch_candles(symbol)
            if quotes and len(quotes.get("close", [])) >= 40:
                bt = BacktestEngine(starting_capital=100000.0)
                res = bt.run_strategy(quotes, strategy_name=strat)
                self.send_json({"symbol": symbol, "result": res})
            else:
                self.send_json({"error": "Insufficient candle data for backtest"}, status=400)

        else:
            self.send_error(404, "API endpoint not found")

    def handle_api_post(self, path, data):
        if path == "/api/bot/start":
            status = service.check_daemon_status()
            if status["status"] == "RUNNING":
                self.send_json({"status": "ALREADY_RUNNING", "pid": status["pid"], "message": "Bot is already running."})
                return

            daemon_script = BASE_DIR / "trader" / "multi_live_trader.py"
            proc = subprocess.Popen(
                [sys.executable, str(daemon_script)],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            service.daemon_process = proc
            service.log_audit_event("START_BOT_DAEMON", {"pid": proc.pid})
            self.send_json({"status": "SUCCESS", "pid": proc.pid, "message": "Trading bot daemon started successfully."})

        elif path == "/api/bot/stop":
            status = service.check_daemon_status()
            if status["status"] != "RUNNING" or not status["pid"]:
                self.send_json({"status": "NOT_RUNNING", "message": "Bot daemon is not running."})
                return

            pid = status["pid"]
            try:
                if os.name == "nt":
                    subprocess.call(["taskkill", "/F", "/PID", str(pid)])
                else:
                    os.kill(int(pid), 9)
                service.daemon_process = None
                service.log_audit_event("STOP_BOT_DAEMON", {"pid": pid})
                self.send_json({"status": "SUCCESS", "message": f"Daemon PID {pid} stopped."})
            except Exception as e:
                service.log_audit_event("STOP_BOT_DAEMON_ERROR", {"error": str(e)}, status="FAILED")
                self.send_json({"status": "ERROR", "message": str(e)}, status=500)

        elif path == "/api/positions/close":
            # Confirmed exit of an open position via existing verified PositionManager / ledger
            symbol = data.get("symbol")
            if not symbol:
                self.send_json({"status": "ERROR", "message": "Symbol is required."}, status=400)
                return

            import ledger
            quote = service.fetch_live_quote_data(symbol)
            cur_p = quote["price"] if quote else 0.0
            if cur_p <= 0:
                self.send_json({"status": "ERROR", "message": "Unable to verify live price for execution."}, status=400)
                return

            ok, res = ledger.record_trade_exit(symbol, cur_p, "Manual Exit from Dashboard Terminal")
            if ok:
                service.log_audit_event("MANUAL_CLOSE_POSITION", {"symbol": symbol, "exit_price": cur_p})
                self.send_json({"status": "SUCCESS", "message": f"Position {symbol} closed @ ₹{cur_p:,.2f}"})
            else:
                self.send_json({"status": "ERROR", "message": f"Failed to close position: {res}"}, status=400)

        elif path == "/api/config/update":
            # Safe config update for non-secret risk parameters
            allowed_keys = {
                "max_trades_per_day",
                "max_open_positions",
                "cooldown_minutes",
                "no_entries_before",
                "no_entries_after",
                "flat_by",
                "strategy_mode",
                "min_confluence_score"
            }
            cfg = service.read_json_file(TRADER_DIR / "config.json", default={})
            updated = {}
            for k, v in data.items():
                if k in allowed_keys:
                    cfg[k] = v
                    updated[k] = v

            service.write_json_file(TRADER_DIR / "config.json", cfg)
            service.log_audit_event("UPDATE_CONFIGURATION", updated)
            self.send_json({"status": "SUCCESS", "updated": updated, "config": service.sanitize_config(cfg)})

        else:
            self.send_error(404, "API endpoint not found")

def run(port=PORT):
    host = os.environ.get("DASHBOARD_HOST", "0.0.0.0")
    server_address = (host, port)
    httpd = http.server.HTTPServer(server_address, PremiumDashboardHandler)
    print(f"Institutional Algo Trading Terminal running on http://{host}:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    p = PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run(p)

