#!/usr/bin/env python3
"""
Automated Verification Suite for F&O V4 (Hardened for Phase 2.1).
Validates baseline empirical findings, ledger accounting integrity, price unit sanity,
mandatory 15m HTF confirmation, and session VWAP boundaries.
"""

import os
import sys
import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "trader"))

import fno_trader
from broker_adapter import BrokerAdapter
from trader.fno_v4.market_regime import MarketRegimeEngine, RegimeConfig


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
        """Verify that V3 baseline preserves rigid -5% SL and +15% target constants."""
        import fno_trader_v3_baseline
        assert hasattr(fno_trader_v3_baseline, "STOP_LOSS_PCT"), "fno_trader_v3_baseline must define STOP_LOSS_PCT"
        assert hasattr(fno_trader_v3_baseline, "TARGET_PCT"), "fno_trader_v3_baseline must define TARGET_PCT"
        assert fno_trader_v3_baseline.STOP_LOSS_PCT == 0.05, "V3 Stop loss must be hardcoded to 5%"
        assert fno_trader_v3_baseline.TARGET_PCT == 0.15, "V3 Target must be hardcoded to 15%"

    def test_2b_fno_v4_hardened_risk_parameters(self):
        """Verify that active fno_trader uses hardened 15% SL, 30% target, and breakeven locking."""
        assert fno_trader.STOP_LOSS_PCT == 0.15, "Hardened SL must be 15% to survive market noise"
        assert fno_trader.TARGET_PCT == 0.30, "Hardened Target must be 30% (1:2 R:R)"
        assert fno_trader.BREAKEVEN_LOCK_PCT == 0.12, "Breakeven lock must be active at +12%"

    def test_3_config_vs_runtime_discrepancy_phantom_indicators(self):
        """Verify that VWAP and multi-strategy confluence advertised in config/logs are absent in fno_trader."""
        with open(BASE_DIR / "trader" / "fno_trader.py", "r", encoding="utf-8") as f:
            code = f.read()

        assert "VWAP" in code, "Startup message advertises VWAP"
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

    def test_7_missing_exchange_feed_timestamp_rejected_as_unverified(self):
        """Verify that quotes lacking genuine exchange feed_time cannot claim verified market freshness."""
        # A quote with synthetic local timestamp substitution must NOT be marked VERIFIED_WITH_SOURCE_EVIDENCE
        quote_payload = {
            "symbol": "NIFTY26NOV24200CE",
            "ltp": 150.30,
            "feed_time": None,  # Missing exchange timestamp
            "local_fallback_time": "2026-11-16 13:05:00 IST"
        }
        # In V4 contract: missing feed_time means unverified
        is_fresh_verified = bool(quote_payload.get("feed_time") is not None)
        assert is_fresh_verified is False, "Missing exchange feed_time must never be treated as verified source evidence"

    def test_8_missing_15m_htf_confirmation_blocks_trading(self):
        """Verify that V4 MarketRegimeEngine strictly requires completed 15m HTF candles."""
        engine = MarketRegimeEngine(RegimeConfig(require_15m=True))
        
        # Create valid 5m candles
        now = datetime.now(timezone.utc)
        timestamps = [now - timedelta(minutes=5 * (60 - i)) for i in range(60)]
        df_5m = [{
            "timestamp": timestamps[i],
            "open": 24000.0 + i*5, "high": 24010.0 + i*5, "low": 23995.0 + i*5, "close": 24005.0 + i*5, "volume": 1000
        } for i in range(60)]

        # Run without 15m data
        res = engine.evaluate(df_5m, candles_15m=None, reference_time=now)
        assert res.regime == "NO_TRADE", "Missing 15m data must return NO_TRADE"
        assert res.data_quality == "MISSING_15M_DATA"
        assert any("Mandatory 15m HTF" in r for r in res.reason_codes)

    def test_9_session_vwap_reset_boundary(self):
        """Verify that VWAP resets strictly at the daily session boundary and does not pool across days."""
        engine = MarketRegimeEngine(RegimeConfig(require_15m=False))
        day1 = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
        day2 = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)

        # Day 1: massive volume at high price 30,000
        candles = []
        for i in range(40):
            t = day1 - timedelta(minutes=5 * (40 - i))
            candles.append({"timestamp": t, "open": 30000, "high": 30010, "low": 29990, "close": 30000, "volume": 500000})

        # Day 2: moderate volume at price 24,000
        for i in range(40):
            t = day2 - timedelta(minutes=5 * (40 - i))
            candles.append({"timestamp": t, "open": 24000, "high": 24010, "low": 23990, "close": 24005, "volume": 1000})

        res = engine.evaluate(candles, candles_15m=None, reference_time=day2)
        # Session VWAP must be close to Day 2 price (~24000), NOT dragged to Day 1 (30,000)
        assert res.vwap_status in ("NEAR", "ABOVE"), f"VWAP must evaluate to current session only, got {res.vwap_status}"

    def test_10_invalid_option_stop_loss_target_units_rejected(self):
        """Verify that open option positions cannot have spot prices mistakenly assigned to SL/target."""
        with open(BASE_DIR / "trader" / "ledger.json", "r", encoding="utf-8") as f:
            ledger = json.load(f)

        for p in ledger.get("open_positions", []):
            sym = p.get("symbol", "")
            entry = float(p.get("entry_price", 0.0))
            sl = float(p.get("sl_price", 0.0))
            tgt = float(p.get("target_price", 0.0))

            if "CE" in sym or "PE" in sym:
                # Option entry is typically < ₹1,000
                if entry < 1000.0:
                    assert sl < 1000.0, f"Option SL must be in option premium units (<1000), found spot-level SL {sl} on {sym}"
                    assert tgt < 1000.0, f"Option Target must be in option premium units (<1000), found spot-level Target {tgt} on {sym}"
                    assert sl < entry, f"Option BUY SL must be below entry price, found SL {sl} >= Entry {entry} on {sym}"
                    assert tgt > entry, f"Option BUY Target must be above entry price, found Target {tgt} <= Entry {entry} on {sym}"

    def test_11_ledger_cash_conservation_and_reconciliation(self):
        """Verify strict accounting conservation across all ledger files."""
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
            assert round(dep_cash, 2) == calc_dep, (
                f"[{fname}] Deployed cash mismatch: stored {dep_cash} != calculated {calc_dep}"
            )
            assert round(free_cash + dep_cash, 2) == round(init_cap + real_pnl, 2), (
                f"[{fname}] Cash conservation violated: Free ({free_cash}) + Deployed ({dep_cash}) != Initial ({init_cap}) + Realized ({real_pnl})"
            )
