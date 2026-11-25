"""
Unit Tests for StratLab Quantitative Options Architecture
Module: tests/test_stratlab_options_engine.py
"""

import pytest
import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))

from stratlab_options_engine import (
    BlackScholesEngine,
    OptionChainStructureEngine,
    VerticalSpreadResolver
)

class TestStratLabOptionsEngine:

    def test_1_black_scholes_pricing_and_greeks(self):
        """Verify Black-Scholes pricing matches analytical expectations and Greeks are directional."""
        S = 100.0
        K = 100.0
        T = 30.0 / 365.0  # 30 days
        r = 0.07
        sigma = 0.20  # 20% vol

        c_price = BlackScholesEngine.call_price(S, K, T, r, sigma)
        p_price = BlackScholesEngine.put_price(S, K, T, r, sigma)
        assert c_price > 0 and p_price > 0
        # Call price should be greater than Put price due to positive risk-free rate r=7%
        assert c_price > p_price

        # ATM Call Delta should be approximately 0.50
        greeks_call = BlackScholesEngine.compute_greeks(S, K, T, r, sigma, is_call=True)
        assert 0.48 <= greeks_call["delta"] <= 0.56
        assert greeks_call["theta"] < 0  # Time decay is negative
        assert greeks_call["gamma"] > 0
        assert greeks_call["vega"] > 0

        # ATM Put Delta should be approximately -0.50
        greeks_put = BlackScholesEngine.compute_greeks(S, K, T, r, sigma, is_call=False)
        assert -0.56 <= greeks_put["delta"] <= -0.40
        assert greeks_put["theta"] < 0

    def test_2_implied_volatility_solver_and_iv_rank(self):
        """Verify robust convergence of bisection IV solver."""
        S = 1285.0
        K = 1290.0
        T = 10.0 / 365.0
        r = 0.07
        known_sigma = 0.28

        model_price = BlackScholesEngine.call_price(S, K, T, r, known_sigma)
        recovered_iv = BlackScholesEngine.calculate_iv(model_price, S, K, T, r, is_call=True)

        assert abs((recovered_iv / 100.0) - known_sigma) < 0.01

        # IV Rank
        ivr_low = BlackScholesEngine.calculate_iv_rank(20.0, 15.0, 45.0)
        assert ivr_low < 30.0  # Low IV
        ivr_high = BlackScholesEngine.calculate_iv_rank(40.0, 15.0, 45.0)
        assert ivr_high > 75.0  # High IV rank (trigger block)

    def test_3_option_chain_oi_walls_and_pcr(self):
        """Verify Call Wall, Put Wall, PCR, and Max Pain detection."""
        strikes_mock = [
            {"strike": 1260.0, "call_oi": 10000, "put_oi": 80000},
            {"strike": 1280.0, "call_oi": 30000, "put_oi": 150000},  # Put Wall @ 1280
            {"strike": 1300.0, "call_oi": 250000, "put_oi": 40000},  # Call Wall @ 1300
            {"strike": 1320.0, "call_oi": 80000, "put_oi": 10000},
        ]
        res = OptionChainStructureEngine.analyze_chain(strikes_mock)
        assert res["call_wall"] == 1300.0
        assert res["put_wall"] == 1280.0
        # Total call = 370k, total put = 280k -> PCR = 280 / 370 = 0.76
        assert 0.70 <= res["pcr"] <= 0.80
        assert res["max_pain"] in (1280.0, 1300.0)

    def test_4_call_wall_clearance_guard(self):
        """Verify that spot sitting right below Call Wall is blocked as an institutional trap."""
        call_wall = 1300.0

        # Spot is 1298.0 (0.15% below wall) -> Sitting immediately underneath -> BLOCKED!
        can_buy, reason = OptionChainStructureEngine.validate_call_clearance(1298.0, call_wall)
        assert can_buy is False
        assert "TRAP_BLOCKED" in reason

        # Spot is 1280.0 (1.5% below wall) -> Clear runway -> ALLOWED!
        can_buy, reason = OptionChainStructureEngine.validate_call_clearance(1280.0, call_wall)
        assert can_buy is True
        assert reason == "CALL_CLEARANCE_PASSED"

        # Spot is 1305.0 (Broken above wall) -> Breakout confirmed -> ALLOWED!
        can_buy, reason = OptionChainStructureEngine.validate_call_clearance(1305.0, call_wall)
        assert can_buy is True
        assert reason == "CALL_CLEARANCE_PASSED"

    def test_5_vertical_bull_call_spread_resolution(self):
        """Verify dynamic construction of defined-risk Bull Call Spreads from real contract cache."""
        fno_path = BASE_DIR / "historical_data" / "fno_contracts.json"
        assert fno_path.exists(), "fno_contracts.json must exist"
        with open(fno_path, "r", encoding="utf-8") as f:
            contracts = json.load(f)

        spread = VerticalSpreadResolver.resolve_bull_call_spread(contracts, "RELIANCE", 1285.0)
        assert spread is not None
        assert spread["strategy"] == "BULL_CALL_SPREAD"
        assert spread["underlying"] == "RELIANCE"
        assert spread["long_leg"]["strike"] <= 1290.0
        assert spread["short_leg"]["strike"] > spread["long_leg"]["strike"]
        assert spread["strike_width"] > 0
        assert spread["lotsize"] == 500
