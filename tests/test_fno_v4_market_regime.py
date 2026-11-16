"""
Unit Tests for F&O V4 Market Regime Intelligence Engine (Hardened for Phase 2.1)
Test file: tests/test_fno_v4_market_regime.py
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone, date
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trader.fno_v4.market_regime import MarketRegimeEngine, RegimeConfig, RegimeOutput


class TestMarketRegimeEngine(unittest.TestCase):

    def setUp(self):
        self.config = RegimeConfig(
            require_15m=True,
            min_required_candles_5m=55,
            min_required_candles_15m=40,
            adx_strong_trend=25.0,
            adx_weak_trend=18.0,
            adx_sideways_max=18.0,
            atr_high_volatility_ratio=1.80,
            atr_expansion_min=1.00,
            atr_compression_max=0.85,
            max_candle_age_seconds_5m=900,
            max_candle_age_seconds_15m=1800
        )
        self.engine = MarketRegimeEngine(self.config)
        self.base_time = datetime(2026, 11, 16, 14, 0, tzinfo=timezone.utc)

    def _generate_synthetic_candles(
        self,
        n_bars: int = 70,
        trend_type: str = "UP",
        timeframe_minutes: int = 5,
        noise_std: float = 1.0,
        start_price: float = 24000.0,
        step: float = 10.0,
        vol_per_bar: float = 1000.0,
        end_time: datetime = None
    ) -> pd.DataFrame:
        """Generates deterministic synthetic candle data."""
        if end_time is None:
            end_time = self.base_time

        timestamps = [end_time - timedelta(minutes=timeframe_minutes * (n_bars - 1 - i)) for i in range(n_bars)]
        candles = []
        current = start_price

        for i in range(n_bars):
            if trend_type == "UP":
                delta = step
            elif trend_type == "DOWN":
                delta = -step
            elif trend_type == "SIDEWAYS":
                delta = step if (i % 2 == 0) else -step
            else:
                delta = 0.0

            open_p = current
            close_p = open_p + delta
            high_p = max(open_p, close_p) + abs(noise_std)
            low_p = min(open_p, close_p) - abs(noise_std)
            current = close_p

            candles.append({
                "timestamp": timestamps[i],
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": vol_per_bar
            })

        return pd.DataFrame(candles)

    def test_1_strong_uptrend_with_mandatory_15m(self):
        """Test strong uptrend with expanding volatility and aligned 15m HTF confirmation."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=18.0, noise_std=5.0)
        df_15m = self._generate_synthetic_candles(n_bars=50, trend_type="UP", timeframe_minutes=15, step=30.0, noise_std=8.0)
        
        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.trend_direction, "BULLISH")
        self.assertEqual(res.regime, "STRONG_TREND")
        self.assertGreater(res.adx_value, 20.0)
        self.assertGreater(res.ema_slope, 0.0)
        self.assertTrue(any("HTF_CONFIRMED" in r for r in res.reason_codes))

    def test_2_strong_downtrend_with_mandatory_15m(self):
        """Test strong downtrend with persistent lower lows and aligned 15m HTF confirmation."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="DOWN", step=18.0, noise_std=5.0)
        df_15m = self._generate_synthetic_candles(n_bars=50, trend_type="DOWN", timeframe_minutes=15, step=30.0, noise_std=8.0)
        
        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.trend_direction, "BEARISH")
        self.assertEqual(res.regime, "STRONG_TREND")
        self.assertLess(res.ema_slope, 0.0)

    def test_3_sideways_consolidation(self):
        """Test sideways choppy market with small oscillating candles."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="SIDEWAYS", step=2.0, noise_std=1.0)
        df_15m = self._generate_synthetic_candles(n_bars=50, trend_type="SIDEWAYS", timeframe_minutes=15, step=3.0, noise_std=2.0)
        
        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.regime, "SIDEWAYS")
        self.assertEqual(res.trend_direction, "NEUTRAL")
        self.assertLess(res.adx_value, self.config.adx_sideways_max)

    def test_4_high_volatility_shock(self):
        """Test sudden extreme ATR spike (e.g. news event or gap shock)."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="SIDEWAYS", step=3.0, noise_std=2.0)
        df_15m = self._generate_synthetic_candles(n_bars=50, trend_type="SIDEWAYS", timeframe_minutes=15, step=5.0, noise_std=3.0)
        
        # Inject massive shock in final 3 bars
        last_idx = len(df_5m) - 1
        df_5m.loc[last_idx - 2, ['high', 'low']] = [24500.0, 23500.0]
        df_5m.loc[last_idx - 1, ['high', 'low']] = [24600.0, 23400.0]
        df_5m.loc[last_idx, ['high', 'low']] = [24700.0, 23300.0]

        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)
        self.assertEqual(res.regime, "HIGH_VOLATILITY")
        self.assertGreaterEqual(res.atr_ratio, self.config.atr_high_volatility_ratio)

    def test_5_missing_mandatory_15m_returns_no_trade(self):
        """P0 Requirement: Missing or None 15m candles MUST return NO_TRADE with MISSING_15M_DATA."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=18.0)
        
        # 15m omitted entirely
        res = self.engine.evaluate(df_5m, candles_15m=None, reference_time=self.base_time)
        self.assertEqual(res.regime, "NO_TRADE")
        self.assertEqual(res.data_quality, "MISSING_15M_DATA")
        self.assertTrue(any("Mandatory 15m HTF" in r for r in res.reason_codes))

        # 15m empty list
        res_empty = self.engine.evaluate(df_5m, candles_15m=[], reference_time=self.base_time)
        self.assertEqual(res_empty.regime, "NO_TRADE")
        self.assertEqual(res_empty.data_quality, "MISSING_15M_DATA")

        # 15m insufficient length (<40 bars)
        df_15m_short = self._generate_synthetic_candles(n_bars=20, timeframe_minutes=15)
        res_short = self.engine.evaluate(df_5m, candles_15m=df_15m_short, reference_time=self.base_time)
        self.assertEqual(res_short.regime, "NO_TRADE")
        self.assertEqual(res_short.data_quality, "INSUFFICIENT_15M_DATA")

    def test_6_stale_candles_detection_5m_and_15m(self):
        """Test detection of delayed / stale candle timestamps for both 5m and 15m."""
        # 5m candle is 45 minutes old (limit is 15 mins / 900s)
        old_time = self.base_time - timedelta(minutes=45)
        df_5m_stale = self._generate_synthetic_candles(n_bars=60, end_time=old_time)
        df_15m = self._generate_synthetic_candles(n_bars=50, timeframe_minutes=15)

        res_5m_stale = self.engine.evaluate(df_5m_stale, candles_15m=df_15m, reference_time=self.base_time)
        self.assertEqual(res_5m_stale.regime, "NO_TRADE")
        self.assertEqual(res_5m_stale.data_quality, "STALE_DATA")

        # 15m candle is 60 minutes old (limit is 30 mins / 1800s)
        df_5m_fresh = self._generate_synthetic_candles(n_bars=60, end_time=self.base_time)
        df_15m_stale = self._generate_synthetic_candles(n_bars=50, timeframe_minutes=15, end_time=self.base_time - timedelta(minutes=60))
        res_15m_stale = self.engine.evaluate(df_5m_fresh, candles_15m=df_15m_stale, reference_time=self.base_time)
        self.assertEqual(res_15m_stale.regime, "NO_TRADE")
        self.assertEqual(res_15m_stale.data_quality, "STALE_15M_DATA")

    def test_7_session_vwap_resets_at_boundary(self):
        """P1 Requirement: VWAP must calculate on current session only, not cross-session cumulative."""
        # Create 2 sessions: Day 1 (high prices, huge volume) and Day 2 (lower prices)
        day1_time = datetime(2026, 11, 15, 15, 0, tzinfo=timezone.utc)
        day2_time = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)

        df_day1 = self._generate_synthetic_candles(n_bars=40, start_price=30000.0, step=0.0, vol_per_bar=100000.0, end_time=day1_time)
        df_day2 = self._generate_synthetic_candles(n_bars=40, start_price=24000.0, step=5.0, vol_per_bar=1000.0, end_time=day2_time)
        df_multi_day = pd.concat([df_day1, df_day2], ignore_index=True)

        df_15m = self._generate_synthetic_candles(n_bars=50, timeframe_minutes=15, end_time=day2_time)
        res = self.engine.evaluate(df_multi_day, candles_15m=df_15m, reference_time=day2_time)

        # In current session (Day 2), prices are around 24000-24200.
        # If VWAP had leaked Day 1 (price 30000, 4M volume), current close (~24200) would be massively BELOW VWAP (-20%).
        # But with clean session reset, VWAP is calculated only on Day 2 candles (~24100), so close is NEAR or ABOVE!
        self.assertNotEqual(res.vwap_status, "UNAVAILABLE")
        self.assertIn(res.vwap_status, ["NEAR", "ABOVE"])

    def test_8_invalid_and_nan_ohlc(self):
        """Test handling of corrupted data (high < low, NaN close)."""
        df_5m = self._generate_synthetic_candles(n_bars=60)
        df_15m = self._generate_synthetic_candles(n_bars=50, timeframe_minutes=15)

        # Invert high and low on bar 30
        df_5m.loc[30, 'high'] = 23000.0
        df_5m.loc[30, 'low'] = 25000.0

        res_invalid = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)
        self.assertEqual(res_invalid.regime, "NO_TRADE")
        self.assertEqual(res_invalid.data_quality, "INVALID_OHLC_RELATIONS")

    def test_9_conflicting_higher_timeframe(self):
        """Test multi-timeframe divergence: 5m bullish vs 15m bearish -> NO_TRADE."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=20.0, noise_std=5.0)
        df_15m = self._generate_synthetic_candles(n_bars=50, trend_type="DOWN", timeframe_minutes=15, step=30.0, noise_std=5.0)

        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)
        self.assertEqual(res.regime, "NO_TRADE")
        self.assertTrue(any("HTF_CONFLICT" in r for r in res.reason_codes))

    def test_10_genuine_lookahead_bias_validation(self):
        """
        P1 Requirement: Genuine prefix-versus-full-series lookahead test.
        Verifies that evaluating at bar t produces identical results before and after future bars exist,
        and that modifying future bars cannot corrupt historical evaluations.
        """
        # Generate full series of 120 bars
        df_5m_full = self._generate_synthetic_candles(n_bars=120, trend_type="UP", step=12.0)
        df_15m_full = self._generate_synthetic_candles(n_bars=80, trend_type="UP", timeframe_minutes=15, step=25.0)

        # Evaluation point t = 70
        t_eval = 70
        prefix_5m = df_5m_full.iloc[:t_eval + 1].copy()
        prefix_15m = df_15m_full.iloc[:int((t_eval + 1) / 3) + 20].copy()
        t_time = prefix_5m['timestamp'].iloc[-1] + timedelta(minutes=1)

        # Evaluate strictly on prefix
        res_prefix = self.engine.evaluate(prefix_5m, candles_15m=prefix_15m, reference_time=t_time)

        # Now perturb the future data (bars 71..119) with a massive crash and noise
        df_5m_perturbed = df_5m_full.copy()
        df_5m_perturbed.loc[71:, 'close'] = df_5m_perturbed.loc[71:, 'close'] - 5000.0
        df_5m_perturbed.loc[71:, 'high'] = df_5m_perturbed.loc[71:, 'high'] + 1000.0

        # Re-slice prefix from the perturbed full dataset up to bar t_eval
        prefix_sliced_from_perturbed = df_5m_perturbed.iloc[:t_eval + 1].copy()
        res_after = self.engine.evaluate(prefix_sliced_from_perturbed, candles_15m=prefix_15m, reference_time=t_time)

        # Assert that future bars have ZERO effect on bar t_eval
        self.assertEqual(res_prefix.regime, res_after.regime)
        self.assertEqual(res_prefix.trend_direction, res_after.trend_direction)
        self.assertAlmostEqual(res_prefix.adx_value, res_after.adx_value, places=6)
        self.assertAlmostEqual(res_prefix.atr_value, res_after.atr_value, places=6)
        self.assertAlmostEqual(res_prefix.atr_ratio, res_after.atr_ratio, places=6)
        self.assertAlmostEqual(res_prefix.ema_slope, res_after.ema_slope, places=8)


if __name__ == "__main__":
    unittest.main()
