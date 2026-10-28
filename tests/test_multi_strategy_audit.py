#!/usr/bin/env python3
"""
Comprehensive Integration & Unit Tests for Multi-Strategy Upgrades:
1. Confluence Multi-Strategy threshold & scoring
2. Completed candle filtering, deduplication, and out-of-order handling
3. Risk gate enforcement (kill switch, cash limits, duplicate position, ledger integration)
4. Benchmark alignment and relative strength evaluation
5. Broker adapter paper isolation
6. Exits (ambiguity conservative resolution, gap slippage, flat_by cutoff)
"""

import unittest
from unittest.mock import Mock, patch
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from broker_adapter import BrokerAdapter, PaperModeIsolationError
from multi_live_trader import filter_completed_candles, evaluate_position_exit

class TestMultiStrategyConfluence(unittest.TestCase):
    def test_confluence_rejected_with_single_strategy(self):
        """Proves one signal alone cannot pass when min_confluence_strategies=2."""
        cfg = {
            "strategy_mode": "CONFLUENCE",
            "min_confluence_strategies": 2,
            "min_confluence_score": 65.0,
            "enabled_strategies": ["EMA_CHANNEL", "ORB", "TREND_MOMENTUM"]
        }
        engine = StrategyEngine(cfg)

        # Mock evaluate methods so only EMA_CHANNEL returns LONG
        engine.evaluate_ema_channel = Mock(return_value={
            "symbol": "TCS", "exchange": "NSE", "direction": "LONG",
            "strategy_name": "EMA_CHANNEL", "entry_trigger": 2450.0,
            "proposed_entry_price": 2445.0, "stop_loss": 2435.0,
            "target_price": 2465.0, "reward_risk_ratio": 2.0
        })
        engine.evaluate_orb = Mock(return_value=engine._empty_signal("TCS", "NSE", "ORB"))
        engine.evaluate_trend_momentum = Mock(return_value=engine._empty_signal("TCS", "NSE", "TREND_MOMENTUM"))

        bars = {
            "close": [100.0] * 35, "high": [102.0] * 35, "low": [98.0] * 35, "volume": [1000] * 35
        }
        sig = engine.evaluate("TCS", bars)

        self.assertEqual(sig["direction"], "NO_TRADE")
        self.assertTrue(any("Insufficient independent strategy confirmations" in r for r in sig["rejection_reasons"]))

    def test_confluence_accepted_with_multi_strategies(self):
        """Proves multiple independent strategies meeting score pass confluence."""
        cfg = {
            "strategy_mode": "CONFLUENCE",
            "min_confluence_strategies": 2,
            "min_confluence_score": 60.0,
            "enabled_strategies": ["EMA_CHANNEL", "TREND_MOMENTUM"]
        }
        engine = StrategyEngine(cfg)

        engine.evaluate_ema_channel = Mock(return_value={
            "symbol": "TCS", "exchange": "NSE", "direction": "LONG",
            "strategy_name": "EMA_CHANNEL", "entry_trigger": 2450.0,
            "proposed_entry_price": 2445.0, "stop_loss": 2435.0,
            "target_price": 2465.0, "reward_risk_ratio": 2.0
        })
        engine.evaluate_trend_momentum = Mock(return_value={
            "symbol": "TCS", "exchange": "NSE", "direction": "LONG",
            "strategy_name": "TREND_MOMENTUM", "entry_trigger": 2452.0,
            "proposed_entry_price": 2445.0, "stop_loss": 2435.0,
            "target_price": 2465.0, "reward_risk_ratio": 2.0
        })

        bars = {
            "close": [100.0] * 35, "high": [102.0] * 35, "low": [98.0] * 35, "volume": [1000] * 35
        }
        sig = engine.evaluate("TCS", bars)

        self.assertEqual(sig["direction"], "LONG")
        self.assertEqual(sig["strategy_name"], "CONFLUENCE")
        self.assertGreaterEqual(sig["confluence_score"], 60.0)

    def test_invalid_mode_and_disabled_strategy_rejected(self):
        """Proves invalid modes and disabled strategies are safely rejected."""
        cfg = {"strategy_mode": "NON_EXISTENT_MODE"}
        engine = StrategyEngine(cfg)
        bars = {"close": [100.0] * 35, "high": [101.0] * 35, "low": [99.0] * 35}
        sig = engine.evaluate("TCS", bars)
        self.assertEqual(sig["direction"], "NO_TRADE")
        self.assertIn("Invalid strategy mode", sig["setup_description"])

        # Disabled strategy in single mode
        cfg2 = {"strategy_mode": "EMA_CHANNEL", "enabled_strategies": ["ORB"]}
        engine2 = StrategyEngine(cfg2)
        sig2 = engine2.evaluate("TCS", bars)
        self.assertEqual(sig2["direction"], "NO_TRADE")
        self.assertIn("disabled", sig2["setup_description"])


