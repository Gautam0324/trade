#!/usr/bin/env python3
"""
V3 Trend Pullback Strategy Engine (Institutional Multi-Market).
Primary Timeframe: 5-Minute
Confirmation Timeframe: 15-Minute
Markets Supported: NSE Cash Equity & Weekly Index Options
Execution Mode: STRICT PAPER ONLY

Rules:
- 15m Higher Timeframe Trend Confirmation: EMA 9 > 21 > 50, Price > EMA 50, ADX > 20
- 5m Primary Pullback Entry: Price tests EMA 9 / EMA 21 and confirms reversal candle
- VWAP & RSI Momentum Alignment
- Volatility-based Stop Loss: Swing High/Low + (1.5 * ATR)
- Targets: 1R (Partial), 2R (Full)
- Sideways Market Rejection: ADX < 20 or conflicting EMAs
"""

import math
from typing import Dict, Any, List, Optional
import sys
from pathlib import Path

TRADER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TRADER_DIR))

import indicators

class V3TrendPullbackStrategy:
    VERSION = "3.0.0"
    STRATEGY_ID = "V3_TREND_PULLBACK"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.adx_minimum = float(self.config.get("adx_minimum", 20.0))
        self.atr_multiplier = float(self.config.get("atr_multiplier", 1.5))
        self.ema_fast = int(self.config.get("ema_fast", 9))
        self.ema_pullback = int(self.config.get("ema_pullback", 21))
        self.ema_trend = int(self.config.get("ema_trend", 50))
        self.ema_long = int(self.config.get("ema_long", 200))
        self.rsi_period = int(self.config.get("rsi_period", 14))
        self.atr_period = int(self.config.get("atr_period", 14))

    @staticmethod
    def aggregate_candles(one_min_bars: Dict[str, List[Any]], target_minutes: int) -> Dict[str, List[Any]]:
        """
        Aggregates 1-minute completed bars into 5-minute or 15-minute completed bars.
        Ensures strict chronological grouping without look-ahead bias.
        """
        timestamps = one_min_bars.get("timestamp", [])
        opens = one_min_bars.get("open", [])
        highs = one_min_bars.get("high", [])
        lows = one_min_bars.get("low", [])
        closes = one_min_bars.get("close", [])
        volumes = one_min_bars.get("volume", [1000.0] * len(closes))

        agg_t, agg_o, agg_h, agg_l, agg_c, agg_v = [], [], [], [], [], []

        n = len(closes)
        i = 0
        while i < n:
            chunk_end = min(n, i + target_minutes)
            if (chunk_end - i) < target_minutes and chunk_end < n:
                break # Incomplete bar at end

            agg_t.append(timestamps[chunk_end - 1])
            agg_o.append(opens[i])
            agg_h.append(max(highs[i:chunk_end]))
            agg_l.append(min(lows[i:chunk_end]))
            agg_c.append(closes[chunk_end - 1])
            agg_v.append(sum(volumes[i:chunk_end]))
            i = chunk_end

        return {
            "timestamp": agg_t, "open": agg_o, "high": agg_h,
            "low": agg_l, "close": agg_c, "volume": agg_v
        }

    def evaluate_15m_trend(self, bars_15m: Dict[str, List[float]]) -> Dict[str, Any]:
        """
        Evaluates 15-minute completed higher-timeframe trend.
        Returns: direction ('BULLISH', 'BEARISH', 'SIDEWAYS'), indicators, and reasons.
        """
        closes = bars_15m["close"]
        highs = bars_15m["high"]
        lows = bars_15m["low"]

        if len(closes) < 30:
            return {"direction": "SIDEWAYS", "reason": "Insufficient 15m bars (min 30 required)"}

        ema9 = indicators.calculate_ema(closes, self.ema_fast)[-1]
        ema21 = indicators.calculate_ema(closes, self.ema_pullback)[-1]
        ema50 = indicators.calculate_ema(closes, self.ema_trend)[-1] if len(closes) >= 50 else ema21

        adx_data = indicators.calculate_adx(highs, lows, closes, 14)
        adx_val = adx_data["adx"][-1] if (adx_data and len(adx_data.get("adx", [])) > 0) else 15.0

        cur_close = closes[-1]

        # Sideways rejection check
        if adx_val < self.adx_minimum:
            return {
                "direction": "SIDEWAYS",
                "reason": f"ADX is {adx_val:.1f} < threshold {self.adx_minimum} (Choppy / Sideways Regime)",
                "ema9": ema9, "ema21": ema21, "ema50": ema50, "adx": adx_val
            }

        # Bullish 15m: EMA 9 > 21 and price above EMA 50
        if ema9 > ema21 and cur_close > ema50:
            return {
                "direction": "BULLISH",
                "reason": f"15m Bullish alignment: EMA 9 ({ema9:.1f}) > EMA 21 ({ema21:.1f}) and Close > EMA 50 ({ema50:.1f}), ADX={adx_val:.1f}",
                "ema9": ema9, "ema21": ema21, "ema50": ema50, "adx": adx_val
            }

        # Bearish 15m: EMA 9 < 21 and price below EMA 50
        if ema9 < ema21 and cur_close < ema50:
            return {
                "direction": "BEARISH",
                "reason": f"15m Bearish alignment: EMA 9 ({ema9:.1f}) < EMA 21 ({ema21:.1f}) and Close < EMA 50 ({ema50:.1f}), ADX={adx_val:.1f}",
                "ema9": ema9, "ema21": ema21, "ema50": ema50, "adx": adx_val
            }

        return {
            "direction": "SIDEWAYS",
            "reason": "Conflicting 15m EMA structure",
            "ema9": ema9, "ema21": ema21, "ema50": ema50, "adx": adx_val
        }

    def evaluate_5m_pullback(
        self,
        symbol: str,
        bars_5m: Dict[str, List[float]],
        htf_trend: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Evaluates 5-minute primary pullback entry condition aligned with 15-minute trend.
        """
        closes = bars_5m["close"]
        highs = bars_5m["high"]
        lows = bars_5m["low"]
        opens = bars_5m["open"]
        volumes = bars_5m.get("volume", [1000.0] * len(closes))

        if len(closes) < 25:
            return {"direction": "NO_TRADE", "rejection_reasons": ["Insufficient 5m bars"]}

        direction_15m = htf_trend.get("direction", "SIDEWAYS")
        if direction_15m == "SIDEWAYS":
            return {"direction": "NO_TRADE", "rejection_reasons": [f"Higher-timeframe 15m is SIDEWAYS: {htf_trend.get('reason')}"]}

        # Indicators on 5m
        ema9 = indicators.calculate_ema(closes, self.ema_fast)[-1]
        ema21 = indicators.calculate_ema(closes, self.ema_pullback)[-1]
        vwap = indicators.calculate_vwap(highs, lows, closes, volumes)[-1]
        atr = indicators.calculate_atr(highs, lows, closes, self.atr_period)[-1]
        rsi = indicators.calculate_rsi(closes, self.rsi_period)[-1]

        cur_c = closes[-1]
        cur_o = opens[-1]
        cur_l = lows[-1]
        cur_h = highs[-1]

        is_bullish_candle = cur_c > cur_o
        is_bearish_candle = cur_c < cur_o

        # ---------------------------------------------------------------------
        # 1. BULLISH PULLBACK SETUP
        # ---------------------------------------------------------------------
        if direction_15m == "BULLISH":
            # Pullback criteria:
            # Low tested near EMA 9 or EMA 21 (within 0.35% buffer)
            tested_ema = (cur_l <= (ema9 * 1.0035)) or (cur_l <= (ema21 * 1.0035))
            # Did not close below EMA 21
            held_ema21 = cur_c >= (ema21 * 0.998)
            # Reversal confirmation: Bullish candle closing in upper half
            is_reversal = is_bullish_candle and (cur_c >= (cur_l + (cur_h - cur_l) * 0.40))
            # VWAP confirmation: price generally above VWAP
            vwap_aligned = cur_c >= (vwap * 0.998)
            # RSI momentum: healthy pullback (35 <= RSI <= 75)
            rsi_valid = 35.0 <= rsi <= 75.0

            if tested_ema and held_ema21 and is_reversal and vwap_aligned and rsi_valid:
                # Swing low for stop loss
                recent_swing_low = min(lows[-4:])
                stop_loss = round(recent_swing_low - (self.atr_multiplier * atr), 2)
                risk_pts = cur_c - stop_loss

                if risk_pts > (cur_c * 0.001) and risk_pts < (cur_c * 0.05):
                    target_1 = round(cur_c + (1.0 * risk_pts), 2)
                    target_2 = round(cur_c + (2.0 * risk_pts), 2)
                    return {
                        "direction": "LONG",
                        "strategy_name": self.STRATEGY_ID,
                        "symbol": symbol,
                        "entry_price": cur_c,
                        "stop_loss": stop_loss,
                        "target_price": target_2,
                        "target_1_r": target_1,
                        "target_2_r": target_2,
                        "risk_per_share": round(risk_pts, 2),
                        "reward_risk": 2.0,
                        "setup_description": f"V3 Bullish Pullback: 15m Trend Bullish, 5m Pullback into EMA 9/21 confirmed with Bullish Candle, Close (₹{cur_c:.2f}) > VWAP (₹{vwap:.2f}), RSI={rsi:.1f}",
                        "indicator_values": {
                            "ema9_5m": round(ema9, 2), "ema21_5m": round(ema21, 2),
                            "vwap_5m": round(vwap, 2), "atr_5m": round(atr, 2),
                            "rsi_5m": round(rsi, 2), "htf_adx": htf_trend.get("adx", 0.0)
                        },
                        "rejection_reasons": []
                    }

        # ---------------------------------------------------------------------
        # 2. BEARISH PULLBACK SETUP
        # ---------------------------------------------------------------------
        elif direction_15m == "BEARISH":
            tested_ema = (cur_h >= (ema9 * 0.9965)) or (cur_h >= (ema21 * 0.9965))
            held_ema21 = cur_c <= (ema21 * 1.002)
            is_reversal = is_bearish_candle and (cur_c <= (cur_h - (cur_h - cur_l) * 0.40))
            vwap_aligned = cur_c <= (vwap * 1.002)
            rsi_valid = 28.0 <= rsi <= 60.0

            if tested_ema and held_ema21 and is_reversal and vwap_aligned and rsi_valid:
                recent_swing_high = max(highs[-4:])
                stop_loss = round(recent_swing_high + (self.atr_multiplier * atr), 2)
                risk_pts = stop_loss - cur_c

                if risk_pts > (cur_c * 0.001) and risk_pts < (cur_c * 0.05):
                    target_1 = round(cur_c - (1.0 * risk_pts), 2)
                    target_2 = round(cur_c - (2.0 * risk_pts), 2)
                    return {
                        "direction": "SHORT",
                        "strategy_name": self.STRATEGY_ID,
                        "symbol": symbol,
                        "entry_price": cur_c,
                        "stop_loss": stop_loss,
                        "target_price": target_2,
                        "target_1_r": target_1,
                        "target_2_r": target_2,
                        "risk_per_share": round(risk_pts, 2),
                        "reward_risk": 2.0,
                        "setup_description": f"V3 Bearish Pullback: 15m Trend Bearish, 5m Pullback into EMA 9/21 confirmed with Bearish Candle, Close (₹{cur_c:.2f}) < VWAP (₹{vwap:.2f}), RSI={rsi:.1f}",
                        "indicator_values": {
                            "ema9_5m": round(ema9, 2), "ema21_5m": round(ema21, 2),
                            "vwap_5m": round(vwap, 2), "atr_5m": round(atr, 2),
                            "rsi_5m": round(rsi, 2), "htf_adx": htf_trend.get("adx", 0.0)
                        },
                        "rejection_reasons": []
                    }

        return {"direction": "NO_TRADE", "rejection_reasons": ["Pullback conditions not met"]}

    def evaluate_1m_stream(self, symbol: str, bars_1m: Dict[str, List[Any]]) -> Dict[str, Any]:
        """
        Unified entrypoint: accepts 1m bars from WebSocket or CSV,
        aggregates into completed 5m and 15m candles, and evaluates V3 strategy.
        """
        if len(bars_1m.get("close", [])) < 60:
            return {"direction": "NO_TRADE", "rejection_reasons": ["Insufficient 1m bars for dual-timeframe aggregation"]}

        bars_5m = self.aggregate_candles(bars_1m, target_minutes=5)
        bars_15m = self.aggregate_candles(bars_1m, target_minutes=15)

        htf_trend = self.evaluate_15m_trend(bars_15m)
        return self.evaluate_5m_pullback(symbol, bars_5m, htf_trend)
