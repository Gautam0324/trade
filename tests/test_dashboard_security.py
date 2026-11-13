#!/usr/bin/env python3
"""
Comprehensive Security & Penetration Unit Tests for Day Trader Pro Dashboard Server (Phase 2).
Covers:
1. Dashboard GET responses never expose API tokens.
2. Unauthenticated financial GET requests return 401.
3. Unauthorized POST requests return 401 or 403.
4. Invalid login credentials are rejected (401).
5. Valid login creates a secure session with HttpOnly cookie.
6. Expired sessions are rejected.
7. Logout invalidates the session.
8. CSRF validation works on state-changing requests.
9. Untrusted origins are rejected (403).
10. Unknown Host headers are rejected (403).
11. Sensitive configuration values are never serialized (sanitize_config).
12. Frontend does not retrieve secrets from API responses.
13. Login rate limiting blocks brute-force attempts (429).
"""

import unittest
import json
import io
import time
import re
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "dashboard"))
sys.path.insert(0, str(BASE_DIR / "trader"))

import server
import service

class MockSocket:
    def __init__(self):
        self.data = b""

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return io.BytesIO(self.data)
        elif "w" in mode or "b" in mode:
            return io.BytesIO()
        return io.BytesIO()

class DummyHandler(server.PremiumDashboardHandler):
    """Subclass that captures HTTP responses for in-memory unit testing."""
    def __init__(self, method="GET", path="/api/dashboard", headers=None, body=""):
        self.command = method
        self.path = path
        self.headers = headers or {"Host": "localhost:10080"}
        self.client_address = ("127.0.0.1", 54321)
        self.rfile = io.BytesIO(body.encode("utf-8") if isinstance(body, str) else body)
        self.wfile = io.BytesIO()
        self._sent_status = None
        self._sent_headers = {}
        self._response_body = None

    def send_response(self, code, message=None):
        self._sent_status = code

    def send_header(self, keyword, value):
        self._sent_headers[keyword.lower()] = value

    def end_headers(self):
        pass

    def send_error(self, code, message=None, explain=None):
        self._sent_status = code
        self._response_body = json.dumps({"error": message})

    def write_response(self, data):
        self._response_body = data