class TestCompletedCandleEnforcement(unittest.TestCase):
    def test_forming_candle_filtered(self):
        """Verifies that an in-progress candle matching current minute is dropped."""
        raw_bars = [
            ["2026-10-28T10:13:00+05:30", 100, 102, 99, 101, 500],
            ["2026-10-28T10:14:00+05:30", 101, 103, 100, 102, 600],
            ["2026-10-28T10:15:00+05:30", 102, 105, 101, 104, 700], # Current forming candle
        ]
        completed = filter_completed_candles(raw_bars, current_time_str="10:15")
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[-1][0], "2026-10-28T10:14:00+05:30")

    def test_duplicate_and_out_of_order_candles_handled(self):
        """Verifies deduplication and chronological sorting."""
        raw_bars = [
            ["2026-10-28T10:14:00+05:30", 101, 103, 100, 102, 600],
            ["2026-10-28T10:13:00+05:30", 100, 102, 99, 101, 500], # Out of order
            ["2026-10-28T10:14:00+05:30", 101, 103, 100, 102, 600], # Duplicate
        ]
        completed = filter_completed_candles(raw_bars, current_time_str="10:15")
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0][0], "2026-10-28T10:13:00+05:30")
        self.assertEqual(completed[1][0], "2026-10-28T10:14:00+05:30")


class TestBenchmarkRelativeStrength(unittest.TestCase):
    def test_missing_benchmark_safely_rejected(self):
        """Proves missing benchmark data safely yields NO_TRADE."""
        engine = StrategyEngine({"strategy_mode": "RELATIVE_STRENGTH_BREAKOUT"})
        bars = {
            "close": [100.0 + i for i in range(30)],
            "high": [101.0 + i for i in range(30)],
            "low": [99.0 + i for i in range(30)],
            "open": [100.0 + i for i in range(30)],
        }
        sig = engine.evaluate_relative_strength_breakout("INFY", bars, benchmark_bars=None)
        self.assertEqual(sig["direction"], "NO_TRADE")
        self.assertIn("missing", sig["setup_description"].lower())

    def test_stale_or_mismatched_benchmark_rejected(self):
        """Proves mismatched timestamp between stock and benchmark data is rejected."""
        engine = StrategyEngine({"strategy_mode": "RELATIVE_STRENGTH_BREAKOUT"})
        bars = {
            "timestamp": [f"10:{i:02d}" for i in range(30)],
            "close": [100.0 + i for i in range(30)],
            "high": [101.0 + i for i in range(30)],
            "low": [99.0 + i for i in range(30)],
            "open": [100.0 + i for i in range(30)],
        }
        # Stale benchmark ending at 10:25 instead of 10:29
        bench_bars = {
            "timestamp": [f"10:{i:02d}" for i in range(26)],
            "close": [25000.0 + i for i in range(26)],
        }
        sig = engine.evaluate_relative_strength_breakout("INFY", bars, benchmark_bars=bench_bars)
        self.assertEqual(sig["direction"], "NO_TRADE")
        self.assertIn("unaligned or stale", sig["setup_description"].lower())


