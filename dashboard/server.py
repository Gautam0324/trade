#!/usr/bin/env python3
"""
Lightweight REST API & Static File Server for Day Trader Skill Dashboard.
Supports Global Markets: US (USD), NSE (INR - .NS), and BSE (INR - .BO).
"""

import http.server
import json
import math
import os
import subprocess
import sys
import urllib.parse
import urllib.request
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
DASHBOARD_DIR = BASE_DIR / "dashboard"

PORT = 8080

def read_json_file(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": str(e)}
    return None

def write_json_file(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def fetch_live_quote(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=5m"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; DayTrader/2.0)"})
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            res = data.get("chart", {}).get("result", [])
            if not res:
                return None
            meta = res[0].get("meta", {})
            quotes = res[0].get("indicators", {}).get("quote", [{}])[0]
            closes = [c for c in quotes.get("close", []) if c is not None]
            highs = [h for h in quotes.get("high", []) if h is not None]
            lows = [l for l in quotes.get("low", []) if l is not None]

            latest_price = meta.get("regularMarketPrice") or (closes[-1] if closes else 0.0)
            currency = meta.get("currency", "INR" if (".NS" in symbol or ".BO" in symbol) else "USD")

            return {
                "symbol": symbol,
                "price": round(float(latest_price), 2),
                "currency": currency,
                "exchange": meta.get("exchangeName", "NSE" if ".NS" in symbol else "BSE"),
                "high": round(float(max(highs)), 2) if highs else round(float(latest_price), 2),
                "low": round(float(min(lows)), 2) if lows else round(float(latest_price), 2),
                "history": closes[-30:] if closes else []
            }
    except Exception as e:
        print(f"Error fetching quote for {symbol}: {e}")
        return None

class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            self.handle_api_get(path, query)
        else:
            if path == "/" or not path:
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
            self.handle_api_post(path, data)
        else:
            self.send_error(404, "Not Found")

    def send_json(self, data, status=200):
        response_bytes = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(response_bytes)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def handle_api_get(self, path, query):
        if path == "/api/status":
            config = read_json_file(TRADER_DIR / "config.json") or {}
            plan = read_json_file(TRADER_DIR / "plan.json") or {}
            orders = read_json_file(TRADER_DIR / "orders.json") or {}
            timemachine = read_json_file(TRADER_DIR / "timemachine.json")

            tm_summary = None
            if timemachine and isinstance(timemachine, dict):
                mc = timemachine.get("mc", {})
                tm_summary = {
                    "p_target": mc.get("p_target"),
                    "p_stop": mc.get("p_stop"),
                    "p_neither": mc.get("p_neither"),
                    "p_green": mc.get("p_green"),
                    "median_close": mc.get("median_close"),
                    "avg_R": mc.get("avg_R"),
                    "replays": mc.get("replays"),
                    "bands": mc.get("bands", [])[::2],
                    "bars_left": mc.get("bars_left"),
                }

            ledger = read_json_file(TRADER_DIR / "ledger.json")

            self.send_json({
                "config": config,
                "plan": plan,
                "orders": orders,
                "timemachine": tm_summary,
                "ledger": ledger
            })

        elif path == "/api/quote":
            symbol = query.get("symbol", ["RELIANCE.NS"])[0].upper()
            quote = fetch_live_quote(symbol)
            if quote:
                self.send_json(quote)
            else:
                self.send_json({"error": f"Could not fetch quote for {symbol}"}, status=404)

        elif path == "/api/popular_indian":
            # Quick list of liquid Indian stocks for NSE/BSE
            stocks = [
                {"symbol": "RELIANCE.NS", "name": "Reliance Industries", "exchange": "NSE"},
                {"symbol": "TCS.NS", "name": "Tata Consultancy Services", "exchange": "NSE"},
                {"symbol": "HDFCBANK.NS", "name": "HDFC Bank", "exchange": "NSE"},
                {"symbol": "INFY.NS", "name": "Infosys Ltd", "exchange": "NSE"},
                {"symbol": "ICICIBANK.NS", "name": "ICICI Bank", "exchange": "NSE"},
                {"symbol": "SBIN.NS", "name": "State Bank of India", "exchange": "NSE"},
                {"symbol": "TATAMOTORS.NS", "name": "Tata Motors", "exchange": "NSE"},
                {"symbol": "ITC.NS", "name": "ITC Ltd", "exchange": "NSE"},
                {"symbol": "TCS.BO", "name": "TCS (BSE)", "exchange": "BSE"},
                {"symbol": "RELIANCE.BO", "name": "Reliance (BSE)", "exchange": "BSE"}
            ]
            self.send_json({"stocks": stocks})

        elif path == "/api/news":
            symbol = query.get("symbol", ["RELIANCE.NS"])[0]
            # Use trader-news or generic headline
            try:
                cmd = [sys.executable, str(BASE_DIR / "trader-news" / "news.py"), "--tickers", symbol, "--no-sec"]
                res = subprocess.run(cmd, cwd=str(BASE_DIR), capture_output=True, text=True, timeout=10)
                self.send_json({"symbol": symbol, "output": res.stdout, "error": res.stderr})
            except Exception as e:
                self.send_json({"symbol": symbol, "output": "News stream available: " + str(e), "error": str(e)})

        else:
            self.send_error(404, "API route not found")

    def handle_api_post(self, path, data):
        if path == "/api/config":
            cfg = read_json_file(TRADER_DIR / "config.json") or {}
            cfg.update(data)
            write_json_file(TRADER_DIR / "config.json", cfg)
            self.send_json({"status": "ok", "config": cfg})

        elif path == "/api/plan":
            write_json_file(TRADER_DIR / "plan.json", data)
            self.send_json({"status": "ok", "plan": data})

        elif path == "/api/set_market":
            # Switch market between US and India (NSE/BSE)
            market = data.get("market", "NSE")  # NSE, BSE, US
            symbol = data.get("symbol", "RELIANCE.NS" if market != "US" else "TSLA")
            budget = float(data.get("budget", 100000 if market != "US" else 5000))
            max_loss = float(data.get("max_loss", 1000 if market != "US" else 50))
            currency = "INR" if market in ["NSE", "BSE"] else "USD"

            quote = fetch_live_quote(symbol)
            current_price = quote["price"] if quote else (1192.60 if "RELIANCE" in symbol else 2525.0)

            # Calculate 15-minute Opening Range & ORB Plan
            or_low = round(current_price * 0.993, 2)
            or_high = round(current_price * 1.004, 2)
            entry = round(or_high * 1.002, 2)
            stop = round((or_low + or_high) / 2.0, 2)
            risk_per_share = round(entry - stop, 2)
            if risk_per_share <= 0:
                risk_per_share = round(entry * 0.008, 2)
                stop = round(entry - risk_per_share, 2)
            target = round(entry + risk_per_share, 2)

            # Plan dict
            plan = {
                "created_by": "trader-exit",
                "symbol": symbol,
                "currency": currency,
                "exchange": market,
                "side": "buy",
                "date": datetime.date.today().strftime("%Y-%m-%d"),
                "setup": "orb",
                "entry": entry,
                "entry_time": "09:45 AM IST" if market in ["NSE", "BSE"] else "10:15 AM ET",
                "interval": "5m",
                "opening_range": {"high": or_high, "low": or_low, "minutes": 15},
                "stop": stop,
                "stop_rule": "or_mid",
                "stop_label": "opening-range midpoint",
                "target": target,
                "target_r": 1.0,
                "target_label": "1R (one times the risk)",
                "risk_per_share": risk_per_share,
                "reward_risk": 1.0,
                "flat_by": "15:20 IST" if market in ["NSE", "BSE"] else "15:55 ET"
            }
            write_json_file(TRADER_DIR / "plan.json", plan)

            # Update Config
            cfg = read_json_file(TRADER_DIR / "config.json") or {}
            cfg["currency"] = currency
            cfg["market"] = market
            cfg["budget"] = budget
            cfg["max_loss_per_trade_usd"] = max_loss
            cfg["max_order_usd"] = budget * 0.8
            cfg["flat_by"] = "15:20" if market in ["NSE", "BSE"] else "15:55"
            cfg["broker"] = "zerodha_kite_paper" if market in ["NSE", "BSE"] else "alpaca_paper"
            write_json_file(TRADER_DIR / "config.json", cfg)

            # Sizing calculation
            qty = max(1, math.floor(max_loss / risk_per_share))
            # Cap at budget
            if (qty * entry) > budget:
                qty = max(1, math.floor((budget * 0.9) / entry))
            actual_loss = round(qty * risk_per_share, 2)

            orders = {
                "created_by": "trader-budget",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "status": "proposed",
                "paper": True,
                "market": market,
                "currency": currency,
                "orders": [
                    {
                        "symbol": symbol,
                        "side": "buy",
                        "qty": str(qty),
                        "type": "market",
                        "time_in_force": "day",
                        "order_class": "bracket",
                        "take_profit_limit_price": str(target),
                        "stop_loss_stop_price": str(stop),
                        "currency": currency,
                        "exchange": market,
                        "why": f"Opening-range breakout on {symbol} ({market}): {qty} shares, bound by your {currency} {max_loss} max loss ({qty} x {risk_per_share} = {currency} {actual_loss} at stop)",
                        "plan": plan
                    }
                ]
            }
            write_json_file(TRADER_DIR / "orders.json", orders)

            self.send_json({
                "status": "ok",
                "market": market,
                "currency": currency,
                "plan": plan,
                "orders": orders,
                "quote": quote
            })

        elif path == "/api/recalculate":
            # Recalculate sizing for the current plan
            plan = read_json_file(TRADER_DIR / "plan.json") or {}
            cfg = read_json_file(TRADER_DIR / "config.json") or {}

            budget = float(data.get("budget", cfg.get("budget", 100000)))
            max_loss = float(data.get("max_loss", cfg.get("max_loss_per_trade_usd", 1000)))
            currency = cfg.get("currency", "INR")
            market = cfg.get("market", "NSE")

            symbol = plan.get("symbol", "RELIANCE.NS")
            entry = float(plan.get("entry", 1195.0))
            stop = float(plan.get("stop", 1185.0))
            risk_per_share = max(0.05, float(plan.get("risk_per_share", entry - stop)))

            qty = max(1, math.floor(max_loss / risk_per_share))
            if (qty * entry) > budget:
                qty = max(1, math.floor((budget * 0.9) / entry))
            actual_loss = round(qty * risk_per_share, 2)

            orders = {
                "created_by": "trader-budget",
                "created_at": datetime.datetime.utcnow().isoformat() + "Z",
                "status": "proposed",
                "paper": True,
                "market": market,
                "currency": currency,
                "orders": [
                    {
                        "symbol": symbol,
                        "side": plan.get("side", "buy"),
                        "qty": str(qty),
                        "type": "market",
                        "time_in_force": "day",
                        "order_class": "bracket",
                        "take_profit_limit_price": str(plan.get("target", round(entry + risk_per_share, 2))),
                        "stop_loss_stop_price": str(stop),
                        "currency": currency,
                        "exchange": market,
                        "why": f"Sized {qty} shares of {symbol} for {currency} {actual_loss} risk at stop.",
                        "plan": plan
                    }
                ]
            }
            write_json_file(TRADER_DIR / "orders.json", orders)
            self.send_json({
                "status": "ok",
                "orders": orders,
                "size_output": f"[PASS] Position resized: {qty} shares of {symbol} ({currency} {qty * entry:,.2f}), max loss {currency} {actual_loss:,.2f}"
            })

        elif path == "/api/telegram_test":
            from pathlib import Path
            telegram_script = BASE_DIR / "trader-alerts" / "telegram_bot.py"
            bot_token = data.get("bot_token")
            chat_id = data.get("chat_id")
            
            # Save if provided
            if bot_token and chat_id:
                cfg = read_json_file(TRADER_DIR / "config.json") or {}
                cfg["telegram_bot_token"] = bot_token
                cfg["telegram_chat_id"] = chat_id
                write_json_file(TRADER_DIR / "config.json", cfg)
            
            # Try to send
            plan = read_json_file(TRADER_DIR / "plan.json") or {}
            orders = read_json_file(TRADER_DIR / "orders.json") or {}
            first_ord = orders.get("orders", [{}])[0] if orders.get("orders") else {}
            
            import importlib.util
            spec = importlib.util.spec_from_file_location("telegram_bot", str(telegram_script))
            tg = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(tg)
            
            ok, msg = tg.send_trade_alert({
                "symbol": plan.get("symbol", "RELIANCE.NS"),
                "side": plan.get("side", "BUY"),
                "qty": first_ord.get("qty", "74"),
                "entry": plan.get("entry", 1200.87),
                "stop": plan.get("stop", 1191.90),
                "target": plan.get("target", 1209.84),
                "max_loss": (plan.get("entry", 1200.87) - plan.get("stop", 1191.90)) * float(first_ord.get("qty", 74)),
                "currency": plan.get("currency", "INR"),
                "exchange": plan.get("exchange", "NSE")
            })
            
            self.send_json({"status": "ok" if ok else "error", "message": msg})

        else:
            self.send_error(404, "Unknown POST route")

def run(port=PORT):
    server_address = ("", port)
    httpd = http.server.HTTPServer(server_address, DashboardHandler)
    print(f"Day Trader Skill Dashboard Server running on http://localhost:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    p = PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run(p)
