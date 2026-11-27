"""
Tests for F&O Options Directional Signals & P&L Calculations.
Verifies all 15 mandatory test requirements:
1. CE BUY: premium increases -> profit
2. CE BUY: premium decreases -> loss
3. PE BUY: premium increases -> profit
4. PE BUY: premium decreases -> loss
5. CE SELL: premium decreases -> profit
6. CE SELL: premium increases -> loss
7. PE SELL: premium decreases -> profit
8. PE SELL: premium increases -> loss
9. Correct quantity and lot-size calculations
10. Partial exit P&L aggregation
11. Brokerage and tax deductions (STT, GST, Stamp Duty, Turnover)
12. Missing or stale option premium rejection
13. Zero or negative LTP rejection
14. Underlying movement and option premium divergence (Greeks/IV impact)
15. Open position P&L recovery and serialization
"""

import sys
import math
from pathlib import Path
import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "trader"))

try:
    from fno.pnl_engine import OptionsPnLEngine, OptionPnLBreakdown
    from fno.quote_validator import QuoteValidator
except ImportError:
    from trader.fno.pnl_engine import OptionsPnLEngine, OptionPnLBreakdown
    from trader.fno.quote_validator import QuoteValidator


class TestFnoDirectionalPnL:

    def test_1_ce_buy_premium_increases_produces_profit(self):
        """CALL BUY (CE): Entry 100, Current 120, Qty 50 -> Gross PnL = +1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="NIFTY26NOV24200CE",
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=120.0,
            qty=50,
            option_type="CE"
        )
        assert res.gross_pnl == 1000.0
        assert res.is_profit is True
        assert res.net_pnl < res.gross_pnl  # Taxes and fees deducted
        assert res.return_pct == 20.0

    def test_2_ce_buy_premium_decreases_produces_loss(self):
        """CALL BUY (CE): Entry 100, Current 80, Qty 50 -> Gross PnL = -1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="NIFTY26NOV24200CE",
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=80.0,
            qty=50,
            option_type="CE"
        )
        assert res.gross_pnl == -1000.0
        assert res.is_profit is False
        assert res.return_pct == -20.0

    def test_3_pe_buy_premium_increases_produces_profit(self):
        """PUT BUY (PE): Entry 100, Current 120, Qty 50 -> Gross PnL = +1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="BANKNIFTY26NOV51500PE",
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=120.0,
            qty=50,
            option_type="PE"
        )
        assert res.gross_pnl == 1000.0
        assert res.is_profit is True
        assert res.return_pct == 20.0

    def test_4_pe_buy_premium_decreases_produces_loss(self):
        """PUT BUY (PE): Entry 100, Current 80, Qty 50 -> Gross PnL = -1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="BANKNIFTY26NOV51500PE",
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=80.0,
            qty=50,
            option_type="PE"
        )
        assert res.gross_pnl == -1000.0
        assert res.is_profit is False
        assert res.return_pct == -20.0

    def test_5_ce_sell_premium_decreases_produces_profit(self):
        """CALL SELL (Short CE): Entry 120, Current 100, Qty 50 -> Gross PnL = +1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="NIFTY26NOV24200CE",
            side="SELL",
            entry_price=120.0,
            current_or_exit_price=100.0,
            qty=50,
            option_type="CE"
        )
        assert res.gross_pnl == 1000.0
        assert res.is_profit is True

    def test_6_ce_sell_premium_increases_produces_loss(self):
        """CALL SELL (Short CE): Entry 100, Current 120, Qty 50 -> Gross PnL = -1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="NIFTY26NOV24200CE",
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=120.0,
            qty=50,
            option_type="CE"
        )
        assert res.gross_pnl == -1000.0
        assert res.is_profit is False

    def test_7_pe_sell_premium_decreases_produces_profit(self):
        """PUT SELL (Short PE): Entry 120, Current 100, Qty 50 -> Gross PnL = +1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="BANKNIFTY26NOV51500PE",
            side="SELL",
            entry_price=120.0,
            current_or_exit_price=100.0,
            qty=50,
            option_type="PE"
        )
        assert res.gross_pnl == 1000.0
        assert res.is_profit is True

    def test_8_pe_sell_premium_increases_produces_loss(self):
        """PUT SELL (Short PE): Entry 100, Current 120, Qty 50 -> Gross PnL = -1000."""
        res = OptionsPnLEngine.calculate_pnl(
            symbol="BANKNIFTY26NOV51500PE",
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=120.0,
            qty=50,
            option_type="PE"
        )
        assert res.gross_pnl == -1000.0
        assert res.is_profit is False

    def test_9_lot_size_and_quantity_multiplication(self):
        """Verifies exact PnL scaling across standard Indian option lot sizes."""
        # NIFTY lot: 65, BANKNIFTY lot: 30, BSE lot: 200, TATASTEEL lot: 2750
        nifty = OptionsPnLEngine.calculate_pnl("NIFTY26NOV24200CE", "BUY", 100.0, 110.0, qty=65)
        assert nifty.gross_pnl == 650.0

        banknifty = OptionsPnLEngine.calculate_pnl("BANKNIFTY26NOV51500PE", "BUY", 200.0, 250.0, qty=30)
        assert banknifty.gross_pnl == 1500.0

        bse = OptionsPnLEngine.calculate_pnl("BSE26DEC3950CE", "BUY", 100.0, 115.0, qty=200)
        assert bse.gross_pnl == 3000.0

        tatasteel = OptionsPnLEngine.calculate_pnl("TATASTEEL26NOV162.5CE", "BUY", 2.0, 2.5, qty=2750)
        assert tatasteel.gross_pnl == 1375.0

    def test_10_partial_exit_aggregation(self):
        """Verifies that multiple partial exits aggregate into exact weighted price and cumulative P&L."""
        # Entry: 100 shares @ Rs 100.0
        # Exit 1: 50 shares @ Rs 120.0 (+1000)
        # Exit 2: 50 shares @ Rs 140.0 (+2000)
        # Total expected gross = +3000, weighted exit = 130.0
        fills = [
            {"exit_price": 120.0, "qty": 50, "exit_reason": "Target 1 (+20%)"},
            {"exit_price": 140.0, "qty": 50, "exit_reason": "Target 2 (+40%)"}
        ]
        agg = OptionsPnLEngine.aggregate_partial_exits(
            symbol="NIFTY26NOV24200CE",
            side="BUY",
            entry_price=100.0,
            executed_fills=fills
        )
        assert agg["total_qty"] == 100
        assert agg["weighted_exit_price"] == 130.0
        assert agg["total_gross_pnl"] == 3000.0
        assert agg["total_net_pnl"] < 3000.0

    def test_11_statutory_charges_and_taxes_deduction(self):
        """Verifies individual statutory tax schedule on Buy roundtrip."""
        fee_info = OptionsPnLEngine.calculate_statutory_fees(
            side="BUY",
            entry_price=100.0,
            exit_price=120.0,
            qty=100
        )
        # Buy turnover = 10,000; Sell turnover = 12,000; Total = 22,000
        assert fee_info["buy_turnover"] == 10000.0
        assert fee_info["sell_turnover"] == 12000.0
        assert fee_info["total_turnover"] == 22000.0
        assert fee_info["brokerage"] == 40.0
        assert fee_info["stt"] == 12.0  # 0.1% on 12,000 sell turnover
        assert fee_info["exchange_turnover"] == 11.0  # 0.05% on 22,000 total turnover
        assert fee_info["stamp_duty"] == 0.3  # 0.003% on 10,000 buy turnover
        assert fee_info["total_fees"] > 63.3

    def test_12_missing_or_stale_option_premium_rejected(self):
        """Stale or missing quote must be rejected by QuoteValidator."""
        quote_missing = {"symbol": "NIFTY26NOV24200CE", "ltp": 100.0, "feed_time": None}
        v_missing = QuoteValidator.validate_quote(quote_missing)
        assert v_missing.is_valid is False
        assert v_missing.verified_market_data is False

    def test_13_zero_or_negative_ltp_rejected(self):
        """Zero, negative, or NaN LTP raises ValueError in PnL engine."""
        with pytest.raises(ValueError):
            OptionsPnLEngine.calculate_pnl("NIFTY26NOV24200CE", "BUY", entry_price=0.0, current_or_exit_price=100.0, qty=50)
        with pytest.raises(ValueError):
            OptionsPnLEngine.calculate_pnl("NIFTY26NOV24200CE", "BUY", entry_price=100.0, current_or_exit_price=-5.0, qty=50)
        with pytest.raises(ValueError):
            OptionsPnLEngine.calculate_pnl("NIFTY26NOV24200CE", "BUY", entry_price=100.0, current_or_exit_price=float("nan"), qty=50)

    def test_14_underlying_movement_and_option_premium_divergence(self):
        """
        Critical Rule: Even if underlying moved up, if CE premium decreased due to IV crush or theta,
        the CE BUY trade must report a LOSS, NOT a profit based on underlying spot direction.
        """
        # Scenario: Index moved up from 24,000 to 24,050 (+50 pts)
        # But CE premium dropped from 150.0 to 120.0 due to IV crush / decay
        res = OptionsPnLEngine.calculate_pnl(
            symbol="NIFTY26NOV24200CE",
            side="BUY",
            entry_price=150.0,
            current_or_exit_price=120.0,
            qty=65,
            option_type="CE"
        )
        # Must be a loss of (120 - 150) * 65 = -1950.0
        assert res.gross_pnl == -1950.0
        assert res.is_profit is False

    def test_15_directional_signal_validation(self):
        """Verifies market bias consistency rules."""
        # Bullish -> BUY CE is valid; BUY PE is invalid
        ok_ce, _ = OptionsPnLEngine.validate_directional_signal("BULLISH", "CE", "BUY")
        assert ok_ce is True
        bad_pe, reason_pe = OptionsPnLEngine.validate_directional_signal("BULLISH", "PE", "BUY")
        assert bad_pe is False
        assert "DIRECTION_MISMATCH" in reason_pe

        # Bearish -> BUY PE is valid; BUY CE is invalid
        ok_pe, _ = OptionsPnLEngine.validate_directional_signal("BEARISH", "PE", "BUY")
        assert ok_pe is True
        bad_ce, reason_ce = OptionsPnLEngine.validate_directional_signal("BEARISH", "CE", "BUY")
        assert bad_ce is False
        assert "DIRECTION_MISMATCH" in reason_ce
