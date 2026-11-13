#!/usr/bin/env python3
"""
Unit and Regression Tests for Phase 3: Critical F&O Price Validation Patch.
Verifies that:
1. Fallback premiums (85.0 and 220.0) are completely eliminated from the execution path.
2. Entries are strictly rejected/deferred when option quotes fail, return 0, or are missing.
3. Successful entries use verified market quotes with documented slippage.
4. Comprehensive audit metadata is recorded on every fill.
5. Historical fallback trades in journals are flagged without altering P&L.
"""

import unittest
from unittest.mock import MagicMock, patch
import json
import time
import datetime
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import fno_trader

class MockSmartApi:
    def __init__(self, ltp=None, bid=0.0, ask=0.0, fail=False):
        self.ltp = ltp
        self.bid = bid
        self.ask = ask
        self.fail = fail

    def getLtpData(self, exchange, tradingsymbol, symboltoken):
        if self.fail or self.ltp is None:
            return {"status": False, "message": "Failed to fetch LTP"}
        return {
            "status": True,
            "message": "SUCCESS",
            "data": {
                "exchange": exchange,
                "tradingsymbol": tradingsymbol,
                "symboltoken": symboltoken,
                "ltp": self.ltp,
                "bid": self.bid,
                "ask": self.ask
            }
        }

    def getMarketData(self, mode, exchangeTokens):
        if self.fail or self.ltp is None:
            return {"status": False, "message": "Failed"}
        return {
            "status": True,
            "data": {
                "fetched": [{
                    "ltp": self.ltp,
                    "depth": {
                        "buy": [{"price": self.bid}],
                        "sell": [{"price": self.ask}]
                    }
                }]
            }
        }

    def getCandleData(self, params):
        if self.fail or self.ltp is None:
            return {"status": False, "data": []}
        return {
            "status": True,
            "data": [["2026-11-12T10:00:00+05:30", 100, 110, 95, self.ltp, 500]]
        }


