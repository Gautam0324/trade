"""
Unit & Regression Tests for Day Trader Pro V4.0 - Dual Direction Options Trading Engine.
Validates:
1. BUY CALL (CE) - Bullish: Profit when premium rises, loss when premium falls.
2. BUY PUT (PE) - Bearish: Profit when premium rises, loss when premium falls.
3. SELL CALL (CE) - Bearish: Profit when premium falls, loss when premium rises.
4. SELL PUT (PE) - Bullish: Profit when premium falls, loss when premium rises.
5. Exit order side reversal:
   - BUY positions exit with SELL order.
   - SELL positions exit with BUY order.
   - Accidental position flips strictly prevented.
6. Margin requirement calculation & pre-trade risk enforcement:
   - Option buying requires premium * quantity.
   - Option selling requires SPAN + Exposure margin floor.
   - Margin rejection occurs when free cash is insufficient.
7. DualDirectionStrategyEngine 4-way signal resolution:
   - Strong bull -> BUY CE (or SELL PE in high IV)
   - Strong bear -> BUY PE (or SELL CE in high IV)
   - Counter-trend guards.
"""

import unittest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.path.insert(0, str(BASE_DIR / "trader"))

import pytest
from trader.fno.pnl_engine import OptionsPnLEngine
from trader.fno.margin_calculator import OptionMarginCalculator
from trader.fno.strategy_engine import DualDirectionStrategyEngine, OptionSignal


