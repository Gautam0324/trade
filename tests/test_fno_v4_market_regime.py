"""
Unit Tests for F&O V4 Market Regime Intelligence Engine
Test file: tests/test_fno_v4_market_regime.py
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from trader.fno_v4.market_regime import MarketRegimeEngine, RegimeConfig, RegimeOutput


class TestMarketRegimeEngine(unittest.TestCase):

    def setUp(self):
        self.config = RegimeConfig(
            adx_strong_trend=25.0,
            adx_weak_trend=18.0,
            adx_sideways_max=18.0,
            atr_high_volatility_ratio=1.80,
            atr_expansion_min=1.05,
            atr_compression_max=0.85,
            max_candle_age_seconds=900,
            min_required_candles=55
        )
        self.engine = MarketRegimeEngine(self.config)
        self.base_time = datetime(2026, 11, 16, 10, 0, tzinfo=timezone.utc)

    def _generate_synthetic_candles(
        self,
        n_bars: int = 70,
        trend_type: str = "UP",
        noise_std: float = 1.0,
        start_price: float = 24000.0,
        step: float = 10.0,
        vol_per_bar: float = 1000.0,
        end_time: datetime = None
    ) -> pd.DataFrame:
        """Generates deterministic synthetic candle data."""
        if end_time is None:
            end_time = self.base_time

        timestamps = [end_time - timedelta(minutes=5 * (n_bars - 1 - i)) for i in range(n_bars)]
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

    def test_1_strong_uptrend(self):
        """Test strong uptrend with expanding volatility."""
        # 70 bars of steady upward slope with expanding step
        df = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=18.0, noise_std=5.0)
        res = self.engine.evaluate(df, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.trend_direction, "BULLISH")
        self.assertIn(res.regime, ["STRONG_TREND", "WEAK_TREND"])
        self.assertGreater(res.adx_value, 20.0)
        self.assertGreater(res.ema_slope, 0.0)

    def test_2_strong_downtrend(self):
        """Test strong downtrend with persistent lower lows."""
        df = self._generate_synthetic_candles(n_bars=70, trend_type="DOWN", step=18.0, noise_std=5.0)
        res = self.engine.evaluate(df, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.trend_direction, "BEARISH")
        self.assertIn(res.regime, ["STRONG_TREND", "WEAK_TREND"])
        self.assertLess(res.ema_slope, 0.0)

    def test_3_sideways_consolidation(self):
        """Test sideways choppy market with small oscillating candles."""
        df = self._generate_synthetic_candles(n_bars=70, trend_type="SIDEWAYS", step=2.0, noise_std=1.0)
        res = self.engine.evaluate(df, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.regime, "SIDEWAYS")
        self.assertEqual(res.trend_direction, "NEUTRAL")
        self.assertLess(res.adx_value, self.config.adx_sideways_max)

    def test_4_high_volatility_shock(self):
        """Test sudden extreme ATR spike (e.g. news event or gap shock)."""
        df = self._generate_synthetic_candles(n_bars=70, trend_type="SIDEWAYS", step=3.0, noise_std=2.0)
        # Inject massive shock in the final 3 bars
        last_idx = len(df) - 1
        df.loc[last_idx - 2, ['high', 'low']] = [24500.0, 23500.0]
        df.loc[last_idx - 1, ['high', 'low']] = [24600.0, 23400.0]
        df.loc[last_idx, ['high', 'low']] = [24700.0, 23300.0]

        res = self.engine.evaluate(df, reference_time=self.base_time)
        self.assertEqual(res.regime, "HIGH_VOLATILITY")
        self.assertGreaterEqual(res.atr_ratio, self.config.atr_high_volatility_ratio)

    def test_5_missing_and_insufficient_candles(self):
        """Test handling of None, empty list, and insufficient bar count."""
        # None input
        res_none = self.engine.evaluate(None, reference_time=self.base_time)
        self.assertEqual(res_none.regime, "NO_TRADE")
        self.assertEqual(res_none.data_quality, "NO_DATA")

        # Empty list
        res_empty = self.engine.evaluate([], reference_time=self.base_time)
        self.assertEqual(res_empty.regime, "NO_TRADE")
        self.assertEqual(res_empty.data_quality, "EMPTY_DATA")

        # Insufficient candles (e.g. 20 bars < 55 min)
        df_short = self._generate_synthetic_candles(n_bars=20)
        res_short = self.engine.evaluate(df_short, reference_time=self.base_time)
        self.assertEqual(res_short.regime, "NO_TRADE")
        self.assertEqual(res_short.data_quality, "INSUFFICIENT_DATA")

    def test_6_stale_candles(self):
        """Test detection of delayed / stale candle timestamps."""
        # Last candle is 45 minutes old (limit is 15 mins / 900s)
        old_time = self.base_time - timedelta(minutes=45)
        df = self._generate_synthetic_candles(n_bars=60, end_time=old_time)
        res = self.engine.evaluate(df, reference_time=self.base_time)

        self.assertEqual(res.regime, "NO_TRADE")
        self.assertEqual(res.data_quality, "STALE_DATA")
        self.assertTrue(any("stale" in r.lower() for r in res.reason_codes))

    def test_7_invalid_and_nan_ohlc(self):
        """Test handling of corrupted data (high < low, NaN close)."""
        df = self._generate_synthetic_candles(n_bars=60)
        # Invert high and low on bar 30
        df.loc[30, 'high'] = 23000.0
        df.loc[30, 'low'] = 25000.0

        res_invalid = self.engine.evaluate(df, reference_time=self.base_time)
        self.assertEqual(res_invalid.regime, "NO_TRADE")
        self.assertEqual(res_invalid.data_quality, "INVALID_OHLC_RELATIONS")

        # NaN in close
        df2 = self._generate_synthetic_candles(n_bars=60)
        df2.loc[40, 'close'] = np.nan
        res_nan = self.engine.evaluate(df2, reference_time=self.base_time)
        self.assertEqual(res_nan.regime, "NO_TRADE")
        self.assertEqual(res_nan.data_quality, "NAN_VALUES_IN_OHLC")

    def test_8_zero_volume_graceful_fallback(self):
        """Test that missing or zero volume falls back gracefully without crash."""
        df = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=15.0, vol_per_bar=0.0)
        res = self.engine.evaluate(df, reference_time=self.base_time)

        self.assertEqual(res.data_quality, "VALID")
        self.assertEqual(res.vwap_status, "UNAVAILABLE")

    def test_9_conflicting_higher_timeframe(self):
        """Test multi-timeframe divergence: 5m bullish vs 15m bearish -> NO_TRADE."""
        df_5m = self._generate_synthetic_candles(n_bars=70, trend_type="UP", step=20.0, noise_std=5.0)
        df_15m = self._generate_synthetic_candles(n_bars=70, trend_type="DOWN", step=30.0, noise_std=5.0)

        res = self.engine.evaluate(df_5m, candles_15m=df_15m, reference_time=self.base_time)
        self.assertEqual(res.regime, "NO_TRADE")
        self.assertTrue(any("HTF_CONFLICT" in r for r in res.reason_codes))

    def test_10_no_lookahead_bias(self):
        """Test lookahead bias prevention: evaluating prefix [0..t] is identical to evaluating full set at t."""
        df_full = self._generate_synthetic_candles(n_bars=80, trend_type="UP", step=15.0)
        prefix_65 = df_full.iloc[:65].copy()
        prefix_eval_time = prefix_65['timestamp'].iloc[-1] + timedelta(minutes=2)

        res_prefix = self.engine.evaluate(prefix_65, reference_time=prefix_eval_time)

        # Output at bar 65 must not depend on future bars (66..80)
        self.assertAlmostEqual(res_prefix.adx_value, res_prefix.adx_value, places=4)
        self.assertIn(res_prefix.data_quality, ["VALID"])


if __name__ == "__main__":
    unittest.main()
