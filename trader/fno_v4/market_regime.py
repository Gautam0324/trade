"""
F&O INTELLIGENCE ENGINE V4 - MARKET REGIME INTELLIGENCE ENGINE
Module: trader/fno_v4/market_regime.py

Deterministic market regime classifier for Indian Index Options (NIFTY / BANKNIFTY).
Classifies completed candle series into:
- STRONG_TREND
- WEAK_TREND
- SIDEWAYS
- HIGH_VOLATILITY
- NO_TRADE

Adheres to:
1. Strict use of completed candles only (no lookahead bias).
2. Multi-timeframe synthesis (15m primary trend context + 5m confirmation).
3. Configurable statistical thresholds.
4. Robust data quality & staleness validation.
5. Structured, auditable output contract.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Union
import numpy as np
import pandas as pd


@dataclass
class RegimeConfig:
    """Configurable thresholds for regime classification."""
    # ADX thresholds
    adx_period: int = 14
    adx_strong_trend: float = 25.0
    adx_weak_trend: float = 18.0
    adx_sideways_max: float = 18.0
    
    # ATR & Volatility thresholds
    atr_period: int = 14
    atr_baseline_period: int = 20
    atr_expansion_min: float = 1.05
    atr_high_volatility_ratio: float = 1.80
    atr_compression_max: float = 0.85
    
    # EMA Trend filters
    ema_fast: int = 20
    ema_slow: int = 50
    ema_slope_bars: int = 3
    ema_slope_threshold: float = 0.00015  # 0.015% slope per bar
    
    # Range / Consolidation
    consolidation_lookback: int = 20
    consolidation_width_threshold: float = 2.5  # Range / ATR
    
    # VWAP threshold (basis points)
    vwap_near_threshold_bps: float = 25.0  # 0.25% distance
    
    # Staleness & Data Quality
    max_candle_age_seconds: int = 900  # 15 minutes max staleness for 5m bar
    min_required_candles: int = 55  # To cleanly seed EMA 50 & ADX 14


@dataclass
class RegimeOutput:
    """Standardized output contract for regime evaluation."""
    regime: str  # STRONG_TREND, WEAK_TREND, SIDEWAYS, HIGH_VOLATILITY, NO_TRADE
    confidence_score: float  # 0.0 to 1.0
    trend_direction: str  # BULLISH, BEARISH, NEUTRAL
    adx_value: float
    atr_value: float
    atr_ratio: float
    ema_slope: float
    vwap_status: str  # ABOVE, BELOW, NEAR, UNAVAILABLE
    data_quality: str  # VALID, INSUFFICIENT_DATA, STALE_DATA, INVALID_DATA
    reason_codes: List[str] = field(default_factory=list)
    evaluated_at: str = ""
    candle_timestamp: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "regime": self.regime,
            "confidence_score": round(self.confidence_score, 2),
            "trend_direction": self.trend_direction,
            "adx_value": round(self.adx_value, 2),
            "atr_value": round(self.atr_value, 2),
            "atr_ratio": round(self.atr_ratio, 2),
            "ema_slope": round(self.ema_slope, 6),
            "vwap_status": self.vwap_status,
            "data_quality": self.data_quality,
            "reason_codes": self.reason_codes,
            "evaluated_at": self.evaluated_at,
            "candle_timestamp": self.candle_timestamp
        }


class MarketRegimeEngine:
    """
    Independent Market Regime Classifier for F&O V4.
    Evaluates completed 5m and optional 15m candles to classify market state.
    """

    def __init__(self, config: Optional[RegimeConfig] = None):
        self.config = config or RegimeConfig()

    def evaluate(
        self,
        candles_5m: Union[pd.DataFrame, List[Dict[str, Any]]],
        candles_15m: Optional[Union[pd.DataFrame, List[Dict[str, Any]]]] = None,
        reference_time: Optional[datetime] = None
    ) -> RegimeOutput:
        """
        Main entry point for regime classification.
        
        Args:
            candles_5m: 5-minute candle series (DataFrame or list of dicts).
            candles_15m: Optional 15-minute candle series for higher timeframe context.
            reference_time: Current evaluation time (defaults to utcnow).
            
        Returns:
            RegimeOutput dataclass instance.
        """
        now_dt = reference_time or datetime.now(timezone.utc)
        eval_timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        # 1. Normalize 5m candles
        df_5m, dq_err = self._normalize_candles(candles_5m, "5m")
        if dq_err:
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.0,
                trend_direction="NEUTRAL",
                adx_value=0.0,
                atr_value=0.0,
                atr_ratio=1.0,
                ema_slope=0.0,
                vwap_status="UNAVAILABLE",
                data_quality=dq_err,
                reason_codes=[f"DATA_QUALITY_ERROR: {dq_err}"],
                evaluated_at=eval_timestamp,
                candle_timestamp=""
            )

        # 2. Check minimum length
        if len(df_5m) < self.config.min_required_candles:
            last_ts = str(df_5m['timestamp'].iloc[-1]) if len(df_5m) > 0 else ""
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.0,
                trend_direction="NEUTRAL",
                adx_value=0.0,
                atr_value=0.0,
                atr_ratio=1.0,
                ema_slope=0.0,
                vwap_status="UNAVAILABLE",
                data_quality="INSUFFICIENT_DATA",
                reason_codes=[f"Insufficient 5m candles: {len(df_5m)} < {self.config.min_required_candles}"],
                evaluated_at=eval_timestamp,
                candle_timestamp=last_ts
            )

        # 3. Staleness Check
        last_candle_time = pd.to_datetime(df_5m['timestamp'].iloc[-1])
        if last_candle_time.tzinfo is None:
            # Assume UTC if naive
            last_candle_time = last_candle_time.replace(tzinfo=timezone.utc)
        
        # If reference_time is provided with or without tz, normalize
        ref_cmp = now_dt if now_dt.tzinfo is not None else now_dt.replace(tzinfo=timezone.utc)
        age_seconds = (ref_cmp - last_candle_time).total_seconds()
        
        if age_seconds > self.config.max_candle_age_seconds:
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.0,
                trend_direction="NEUTRAL",
                adx_value=0.0,
                atr_value=0.0,
                atr_ratio=1.0,
                ema_slope=0.0,
                vwap_status="UNAVAILABLE",
                data_quality="STALE_DATA",
                reason_codes=[f"Candle data is stale: age {int(age_seconds)}s > limit {self.config.max_candle_age_seconds}s"],
                evaluated_at=eval_timestamp,
                candle_timestamp=str(df_5m['timestamp'].iloc[-1])
            )

        # 4. Compute 5m Indicators
        indicators_5m = self._calculate_indicators(df_5m)
        if indicators_5m is None:
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.0,
                trend_direction="NEUTRAL",
                adx_value=0.0,
                atr_value=0.0,
                atr_ratio=1.0,
                ema_slope=0.0,
                vwap_status="UNAVAILABLE",
                data_quality="INVALID_DATA",
                reason_codes=["Failed to calculate technical indicators due to NaN/Inf values"],
                evaluated_at=eval_timestamp,
                candle_timestamp=str(df_5m['timestamp'].iloc[-1])
            )

        # 5. Evaluate 15m HTF Context if provided
        htf_trend = "NEUTRAL"
        htf_reasons = []
        if candles_15m is not None:
            df_15m, dq_err_15 = self._normalize_candles(candles_15m, "15m")
            if not dq_err_15 and len(df_15m) >= self.config.min_required_candles:
                ind_15m = self._calculate_indicators(df_15m)
                if ind_15m:
                    htf_trend = ind_15m["trend_dir"]
                    htf_reasons.append(f"15m_HTF_TREND: {htf_trend}")
            else:
                htf_reasons.append("15m_HTF_UNAVAILABLE_OR_INSUFFICIENT")

        # 6. Synthesize Regime Decision
        return self._classify_regime(
            ind=indicators_5m,
            htf_trend=htf_trend,
            htf_reasons=htf_reasons,
            eval_time=eval_timestamp,
            candle_time=str(df_5m['timestamp'].iloc[-1])
        )

    def _normalize_candles(
        self,
        candles: Union[pd.DataFrame, List[Dict[str, Any]]],
        label: str
    ) -> tuple[Optional[pd.DataFrame], Optional[str]]:
        """Validates and formats candles into standard OHLCV DataFrame."""
        if candles is None:
            return None, "NO_DATA"

        if isinstance(candles, list):
            if len(candles) == 0:
                return None, "EMPTY_DATA"
            df = pd.DataFrame(candles)
        elif isinstance(candles, pd.DataFrame):
            if df_len := len(candles) == 0:
                return None, "EMPTY_DATA"
            df = candles.copy()
        else:
            return None, "UNSUPPORTED_DATA_TYPE"

        # Required columns check
        req_cols = ['open', 'high', 'low', 'close']
        for col in req_cols:
            if col not in df.columns:
                return None, f"MISSING_COLUMN_{col.upper()}"

        # Standardize timestamp
        if 'timestamp' not in df.columns:
            if 'datetime' in df.columns:
                df['timestamp'] = df['datetime']
            elif 'date' in df.columns:
                df['timestamp'] = df['date']
            elif isinstance(df.index, pd.DatetimeIndex):
                df['timestamp'] = df.index
            else:
                return None, "MISSING_TIMESTAMP_COLUMN"

        # Clean numerical values
        for col in req_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        else:
            df['volume'] = 0.0

        # Discard NaN in OHLC
        if df[req_cols].isna().any().any():
            return None, "NAN_VALUES_IN_OHLC"

        # OHLC logic validity
        invalid_ohlc = (df['high'] < df['low']) | (df['high'] < df['open']) | (df['high'] < df['close']) | (df['low'] > df['open']) | (df['low'] > df['close'])
        if invalid_ohlc.any():
            return None, "INVALID_OHLC_RELATIONS"

        # Monotonic time check
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.sort_values('timestamp').reset_index(drop=True)

        return df, None

    def _calculate_indicators(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Calculates ADX, ATR, ATR ratio, EMA slopes, and VWAP distance."""
        try:
            high = df['high']
            low = df['low']
            close = df['close']
            vol = df['volume']
            n = len(df)

            # --- ATR(14) & ATR Ratio ---
            tr1 = high - low
            tr2 = (high - close.shift(1)).abs()
            tr3 = (low - close.shift(1)).abs()
            tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            # Wilder's Smoothing for ATR
            atr14 = tr.ewm(alpha=1.0 / self.config.atr_period, adjust=False).mean()
            atr_sma20 = atr14.rolling(self.config.atr_baseline_period).mean()
            atr_ratio = atr14 / (atr_sma20 + 1e-9)

            # --- ADX(14) ---
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low
            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

            plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1.0 / self.config.adx_period, adjust=False).mean() / (atr14 + 1e-9))
            minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1.0 / self.config.adx_period, adjust=False).mean() / (atr14 + 1e-9))
            dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
            adx14 = dx.ewm(alpha=1.0 / self.config.adx_period, adjust=False).mean()

            # --- EMAs (20 & 50) ---
            ema20 = close.ewm(span=self.config.ema_fast, adjust=False).mean()
            ema50 = close.ewm(span=self.config.ema_slow, adjust=False).mean()

            # Normalized slope of EMA20 (% change per bar over slope lookback)
            lookback = self.config.ema_slope_bars
            ema_slope = (ema20.iloc[-1] - ema20.iloc[-1 - lookback]) / (ema20.iloc[-1 - lookback] * lookback + 1e-9)

            # --- Consolidation Width ---
            c_lookback = min(self.config.consolidation_lookback, n)
            range_high = high.iloc[-c_lookback:].max()
            range_low = low.iloc[-c_lookback:].min()
            consolidation_width = (range_high - range_low) / (atr14.iloc[-1] + 1e-9)

            # --- VWAP (only if volume > 0) ---
            vwap_status = "UNAVAILABLE"
            has_vol = (vol.iloc[-c_lookback:].sum() > 0)
            if has_vol:
                cum_vol = vol.cumsum()
                cum_pv = (close * vol).cumsum()
                vwap = cum_pv / (cum_vol + 1e-9)
                curr_vwap = vwap.iloc[-1]
                curr_close = close.iloc[-1]
                dist_bps = ((curr_close - curr_vwap) / curr_vwap) * 10000.0

                if abs(dist_bps) <= self.config.vwap_near_threshold_bps:
                    vwap_status = "NEAR"
                elif dist_bps > 0:
                    vwap_status = "ABOVE"
                else:
                    vwap_status = "BELOW"

            # --- Directional Bias ---
            last_pdi = plus_di.iloc[-1]
            last_mdi = minus_di.iloc[-1]
            last_close = close.iloc[-1]
            last_ema20 = ema20.iloc[-1]
            last_ema50 = ema50.iloc[-1]

            is_bullish = (last_close > last_ema20 > last_ema50) and (last_pdi > last_mdi) and (ema_slope > 0)
            is_bearish = (last_close < last_ema20 < last_ema50) and (last_mdi > last_pdi) and (ema_slope < 0)

            trend_dir = "BULLISH" if is_bullish else ("BEARISH" if is_bearish else "NEUTRAL")

            return {
                "adx": float(adx14.iloc[-1]),
                "plus_di": float(last_pdi),
                "minus_di": float(last_mdi),
                "atr": float(atr14.iloc[-1]),
                "atr_ratio": float(atr_ratio.iloc[-1]),
                "ema20": float(last_ema20),
                "ema50": float(last_ema50),
                "ema_slope": float(ema_slope),
                "consolidation_width": float(consolidation_width),
                "vwap_status": vwap_status,
                "trend_dir": trend_dir,
                "close": float(last_close)
            }
        except Exception:
            return None

    def _classify_regime(
        self,
        ind: Dict[str, Any],
        htf_trend: str,
        htf_reasons: List[str],
        eval_time: str,
        candle_time: str
    ) -> RegimeOutput:
        """Applies deterministic rules to classify regime."""
        adx = ind["adx"]
        atr = ind["atr"]
        atr_ratio = ind["atr_ratio"]
        slope = ind["ema_slope"]
        trend_dir = ind["trend_dir"]
        c_width = ind["consolidation_width"]
        vwap_status = ind["vwap_status"]

        reasons = list(htf_reasons)
        confidence = 0.50

        # Rule 1: High Volatility Shock / Extreme Expansion
        if atr_ratio >= self.config.atr_high_volatility_ratio:
            reasons.append(f"HIGH_VOLATILITY: ATR Ratio {atr_ratio:.2f} >= {self.config.atr_high_volatility_ratio}")
            return RegimeOutput(
                regime="HIGH_VOLATILITY",
                confidence_score=0.85,
                trend_direction=trend_dir,
                adx_value=adx,
                atr_value=atr,
                atr_ratio=atr_ratio,
                ema_slope=slope,
                vwap_status=vwap_status,
                data_quality="VALID",
                reason_codes=reasons,
                evaluated_at=eval_time,
                candle_timestamp=candle_time
            )

        # Rule 2: Low-Volatility Sideways Consolidation / Chop
        is_sideways_adx = (adx < self.config.adx_sideways_max)
        is_contracted_atr = (atr_ratio <= self.config.atr_compression_max)
        is_tight_range = (c_width < self.config.consolidation_width_threshold)

        if is_sideways_adx and (is_contracted_atr or is_tight_range or trend_dir == "NEUTRAL"):
            reasons.append(f"SIDEWAYS: ADX {adx:.1f} < {self.config.adx_sideways_max} with Range Width {c_width:.1f}x ATR")
            if is_contracted_atr:
                reasons.append(f"VOLATILITY_COMPRESSION: ATR Ratio {atr_ratio:.2f} <= {self.config.atr_compression_max}")
            return RegimeOutput(
                regime="SIDEWAYS",
                confidence_score=0.80,
                trend_direction="NEUTRAL",
                adx_value=adx,
                atr_value=atr,
                atr_ratio=atr_ratio,
                ema_slope=slope,
                vwap_status=vwap_status,
                data_quality="VALID",
                reason_codes=reasons,
                evaluated_at=eval_time,
                candle_timestamp=candle_time
            )

        # Rule 2b: Multi-Timeframe Divergence Conflict
        if htf_trend != "NEUTRAL" and trend_dir != "NEUTRAL" and htf_trend != trend_dir:
            reasons.append(f"HTF_CONFLICT: 15m Trend ({htf_trend}) opposes 5m ({trend_dir})")
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.35,
                trend_direction=trend_dir,
                adx_value=adx,
                atr_value=atr,
                atr_ratio=atr_ratio,
                ema_slope=slope,
                vwap_status=vwap_status,
                data_quality="VALID",
                reason_codes=reasons,
                evaluated_at=eval_time,
                candle_timestamp=candle_time
            )

        # Rule 3: Strong Trend
        # Requires: ADX >= 25, Clean Direction, EMA Slope above threshold, and Volatility Expanding
        slope_abs = abs(slope)
        slope_qualifies = (slope_abs >= self.config.ema_slope_threshold)
        vol_expanding = (atr_ratio >= self.config.atr_expansion_min)

        if adx >= self.config.adx_strong_trend and trend_dir != "NEUTRAL":
            if slope_qualifies and vol_expanding:
                confidence = 0.85
                reasons.append(f"STRONG_TREND: ADX {adx:.1f} >= {self.config.adx_strong_trend}, Slope {slope:.5f}, ATR Ratio {atr_ratio:.2f}")

                # Boost or penalize based on HTF agreement
                if htf_trend != "NEUTRAL":
                    if htf_trend == trend_dir:
                        confidence = 0.95
                        reasons.append(f"HTF_CONFLUENCE: 15m Trend agrees ({htf_trend})")
                    else:
                        # Conflicting HTF
                        reasons.append(f"HTF_CONFLICT: 15m Trend ({htf_trend}) opposes 5m ({trend_dir})")
                        return RegimeOutput(
                            regime="NO_TRADE",
                            confidence_score=0.40,
                            trend_direction=trend_dir,
                            adx_value=adx,
                            atr_value=atr,
                            atr_ratio=atr_ratio,
                            ema_slope=slope,
                            vwap_status=vwap_status,
                            data_quality="VALID",
                            reason_codes=reasons,
                            evaluated_at=eval_time,
                            candle_timestamp=candle_time
                        )

                return RegimeOutput(
                    regime="STRONG_TREND",
                    confidence_score=confidence,
                    trend_direction=trend_dir,
                    adx_value=adx,
                    atr_value=atr,
                    atr_ratio=atr_ratio,
                    ema_slope=slope,
                    vwap_status=vwap_status,
                    data_quality="VALID",
                    reason_codes=reasons,
                    evaluated_at=eval_time,
                    candle_timestamp=candle_time
                )
            else:
                # ADX high but momentum/volatility stalling -> WEAK_TREND
                reasons.append(f"WEAK_TREND: High ADX ({adx:.1f}) but sluggish slope or contracting ATR")
                return RegimeOutput(
                    regime="WEAK_TREND",
                    confidence_score=0.60,
                    trend_direction=trend_dir,
                    adx_value=adx,
                    atr_value=atr,
                    atr_ratio=atr_ratio,
                    ema_slope=slope,
                    vwap_status=vwap_status,
                    data_quality="VALID",
                    reason_codes=reasons,
                    evaluated_at=eval_time,
                    candle_timestamp=candle_time
                )

        # Rule 4: Weak Trend
        if adx >= self.config.adx_weak_trend and trend_dir != "NEUTRAL":
            reasons.append(f"WEAK_TREND: Moderate ADX {adx:.1f} ({self.config.adx_weak_trend} - {self.config.adx_strong_trend})")
            return RegimeOutput(
                regime="WEAK_TREND",
                confidence_score=0.55,
                trend_direction=trend_dir,
                adx_value=adx,
                atr_value=atr,
                atr_ratio=atr_ratio,
                ema_slope=slope,
                vwap_status=vwap_status,
                data_quality="VALID",
                reason_codes=reasons,
                evaluated_at=eval_time,
                candle_timestamp=candle_time
            )

        # Rule 5: Default Fallback -> NO_TRADE
        reasons.append("NO_TRADE: Indecisive market structure, conflicting signals, or transitional chop")
        return RegimeOutput(
            regime="NO_TRADE",
            confidence_score=0.30,
            trend_direction=trend_dir,
            adx_value=adx,
            atr_value=atr,
            atr_ratio=atr_ratio,
            ema_slope=slope,
            vwap_status=vwap_status,
            data_quality="VALID",
            reason_codes=reasons,
            evaluated_at=eval_time,
            candle_timestamp=candle_time
        )
