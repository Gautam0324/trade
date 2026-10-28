#!/usr/bin/env python3
"""
Centralized Market Regime Detection Engine.
Identifies:
- TRENDING_UP: Bullish trend structure (EMA alignment, RSI momentum, ADX strength)
- TRENDING_DOWN: Bearish trend structure (EMA alignment, RSI bearish, ADX strength)
- RANGE_BOUND: Low ADX / low directional movement, oscillating around mean
- HIGH_VOLATILITY: ATR exceeds 2.2x rolling 20-period baseline
- LOW_LIQUIDITY: Relative volume < 0.4x
- UNKNOWN: Insufficient data or contradictory metrics

Explainable, deterministic indicators only.
"""

from pathlib import Path
import sys

TRADER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADER_DIR))

import indicators

def detect_market_regime(highs, lows, closes, volumes, benchmark_closes=None, adx_threshold=22.0):
    """
    Evaluates market regime on completed OHLCV arrays.
    Returns:
    {
      "regime": "TRENDING_UP" | "TRENDING_DOWN" | "RANGE_BOUND" | "HIGH_VOLATILITY" | "LOW_LIQUIDITY" | "UNKNOWN",
      "adx": float,
      "rsi": float,
      "atr": float,
      "rvol": float,
      "ema_alignment": "BULL" | "BEAR" | "MIXED",
      "summary": str
    }
    """
    n = len(closes)
    if n < 35:
        return {
            "regime": "UNKNOWN",
            "adx": 0.0,
            "rsi": 50.0,
            "atr": 0.0,
            "rvol": 1.0,
            "ema_alignment": "MIXED",
            "summary": "Insufficient bars (< 35) to assess regime"
        }

    ema9 = indicators.calculate_ema(closes, 9)[-1]
    ema33 = indicators.calculate_ema(closes, 33)[-1]
    rsi = indicators.calculate_rsi(closes, 14)[-1] or 50.0
    adx_dict = indicators.calculate_adx(highs, lows, closes, 14)
    adx = adx_dict.get("adx", [15.0])[-1] or 15.0
    plus_di = adx_dict.get("plus_di", [15.0])[-1] or 15.0
    minus_di = adx_dict.get("minus_di", [15.0])[-1] or 15.0

    atr_series = indicators.calculate_atr(highs, lows, closes, 14)
    atr = atr_series[-1] or (closes[-1] * 0.01)

    rvol_series = indicators.calculate_relative_volume(volumes, 20)
    rvol = rvol_series[-1] if (rvol_series and isinstance(rvol_series, list)) else 1.0
    if rvol is None:
        rvol = 1.0

    # Volatility expansion check
    valid_atrs = [a for a in atr_series[-25:-1] if a is not None]
    avg_atr = sum(valid_atrs) / len(valid_atrs) if valid_atrs else atr
    is_high_vol = atr > (avg_atr * 2.2) if avg_atr > 0 else False

    # Alignment
    if ema9 and ema33:
        if ema9 > ema33:
            ema_align = "BULL"
        elif ema9 < ema33:
            ema_align = "BEAR"
        else:
            ema_align = "MIXED"
    else:
        ema_align = "MIXED"

    # Regime decision rules
    if rvol < 0.35:
        regime = "LOW_LIQUIDITY"
        summary = f"Volume dry-up (RVol: {rvol:.2f}x < 0.35x)"
    elif is_high_vol:
        regime = "HIGH_VOLATILITY"
        summary = f"High volatility shock (ATR: {atr:.2f} > 2.2x avg {avg_atr:.2f})"
    elif adx < 18.0:
        regime = "RANGE_BOUND"
        summary = f"Range-bound consolidation (ADX: {adx:.1f} < 18.0)"
    elif ema_align == "BULL" and rsi > 50.0 and plus_di > minus_di:
        regime = "TRENDING_UP"
        summary = f"Bullish trend intact (EMA9 > EMA33, RSI: {rsi:.1f}, +DI > -DI, ADX: {adx:.1f})"
    elif ema_align == "BEAR" and rsi < 50.0 and minus_di > plus_di:
        regime = "TRENDING_DOWN"
        summary = f"Bearish trend intact (EMA9 < EMA33, RSI: {rsi:.1f}, -DI > +DI, ADX: {adx:.1f})"
    elif adx >= adx_threshold and plus_di > minus_di:
        regime = "TRENDING_UP"
        summary = f"Emerging uptrend (ADX {adx:.1f} >= {adx_threshold}, +DI > -DI)"
    elif adx >= adx_threshold and minus_di > plus_di:
        regime = "TRENDING_DOWN"
        summary = f"Emerging downtrend (ADX {adx:.1f} >= {adx_threshold}, -DI > +DI)"
    else:
        regime = "RANGE_BOUND"
        summary = f"Sideways / choppy action (ADX: {adx:.1f}, RSI: {rsi:.1f})"

    return {
        "regime": regime,
        "adx": round(adx, 2),
        "rsi": round(rsi, 2),
        "atr": round(atr, 2),
        "rvol": round(rvol, 2),
        "ema_alignment": ema_align,
        "summary": summary
    }
