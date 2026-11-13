#!/usr/bin/env python3
"""
Production-Quality Hardened HTTP & REST API Server for Algo Trading Terminal.
Implements Institutional Security Standard (Phase 2):
- Constant-time authentication via hmac.compare_digest
- Secure HttpOnly & SameSite session cookie management
- Anti-CSRF token verification on authenticated state-changing requests
- IP-based login rate limiting to mitigate brute-force attacks
- Configurable ALLOWED_HOSTS & ALLOWED_ORIGINS allowlist validation
- Full server-side authentication guard across all financial GET and POST endpoints
- Standard HTTP security headers (CSP, X-Frame-Options, X-Content-Type-Options, etc.)
- Default network binding to 127.0.0.1 (local only; requires explicit configuration for reverse proxy)
- Zero secret exposure in responses or application startup logs
"""

import http.server
import json
import os
import sys
import subprocess
import urllib.parse
import hmac
import time
import secrets
import threading
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DASHBOARD_DIR = BASE_DIR / "dashboard"
TRADER_DIR = BASE_DIR / "trader"

sys.path.insert(0, str(DASHBOARD_DIR))
sys.path.insert(0, str(TRADER_DIR))

# Load environment variables from .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except Exception:
    pass

import service

PORT = int(os.environ.get("DASHBOARD_PORT", 10080))

# --------------------------------------------------------------------------
# 1. SECURITY CONFIGURATION & TOKEN INITIALIZATION
# --------------------------------------------------------------------------
# Configured or cryptographically secure random token (32 bytes / 64 hex chars)
DASHBOARD_API_TOKEN = os.environ.get("DASHBOARD_API_TOKEN") or secrets.token_hex(32)

# Inform operators without printing secret credentials in logs
if "DASHBOARD_API_TOKEN" not in os.environ:
    print("[SECURITY] Dashboard initialized with generated session token (Protected from logs).")
else:
    print("[SECURITY] Dashboard initialized with configured environment token.")

# Configurable host allowlist (default: loopback addresses)
ALLOWED_HOSTS_RAW = os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")
ALLOWED_HOSTS = {h.strip().lower() for h in ALLOWED_HOSTS_RAW.split(",") if h.strip()}

# Trusted proxies allowlist for forwarded headers
TRUSTED_PROXIES_RAW = os.environ.get("TRUSTED_PROXIES", "127.0.0.1,::1")
TRUSTED_PROXIES = {p.strip().lower() for p in TRUSTED_PROXIES_RAW.split(",") if p.strip()}

# Thread safety lock for in-memory session and rate limit management
_security_lock = threading.Lock()

# Session store: {session_id: {"created_at": float, "expires_at": float, "csrf_token": str, "ip": str}}
SESSION_STORE = {}
SESSION_TTL_SECONDS = int(os.environ.get("SESSION_TTL_SECONDS", 12 * 3600))  # 12 hours default
MAX_SESSION_ENTRIES = 5000

# Rate limiting for login: {ip: [timestamp1, timestamp2, ...]}
LOGIN_ATTEMPTS = {}
RATE_LIMIT_WINDOW = 60  # 60 seconds
RATE_LIMIT_MAX_ATTEMPTS = 5
MAX_RATE_LIMIT_ENTRIES = 5000

def clean_expired_sessions():
    """Prunes expired sessions from memory in a thread-safe manner."""
    with _security_lock:
        now = time.time()
        expired = [sid for sid, sdata in SESSION_STORE.items() if sdata.get("expires_at", 0) <= now]
        for sid in expired:
            SESSION_STORE.pop(sid, None)

        # Enforce memory safety cap
        if len(SESSION_STORE) > MAX_SESSION_ENTRIES:
            sorted_sids = sorted(SESSION_STORE.keys(), key=lambda k: SESSION_STORE[k].get("created_at", 0))
            to_remove = sorted_sids[: len(SESSION_STORE) - MAX_SESSION_ENTRIES]
            for sid in to_remove:
                SESSION_STORE.pop(sid, None)