class TestFnoPriceValidation(unittest.TestCase):
    def setUp(self):
        self.nifty_contract = {
            "symbol": "NIFTY26NOV24200CE",
            "token": "45001",
            "strike": 24200.0,
            "expiry": "26NOV2026",
            "lotsize": 65,
            "option_type": "CE",
            "underlying": "NIFTY",
            "atm_strike": 24200
        }
        self.banknifty_contract = {
            "symbol": "BANKNIFTY26NOV51500PE",
            "token": "45002",
            "strike": 51500.0,
            "expiry": "26NOV2026",
            "lotsize": 30,
            "option_type": "PE",
            "underlying": "BANKNIFTY",
            "atm_strike": 51500
        }

    def _get_clean_trader(self):
        import tempfile
        tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp_db.close()
        om = fno_trader.OrderManager(db_path=Path(tmp_db.name))
        trader = fno_trader.FnoOptionsTrader(order_manager=om)
        trader.open_positions = {}
        trader.completed_trades = []
        return trader

    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_1_fno_entry_rejected_when_quote_fails_or_none(self, mock_tg, mock_save):
        """Test that an F&O entry is strictly rejected when the broker quote returns failure or None."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(fail=True)
        initial_cash = trader.free_cash

        # Attempt NIFTY entry
        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Confluence Signal")

        # Entry must be rejected: zero positions opened, cash untouched
        self.assertNotIn(self.nifty_contract["symbol"], trader.open_positions)
        self.assertEqual(trader.free_cash, initial_cash)
        mock_save.assert_not_called()

        # Attempt BANKNIFTY entry
        trader.enter_option_trade(self.banknifty_contract, spot_price=51490.0, option_type="PE", reason="Confluence Signal")
        self.assertNotIn(self.banknifty_contract["symbol"], trader.open_positions)
        self.assertEqual(trader.free_cash, initial_cash)

    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_2_fno_entry_rejected_when_quote_zero_or_negative(self, mock_tg, mock_save):
        """Test that an entry is deferred if quote returns 0.0 or negative LTP."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=0.0)
        initial_cash = trader.free_cash

        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Confluence Signal")
        self.assertEqual(len(trader.open_positions), 0)
        self.assertEqual(trader.free_cash, initial_cash)

    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_3_fno_entry_never_fills_at_fallback_values_85_or_220(self, mock_tg, mock_save):
        """Regression test proving quote failure CANNOT create a fill at Rs 85.0 or Rs 220.0."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(fail=True)

        # NIFTY attempt
        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Confluence Signal")
        for pos in trader.open_positions.values():
            self.assertNotEqual(pos["entry_price"], 85.0)
            self.assertNotEqual(pos["entry_price"], 220.0)

        # BANKNIFTY attempt
        trader.enter_option_trade(self.banknifty_contract, spot_price=51490.0, option_type="PE", reason="Confluence Signal")
        for pos in trader.open_positions.values():
            self.assertNotEqual(pos["entry_price"], 85.0)
            self.assertNotEqual(pos["entry_price"], 220.0)

        self.assertEqual(len(trader.open_positions), 0)

    @patch("fno_trader.broadcast_option_entry_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_4_fno_entry_succeeds_with_valid_quote_and_audit_fields(self, mock_tg, mock_save, mock_bcast):
        """Test that a valid quote creates a position containing all Phase 3 required contract and audit fields."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=152.40, bid=152.00, ask=152.80)
        initial_cash = trader.free_cash

        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Bullish Alignment")

        opt_sym = self.nifty_contract["symbol"]
        self.assertIn(opt_sym, trader.open_positions)
        pos = trader.open_positions[opt_sym]

        # Verification of fill price and slippage
        self.assertEqual(pos["entry_price"], 152.80)  # Taken ask price
        self.assertAlmostEqual(pos["slippage_applied"], 0.40, places=2)
        self.assertEqual(pos["base_ltp"], 152.40)
        self.assertEqual(pos["bid"], 152.00)
        self.assertEqual(pos["ask"], 152.80)

        # Verification of required contract details
        details = pos.get("contract_details", {})
        self.assertEqual(details.get("exchange"), "NFO")
        self.assertEqual(details.get("trading_symbol"), opt_sym)
        self.assertEqual(details.get("instrument_token"), "45001")
        self.assertEqual(details.get("strike"), 24200.0)
        self.assertEqual(details.get("expiry"), "26NOV2026")
        self.assertEqual(details.get("option_type"), "CE")

        # Verification of audit fields
        self.assertEqual(pos["price_source"], "VERIFIED_MARKET_QUOTE")
        self.assertTrue(pos["verified_market_data"])
        self.assertEqual(pos["quote_source"], "ANGEL_LTP_API")
        self.assertIn("IST", pos["quote_timestamp"])

        # Cash correctly deducted
        expected_investment = round(152.80 * 65, 2)
        self.assertAlmostEqual(trader.free_cash, initial_cash - expected_investment, places=2)

    @patch("fno_trader.broadcast_option_entry_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_5_simulated_slippage_when_ask_depth_unavailable(self, mock_tg, mock_save, mock_bcast):
        """Test that documented 0.2% slippage is applied when order book ask is unavailable."""
        trader = self._get_clean_trader()
        # ltp = 200.0, no bid/ask depth
        trader.api = MockSmartApi(ltp=200.0, bid=0.0, ask=0.0)

        trader.enter_option_trade(self.banknifty_contract, spot_price=51490.0, option_type="PE", reason="Bearish Setup")

        opt_sym = self.banknifty_contract["symbol"]
        self.assertIn(opt_sym, trader.open_positions)
        pos = trader.open_positions[opt_sym]

        # 0.2% slippage on 200.0 = 0.40 -> fill price = 200.40
        self.assertEqual(pos["base_ltp"], 200.0)
        self.assertEqual(pos["slippage_applied"], 0.40)
        self.assertEqual(pos["entry_price"], 200.40)

    @patch("fno_trader.broadcast_option_entry_to_accounts")
    @patch("fno_trader.broadcast_option_exit_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_6_exit_position_preserves_audit_fields(self, mock_tg, mock_save, mock_bcast_exit, mock_bcast_entry):
        """Test that position exit carries over audit fields to the completed trade record."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=100.0, bid=100.0, ask=100.0)

        initial_count = len(trader.completed_trades)
        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Confluence")
        opt_sym = self.nifty_contract["symbol"]

        trader.exit_position(opt_sym, "Target Hit", exit_price=115.0)

        self.assertEqual(len(trader.completed_trades), initial_count + 1)
        trade = trader.completed_trades[-1]
        self.assertEqual(trade["price_source"], "VERIFIED_MARKET_QUOTE")
        self.assertTrue(trade["verified_market_data"])
        self.assertEqual(trade["contract_details"]["exchange"], "NFO")
        self.assertEqual(trade["contract_details"]["trading_symbol"], opt_sym)

    def test_7_historical_reconciliation_integrity(self):
        """Test that existing ledger files contain flagged entries without altering P&L integrity."""
        ledger_path = BASE_DIR / "trader" / "ledger_fno.json"
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        th = data.get("trade_history", [])
        self.assertGreater(len(th), 0)

        flagged = [t for t in th if t.get("audit_status") == "FLAGGED_FALLBACK_PRICE"]
        self.assertGreater(len(flagged), 0)

        for t in flagged:
            self.assertIn(t["entry_price"], (85.0, 220.0))
            self.assertFalse(t["verified_market_data"])
            self.assertIn("Suspicious entry", t["audit_note"])

    def test_8_stale_quote_rejection(self):
        """Test that stale quotes (older than 180 seconds) from candle data are strictly rejected."""
        api = MagicMock()
        # Mock candle with timestamp 30 minutes ago
        old_time = (datetime.datetime.now() - datetime.timedelta(minutes=30)).strftime("%H:%M")
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        api.getLtpData.return_value = {"status": False}
        api.getMarketData.return_value = {"status": False}
        api.getCandleData.return_value = {
            "status": True,
            "data": [[f"{today_str}T{old_time}:00+05:30", 100, 105, 95, 102.5, 500]]
        }

        quote = fno_trader.get_option_market_quote(api, "NIFTY26FEB24200CE", "45001")
        self.assertIsNone(quote)

    @patch("fno_trader.broadcast_option_entry_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_9_expired_option_rejection(self, mock_tg, mock_save, mock_bcast):
        """Test that an expired option contract (expiry date before today) is strictly rejected."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=150.0)

        expired_contract = dict(self.nifty_contract)
        expired_contract["expiry"] = "01JAN2020"  # Clearly expired

        trader.enter_option_trade(expired_contract, spot_price=24210.0, option_type="CE", reason="Confluence")
        self.assertNotIn(expired_contract["symbol"], trader.open_positions)
        self.assertEqual(len(trader.open_positions), 0)

    def test_10_invalid_and_crossed_bid_ask_rejection(self):
        """Test that negative bid/ask or crossed book (bid > ask) is rejected."""
        api = MagicMock()
        # Case A: Crossed book (bid 155.0 > ask 150.0)
        api.getLtpData.return_value = {
            "status": True,
            "data": {"exchange": "NFO", "tradingsymbol": "OPT", "symboltoken": "123", "ltp": 152.0, "bid": 155.0, "ask": 150.0}
        }
        api.getMarketData.return_value = {"status": False}
        api.getCandleData.return_value = {"status": False}

        quote = fno_trader.get_option_market_quote(api, "OPT", "123")
        self.assertIsNone(quote)

        # Case B: Negative bid
        api.getLtpData.return_value = {
            "status": True,
            "data": {"exchange": "NFO", "tradingsymbol": "OPT", "symboltoken": "123", "ltp": 152.0, "bid": -10.0, "ask": 155.0}
        }
        quote_neg = fno_trader.get_option_market_quote(api, "OPT", "123")
        self.assertIsNone(quote_neg)

    def test_11_incomplete_candle_rejection(self):
        """Test that in-progress candle bars (bar_time >= current minute) cannot be used as quotes."""
        api = MagicMock()
        now_dt = datetime.datetime.now()
        current_minute = now_dt.strftime("%H:%M")
        today_str = now_dt.strftime("%Y-%m-%d")

        api.getLtpData.return_value = {"status": False}
        api.getMarketData.return_value = {"status": False}
        # Candle is at or after current minute -> incomplete bar
        api.getCandleData.return_value = {
            "status": True,
            "data": [[f"{today_str}T{current_minute}:00+05:30", 100, 105, 95, 102.5, 500]]
        }

        quote = fno_trader.get_option_market_quote(api, "OPT", "123")
        self.assertIsNone(quote)

    def test_12_historical_candle_price_classification(self):
        """Test that candle quotes are explicitly classified with is_realtime_quote=False."""
        api = MagicMock()
        now_dt = datetime.datetime.now()
        # 1 minute ago completed bar
        completed_minute = (now_dt - datetime.timedelta(minutes=1)).strftime("%H:%M")
        today_str = now_dt.strftime("%Y-%m-%d")

        api.getLtpData.return_value = {"status": False}
        api.getMarketData.return_value = {"status": False}
        api.getCandleData.return_value = {
            "status": True,
            "data": [[f"{today_str}T{completed_minute}:00+05:30", 100, 105, 95, 103.5, 500]]
        }

        quote = fno_trader.get_option_market_quote(api, "OPT", "123")
        self.assertIsNotNone(quote)
        self.assertFalse(quote["is_realtime_quote"])
        self.assertEqual(quote["quote_type"], "HISTORICAL_CANDLE")
        self.assertEqual(quote["quote_source"], "ANGEL_CANDLE_CLOSE")

    def test_13_ledger_reconciliation_idempotency(self):
        """Test that re-running ledger reconciliation is completely idempotent."""
        import apply_phase3_1_patch
        stats1 = apply_phase3_1_patch.reconcile_ledgers_phase3_1()
        stats2 = apply_phase3_1_patch.reconcile_ledgers_phase3_1()
        self.assertEqual(stats1, stats2)

    def test_14_historical_pnl_integrity(self):
        """Test that historical P&L and trade counts are perfectly preserved after reconciliation."""
        ledger_path = BASE_DIR / "trader" / "ledger_fno.json"
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        th = data.get("trade_history", [])
        calculated_pnl = sum(float(t.get("pnl", 0.0)) for t in th)
        recorded_pnl = float(data.get("realized_pnl", 0.0))
        self.assertAlmostEqual(calculated_pnl, recorded_pnl, places=1)

    @patch("fno_trader.broadcast_option_entry_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_15_duplicate_entry_prevention(self, mock_tg, mock_save, mock_bcast):
        """Test that duplicate entry attempts for an existing open position are blocked."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=150.0)

        # First entry succeeds
        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Signal 1")
        self.assertEqual(len(trader.open_positions), 1)

        # Second entry attempt for same contract is blocked
        trader.enter_option_trade(self.nifty_contract, spot_price=24220.0, option_type="CE", reason="Signal 2")
        self.assertEqual(len(trader.open_positions), 1)

    @patch("fno_trader.broadcast_option_exit_to_accounts")
    @patch("fno_trader.FnoOptionsTrader.save_ledger")
    @patch("fno_trader.FnoOptionsTrader.send_telegram")
    def test_16_failed_exit_preserves_open_position(self, mock_tg, mock_save, mock_bcast):
        """Test that if exit quote fails or exit cannot be confirmed, position is NOT popped."""
        trader = self._get_clean_trader()
        trader.api = MockSmartApi(ltp=100.0)

        # Open position
        trader.enter_option_trade(self.nifty_contract, spot_price=24210.0, option_type="CE", reason="Initial")
        opt_sym = self.nifty_contract["symbol"]
        self.assertIn(opt_sym, trader.open_positions)

        # Now quote fails during exit attempt
        trader.api = MockSmartApi(fail=True)
        res = trader.exit_position(opt_sym, "Target Hit", exit_price=None)

        self.assertFalse(res)
        # Position MUST remain open
        self.assertIn(opt_sym, trader.open_positions)

    def test_17_paper_only_execution_isolation(self):
        """Test that broker adapter strictly enforces paper mode and cannot place real orders."""
        trader = fno_trader.FnoOptionsTrader()
        self.assertFalse(trader.broker_adapter.is_live())
        self.assertTrue(trader.config.get("paper", True))

if __name__ == "__main__":
    unittest.main()
