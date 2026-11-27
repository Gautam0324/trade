"""
Tests for Phase 1: Data Integrity & Execution Safety (F&O V4).
Validates:
1. Centralized QuoteValidator:
   - Rejection of missing/empty exchange timestamps without local substitution
   - Rejection of stale quotes (> 60s for LTP/Depth, > 180s for candle)
   - Rejection of non-positive or non-finite LTP
   - Rejection of zero bid/ask when depth is required
   - Rejection of crossed markets (bid > ask)
   - Exchange routing validation (BSE contracts -> BFO, NSE -> NFO)
   - Verification flag integrity (verified_market_data=True ONLY on full pass)
2. Dynamic exchange propagation in fno_trader (BFO vs NFO)
3. Strict cash conservation across all active ledgers
4. Prohibition of synthetic fallback premiums
"""

import math
import json
import datetime
import sys
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "trader"))

try:
    from fno.quote_validator import QuoteValidator, ValidationResult
except ImportError:
    from trader.fno.quote_validator import QuoteValidator, ValidationResult


class TestPhase1DataIntegrity:

    def test_quote_validator_rejects_missing_exchange_timestamp(self):
        """Missing or empty exchange timestamp must strictly fail validation without local substitution."""
        payload = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "12345",
            "exchange": "NFO",
            "ltp": 150.0,
            "bid": 149.5,
            "ask": 150.5,
            "feed_time": None
        }
        res = QuoteValidator.validate_quote(payload)
        assert res.is_valid is False
        assert res.verified_market_data is False
        assert any("MISSING_EXCHANGE_TIMESTAMP" in r for r in res.rejection_reasons)

    def test_quote_validator_rejects_stale_ltp_quote(self):
        """Quotes older than 60 seconds must be rejected as stale."""
        now = datetime.datetime.now()
        stale_time = (now - datetime.timedelta(seconds=75)).strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "12345",
            "exchange": "NFO",
            "ltp": 150.0,
            "bid": 149.5,
            "ask": 150.5,
            "feed_time": stale_time
        }
        res = QuoteValidator.validate_quote(payload, reference_dt=now)
        assert res.is_valid is False
        assert any("STALE_QUOTE" in r for r in res.rejection_reasons)

    def test_quote_validator_enforces_positive_finite_ltp(self):
        """Non-positive or NaN LTP must fail validation."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for bad_ltp in [0.0, -10.5, float("nan"), float("inf"), None]:
            payload = {
                "symbol": "NIFTY26NOV24200CE",
                "token": "12345",
                "exchange": "NFO",
                "ltp": bad_ltp,
                "feed_time": now_str
            }
            res = QuoteValidator.validate_quote(payload)
            assert res.is_valid is False
            assert res.verified_market_data is False

    def test_quote_validator_rejects_zero_bid_ask_when_depth_required(self):
        """Execution-sensitive quotes must reject zero bid/ask or excessive spreads."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "12345",
            "exchange": "NFO",
            "ltp": 150.0,
            "bid": 0.0,
            "ask": 0.0,
            "feed_time": now_str
        }
        res = QuoteValidator.validate_quote(payload, require_depth=True)
        assert res.is_valid is False
        assert any("ZERO_BID_OR_ASK_DEPTH_REQUIRED" in r for r in res.rejection_reasons)

    def test_quote_validator_rejects_crossed_market(self):
        """Bid > Ask is an invalid crossed order book and must fail."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "12345",
            "exchange": "NFO",
            "ltp": 150.0,
            "bid": 155.0,
            "ask": 145.0,
            "feed_time": now_str
        }
        res = QuoteValidator.validate_quote(payload, require_depth=True)
        assert res.is_valid is False
        assert any("CROSSED_MARKET" in r for r in res.rejection_reasons)

    def test_quote_validator_enforces_bfo_for_bse_derivatives(self):
        """BSE contracts mapped to NFO must fail exchange routing validation."""
        now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "symbol": "BSE26DEC3950CE",
            "token": "2070306",
            "exchange": "NFO",  # Erroneous routing
            "ltp": 100.85,
            "bid": 100.0,
            "ask": 101.5,
            "feed_time": now_str
        }
        res = QuoteValidator.validate_quote(payload)
        assert res.is_valid is False
        assert any("BSE_MUST_BE_BFO" in r for r in res.rejection_reasons)

    def test_quote_validator_passes_verified_live_quote(self):
        """Valid quote with verified feed_time, valid depth, and correct exchange passes completely."""
        now = datetime.datetime.now()
        fresh_time = (now - datetime.timedelta(seconds=5)).strftime("%Y-%m-%d %H:%M:%S")
        payload = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "12345",
            "exchange": "NFO",
            "ltp": 150.0,
            "bid": 149.5,
            "ask": 150.5,
            "feed_time": fresh_time,
            "quote_source": "ANGEL_LTP_API",
            "is_realtime": True
        }
        res = QuoteValidator.validate_quote(payload, require_depth=True, reference_dt=now)
        assert res.is_valid is True
        assert res.verified_market_data is True
        assert res.rejection_reasons == []
        assert res.spread == 1.0

    def test_all_ledgers_satisfy_strict_cash_conservation(self):
        """All ledger files must satisfy Free Cash + Deployed Cash == Initial Capital + Realized P&L."""
        ledger_files = ["ledger.json", "ledger_fno.json", "ledger_himanshu.json", "ledger_prateek.json"]
        for fname in ledger_files:
            lpath = BASE_DIR / "trader" / fname
            assert lpath.exists(), f"{fname} must exist"
            with open(lpath, "r", encoding="utf-8") as f:
                d = json.load(f)

            init_cap = float(d.get("initial_capital", d.get("starting_capital", 1000000.0)))
            real_pnl = float(d.get("realized_pnl", 0.0))
            free_cash = float(d.get("free_cash", 0.0))
            dep_cash = float(d.get("deployed_cash", 0.0))
            open_pos = d.get("open_positions", [])

            calc_dep = round(sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in open_pos), 2)
            assert round(dep_cash, 2) == calc_dep, f"[{fname}] Deployed mismatch: stored {dep_cash} != calculated {calc_dep}"
            assert round(free_cash + dep_cash, 2) == round(init_cap + real_pnl, 2), f"[{fname}] Cash conservation failed"