class TestRiskGate(unittest.TestCase):
    def test_kill_switch_blocks_trade(self):
        """Simulates losing trades breaching daily limit and proves next trade is blocked."""
        risk = RiskEngine({"max_daily_loss_inr": 20000.0, "budget": 1000000.0})
        # Daily loss already -20,500
        res = risk.evaluate_order_risk(
            symbol="RELIANCE", side="BUY", entry_price=1200.0, stop_loss=1190.0,
            target_price=1220.0, available_cash=900000.0, daily_realized_loss=-20500.0,
            current_trades_count=2, current_open_positions_count=0
        )
        self.assertFalse(res["passed"])
        self.assertIn("kill-switch triggered", res["reasons"][0])

    def test_duplicate_position_blocked(self):
        """Ensures an order for an already open symbol is rejected."""
        risk = RiskEngine({"budget": 1000000.0})
        res = risk.evaluate_order_risk(
            symbol="JPL", side="BUY", entry_price=1400.0, stop_loss=1380.0,
            target_price=1440.0, available_cash=600000.0, daily_realized_loss=0.0,
            current_trades_count=1, current_open_positions_count=1,
            existing_symbols=["JPL"]
        )
        self.assertFalse(res["passed"])
        self.assertIn("Duplicate position rejected", res["reasons"][0])

    def test_zero_cash_blocked(self):
        """Ensures order is rejected if available cash is zero or negative."""
        risk = RiskEngine({"budget": 1000000.0})
        res = risk.evaluate_order_risk(
            symbol="TCS", side="BUY", entry_price=2400.0, stop_loss=2380.0,
            target_price=2440.0, available_cash=-5000.0, daily_realized_loss=0.0,
            current_trades_count=0, current_open_positions_count=0
        )
        self.assertFalse(res["passed"])
        self.assertIn("Insufficient available cash", res["reasons"][0])


class TestBrokerIsolation(unittest.TestCase):
    def test_paper_mode_blocks_live_order(self):
        """Proves BrokerAdapter raises PaperModeIsolationError when paper=True."""
        adapter = BrokerAdapter({"paper": True})
        with self.assertRaises(PaperModeIsolationError):
            adapter.place_live_order("RELIANCE", "BUY", 100, 1200.0)

    def test_mock_live_broker_never_called_in_paper_mode(self):
        """Ensures that executing orders in paper mode NEVER touches the live broker client."""
        mock_client = Mock()
        adapter = BrokerAdapter({"paper": True}, live_broker_client=mock_client)
        res = adapter.place_order("TCS", "BUY", 50, 2400.0)

        self.assertEqual(res["status"], "EXECUTED")
        self.assertTrue(res["paper"])
        mock_client.placeOrder.assert_not_called()


class TestExitAndSessionControls(unittest.TestCase):
    def test_ambiguous_candle_resolves_to_stop_loss(self):
        """When a candle touches both target and stop, conservative rule assumes stop hit first."""
        pos = {
            "symbol": "RELIANCE", "side": "BUY", "entry_price": 1200.0,
            "sl_price": 1190.0, "target_price": 1215.0, "quantity": 100, "entry_time": "10:00"
        }
        # Candle high reaches 1220 (target touched) AND low drops to 1185 (stop touched)
        ambiguous_bar = ["2026-10-28T10:05:00+05:30", 1202.0, 1220.0, 1185.0, 1205.0, 1000]
        exit_trade, exit_price, reason = evaluate_position_exit(pos, ambiguous_bar)

        self.assertTrue(exit_trade)
        self.assertEqual(exit_price, 1190.0) # SL price
        self.assertIn("Conservative Ambiguity Resolution", reason)

    def test_gap_down_through_stop_uses_open_price(self):
        """When candle opens below stop loss, slippage fill is at open price."""
        pos = {
            "symbol": "RELIANCE", "side": "BUY", "entry_price": 1200.0,
            "sl_price": 1190.0, "target_price": 1215.0, "quantity": 100, "entry_time": "10:00"
        }
        # Gapped down to open at 1182 (below 1190)
        gap_bar = ["2026-10-28T10:05:00+05:30", 1182.0, 1184.0, 1180.0, 1183.0, 1000]
        exit_trade, exit_price, reason = evaluate_position_exit(pos, gap_bar)

        self.assertTrue(exit_trade)
        self.assertEqual(exit_price, 1182.0) # Fill at open price
        self.assertIn("Gapped Below SL", reason)

    def test_flat_by_session_cutoff_squares_off(self):
        """Proves flat_by cutoff enforces immediate square-off."""
        pos = {
            "symbol": "RELIANCE", "side": "BUY", "entry_price": 1200.0,
            "sl_price": 1190.0, "target_price": 1215.0, "quantity": 100, "entry_time": "10:00"
        }
        normal_bar = ["2026-10-28T15:21:00+05:30", 1204.0, 1206.0, 1202.0, 1205.0, 1000]
        exit_trade, exit_price, reason = evaluate_position_exit(
            pos, normal_bar, flat_by="15:20", current_time_str="15:21"
        )
        self.assertTrue(exit_trade)
        self.assertIn("flat_by 15:20 reached", reason)

if __name__ == "__main__":
    unittest.main()
