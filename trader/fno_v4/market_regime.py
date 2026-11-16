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

Hardened in Phase 2.1:
1. Strict use of completed candles only (no lookahead bias).
2. Mandatory 15m Higher Timeframe confirmation for production regime classification.
3. Intraday Session VWAP reset (resets at 09:15 IST / daily session boundary; never cross-session).
4. Robust timezone-aware staleness checking (IST/UTC normalized).
5. Explicit reason codes and data quality auditing.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone, time
from typing import Dict, List, Optional, Any, Union
import numpy as np
import pandas as pd


@dataclass
class RegimeConfig:
    """Configurable thresholds for regime classification."""
    # Multi-timeframe validation
    require_15m: bool = True  # Mandatory 15m HTF candle confirmation
    min_required_candles_5m: int = 55  # To cleanly seed EMA 50 & ADX 14
    min_required_candles_15m: int = 40  # To cleanly seed HTF EMA 50 & ADX 14
    
    # ADX thresholds
    adx_period: int = 14
    adx_strong_trend: float = 25.0
    adx_weak_trend: float = 18.0
    adx_sideways_max: float = 18.0
    
    # ATR & Volatility thresholds
    atr_period: int = 14
    atr_baseline_period: int = 20
    atr_expansion_min: float = 1.00
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
    max_candle_age_seconds_5m: int = 900  # 15 minutes max staleness for 5m bar
    max_candle_age_seconds_15m: int = 1800  # 30 minutes max staleness for 15m bar


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
    Hardened Market Regime Classifier for F&O V4.
    Evaluates completed 5m and mandatory 15m candles to classify market state.
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
            candles_5m: 5-minute completed candle series.
            candles_15m: 15-minute completed candle series (mandatory if config.require_15m=True).
            reference_time: Current evaluation time.
            
        Returns:
            RegimeOutput dataclass instance.
        """
        now_dt = reference_time or datetime.now(timezone.utc)
        eval_timestamp = now_dt.strftime("%Y-%m-%d %H:%M:%S UTC")

        # 1. Normalize 5m candles
        df_5m, dq_err_5m = self._normalize_candles(candles_5m, "5m")
        if dq_err_5m:
            return RegimeOutput(
                regime="NO_TRADE",
                confidence_score=0.0,
                trend_direction="NEUTRAL",
                adx_value=0.0,
                atr_value=0.0,
                atr_ratio=1.0,
                ema_slope=0.0,
                vwap_status="UNAVAILABLE",
                data_quality=dq_err_5m,
                reason_codes=[f"DATA_QUALITY_ERROR_5M: {dq_err_5m}"],
                evaluated_at=eval_timestamp,
                candle_timestamp=""
            )

        # 2. Check 5m minimum length
        if len(df_5m) < self.config.min_required_candles_5m:
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
                reason_codes=[f"Insufficient 5m candles: {len(df_5m)} < {self.config.min_required_candles_5m}"],
                evaluated_at=eval_timestamp,
                candle_timestamp=last_ts
            )

        # 3. 5m Staleness Check
        last_5m_time = df_5m['timestamp'].iloc[-1]
        age_5m = self._calculate_age_seconds(now_dt, last_5m_time)
        if age_5m is not None and age_5m > self.config.max_candle_age_seconds_5m:
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
                reason_codes=[f"5m candle data is stale: age {int(age_5m)}s > limit {self.config.max_candle_age_seconds_5m}s"],
                evaluated_at=eval_timestamp,
                candle_timestamp=str(last_5m_time)
            )

        # 4. Mandatory 15m HTF Validation
        htf_trend = "NEUTRAL"
        htf_reasons = []

        if self.config.require_15m:
            if candles_15m is None or len(candles_15m) == 0:
                return RegimeOutput(
                    regime="NO_TRADE",
                    confidence_score=0.0,
                    trend_direction="NEUTRAL",
                    adx_value=0.0,
                    atr_value=0.0,
                    atr_ratio=1.0,
                    ema_slope=0.0,
                    vwap_status="UNAVAILABLE",
                    data_quality="MISSING_15M_DATA",
                    reason_codes=["NO_TRADE: Mandatory 15m HTF candle confirmation is missing"],
                    evaluated_at=eval_timestamp,
                    candle_timestamp=str(last_5m_time)
                )

            df_15m, dq_err_15m = self._normalize_candles(candles_15m, "15m")
            if dq_err_15m:
                return RegimeOutput(
                    regime="NO_TRADE",
                    confidence_score=0.0,
                    trend_direction="NEUTRAL",
                    adx_value=0.0,
                    atr_value=0.0,
                    atr_ratio=1.0,
                    ema_slope=0.0,
                    vwap_status="UNAVAILABLE",
                    data_quality=f"INVALID_15M_DATA: {dq_err_15m}",
                    reason_codes=[f"NO_TRADE: 15m HTF data error: {dq_err_15m}"],
                    evaluated_at=eval_timestamp,
                    candle_timestamp=str(last_5m_time)
                )

            if len(df_15m) < self.config.min_required_candles_15m:
                return RegimeOutput(
                    regime="NO_TRADE",
                    confidence_score=0.0,
                    trend_direction="NEUTRAL",
                    adx_value=0.0,
                    atr_value=0.0,
                    atr_ratio=1.0,
                    ema_slope=0.0,
                    vwap_status="UNAVAILABLE",
                    data_quality="INSUFFICIENT_15M_DATA",
                    reason_codes=[f"NO_TRADE: Insufficient 15m candles: {len(df_15m)} < {self.config.min_required_candles_15m}"],
                    evaluated_at=eval_timestamp,
                    candle_timestamp=str(last_5m_time)
                )

            last_15m_time = df_15m['timestamp'].iloc[-1]
            age_15m = self._calculate_age_seconds(now_dt, last_15m_time)
            if age_15m is not None and age_15m > self.config.max_candle_age_seconds_15m:
                return RegimeOutput(
                    regime="NO_TRADE",
                    confidence_score=0.0,
                    trend_direction="NEUTRAL",
                    adx_value=0.0,
                    atr_value=0.0,
                    atr_ratio=1.0,
                    ema_slope=0.0,
                    vwap_status="UNAVAILABLE",
                    data_quality="STALE_15M_DATA",
                    reason_codes=[f"NO_TRADE: 15m candle is stale: age {int(age_15m)}s > limit {self.config.max_candle_age_seconds_15m}s"],
                    evaluated_at=eval_timestamp,
                    candle_timestamp=str(last_15m_time)
                )

            ind_15m = self._calculate_indicators(df_15m)
            if ind_15m is None:
                return RegimeOutput(
                    regime="NO_TRADE",
                    confidence_score=0.0,
                    trend_direction="NEUTRAL",
                    adx_value=0.0,
                    atr_value=0.0,
                    atr_ratio=1.0,
                    ema_slope=0.0,
                    vwap_status="UNAVAILABLE",
                    data_quality="INVALID_15M_INDICATORS",
                    reason_codes=["NO_TRADE: Failed to compute 15m technical indicators"],
                    evaluated_at=eval_timestamp,
                    candle_timestamp=str(last_15m_time)
                )

            htf_trend = ind_15m["trend_dir"]
            htf_reasons.append(f"15m_HTF_TREND: {htf_trend} (ADX {ind_15m['adx']:.1f})")

        # 5. Compute 5m Indicators
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
                candle_timestamp=str(last_5m_time)
            )

        # 6. Synthesize Regime Decision
        return self._classify_regime(
            ind=indicators_5m,
            htf_trend=htf_trend,
            htf_reasons=htf_reasons,
            eval_time=eval_timestamp,
            candle_time=str(last_5m_time)
        )

    def _calculate_age_seconds(self, ref_time: datetime, candle_time: pd.Timestamp) -> Optional[float]:
        """Calculates seconds between reference time and candle timestamp, normalizing timezones."""
        try:
            c_dt = candle_time.to_pydatetime() if isinstance(candle_time, pd.Timestamp) else candle_time
            if ref_time.tzinfo is not None and c_dt.tzinfo is not None:
                return (ref_time - c_dt).total_seconds()
            elif ref_time.tzinfo is None and c_dt.tzinfo is None:
                return (ref_time - c_dt).total_seconds()
            else:
                # One is naive, one is aware: normalize both to UTC
                r = ref_time.replace(tzinfo=timezone.utc) if ref_time.tzinfo is None else ref_time.astimezone(timezone.utc)
                c = c_dt.replace(tzinfo=timezone.utc) if c_dt.tzinfo is None else c_dt.astimezone(timezone.utc)
                return (r - c).total_seconds()
        except Exception:
            return None

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
            if len(candles) == 0:
                return None, "EMPTY_DATA"
            df = candles.copy()
        else:
            return None, "UNSUPPORTED_DATA_TYPE"

        req_cols = ['open', 'high', 'low', 'close']
        for col in req_cols:
            if col not in df.columns:
                return None, f"MISSING_COLUMN_{col.upper()}"

        if 'timestamp' not in df.columns:
            if 'datetime' in df.columns:
                df['timestamp'] = df['datetime']
            elif 'date' in df.columns:
                df['timestamp'] = df['date']
            elif isinstance(df.index, pd.DatetimeIndex):
                df['timestamp'] = df.index
            else:
                return None, "MISSING_TIMESTAMP_COLUMN"

        for col in req_cols:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        if 'volume' in df.columns:
            df['volume'] = pd.to_numeric(df['volume'], errors='coerce').fillna(0)
        else:
            df['volume'] = 0.0

        if df[req_cols].isna().any().any():
            return None, "NAN_VALUES_IN_OHLC"

        invalid_ohlc = (df['high'] < df['low']) | (df['high'] < df['open']) | (df['high'] < df['close']) | (df['low'] > df['open']) | (df['low'] > df['close'])
        if invalid_ohlc.any():
            return None, "INVALID_OHLC_RELATIONS"

        df['timestamp'] = pd.to_datetime(df['timestamp'])
        
        # Check and handle duplicates
        if df['timestamp'].duplicated().any():
            df = df.drop_duplicates(subset=['timestamp'], keep='last')

        df = df.sort_values('timestamp').reset_index(drop=True)
        return df, None

    def _calculate_indicators(self, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        """Calculates ADX, ATR, ATR ratio, EMA slopes, and Intraday Session VWAP."""
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

            # Normalized slope of EMA20
            lookback = self.config.ema_slope_bars
            ema_slope = (ema20.iloc[-1] - ema20.iloc[-1 - lookback]) / (ema20.iloc[-1 - lookback] * lookback + 1e-9)

            # --- Consolidation Width ---
            c_lookback = min(self.config.consolidation_lookback, n)
            range_high = high.iloc[-c_lookback:].max()
            range_low = low.iloc[-c_lookback:].min()
            consolidation_width = (range_high - range_low) / (atr14.iloc[-1] + 1e-9)

            # --- Correct Intraday Session VWAP ---
            vwap_status = "UNAVAILABLE"
            # Extract date for session boundary
            ts_series = df['timestamp']
            if ts_series.dt.tz is not None:
                # Convert to IST if tz-aware
                session_dates = ts_series.dt.tz_convert('Asia/Kolkata').dt.date
            else:
                session_dates = ts_series.dt.date

            current_session_date = session_dates.iloc[-1]
            session_mask = (session_dates == current_session_date)
            session_df = df[session_mask]

            session_vol = session_df['volume']
            if session_vol.sum() > 0:
                # Typical price = (H + L + C) / 3
                tp = (session_df['high'] + session_df['low'] + session_df['close']) / 3.0
                cum_vol = session_vol.cumsum()
                cum_pv = (tp * session_vol).cumsum()
                session_vwap = cum_pv / (cum_vol + 1e-9)
                curr_vwap = session_vwap.iloc[-1]
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
        if self.config.require_15m:
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
        slope_abs = abs(slope)
        slope_qualifies = (slope_abs >= self.config.ema_slope_threshold)
        vol_expanding = (atr_ratio >= self.config.atr_expansion_min - 1e-4)

        if adx >= self.config.adx_strong_trend and trend_dir != "NEUTRAL":
            if slope_qualifies and vol_expanding:
                confidence = 0.85
                reasons.append(f"STRONG_TREND: ADX {adx:.1f} >= {self.config.adx_strong_trend}, Slope {slope:.5f}, ATR Ratio {atr_ratio:.2f}")

                # If 15m is required, check if HTF confirms or is neutral
                if self.config.require_15m:
                    if htf_trend == trend_dir:
                        confidence = 0.95
                        reasons.append(f"HTF_CONFIRMED: 15m aligns with 5m ({trend_dir})")
                    elif htf_trend == "NEUTRAL":
                        # HTF hasn't confirmed yet -> downgrade to WEAK_TREND
                        reasons.append("HTF_NEUTRAL: 15m has not confirmed strong directional momentum")
                        return RegimeOutput(
                            regime="WEAK_TREND",
                            confidence_score=0.65,
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
