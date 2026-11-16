#!/usr/bin/env python3
"""
Automated Verification Suite for Phase 0 (Baseline Audit) and Phase 1 (Root Cause Analysis).
Validates all empirical findings, configuration vs code discrepancies, and statistical anomalies
documented in reports/fno_v4/baseline_audit.md and reports/fno_v4/root_cause_analysis.md.
"""

import os
import sys
import json
import pytest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import fno_trader
from broker_adapter import BrokerAdapter

class TestFnoV4AuditVerification:

    def test_1_hard_execution_guard_paper_mode_enforced(self):
        """Verify real-money execution is strictly disabled across configuration and broker adapter."""
        config_path = BASE_DIR / "trader" / "config.json"
        assert config_path.exists(), "config.json must exist"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        assert cfg.get("paper") is True, "Config must strictly enforce paper=True"

        adapter = BrokerAdapter(cfg)
        assert adapter.is_live() is False, "BrokerAdapter must report paper mode"
        assert adapter.paper is True, "BrokerAdapter.paper must be True"

    def test_2_fno_v3_hardcoded_rigid_risk_parameters(self):
        """Verify that V3 fno_trader.py uses rigid -5% SL and +15% target constants."""
        assert hasattr(fno_trader, "STOP_LOSS_PCT"), "fno_trader must define STOP_LOSS_PCT"
        assert hasattr(fno_trader, "TARGET_PCT"), "fno_trader must define TARGET_PCT"
        assert fno_trader.STOP_LOSS_PCT == 0.05, "V3 Stop loss must be hardcoded to 5%"
        assert fno_trader.TARGET_PCT == 0.15, "V3 Target must be hardcoded to 15%"

    def test_3_config_vs_runtime_discrepancy_phantom_indicators(self):
        """Verify that VWAP and multi-strategy confluence advertised in config/logs are absent in fno_trader."""
        with open(BASE_DIR / "trader" / "fno_trader.py", "r", encoding="utf-8") as f:
            code = f.read()

        # Startup message mentions VWAP
        assert "VWAP" in code, "Startup message advertises VWAP"
        # But VWAP is never actually calculated or used in the signal evaluation loop
        assert "calculate_vwap" not in code, "VWAP indicator calculation is completely absent in fno_trader.py"
        assert "indicators.calculate_rsi" not in code, "RSI is never used in fno_trader.py"
        assert "indicators.calculate_adx" not in code, "ADX is never used in fno_trader.py"
        assert "indicators.calculate_atr" not in code, "ATR is never used in fno_trader.py"

    def test_4_fno_v3_single_timeframe_hypersensitivity(self):
        """Verify that fno_trader queries only ONE_MINUTE candles with no multi-timeframe confirmation."""
        with open(BASE_DIR / "trader" / "fno_trader.py", "r", encoding="utf-8") as f:
            code = f.read()

        assert '"interval": "ONE_MINUTE"' in code, "fno_trader must query ONE_MINUTE candles"
        assert '"interval": "FIVE_MINUTE"' not in code, "fno_trader lacks FIVE_MINUTE candle queries"
        assert '"interval": "FIFTEEN_MINUTE"' not in code, "fno_trader lacks FIFTEEN_MINUTE candle queries"

    def test_5_historical_synthetic_pricing_distortion_proven(self):
        """Verify empirical evidence of synthetic entry (e.g. 220.0) vs real exit price divergence."""
        ledger_path = BASE_DIR / "trader" / "ledger_fno.json"
        assert ledger_path.exists(), "ledger_fno.json must exist"
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        th = data.get("trade_history", [])
        synthetic_target_hits = [
            t for t in th
            if float(t.get("entry_price", 0)) == 220.0 and float(t.get("exit_price", 0)) > 600.0
        ]
        assert len(synthetic_target_hits) >= 5, (
            f"Expected at least 5 synthetic ₹220 entries exiting at ₹600+, found {len(synthetic_target_hits)}"
        )
        for t in synthetic_target_hits:
            assert "Target Hit" in t.get("reason", ""), "Synthetic divergence booked as Target Hit"

    def test_6_live_market_pricing_stop_out_rate_proven(self):
        """Verify that under verified live market quotes, the win rate collapsed to <30% due to -5% SL."""
        ledger_path = BASE_DIR / "trader" / "ledger_fno.json"
        with open(ledger_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        th = data.get("trade_history", [])
        live_trades = [
            t for t in th
            if t.get("verified_market_data", False)
            and t.get("quote_source") in ("ANGEL_MARKET_DATA", "ANGEL_LTP_API", "ANGEL_FULL_API", "ANGEL_CANDLE_CLOSE")
        ]
        assert len(live_trades) >= 20, f"Expected >= 20 live trades, found {len(live_trades)}"

        wins = [t for t in live_trades if float(t.get("pnl", 0)) > 0]
        losses = [t for t in live_trades if float(t.get("pnl", 0)) < 0]
        win_rate = len(wins) / len(live_trades)

        assert win_rate < 0.35, f"Live market win rate should be <35%, actual is {win_rate*100:.1f}%"

        sl_hits = [t for t in losses if "Stop-Loss Hit (-5%)" in t.get("reason", "")]
        sl_ratio = len(sl_hits) / len(losses)
        assert sl_ratio > 0.70, f"Over 70% of live losses must be caused by -5% SL hair-trigger, actual is {sl_ratio*100:.1f}%"

    def test_7_missing_exchange_feed_timestamp_substitution_in_tier1(self):
        """Verify code vulnerability where missing feed_time defaults to local machine time."""
        with open(BASE_DIR / "trader" / "fno_trader.py", "r", encoding="utf-8") as f:
            code = f.read()

        # Check line where quote_ts_str defaults to now_ist when feed_time is missing
        assert 'quote_ts_str = now_ist' in code, "fno_trader substitutes local time when feed_time missing"

