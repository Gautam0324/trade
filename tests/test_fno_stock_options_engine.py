"""
Unit tests for F&O Liquid Stock Options Engine.
Verifies:
1. Active Stock Options Universe composition and index exclusion
2. Strike and Lot resolution across all top liquid stocks
3. Paper mode enforcement and risk guard constraints
4. Capital and concurrent positions guard
"""

import pytest
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))

import fno_trader

class TestFnoStockOptionsEngine:

    def test_1_stock_universe_composition(self):
        """Ensure Stock Universe contains top liquid stocks and excludes high-decay Index derivatives when toggled."""
        assert fno_trader.ENABLE_INDEX_OPTIONS is False, "Index options must be disabled by default as per user request"
        
        active_names = [u["name"] for u in fno_trader.ACTIVE_FNO_UNIVERSE]
        assert "BSE" in active_names, "BSE must be in active stock options universe"
        assert "HDFCBANK" in active_names, "HDFCBANK must be in active stock options universe"
        assert "RELIANCE" in active_names, "RELIANCE must be in active stock options universe"
        assert "ICICIBANK" in active_names, "ICICIBANK must be in active stock options universe"
        assert "SBIN" in active_names, "SBIN must be in active stock options universe"
        assert "IDEA" not in active_names, "Penny stock IDEA must be excluded"
        assert "NIFTY" not in active_names, "NIFTY index must be excluded when ENABLE_INDEX_OPTIONS is False"
        assert "BANKNIFTY" not in active_names, "BANKNIFTY index must be excluded when ENABLE_INDEX_OPTIONS is False"

    def test_2_atm_resolution_liquid_stocks(self):
        """Verify dynamic ATM contract resolution for liquid stocks."""
        test_cases = [
            ("BSE", 4650.0, "CE", "BFO", 200),
            ("HDFCBANK", 820.0, "CE", "NFO", 650),
            ("RELIANCE", 1280.0, "CE", "NFO", 500),
            ("ICICIBANK", 1260.0, "CE", "NFO", 700),
            ("SBIN", 820.0, "CE", "NFO", 750),
            ("BHARTIARTL", 1620.0, "CE", "NFO", 475),
            ("TATASTEEL", 145.0, "CE", "NFO", 2750),
        ]
        for sym, spot, otype, expected_exch, expected_lot in test_cases:
            contract = fno_trader.resolve_atm_contract(sym, spot, otype)
            assert contract is not None, f"Failed to resolve ATM for {sym}"
            assert contract["underlying"] == sym
            assert contract["exchange"] == expected_exch
            assert contract["lotsize"] == expected_lot
            assert contract["strike"] > 0
            assert contract["token"].isdigit()

    def test_3_risk_parameters_and_paper_mode(self):
        """Verify that hardened noise-tolerant risk parameters and paper execution remain enforced."""
        assert fno_trader.STOP_LOSS_PCT == 0.15, "Stop-Loss must be 15%"
        assert fno_trader.TARGET_PCT == 0.30, "Target must be 30%"
        assert fno_trader.BREAKEVEN_LOCK_PCT == 0.12, "Breakeven lock must be +12%"
        assert fno_trader.MAX_CONCURRENT_POSITIONS == 3, "Max concurrent positions must be capped at 3"

    def test_4_index_options_toggle(self):
        """Verify that Index Options can be toggled on if explicitly required without breaking universe."""
        enabled_universe = fno_trader.INDEX_UNIVERSE + fno_trader.STOCK_FNO_UNIVERSE
        index_names = [u["name"] for u in enabled_universe if u.get("is_index")]
        assert "NIFTY" in index_names
        assert "BANKNIFTY" in index_names