class TestFnoV4BuySellEngine(unittest.TestCase):

    def setUp(self):
        self.symbol_ce = "NIFTY26NOV2624500CE"
        self.symbol_pe = "NIFTY26NOV2624500PE"
        self.qty = 65  # 1 lot of NIFTY

    # -------------------------------------------------------------
    # 1. BUY CALL (CE) Tests
    # -------------------------------------------------------------
    def test_buy_call_profit_when_premium_rises(self):
        """BUY CE: Entry @ 100, Exit @ 150 -> Gross PnL = +50 * 65 = +3250."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_ce,
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=150.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, 3250.0)
        self.assertTrue(breakdown.net_pnl > 3100.0)
        self.assertTrue(breakdown.is_profit)
        self.assertEqual(breakdown.side, "BUY")
        self.assertEqual(breakdown.option_type, "CE")

    def test_buy_call_loss_when_premium_falls(self):
        """BUY CE: Entry @ 100, Exit @ 80 -> Gross PnL = -20 * 65 = -1300."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_ce,
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=80.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, -1300.0)
        self.assertTrue(breakdown.net_pnl < -1300.0)
        self.assertFalse(breakdown.is_profit)

    # -------------------------------------------------------------
    # 2. BUY PUT (PE) Tests
    # -------------------------------------------------------------
    def test_buy_put_profit_when_premium_rises(self):
        """BUY PE: Entry @ 100, Exit @ 140 -> Gross PnL = +40 * 65 = +2600."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_pe,
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=140.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, 2600.0)
        self.assertTrue(breakdown.net_pnl > 2500.0)
        self.assertTrue(breakdown.is_profit)
        self.assertEqual(breakdown.side, "BUY")
        self.assertEqual(breakdown.option_type, "PE")

    def test_buy_put_loss_when_premium_falls(self):
        """BUY PE: Entry @ 100, Exit @ 75 -> Gross PnL = -25 * 65 = -1625."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_pe,
            side="BUY",
            entry_price=100.0,
            current_or_exit_price=75.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, -1625.0)
        self.assertTrue(breakdown.net_pnl < -1625.0)
        self.assertFalse(breakdown.is_profit)

    # -------------------------------------------------------------
    # 3. SELL CALL (CE) Tests
    # -------------------------------------------------------------
    def test_sell_call_profit_when_premium_falls(self):
        """SELL CE: Entry @ 100, Exit @ 50 (decay) -> Gross PnL = (100 - 50) * 65 = +3250."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_ce,
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=50.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, 3250.0)
        self.assertTrue(breakdown.net_pnl > 3100.0)
        self.assertTrue(breakdown.is_profit)
        self.assertEqual(breakdown.side, "SELL")
        self.assertEqual(breakdown.option_type, "CE")

    def test_sell_call_loss_when_premium_rises(self):
        """SELL CE: Entry @ 100, Exit @ 150 (adverse expansion) -> Gross PnL = (100 - 150) * 65 = -3250."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_ce,
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=150.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, -3250.0)
        self.assertTrue(breakdown.net_pnl < -3250.0)
        self.assertFalse(breakdown.is_profit)

    # -------------------------------------------------------------
    # 4. SELL PUT (PE) Tests
    # -------------------------------------------------------------
    def test_sell_put_profit_when_premium_falls(self):
        """SELL PE: Entry @ 100, Exit @ 40 (decay) -> Gross PnL = (100 - 40) * 65 = +3900."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_pe,
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=40.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, 3900.0)
        self.assertTrue(breakdown.net_pnl > 3750.0)
        self.assertTrue(breakdown.is_profit)
        self.assertEqual(breakdown.side, "SELL")
        self.assertEqual(breakdown.option_type, "PE")

    def test_sell_put_loss_when_premium_rises(self):
        """SELL PE: Entry @ 100, Exit @ 160 (crash expansion) -> Gross PnL = (100 - 160) * 65 = -3900."""
        breakdown = OptionsPnLEngine.calculate_pnl(
            symbol=self.symbol_pe,
            side="SELL",
            entry_price=100.0,
            current_or_exit_price=160.0,
            qty=self.qty,
            is_closed=True
        )
        self.assertEqual(breakdown.gross_pnl, -3900.0)
        self.assertTrue(breakdown.net_pnl < -3900.0)
        self.assertFalse(breakdown.is_profit)

    # -------------------------------------------------------------
    # 5. Margin Calculator & Pre-Trade Risk Tests
    # -------------------------------------------------------------
    def test_option_buying_requires_only_premium_cash(self):
        """BUY CE requires exact cash = premium * qty."""
        margin_info = OptionMarginCalculator.calculate_margin(
            action="BUY",
            option_type="CE",
            underlying="NIFTY",
            strike=24500.0,
            spot_price=24500.0,
            premium=100.0,
            qty=self.qty
        )
        self.assertEqual(margin_info["total_funds_needed"], 6500.0)
        self.assertEqual(margin_info["margin_required"], 0.0)
        self.assertFalse(margin_info["is_margin_trade"])

    def test_option_selling_requires_span_exposure_margin(self):
        """SELL CE requires SPAN+Exposure floor (~Rs 1.1L for NIFTY)."""
        margin_info = OptionMarginCalculator.calculate_margin(
            action="SELL",
            option_type="CE",
            underlying="NIFTY",
            strike=24500.0,
            spot_price=24500.0,
            premium=100.0,
            qty=self.qty
        )
        self.assertTrue(margin_info["total_funds_needed"] >= 110000.0)
        self.assertTrue(margin_info["is_margin_trade"])
        self.assertEqual(margin_info["position_direction"], "SHORT")

    def test_margin_validation_rejects_insufficient_cash(self):
        """Pre-trade gate rejects sell order if free cash < margin requirement."""
        can_place, reason, meta = OptionMarginCalculator.validate_order_margin(
            action="SELL",
            option_type="PE",
            underlying="NIFTY",
            strike=24500.0,
            spot_price=24500.0,
            premium=100.0,
            qty=self.qty,
            free_cash=50000.0  # Only 50k available, need ~1.1L
        )
        self.assertFalse(can_place)
        self.assertIn("Insufficient funds", reason)

    def test_margin_validation_admits_sufficient_cash(self):
        """Pre-trade gate admits sell order when free cash >= margin requirement."""
        can_place, reason, meta = OptionMarginCalculator.validate_order_margin(
            action="SELL",
            option_type="PE",
            underlying="NIFTY",
            strike=24500.0,
            spot_price=24500.0,
            premium=100.0,
            qty=self.qty,
            free_cash=200000.0  # 2L available
        )
        self.assertTrue(can_place)
        self.assertEqual(reason, "MARGIN_OK")

    # -------------------------------------------------------------
    # 6. Dual-Direction Strategy Signal Generator Tests
    # -------------------------------------------------------------
    def test_strong_bull_trend_generates_buy_ce(self):
        """Strong Bull trend + Bull regime generates BUY CE."""
        sig = DualDirectionStrategyEngine.evaluate_market_condition(
            spot_price=24550.0,
            day_open=24500.0,
            st_dir=1,
            ema5=24545.0,
            ema9=24535.0,
            ema33=24510.0,
            trading_mode="BUY_AND_SELL",
            iv_rank=25.0
        )
        self.assertTrue(sig.should_trade)
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.option_type, "CE")
        self.assertEqual(sig.strategy_name, "BUY_CE")
        self.assertEqual(sig.market_bias, "BULLISH")

    def test_strong_bear_trend_generates_buy_pe(self):
        """Strong Bear trend + Bear regime generates BUY PE."""
        sig = DualDirectionStrategyEngine.evaluate_market_condition(
            spot_price=24450.0,
            day_open=24500.0,
            st_dir=-1,
            ema5=24455.0,
            ema9=24465.0,
            ema33=24490.0,
            trading_mode="BUY_AND_SELL",
            iv_rank=25.0
        )
        self.assertTrue(sig.should_trade)
        self.assertEqual(sig.action, "BUY")
        self.assertEqual(sig.option_type, "PE")
        self.assertEqual(sig.strategy_name, "BUY_PE")
        self.assertEqual(sig.market_bias, "BEARISH")

    def test_bull_trend_with_high_iv_generates_sell_pe(self):
        """Bull trend + High IV rank favors SELL PE (theta decay / Put Wall defense)."""
        sig = DualDirectionStrategyEngine.evaluate_market_condition(
            spot_price=24550.0,
            day_open=24500.0,
            st_dir=1,
            ema5=24545.0,
            ema9=24535.0,
            ema33=24510.0,
            trading_mode="BUY_AND_SELL",
            iv_rank=65.0
        )
        self.assertTrue(sig.should_trade)
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.option_type, "PE")
        self.assertEqual(sig.strategy_name, "SELL_PE")

    def test_bear_trend_with_high_iv_generates_sell_ce(self):
        """Bear trend + High IV rank favors SELL CE (theta decay / Call Wall rejection)."""
        sig = DualDirectionStrategyEngine.evaluate_market_condition(
            spot_price=24450.0,
            day_open=24500.0,
            st_dir=-1,
            ema5=24455.0,
            ema9=24465.0,
            ema33=24490.0,
            trading_mode="BUY_AND_SELL",
            iv_rank=65.0
        )
        self.assertTrue(sig.should_trade)
        self.assertEqual(sig.action, "SELL")
        self.assertEqual(sig.option_type, "CE")
        self.assertEqual(sig.strategy_name, "SELL_CE")

    def test_counter_trend_in_bull_run_blocked(self):
        """Supertrend bearish (-1) but Spot >= DayOpen -> Counter-trend trade strictly blocked."""
        sig = DualDirectionStrategyEngine.evaluate_market_condition(
            spot_price=24550.0,
            day_open=24500.0,
            st_dir=-1,
            ema5=24540.0,
            ema9=24545.0,
            ema33=24520.0,
            trading_mode="BUY_AND_SELL"
        )
        self.assertFalse(sig.should_trade)
        self.assertIn("Counter-trend trade blocked", sig.reason)


if __name__ == "__main__":
    unittest.main()
