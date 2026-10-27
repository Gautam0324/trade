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

class PremiumDashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

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
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def send_json(self, data, status=200):
        response_bytes = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response_bytes)

    def handle_api_get(self, path, query):
        if path == "/api/status" or path == "/api/overview":
            # Complete Overview Payload
            kpis = service.compute_dashboard_metrics()
            daemon_info = service.check_daemon_status()
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            market_status = service.is_market_open()
            ist_time = service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

            self.send_json({
                "market_status": market_status,
                "trading_mode": "PAPER" if config.get("paper", True) else "LIVE",
                "ist_time": ist_time,
                "bot_status": daemon_info,
                "kpis": kpis,
                "config": service.sanitize_config(config)
            })

        elif path == "/api/equity_curve":
            points = service.get_equity_curve_data()
            self.send_json({"points": points})

        elif path == "/api/watchlist":
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            symbols = config.get("watchlist", [
                "RELIANCE.NS",
                "TCS.NS",
                "HDFCBANK.NS",
                "INFY.NS",
                "ICICIBANK.NS",
                "TATAMOTORS.NS",
                "SBIN.NS"
            ])
            quotes = []
            for s in symbols:
                q = service.fetch_live_quote_data(s)
                if q:
                    quotes.append(q)
            self.send_json({"symbols": quotes})

        elif path == "/api/positions":
            kpis = service.compute_dashboard_metrics()
            self.send_json({
                "open_positions": kpis["enriched_positions"],
                "total_unrealized_pnl": kpis["unrealized_pnl"],
                "total_deployed_cash": kpis["deployed_cash"]
            })

        elif path == "/api/trades":
            ledger = service.read_json_file(TRADER_DIR / "ledger.json", default={})
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

        elif path == "/api/quote":
            symbol = query.get("symbol", ["RELIANCE.NS"])[0].upper()
            quote = service.fetch_live_quote_data(symbol)
            if quote:
                self.send_json(quote)
            else:
                self.send_json({"error": f"Quote unavailable for {symbol}"}, status=404)

        else:
            self.send_error(404, "API endpoint not found")

    def handle_api_post(self, path, data):
        if path == "/api/bot/start":
            status = service.check_daemon_status()
            if status["status"] == "RUNNING":
                self.send_json({"status": "ALREADY_RUNNING", "pid": status["pid"], "message": "Bot is already running."})
                return

            daemon_script = BASE_DIR / "trader-scanner" / "daemon.py"
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
    server_address = ("", port)
    httpd = http.server.HTTPServer(server_address, PremiumDashboardHandler)
    print(f"Institutional Algo Trading Terminal running on http://localhost:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    p = PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run(p)
