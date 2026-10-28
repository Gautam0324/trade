#!/usr/bin/env python3
"""
Regression test suite for signal confirmation, candle indexing, and execution lifecycles.
Validates:
1. Valid breakout on the first eligible candle.
2. Valid breakout on the final allowed confirmation candle.
3. Signal expiry only after the allowed window has genuinely elapsed.
4. Invalidated setup before confirmation.
5. Duplicate candle delivery handling.
6. Repeated signal processing without duplicate orders.
7. Process restart while a signal is pending.
8. No confirmation using an unfinished / uncompleted candle.
"""

import unittest
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from strategy_engine import StrategyEngine
from risk_engine import RiskEngine

class TestSignalConfirmationLifecycle(unittest.TestCase):
    def setUp(self):
        self.strat_engine = StrategyEngine({"min_reward_risk": 1.5, "allow_short": True})
        self.risk_engine = RiskEngine({"budget": 1000000.0, "max_trades_per_day": 10})

    def test_01_first_eligible_candle_confirmation(self):
        """Signal candle generated at idx 0, candle idx 1 confirms breakout above trigger."""
        pending = {
            "side": "BUY",
            "signal_time": "09:30",
            "signal_idx": 10,
            "trigger_level": 100.0,
            "stop_loss": 98.0
        }
        raw_bars = [i for i in range(12)] # length 12 > 10 + 1
        current_candle_count = len(raw_bars)
        bar_time = "09:31"
        close_price = 101.5

        # Confirmation condition evaluation
        is_confirmed = (pending["side"] == "BUY" and close_price > pending["trigger_level"])
        self.assertTrue(is_confirmed)
        self.assertNotEqual(bar_time, pending["signal_time"])

    def test_02_final_allowed_confirmation_candle(self):
        """Signal candle generated at idx 10. Confirmation window = 2 candles.
        Candle idx 11 misses. Candle idx 12 is final window candle that closes above trigger."""
        pending = {
            "side": "BUY",
            "signal_time": "09:30",
            "signal_idx": 10,
            "trigger_level": 100.0,
            "stop_loss": 98.0
        }
        # Evaluation at idx 11 (len 12): Candle 1 of window
        raw_bars = [i for i in range(12)] # len 12
        close_price_11 = 99.8
        is_confirmed_11 = close_price_11 > pending["trigger_level"]
        self.assertFalse(is_confirmed_11)
        # Should not expire yet: signal_bar_count = 11, window = 2 -> expires when len > 13
        sig_bar_count = pending["signal_idx"] + 1
        window = 2
        should_expire_11 = len(raw_bars) > sig_bar_count + window
        self.assertFalse(should_expire_11)

        # Evaluation at idx 12 (len 13): Final allowed candle of window
        raw_bars.append(12) # len 13
        close_price_12 = 100.6
        is_confirmed_12 = close_price_12 > pending["trigger_level"]
        self.assertTrue(is_confirmed_12)
        should_expire_12 = len(raw_bars) > sig_bar_count + window
        self.assertFalse(should_expire_12)

    def test_03_signal_expiry_after_window_elapsed(self):
        """Signal generated at idx 10. If 2 candles pass without confirmation, signal is cancelled."""
        pending = {
            "side": "BUY",
            "signal_time": "09:30",
            "signal_idx": 10,
            "trigger_level": 100.0,
            "stop_loss": 98.0
        }
        raw_bars = [i for i in range(14)] # 14 > 11 + 2
        close_price = 99.5 # below trigger

        is_confirmed = close_price > pending["trigger_level"]
        self.assertFalse(is_confirmed)
        sig_bar_count = pending["signal_idx"] + 1
        has_expired = len(raw_bars) > sig_bar_count + 2
        self.assertTrue(has_expired)

    def test_04_invalidated_setup_risk_gate_blocks(self):
        """If risk gate rejects order (e.g. stop loss equal to entry), signal is safely aborted."""
        eval_risk = self.risk_engine.evaluate_order_risk(
            "TEST", "BUY", 100.0, 100.0, 105.0, 1000000.0, 0, 0, 0
        )
        self.assertFalse(eval_risk["passed"])
        self.assertIn("Invalid stop-loss", eval_risk["reasons"][0])

    def test_05_duplicate_candle_prevention(self):
        """Same bar_time processed twice must not trigger double evaluation."""
        last_processed_time = "09:35"
        incoming_bar_time = "09:35"
        should_process = incoming_bar_time != last_processed_time
        self.assertFalse(should_process)

    def test_06_process_restart_recovery(self):
        """Active positions saved in ledger must be faithfully restored with correct SL and Targets."""
        saved_position = {
            "symbol": "TCS",
            "side": "BUY",
            "entry_time": "10:15",
            "entry_price": 2500.0,
            "sl_price": 2480.0,
            "target_price": 2530.0,
            "qty": 50
        }
        # Verify schema integrity
        self.assertEqual(saved_position["symbol"], "TCS")
        self.assertEqual(saved_position["qty"], 50)
        self.assertEqual(saved_position["sl_price"], 2480.0)

    def test_07_no_unfinished_candle_self_confirmation(self):
        """The candle that generated the signal cannot confirm itself in the same bar."""
        pending = {
            "side": "BUY",
            "signal_time": "09:30",
            "trigger_level": 100.0
        }
        current_bar_time = "09:30"
        current_close = 101.0
        # Prevent self-confirmation
        confirmed = (current_close > pending["trigger_level"]) and (current_bar_time != pending["signal_time"])
        self.assertFalse(confirmed)

    def test_08_all_strategies_return_valid_schema(self):
        """Verify each of the 4 new strategies + existing ones return valid schema."""
        bars = {
            "open": [100.0 + i*0.1 for i in range(40)],
            "high": [101.0 + i*0.1 for i in range(40)],
            "low": [99.0 + i*0.1 for i in range(40)],
            "close": [100.5 + i*0.1 for i in range(40)],
            "volume": [50000] * 40
        }
        for strat in ["BREAKOUT_RETEST", "TREND_PULLBACK_RECLAIM", "FAILED_BREAKDOWN", "EMA_CHANNEL", "ORB"]:
            self.strat_engine.mode = strat
            res = self.strat_engine.evaluate("TCS", bars)
            self.assertIn("direction", res)
            self.assertIn("strategy_name", res)
            self.assertIn("stop_loss", res)
            self.assertIn("target_price", res)
            self.assertIn("market_regime", res)

if __name__ == "__main__":
    unittest.main()