class TestDashboardSecurity(unittest.TestCase):
    def setUp(self):
        server.SESSION_STORE.clear()
        server.LOGIN_ATTEMPTS.clear()
        server.ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
        self.test_token = server.DASHBOARD_API_TOKEN

    def test_1_dashboard_get_responses_never_expose_api_token(self):
        """Test that /api/dashboard and /api/status never contain api_token in JSON."""
        # Authenticate via valid token header
        handler = DummyHandler(
            method="GET",
            path="/api/dashboard?account=gautam",
            headers={"Host": "localhost", "X-API-Token": self.test_token}
        )
        handler.do_GET()
        self.assertEqual(handler._sent_status, 200)

        # Inspect written body
        body_str = handler.wfile.getvalue().decode("utf-8")
        parsed = json.loads(body_str)
        self.assertNotIn("api_token", parsed)
        self.assertNotIn(self.test_token, body_str)

    def test_2_unauthenticated_financial_get_requests_return_401(self):
        """Test that unauthenticated requests to financial endpoints return 401."""
        endpoints = [
            "/api/dashboard",
            "/api/overview",
            "/api/status",
            "/api/config",
            "/api/trades",
            "/api/watchlist",
            "/api/positions",
            "/api/equity_curve",
            "/api/risk",
            "/api/fno"
        ]
        for ep in endpoints:
            handler = DummyHandler(
                method="GET",
                path=ep,
                headers={"Host": "localhost"}
            )
            handler.do_GET()
            self.assertEqual(
                handler._sent_status, 401,
                f"Endpoint {ep} allowed unauthenticated access with status {handler._sent_status}"
            )

    def test_3_unauthorized_post_requests_return_401_or_403(self):
        """Test that unauthorized POST requests return 401 (no auth) or 403 (CSRF fail)."""
        handler = DummyHandler(
            method="POST",
            path="/api/positions/close",
            headers={"Host": "localhost", "Content-Length": "15"},
            body='{"symbol":"TCS"}'
        )
        handler.do_POST()
        self.assertEqual(handler._sent_status, 401)

    def test_4_invalid_login_credentials_rejected(self):
        """Test that wrong credentials return 401 Unauthorized."""
        handler = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "Content-Length": "25"},
            body='{"token":"wrong-password"}'
        )
        handler.do_POST()
        self.assertEqual(handler._sent_status, 401)

    def test_5_valid_login_creates_session_with_httponly_cookie(self):
        """Test that valid login generates session in SESSION_STORE and sets HttpOnly cookie."""
        handler = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "Content-Length": str(len(self.test_token) + 15)},
            body=json.dumps({"token": self.test_token})
        )
        handler.do_POST()
        self.assertEqual(handler._sent_status, 200)

        # Verify Set-Cookie header contains HttpOnly and SameSite
        cookie_header = handler._sent_headers.get("set-cookie", "")
        self.assertIn("session_id=", cookie_header)
        self.assertIn("HttpOnly", cookie_header)
        self.assertIn("SameSite=Lax", cookie_header)

        # Verify session is registered in SESSION_STORE
        match = re.search(r"session_id=([a-f0-9]+)", cookie_header)
        self.assertIsNotNone(match)
        session_id = match.group(1)
        self.assertIn(session_id, server.SESSION_STORE)

    def test_6_expired_sessions_are_rejected(self):
        """Test that sessions past expires_at are purged and return 401."""
        expired_sid = "expired_session_12345"
        server.SESSION_STORE[expired_sid] = {
            "created_at": time.time() - 5000,
            "expires_at": time.time() - 100,  # Expired
            "csrf_token": "csrf123",
            "ip": "127.0.0.1"
        }

        handler = DummyHandler(
            method="GET",
            path="/api/dashboard",
            headers={"Host": "localhost", "Cookie": f"session_id={expired_sid}"}
        )
        handler.do_GET()
        self.assertEqual(handler._sent_status, 401)
        self.assertNotIn(expired_sid, server.SESSION_STORE)

    def test_7_logout_invalidates_session(self):
        """Test that logging out removes session and clears session cookie."""
        active_sid = "valid_active_session_abc"
        server.SESSION_STORE[active_sid] = {
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "csrf_token": "csrf123",
            "ip": "127.0.0.1"
        }

        handler = DummyHandler(
            method="POST",
            path="/api/auth/logout",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={active_sid}",
                "X-CSRF-Token": "csrf123",
                "Content-Length": "2"
            },
            body="{}"
        )
        handler.do_POST()
        self.assertEqual(handler._sent_status, 200)
        self.assertNotIn(active_sid, server.SESSION_STORE)

        # Verify expired cookie is sent
        cookie_header = handler._sent_headers.get("set-cookie", "")
        self.assertIn("Expires=Thu, 01 Jan 1970", cookie_header)

    def test_8_csrf_validation_for_state_changing_requests(self):
        """Test that session-authenticated POST requests require matching X-CSRF-Token."""
        active_sid = "session_for_csrf_test"
        correct_csrf = "csrf_token_secret_xyz"
        server.SESSION_STORE[active_sid] = {
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "csrf_token": correct_csrf,
            "ip": "127.0.0.1"
        }

        # Attempt POST without CSRF header -> Must be 403
        handler_no_csrf = DummyHandler(
            method="POST",
            path="/api/watchlist/add",
            headers={"Host": "localhost", "Cookie": f"session_id={active_sid}", "Content-Length": "20"},
            body='{"symbol":"SBIN"}'
        )
        handler_no_csrf.do_POST()
        self.assertEqual(handler_no_csrf._sent_status, 403)

        # Attempt POST with wrong CSRF header -> Must be 403
        handler_wrong_csrf = DummyHandler(
            method="POST",
            path="/api/watchlist/add",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={active_sid}",
                "X-CSRF-Token": "attacker_csrf_attempt",
                "Content-Length": "20"
            },
            body='{"symbol":"SBIN"}'
        )
        handler_wrong_csrf.do_POST()
        self.assertEqual(handler_wrong_csrf._sent_status, 403)

        # Attempt POST with correct CSRF header -> Must be 200
        handler_valid = DummyHandler(
            method="POST",
            path="/api/watchlist/add",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={active_sid}",
                "X-CSRF-Token": correct_csrf,
                "Content-Length": "20"
            },
            body='{"symbol":"SBIN"}'
        )
        handler_valid.do_POST()
        self.assertEqual(handler_valid._sent_status, 200)

    def test_9_untrusted_origins_rejected_with_403(self):
        """Test that Origin headers not in ALLOWED_HOSTS are blocked with 403."""
        handler = DummyHandler(
            method="GET",
            path="/health",
            headers={"Host": "localhost", "Origin": "http://evil-attacker.com"}
        )
        handler.do_GET()
        self.assertEqual(handler._sent_status, 403)

    def test_10_unknown_host_headers_rejected_with_403(self):
        """Test that Host headers not in ALLOWED_HOSTS are blocked with 403."""
        handler = DummyHandler(
            method="GET",
            path="/health",
            headers={"Host": "unknown-host.net"}
        )
        handler.do_GET()
        self.assertEqual(handler._sent_status, 403)

    def test_11_sensitive_config_values_never_serialized(self):
        """Test that sanitize_config masks any keys containing token, secret, password, or key."""
        sensitive_cfg = {
            "budget": 500000.0,
            "smartapi_api_key": "LIVE_API_KEY_12345",
            "smartapi_pin": "9988",
            "smartapi_totp_secret": "JBSWY3DPEHPK3PXP",
            "telegram_bot_token": "987654321:AAH_SECRET_TOKEN",
            "sec_user_agent": "John Doe",
            "custom_secret_key": "supersecret"
        }
        sanitized = service.sanitize_config(sensitive_cfg)
        serialized = json.dumps(sanitized)

        self.assertNotIn("LIVE_API_KEY_12345", serialized)
        self.assertNotIn("9988", serialized)
        self.assertNotIn("JBSWY3DPEHPK3PXP", serialized)
        self.assertNotIn("AAH_SECRET_TOKEN", serialized)
        self.assertNotIn("supersecret", serialized)
        self.assertEqual(sanitized["sec_user_agent"], "Configured (Protected)")

    def test_12_frontend_does_not_parse_secret_tokens(self):
        """Test that app.js does not contain references to data.api_token assignment or localStorage token."""
        app_js_path = BASE_DIR / "dashboard" / "app.js"
        with open(app_js_path, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertNotIn("data.api_token", content)
        self.assertNotIn("localStorage.setItem('api_token'", content)
        self.assertNotIn("sessionStorage.setItem('api_token'", content)

    def test_13_login_rate_limiting_triggers_429(self):
        """Test that brute force attempts (>5 attempts in 60s) trigger 429 Too Many Requests."""
        for attempt in range(5):
            h = DummyHandler(
                method="POST",
                path="/api/auth/login",
                headers={"Host": "localhost", "Content-Length": "17"},
                body='{"token":"wrong"}'
            )
            h.do_POST()
            self.assertEqual(h._sent_status, 401)

        # 6th attempt from the same client IP must be 429
        h_blocked = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "Content-Length": "17"},
            body='{"token":"wrong"}'
        )
        h_blocked.do_POST()
        self.assertEqual(h_blocked._sent_status, 429)

    def test_14_logout_csrf_enforcement(self):
        """Test that cookie-authenticated logout requires valid CSRF and rejects forged requests."""
        sid = "logout_csrf_session"
        valid_csrf = "csrf_token_secret_123"
        server.SESSION_STORE[sid] = {
            "type": "cookie",
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "csrf_token": valid_csrf,
            "ip": "127.0.0.1"
        }

        # 1. Missing CSRF -> 403 Forbidden
        h_no_csrf = DummyHandler(
            method="POST",
            path="/api/auth/logout",
            headers={"Host": "localhost", "Cookie": f"session_id={sid}", "Content-Length": "2"},
            body="{}"
        )
        h_no_csrf.do_POST()
        self.assertEqual(h_no_csrf._sent_status, 403)
        self.assertIn(sid, server.SESSION_STORE)

        # 2. Invalid CSRF -> 403 Forbidden
        h_bad_csrf = DummyHandler(
            method="POST",
            path="/api/auth/logout",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={sid}",
                "X-CSRF-Token": "forged_csrf_val",
                "Content-Length": "2"
            },
            body="{}"
        )
        h_bad_csrf.do_POST()
        self.assertEqual(h_bad_csrf._sent_status, 403)
        self.assertIn(sid, server.SESSION_STORE)

        # 3. Valid CSRF -> 200 OK and session destroyed
        h_ok = DummyHandler(
            method="POST",
            path="/api/auth/logout",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={sid}",
                "X-CSRF-Token": valid_csrf,
                "Content-Length": "2"
            },
            body="{}"
        )
        h_ok.do_POST()
        self.assertEqual(h_ok._sent_status, 200)
        self.assertNotIn(sid, server.SESSION_STORE)

        # 4. Machine client via API token can logout without cookie or CSRF
        h_machine = DummyHandler(
            method="POST",
            path="/api/auth/logout",
            headers={
                "Host": "localhost",
                "X-API-Token": self.test_token,
                "Content-Length": "2"
            },
            body="{}"
        )
        h_machine.do_POST()
        self.assertEqual(h_machine._sent_status, 200)

    def test_15_secure_cookie_handling_https_vs_dev(self):
        """Test that Set-Cookie adds '; Secure' under HTTPS and omits it under plain HTTP."""
        body_json = json.dumps({"token": self.test_token})
        body_len = str(len(body_json.encode("utf-8")))

        # 1. Plain HTTP (Development)
        h_http = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "Content-Length": body_len},
            body=body_json
        )
        h_http.do_POST()
        self.assertEqual(h_http._sent_status, 200)
        cookie_http = h_http._sent_headers.get("set-cookie", "")
        self.assertIn("HttpOnly", cookie_http)
        self.assertIn("SameSite=Lax", cookie_http)
        self.assertIn("Path=/", cookie_http)
        self.assertNotIn("; Secure", cookie_http)

        # 2. Behind HTTPS reverse proxy (Production)
        h_https = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={
                "Host": "localhost",
                "X-Forwarded-Proto": "https",
                "Content-Length": body_len
            },
            body=body_json
        )
        h_https.do_POST()
        self.assertEqual(h_https._sent_status, 200)
        cookie_https = h_https._sent_headers.get("set-cookie", "")
        self.assertIn("HttpOnly", cookie_https)
        self.assertIn("SameSite=Lax", cookie_https)
        self.assertIn("; Secure", cookie_https)

    def test_16_session_fixation_prevention(self):
        """Test that logging in replaces and invalidates any pre-existing session ID."""
        old_sid = "pre_existing_session_xyz"
        server.SESSION_STORE[old_sid] = {
            "type": "cookie",
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "csrf_token": "old_csrf",
            "ip": "127.0.0.1"
        }

        body_json = json.dumps({"token": self.test_token})
        body_len = str(len(body_json.encode("utf-8")))

        # Client submits login with old session cookie attached
        h = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={old_sid}",
                "Content-Length": body_len
            },
            body=body_json
        )
        h.do_POST()
        self.assertEqual(h._sent_status, 200)

        # Old session ID must be destroyed
        self.assertNotIn(old_sid, server.SESSION_STORE)

        # A brand new session must exist
        cookie = h._sent_headers.get("set-cookie", "")
        self.assertIn("session_id=", cookie)
        new_sid = cookie.split("session_id=")[1].split(";")[0]
        self.assertNotEqual(new_sid, old_sid)
        self.assertIn(new_sid, server.SESSION_STORE)

    def test_17_mixed_cookie_and_token_authentication(self):
        """Test boundary invariants when request contains both cookie and token headers."""
        sid = "mixed_auth_session"
        csrf = "csrf_secret_mixed"
        server.SESSION_STORE[sid] = {
            "type": "cookie",
            "created_at": time.time(),
            "expires_at": time.time() + 3600,
            "csrf_token": csrf,
            "ip": "127.0.0.1"
        }

        # Request has session cookie AND an arbitrary token header:
        # Browser session context must be preserved -> CSRF validation is MANDATORY.
        h_bypass_attempt = DummyHandler(
            method="POST",
            path="/api/watchlist/add",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={sid}",
                "X-API-Token": self.test_token,  # Attempting to bypass CSRF by appending token
                "Content-Length": "20"
            },
            body='{"symbol":"SBIN"}'
        )
        h_bypass_attempt.do_POST()
        # Must be rejected because CSRF token is missing!
        self.assertEqual(h_bypass_attempt._sent_status, 403)

        # Request with session cookie + valid CSRF succeeds
        h_valid = DummyHandler(
            method="POST",
            path="/api/watchlist/add",
            headers={
                "Host": "localhost",
                "Cookie": f"session_id={sid}",
                "X-CSRF-Token": csrf,
                "Content-Length": "20"
            },
            body='{"symbol":"SBIN"}'
        )
        h_valid.do_POST()
        self.assertEqual(h_valid._sent_status, 200)

        # Explicit invalid token without cookie must return 401
        h_bad_token = DummyHandler(
            method="GET",
            path="/api/overview",
            headers={"Host": "localhost", "X-API-Token": "completely_wrong_token"}
        )
        h_bad_token.do_GET()
        self.assertEqual(h_bad_token._sent_status, 401)

        # Case-insensitive Bearer token authentication succeeds
        h_bearer = DummyHandler(
            method="GET",
            path="/api/overview",
            headers={"Host": "localhost", "Authorization": f"bearer {self.test_token}"}
        )
        h_bearer.do_GET()
        self.assertEqual(h_bearer._sent_status, 200)

    def test_18_get_endpoint_side_effects_audit(self):
        """Test that GET endpoints are strictly read-only and have zero side effects on system state."""
        import copy
        config_before = copy.deepcopy(service.read_json_file(server.TRADER_DIR / "config.json", default={}))

        # Execute GET requests across sensitive endpoints
        for endpoint in ["/api/dashboard", "/api/overview", "/api/status", "/api/positions", "/api/trades", "/api/config"]:
            h = DummyHandler(
                method="GET",
                path=endpoint,
                headers={"Host": "localhost", "X-API-Token": self.test_token}
            )
            h.do_GET()
            self.assertEqual(h._sent_status, 200)

        config_after = service.read_json_file(server.TRADER_DIR / "config.json", default={})
        self.assertEqual(config_before, config_after)

    def test_19_backtest_method_restriction(self):
        """Test that /api/backtest rejects GET requests (405) and requires POST."""
        # GET /api/backtest must return 405 Method Not Allowed
        h_get = DummyHandler(
            method="GET",
            path="/api/backtest?symbol=RELIANCE.NS",
            headers={"Host": "localhost", "X-API-Token": self.test_token}
        )
        h_get.do_GET()
        self.assertEqual(h_get._sent_status, 405)
        self.assertEqual(h_get._sent_headers.get("allow"), "POST")

        # POST /api/backtest accepts execution (or returns data validation error, not 405)
        h_post = DummyHandler(
            method="POST",
            path="/api/backtest",
            headers={
                "Host": "localhost",
                "X-API-Token": self.test_token,
                "Content-Length": "28"
            },
            body='{"symbol":"RELIANCE.NS"}'
        )
        h_post.do_POST()
        self.assertIn(h_post._sent_status, (200, 400))  # 200 or 400 (insufficient quotes), NOT 405

    def test_20_trusted_proxy_ip_handling_and_spoofing_prevention(self):
        """Test that forwarded headers are only trusted from peers in TRUSTED_PROXIES."""
        server.TRUSTED_PROXIES = {"127.0.0.1", "::1"}

        # 1. Request from trusted proxy (127.0.0.1) with X-Real-IP
        h_proxy = DummyHandler(
            method="GET",
            path="/health",
            headers={"Host": "localhost", "X-Real-IP": "198.51.100.42"}
        )
        h_proxy.client_address = ("127.0.0.1", 50000)
        self.assertEqual(h_proxy.get_client_ip(), "198.51.100.42")

        # 2. Request from trusted proxy with X-Forwarded-For chain
        h_chain = DummyHandler(
            method="GET",
            path="/health",
            headers={"Host": "localhost", "X-Forwarded-For": "203.0.113.19, 10.0.0.1"}
        )
        h_chain.client_address = ("127.0.0.1", 50000)
        self.assertEqual(h_chain.get_client_ip(), "203.0.113.19")

        # 3. Request from UNTRUSTED direct peer (spoofing attempt)
        h_untrusted = DummyHandler(
            method="GET",
            path="/health",
            headers={"Host": "localhost", "X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"}
        )
        h_untrusted.client_address = ("192.0.2.99", 50000)
        # Must return actual peer IP, completely ignoring spoofed headers
        self.assertEqual(h_untrusted.get_client_ip(), "192.0.2.99")

    def test_21_rate_limiting_behind_nginx(self):
        """Test that rate limiting isolates client IPs correctly behind a reverse proxy."""
        server.TRUSTED_PROXIES = {"127.0.0.1"}
        client_a = "198.51.100.10"
        client_b = "198.51.100.20"

        # Client A exhausts rate limit (5 failed attempts)
        for _ in range(5):
            h_a = DummyHandler(
                method="POST",
                path="/api/auth/login",
                headers={"Host": "localhost", "X-Real-IP": client_a, "Content-Length": "17"},
                body='{"token":"wrong"}'
            )
            h_a.client_address = ("127.0.0.1", 50000)
            h_a.do_POST()
            self.assertEqual(h_a._sent_status, 401)

        # 6th attempt from Client A is rate limited
        h_a_blocked = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "X-Real-IP": client_a, "Content-Length": "17"},
            body='{"token":"wrong"}'
        )
        h_a_blocked.client_address = ("127.0.0.1", 50000)
        h_a_blocked.do_POST()
        self.assertEqual(h_a_blocked._sent_status, 429)

        # Client B behind the same Nginx proxy is NOT blocked
        h_b = DummyHandler(
            method="POST",
            path="/api/auth/login",
            headers={"Host": "localhost", "X-Real-IP": client_b, "Content-Length": "17"},
            body='{"token":"wrong"}'
        )
        h_b.client_address = ("127.0.0.1", 50000)
        h_b.do_POST()
        self.assertEqual(h_b._sent_status, 401)

    def test_22_session_expiry_and_cleanup(self):
        """Test that expired sessions are automatically pruned and cannot access protected endpoints."""
        expired_sid = "expired_session_123"
        server.SESSION_STORE[expired_sid] = {
            "type": "cookie",
            "created_at": time.time() - 7200,
            "expires_at": time.time() - 3600,  # Expired 1 hour ago
            "csrf_token": "expired_csrf",
            "ip": "127.0.0.1"
        }

        # Attempt to access protected endpoint with expired cookie -> 401
        h = DummyHandler(
            method="GET",
            path="/api/dashboard",
            headers={"Host": "localhost", "Cookie": f"session_id={expired_sid}"}
        )
        h.do_GET()
        self.assertEqual(h._sent_status, 401)
        self.assertNotIn(expired_sid, server.SESSION_STORE)

if __name__ == "__main__":
    unittest.main()