# --------------------------------------------------------------------------
# 2. HTTP REQUEST HANDLER
# --------------------------------------------------------------------------
class PremiumDashboardHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD_DIR), **kwargs)

    def get_client_ip(self):
        """
        Determines the authentic client IP address.
        Only trusts X-Forwarded-For or X-Real-IP if the direct socket peer (self.client_address[0])
        is explicitly in TRUSTED_PROXIES.
        Arbitrary forwarding headers from untrusted direct peers are strictly ignored.
        """
        peer_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"
        if peer_ip in TRUSTED_PROXIES:
            # 1. Prioritize X-Real-IP if set by trusted reverse proxy (e.g. Nginx proxy_set_header X-Real-IP $remote_addr;)
            real_ip = self.headers.get("X-Real-IP")
            if real_ip and real_ip.strip():
                return real_ip.strip()

            # 2. Inspect X-Forwarded-For: return the originating client IP (first element)
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                parts = [p.strip() for p in forwarded.split(",") if p.strip()]
                if parts:
                    return parts[0]
        return peer_ip

    def validate_host_and_origin(self):
        """
        Validates Host, X-Forwarded-Host, and Origin headers against strict configurable ALLOWED_HOSTS allowlist.
        Rejects unrecognized hosts/origins with 403 Forbidden.
        """
        peer_ip = self.client_address[0] if hasattr(self, "client_address") and self.client_address else "127.0.0.1"

        host_header = self.headers.get("Host", "").split(":")[0].strip().lower()
        if not host_header:
            self.send_error(400, "Bad Request: Missing Host header")
            return False

        if "*" not in ALLOWED_HOSTS and host_header not in ALLOWED_HOSTS:
            self.send_error(403, f"Forbidden Host: Host '{host_header}' is not permitted")
            return False

        # If X-Forwarded-Host is present and request is from a trusted proxy, validate it as well
        if peer_ip in TRUSTED_PROXIES:
            fwd_host = self.headers.get("X-Forwarded-Host")
            if fwd_host:
                clean_fwd_host = fwd_host.split(",")[0].split(":")[0].strip().lower()
                if "*" not in ALLOWED_HOSTS and clean_fwd_host not in ALLOWED_HOSTS:
                    self.send_error(403, f"Forbidden Host: Forwarded Host '{clean_fwd_host}' is not permitted")
                    return False

        origin = self.headers.get("Origin")
        if origin:
            parsed = urllib.parse.urlparse(origin)
            origin_host = (parsed.hostname or "").strip().lower()
            if "*" not in ALLOWED_HOSTS and origin_host not in ALLOWED_HOSTS:
                self.send_error(403, f"Forbidden Origin: Origin '{origin}' is not permitted")
                return False

        return True

    def parse_cookies(self):
        """Parses HTTP Cookie header into key-value dictionary."""
        cookie_header = self.headers.get("Cookie", "")
        cookies = {}
        for item in cookie_header.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    def check_rate_limit(self, client_ip):
        """Enforces sliding-window rate limit for login attempts (thread-safe)."""
        with _security_lock:
            now = time.time()
            attempts = LOGIN_ATTEMPTS.get(client_ip, [])
            valid_attempts = [t for t in attempts if t > (now - RATE_LIMIT_WINDOW)]
            LOGIN_ATTEMPTS[client_ip] = valid_attempts

            if len(valid_attempts) >= RATE_LIMIT_MAX_ATTEMPTS:
                return False

            # Memory safety: prune inactive IPs if store grows large
            if len(LOGIN_ATTEMPTS) > MAX_RATE_LIMIT_ENTRIES:
                stale_ips = [ip for ip, att in LOGIN_ATTEMPTS.items() if not att or att[-1] <= (now - RATE_LIMIT_WINDOW)]
                for ip in stale_ips:
                    LOGIN_ATTEMPTS.pop(ip, None)

            return True

    def record_login_attempt(self, client_ip):
        """Records a failed login attempt for rate limiting (thread-safe)."""
        with _security_lock:
            now = time.time()
            if client_ip not in LOGIN_ATTEMPTS:
                LOGIN_ATTEMPTS[client_ip] = []
            LOGIN_ATTEMPTS[client_ip].append(now)

    def is_authenticated(self):
        """
        Validates request authentication. Accepts:
        1. Valid session_id cookie present in SESSION_STORE and unexpired (browser sessions).
        2. Valid X-API-Token or Authorization: Bearer token matched via constant-time comparison (machine clients).

        Security Invariants:
        - Cookie-authenticated browser sessions are identified by the presence of session_id cookie.
        - Machine clients authenticate directly via API token headers.
        - If an explicit API token header is provided, it must match DASHBOARD_API_TOKEN.
        - Invalid explicit tokens are rejected immediately.
        - Browser sessions presenting session cookies cannot bypass CSRF by mixing tokens.
        """
        clean_expired_sessions()
        cookies = self.parse_cookies()
        session_id = cookies.get("session_id")

        # Extract explicit token header if present
        token = self.headers.get("X-API-Token")
        if not token:
            auth = self.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()

        # If a browser session cookie is present, validate session store
        if session_id:
            with _security_lock:
                sess = SESSION_STORE.get(session_id)
                if sess and time.time() < sess.get("expires_at", 0):
                    # Request is tied to a browser session; CSRF verification is mandatory for state changes
                    return True, {
                        "type": "cookie",
                        "session_id": session_id,
                        "csrf_token": sess.get("csrf_token"),
                        "created_at": sess.get("created_at"),
                        "expires_at": sess.get("expires_at")
                    }
                elif sess:
                    SESSION_STORE.pop(session_id, None)

        # Machine-to-machine authentication (no session cookie)
        if token:
            if hmac.compare_digest(token.encode("utf-8"), DASHBOARD_API_TOKEN.encode("utf-8")):
                return True, {"type": "token", "csrf_token": None}
            else:
                # Explicit token provided but invalid -> reject immediately
                return False, None

        return False, None

    def validate_api_token(self):
        """Backward-compatible helper for legacy tests and API calls."""
        auth_ok, _ = self.is_authenticated()
        if not auth_ok:
            self.send_json({"error": "Unauthorized: Invalid or missing authentication credentials"}, status=401)
            return False
        return True

    def validate_csrf(self, session_data):
        """
        Validates anti-CSRF token on authenticated session requests.
        Token-authenticated requests (Bearer/X-API-Token) bypass CSRF checks.
        Cookie-authenticated browser requests strictly require a matching X-CSRF-Token header.
        """
        if not session_data or session_data.get("type") == "token":
            return True  # Token authentication is not vulnerable to browser CSRF

        expected_csrf = session_data.get("csrf_token")
        if not expected_csrf:
            return False

        provided_csrf = self.headers.get("X-CSRF-Token", "")
        if not provided_csrf or not hmac.compare_digest(provided_csrf.encode("utf-8"), expected_csrf.encode("utf-8")):
            return False

        return True

    def send_json(self, data, status=200, extra_headers=None):
        """Serializes and sends response with full institutional security headers."""
        response_bytes = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response_bytes)))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token, X-CSRF-Token, Authorization")
        
        # Standard Institutional Security Headers
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self';"
        )

        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)

        self.end_headers()
        self.wfile.write(response_bytes)

    def end_headers(self):
        """Injects security headers into static file responses."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        super().end_headers()

    def do_OPTIONS(self):
        if not self.validate_host_and_origin():
            return
        self.send_response(200)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Token, X-CSRF-Token, Authorization")
        self.end_headers()

    # ----------------------------------------------------------------------
    # GET DISPATCHER
    # ----------------------------------------------------------------------
    def do_GET(self):
        if not self.validate_host_and_origin():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 1. Unauthenticated Public Health Endpoint (Minimal status only)
        if path == "/health":
            self.send_json({
                "status": "HEALTHY",
                "paper_trading": True,
                "timestamp": service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
            })
            return

        # 2. Public Session Status Probe
        if path == "/api/auth/session":
            auth_ok, sess = self.is_authenticated()
            if auth_ok and sess:
                self.send_json({
                    "authenticated": True,
                    "csrf_token": sess.get("csrf_token")
                })
            else:
                self.send_json({"authenticated": False}, status=401)
            return

        # 3. Protected Financial & Operational API Endpoints
        if path.startswith("/api/"):
            auth_ok, sess = self.is_authenticated()
            if not auth_ok:
                self.send_json({"error": "Unauthorized: Session invalid or authentication required"}, status=401)
                return

            try:
                self.handle_api_get(path, query)
            except Exception as e:
                self.send_json({"error": f"Internal server error: {str(e)}"}, status=500)
            return

        # 4. Static Frontend Assets (Index, CSS, JS, Images)
        if path == "/" or not path or path == "/index.html":
            self.path = "/index.html"
        super().do_GET()

    # ----------------------------------------------------------------------
    # POST DISPATCHER
    # ----------------------------------------------------------------------
    def do_POST(self):
        if not self.validate_host_and_origin():
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        client_ip = self.get_client_ip()

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            data = json.loads(body) if body else {}
        except Exception:
            data = {}

        # 1. Public Authentication Login Endpoint
        if path == "/api/auth/login":
            if not self.check_rate_limit(client_ip):
                self.send_json(
                    {"status": "ERROR", "message": "Too many failed login attempts. Please wait 60 seconds."},
                    status=429
                )
                return

            provided_token = str(data.get("token") or data.get("password") or "").strip()
            if not provided_token or not hmac.compare_digest(provided_token.encode("utf-8"), DASHBOARD_API_TOKEN.encode("utf-8")):
                self.record_login_attempt(client_ip)
                service.log_audit_event("AUTH_FAILED", {"ip": client_ip}, status="FAILED")
                self.send_json({"status": "ERROR", "message": "Invalid authentication credentials."}, status=401)
                return

            # Session Fixation Prevention: Invalidate prior session if present in cookie
            cookies = self.parse_cookies()
            old_sid = cookies.get("session_id")
            if old_sid:
                with _security_lock:
                    SESSION_STORE.pop(old_sid, None)

            # Successful Authentication: Create Secure Session
            with _security_lock:
                LOGIN_ATTEMPTS.pop(client_ip, None)
                session_id = secrets.token_hex(32)
                csrf_token = secrets.token_hex(16)
                now = time.time()
                expires_at = now + SESSION_TTL_SECONDS

                SESSION_STORE[session_id] = {
                    "type": "cookie",
                    "created_at": now,
                    "expires_at": expires_at,
                    "csrf_token": csrf_token,
                    "ip": client_ip
                }

            service.log_audit_event("AUTH_SUCCESS", {"ip": client_ip})

            is_https = (
                self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or os.environ.get("DASHBOARD_SECURE_COOKIE", "").lower() in ("1", "true")
                or os.environ.get("HTTPS", "").lower() in ("1", "true", "on")
            )
            secure_flag = "; Secure" if is_https else ""
            cookie_val = f"session_id={session_id}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL_SECONDS}{secure_flag}"

            self.send_json(
                {
                    "status": "SUCCESS",
                    "message": "Authenticated successfully",
                    "csrf_token": csrf_token,
                    "expires_in": SESSION_TTL_SECONDS
                },
                extra_headers={"Set-Cookie": cookie_val}
            )
            return

        # 2. Guarded State-Changing Operational APIs & Authenticated Logout
        auth_ok, sess = self.is_authenticated()
        if not auth_ok:
            self.send_json({"error": "Unauthorized: Authentication required"}, status=401)
            return

        if not self.validate_csrf(sess):
            self.send_json({"error": "Forbidden: CSRF validation failed or token missing"}, status=403)
            return

        # Authenticated & CSRF-Validated Logout Endpoint
        if path == "/api/auth/logout":
            cookies = self.parse_cookies()
            sid = cookies.get("session_id")
            if sid:
                with _security_lock:
                    SESSION_STORE.pop(sid, None)

            is_https = (
                self.headers.get("X-Forwarded-Proto", "").lower() == "https"
                or os.environ.get("DASHBOARD_SECURE_COOKIE", "").lower() in ("1", "true")
                or os.environ.get("HTTPS", "").lower() in ("1", "true", "on")
            )
            secure_flag = "; Secure" if is_https else ""
            expired_cookie = f"session_id=; Path=/; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0; HttpOnly; SameSite=Lax{secure_flag}"
            self.send_json(
                {"status": "SUCCESS", "message": "Logged out successfully"},
                extra_headers={"Set-Cookie": expired_cookie}
            )
            return

        if path.startswith("/api/"):
            try:
                self.handle_api_post(path, data)
            except Exception as e:
                self.send_json({"error": f"Execution error: {str(e)}"}, status=500)
        else:
            self.send_error(404, "Not Found")

    # ----------------------------------------------------------------------
    # 3. PROTECTED API BUSINESS LOGIC
    # ----------------------------------------------------------------------
    def handle_api_get(self, path, query):
        account = query.get("account", ["gautam"])[0].lower()
        accounts_data = service.read_json_file(TRADER_DIR / "accounts.json", default={"accounts": {}})
        acc_meta = accounts_data.get("accounts", {}).get(account, {})
        ledger_filename = acc_meta.get("ledger_file", f"ledger_{account}.json" if account != "gautam" else "ledger.json")

        if path in ("/api/status", "/api/overview", "/api/dashboard"):
            kpis = service.compute_dashboard_metrics(account_id=account)
            daemon_info = service.check_daemon_status()
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            market_status = service.is_market_open()
            ist_time = service.get_ist_now().strftime("%Y-%m-%d %H:%M:%S IST")

            # CRITICAL SECURITY FIX (Finding F-01): NEVER serialize api_token into response!
            self.send_json({
                "account": account,
                "accounts": accounts_data.get("accounts", {}),
                "market_status": market_status,
                "market_open": (market_status == "OPEN"),
                "trading_mode": "PAPER" if config.get("paper", True) else "LIVE",
                "ist_time": ist_time,
                "bot_status": daemon_info,
                "kpis": kpis,
                "config": service.sanitize_config(config)
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
            date_param = query.get("date", ["ALL"])[0]
            res_data = service.get_equity_curve_data(account_id=account, date_filter=date_param)
            res_data["account"] = account
            self.send_json(res_data)

        elif path == "/api/watchlist":
            symbols = acc_meta.get("watchlist", [])
            if not symbols:
                config = service.read_json_file(TRADER_DIR / "config.json", default={})
                symbols = config.get("watchlist", ["JPL", "TCS", "RELIANCE", "INFY", "HDFCBANK"])
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
            
            symbol_filter = query.get("symbol", [None])[0]
            outcome_filter = query.get("outcome", [None])[0]

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
            stats = service.get_strategy_performance(account_id=account)
            self.send_json({"strategies": stats})

        elif path == "/api/fno":
            fno_data = service.get_fno_desk_data()
            self.send_json(fno_data)

        elif path == "/api/risk":
            risk_data = service.get_risk_audit(account_id=account)
            self.send_json(risk_data)

        elif path == "/api/logs":
            lvl = query.get("level", [None])[0]
            logs = service.get_system_logs(filter_level=lvl)
            self.send_json({"logs": logs})

        elif path == "/api/config":
            config = service.read_json_file(TRADER_DIR / "config.json", default={})
            self.send_json({"config": service.sanitize_config(config)})

        elif path == "/api/signals":
            import db_v3
            conn = db_v3.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT * FROM signals_audit ORDER BY id DESC LIMIT 50")
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            self.send_json({"signals": rows})

        elif path == "/api/backtest":
            self.send_json(
                {"error": "Method Not Allowed: Backtests must be initiated via POST"},
                status=405,
                extra_headers={"Allow": "POST"}
            )

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

        elif path == "/api/watchlist/add":
            sym = data.get("symbol")
            acc = data.get("account", "gautam")
            res = service.add_symbol_to_watchlist(sym, acc)
            self.send_json(res)

        elif path == "/api/watchlist/remove":
            sym = data.get("symbol")
            acc = data.get("account", "gautam")
            res = service.remove_symbol_from_watchlist(sym, acc)
            self.send_json(res)

        elif path == "/api/accounts/reconcile":
            target_acc = data.get("account", "gautam")
            res = service.reconcile_account_ledger(target_acc)
            self.send_json(res)

        elif path == "/api/risk/emergency_pause":
            status = service.check_daemon_status()
            pid = status.get("pid")
            if pid:
                try:
                    if os.name == "nt":
                        subprocess.call(["taskkill", "/F", "/PID", str(pid)])
                    else:
                        os.kill(int(pid), 9)
                    service.daemon_process = None
                except Exception:
                    pass
            service.log_audit_event("EMERGENCY_PAUSE_TRIGGERED", {"reason": "Manual operator emergency pause"})
            self.send_json({"status": "SUCCESS", "message": "Emergency pause triggered. Bot processes halted."})

        elif path == "/api/backtest":
            from backtest import BacktestEngine
            import daemon
            symbol = data.get("symbol", "RELIANCE.NS").upper()
            strat = data.get("strategy", "CONFLUENCE").upper()
            quotes = daemon.fetch_candles(symbol)
            if quotes and len(quotes.get("close", [])) >= 40:
                bt = BacktestEngine(starting_capital=100000.0)
                res = bt.run_strategy(quotes, strategy_name=strat)
                self.send_json({"symbol": symbol, "result": res})
            else:
                self.send_json({"error": "Insufficient candle data for backtest"}, status=400)

        elif path == "/api/risk/emergency_squareoff":
            target_acc = data.get("account", "gautam")
            kpis = service.compute_dashboard_metrics(account_id=target_acc)
            closed_count = 0
            import ledger
            for pos in kpis.get("enriched_positions", []):
                sym = pos.get("symbol")
                cur_p = pos.get("current_price", 0.0)
                if sym and cur_p > 0:
                    ok, _ = ledger.record_trade_exit(sym, cur_p, "EMERGENCY_SQUARE_OFF")
                    if ok:
                        closed_count += 1
            service.log_audit_event("EMERGENCY_SQUAREOFF_TRIGGERED", {"account": target_acc, "closed_positions": closed_count})
            self.send_json({"status": "SUCCESS", "message": f"Emergency square-off executed. {closed_count} positions closed."})

        else:
            self.send_error(404, "API endpoint not found")

class ReusableHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True

def run(port=PORT):
    # Default to 127.0.0.1 (local only) per Task 7 to prevent unconfigured public exposure
    host = os.environ.get("DASHBOARD_BIND_HOST", os.environ.get("DASHBOARD_HOST", "127.0.0.1"))
    server_address = (host, port)
    httpd = ReusableHTTPServer(server_address, PremiumDashboardHandler)
    print(f"Institutional Algo Trading Terminal running securely on http://{host}:{port}")
    httpd.serve_forever()

if __name__ == "__main__":
    p = PORT
    if len(sys.argv) > 1:
        try:
            p = int(sys.argv[1])
        except ValueError:
            pass
    run(p)
