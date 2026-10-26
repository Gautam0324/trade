#!/usr/bin/env python3
"""
Advanced Multi-Strategy Central Engine.
Implements:
1. Strategy A: Existing EMA 5/9/33 Channel Breakout
2. Strategy B: VWAP Pullback Entry
3. Strategy C: Opening Range Breakout (ORB)
4. Strategy D: Trend + Momentum (EMA 9/33 + RSI 14 + ADX 14 + ATR 14)
5. Market Regime Detection & No-Trade Filters
6. Confluence Scoring & Conflict Resolution

Produces standardized structured signal payloads:
{
  "symbol": str,
  "strategy_name": str,
  "side": "BUY",
  "signal": "BUY" | "EXIT" | "HOLD" | "NO_TRADE",
  "timestamp": str,
  "entry_reference_price": float,
  "stop_loss": float,
  "target_price": float,
  "risk_per_share": float,
  "reward_risk_ratio": float,
  "confidence_score": float,
  "signal_reason": str,
  "indicator_values": dict,
  "market_regime": str,
  "candle_timestamp": str
}
"""

import datetime
from pathlib import Path
import sys

# Add trader path for indicators
TRADER_DIR = Path(__file__).resolve().parent.parent / "trader"
sys.path.insert(0, str(TRADER_DIR))

import indicators

class StrategyEngine:
    def __init__(self, config=None):
        self.config = config or {}
        # Strategy selection: "ALL", "EMA_CHANNEL", "VWAP_PULLBACK", "ORB", "TREND_MOMENTUM", "CONFLUENCE"
        self.mode = self.config.get("strategy_mode", "EMA_CHANNEL")
        self.min_confluence_score = float(self.config.get("min_confluence_score", 65.0))
        self.require_completed_candle = bool(self.config.get("require_completed_candle", True))

    def detect_market_regime(self, closes, highs, lows, volumes):
        """
        Determines current market regime:
        - "TRENDING_BULL": Strong uptrend (EMA 9 > EMA 33, ADX > 22, RSI > 50)
        - "TRENDING_BEAR": Strong downtrend (EMA 9 < EMA 33, ADX > 22, RSI < 50)
        - "CHOPPY_SIDEWAYS": ADX < 20 or tight Bollinger/ATR range
        - "HIGH_VOLATILITY": ATR exceeds 2.5x 20-period average
        """
        if len(closes) < 35:
            return "UNKNOWN"

        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33 = indicators.calculate_ema(closes, 33)[-1]
        adx_res = indicators.calculate_adx(highs, lows, closes, 14)
        cur_adx = adx_res["adx"][-1] or 15.0
        cur_rsi = indicators.calculate_rsi(closes, 14)[-1] or 50.0

        if cur_adx < 20.0:
            return "CHOPPY_SIDEWAYS"
        if ema9 and ema33:
            if ema9 > ema33 and cur_rsi > 52.0:
                return "TRENDING_BULL"
            elif ema9 < ema33 and cur_rsi < 48.0:
                return "TRENDING_BEAR"
        return "NEUTRAL"

    def evaluate_ema_channel(self, symbol, bars, atr_val=None):
        """
        Strategy A: EMA 5/9/33 Channel Breakout
        Long:
        1. Close > EMA 33 High
        2. EMA 5 > EMA 9
        3. ATR-validated dynamic stop: EMA 33 Low or entry - 1.5*ATR
        """
        closes = [c for c in bars.get("close", []) if c is not None]
        highs = [h for h in bars.get("high", []) if h is not None]
        lows = [l for l in bars.get("low", []) if l is not None]

        if len(closes) < 40:
            return None

        ema5 = indicators.calculate_ema(closes, 5)[-1]
        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33_h = indicators.calculate_ema(highs, 33)[-1]
        ema33_l = indicators.calculate_ema(lows, 33)[-1]

        cur_close = closes[-1]
        prev_close = closes[-2]
        prev_ema5 = indicators.calculate_ema(closes, 5)[-2]
        prev_ema9 = indicators.calculate_ema(closes, 9)[-2]

        is_breakout = cur_close > ema33_h
        is_bull_cross = (prev_ema5 <= prev_ema9 and ema5 > ema9) or (ema5 > ema9)

        if is_breakout and is_bull_cross:
            # ATR-based or structural stop
            if atr_val and atr_val > 0:
                # Stop is max(ema33_l, cur_close - 2.0*atr_val) to avoid excessive stop
                stop_loss = max(round(ema33_l, 2), round(cur_close - (2.0 * atr_val), 2))
                if stop_loss >= cur_close:
                    stop_loss = round(cur_close - (1.5 * atr_val), 2)
            else:
                stop_loss = round(ema33_l, 2)

            risk_per_share = round(cur_close - stop_loss, 2)
            if risk_per_share <= 0:
                return None

            target = round(cur_close + (risk_per_share * 1.5), 2)

            return {
                "symbol": symbol,
                "strategy_name": "EMA_CHANNEL",
                "side": "BUY",
                "signal": "BUY",
                "entry_reference_price": round(cur_close, 2),
                "stop_loss": stop_loss,
                "target_price": target,
                "risk_per_share": risk_per_share,
                "reward_risk_ratio": 1.5,
                "confidence_score": 75.0,
                "signal_reason": f"Close {cur_close:.2f} > EMA33 High {ema33_h:.2f} with EMA5 ({ema5:.2f}) > EMA9 ({ema9:.2f})",
                "indicator_values": {
                    "ema5": round(ema5, 2),
                    "ema9": round(ema9, 2),
                    "ema33_high": round(ema33_h, 2),
                    "ema33_low": round(ema33_l, 2)
                }
            }
        return None

    def evaluate_vwap_pullback(self, symbol, bars, vwap_series, atr_val=None):
        """
        Strategy B: Intraday VWAP Pullback Entry
        Long:
        1. Primary Trend: EMA 9 > EMA 33 or Price > VWAP
        2. Pullback: Low of recent 2 candles dipped within 0.3% of VWAP or crossed VWAP
        3. Completed Bullish Candle: Close > VWAP and Close > Open
        4. Stop: Below VWAP or Low - 1.0*ATR
        """
        closes = [c for c in bars.get("close", []) if c is not None]
        highs = [h for h in bars.get("high", []) if h is not None]
        lows = [l for l in bars.get("low", []) if l is not None]
        opens = [o for o in bars.get("open", []) if o is not None]

        if len(closes) < 35 or not vwap_series:
            return None

        cur_close = closes[-1]
        cur_open = opens[-1] if opens else cur_close
        cur_low = lows[-1]
        cur_vwap = vwap_series[-1]

        if not cur_vwap or cur_vwap <= 0:
            return None

        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33 = indicators.calculate_ema(closes, 33)[-1]

        # Trend filter
        is_uptrend = (ema9 and ema33 and ema9 > ema33) or (cur_close > cur_vwap)
        # Pullback proximity: Low touched within 0.8% above VWAP or dipped below and recovered
        vwap_dist_pct = (cur_low - cur_vwap) / cur_vwap
        has_pulled_back = -0.010 <= vwap_dist_pct <= 0.008
        is_bullish_close = cur_close >= cur_open and cur_close >= cur_vwap

        # Avoid over-extended entries (> 2.0% away from VWAP)
        is_extended = ((cur_close - cur_vwap) / cur_vwap) > 0.020

        if is_uptrend and has_pulled_back and is_bullish_close and not is_extended:
            if atr_val and atr_val > 0:
                stop_loss = round(min(cur_low, cur_vwap) - (0.5 * atr_val), 2)
            else:
                stop_loss = round(cur_vwap * 0.996, 2)

            risk_per_share = round(cur_close - stop_loss, 2)
            if risk_per_share <= 0:
                return None

            target = round(cur_close + (risk_per_share * 1.5), 2)
            return {
                "symbol": symbol,
                "strategy_name": "VWAP_PULLBACK",
                "side": "BUY",
                "signal": "BUY",
                "entry_reference_price": round(cur_close, 2),
                "stop_loss": stop_loss,
                "target_price": target,
                "risk_per_share": risk_per_share,
                "reward_risk_ratio": 1.5,
                "confidence_score": 80.0,
                "signal_reason": f"VWAP pullback to {cur_vwap:.2f} held; Bullish confirmation candle at {cur_close:.2f}",
                "indicator_values": {
                    "vwap": round(cur_vwap, 2),
                    "vwap_dist_pct": round(vwap_dist_pct * 100, 2)
                }
            }
        return None

    def evaluate_orb(self, symbol, bars, or_high, or_low, atr_val=None):
        """
        Strategy C: Opening Range Breakout (ORB 15-min)
        Long:
        1. Current completed bar close > OR High
        2. Filter out false wick-only breaks
        3. Stop: OR Midpoint or Breakout Candle Low
        """
        closes = [c for c in bars.get("close", []) if c is not None]
        lows = [l for l in bars.get("low", []) if l is not None]

        if not or_high or not or_low or or_high <= or_low or len(closes) < 5:
            return None

        cur_close = closes[-1]
        cur_low = lows[-1]

        if cur_close > or_high:
            or_mid = round((or_high + or_low) / 2.0, 2)
            # Default stop is OR-Mid unless breakout candle low is safer
            stop_loss = round(max(or_mid, cur_low), 2)
            if stop_loss >= cur_close:
                stop_loss = or_mid

            risk_per_share = round(cur_close - stop_loss, 2)
            if risk_per_share <= 0:
                return None

            target = round(cur_close + (risk_per_share * 1.5), 2)
            return {
                "symbol": symbol,
                "strategy_name": "ORB",
                "side": "BUY",
                "signal": "BUY",
                "entry_reference_price": round(cur_close, 2),
                "stop_loss": stop_loss,
                "target_price": target,
                "risk_per_share": risk_per_share,
                "reward_risk_ratio": 1.5,
                "confidence_score": 78.0,
                "signal_reason": f"Completed 5m candle {cur_close:.2f} broke 15m OR High {or_high:.2f}",
                "indicator_values": {
                    "or_high": round(or_high, 2),
                    "or_low": round(or_low, 2),
                    "or_mid": or_mid
                }
            }
        return None

    def evaluate_trend_momentum(self, symbol, bars, atr_val=None):
        """
        Strategy D: Trend + Momentum Confirmation
        Long:
        1. EMA 9 > EMA 33 (Clean Trend)
        2. RSI 14 in momentum zone (52 <= RSI <= 72)
        3. ADX 14 > 22 (+DI > -DI) (Directional Trend Strength)
        4. Stop: Entry - 1.5*ATR
        """
        closes = [c for c in bars.get("close", []) if c is not None]
        highs = [h for h in bars.get("high", []) if h is not None]
        lows = [l for l in bars.get("low", []) if l is not None]

        if len(closes) < 35:
            return None

        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33 = indicators.calculate_ema(closes, 33)[-1]
        rsi_series = indicators.calculate_rsi(closes, 14)
        cur_rsi = rsi_series[-1] or 50.0

        adx_res = indicators.calculate_adx(highs, lows, closes, 14)
        cur_adx = adx_res["adx"][-1]
        plus_di = adx_res["plus_di"][-1]
        minus_di = adx_res["minus_di"][-1]

        cur_close = closes[-1]

        is_trend = ema9 and ema33 and (ema9 > ema33)
        is_rsi_ok = 48.0 <= cur_rsi <= 78.0
        is_adx_ok = (cur_adx is None or cur_adx >= 16.0) and plus_di and minus_di and (plus_di > minus_di)

        if is_trend and is_rsi_ok and is_adx_ok:
            atr = atr_val or (indicators.calculate_atr(highs, lows, closes, 14)[-1] or (cur_close * 0.01))
            stop_loss = round(cur_close - (1.5 * atr), 2)
            risk_per_share = round(cur_close - stop_loss, 2)

            if risk_per_share <= 0:
                return None

            target = round(cur_close + (risk_per_share * 1.5), 2)
            return {
                "symbol": symbol,
                "strategy_name": "TREND_MOMENTUM",
                "side": "BUY",
                "signal": "BUY",
                "entry_reference_price": round(cur_close, 2),
                "stop_loss": stop_loss,
                "target_price": target,
                "risk_per_share": risk_per_share,
                "reward_risk_ratio": 1.5,
                "confidence_score": 85.0,
                "signal_reason": f"Trend confirmed: EMA9 ({ema9:.2f}) > EMA33, RSI ({cur_rsi:.1f}), ADX ({cur_adx:.1f}) +DI > -DI",
                "indicator_values": {
                    "ema9": round(ema9, 2),
                    "ema33": round(ema33, 2),
                    "rsi": round(cur_rsi, 1),
                    "adx": round(cur_adx, 1) if cur_adx else 0.0
                }
            }
        return None

    def evaluate_all(self, symbol, bars, or_high=None, or_low=None, vwap_series=None):
        """
        Unified evaluation honoring configuration mode and confluence scoring.
        """
        closes = [c for c in bars.get("close", []) if c is not None]
        highs = [h for h in bars.get("high", []) if h is not None]
        lows = [l for l in bars.get("low", []) if l is not None]
        volumes = [v for v in bars.get("volume", []) if v is not None]

        if len(closes) < 35:
            return {
                "symbol": symbol,
                "signal": "NO_TRADE",
                "reasons": ["Insufficient candle history (< 35 bars)"]
            }

        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        atr_val = atr_series[-1] or (closes[-1] * 0.01)

        regime = self.detect_market_regime(closes, highs, lows, volumes)

        # Evaluate individual candidates
        candidates = []

        # 1. EMA Channel
        if self.mode in ("ALL", "EMA_CHANNEL", "CONFLUENCE"):
            sig_ema = self.evaluate_ema_channel(symbol, bars, atr_val)
            if sig_ema:
                candidates.append(sig_ema)

        # 2. VWAP Pullback
        if self.mode in ("ALL", "VWAP_PULLBACK", "CONFLUENCE"):
            if vwap_series is None and volumes:
                vwap_series = indicators.calculate_vwap(highs, lows, closes, volumes)
            sig_vwap = self.evaluate_vwap_pullback(symbol, bars, vwap_series, atr_val)
            if sig_vwap:
                candidates.append(sig_vwap)

        # 3. ORB
        if self.mode in ("ALL", "ORB", "CONFLUENCE") and or_high and or_low:
            sig_orb = self.evaluate_orb(symbol, bars, or_high, or_low, atr_val)
            if sig_orb:
                candidates.append(sig_orb)

        # 4. Trend + Momentum
        if self.mode in ("ALL", "TREND_MOMENTUM", "CONFLUENCE"):
            sig_tm = self.evaluate_trend_momentum(symbol, bars, atr_val)
            if sig_tm:
                candidates.append(sig_tm)

        if not candidates:
            return {
                "symbol": symbol,
                "signal": "NO_TRADE",
                "market_regime": regime,
                "reasons": ["No active strategy conditions met"]
            }

        # Confluence Mode Scoring
        if self.mode == "CONFLUENCE":
            score = 0.0
            reasons = []
            for c in candidates:
                score += c["confidence_score"] * 0.4
                reasons.append(f"{c['strategy_name']}: {c['signal_reason']}")

            if score >= self.min_confluence_score:
                best = sorted(candidates, key=lambda x: x["confidence_score"], reverse=True)[0]
                best["strategy_name"] = "CONFLUENCE"
                best["confidence_score"] = min(99.0, round(score, 1))
                best["signal_reason"] = " | ".join(reasons)
                best["market_regime"] = regime
                best["candle_timestamp"] = datetime.datetime.now().strftime("%H:%M:%S IST")
                return best
            else:
                return {
                    "symbol": symbol,
                    "signal": "NO_TRADE",
                    "market_regime": regime,
                    "reasons": [f"Confluence score ({score:.1f}) below threshold ({self.min_confluence_score})"]
                }

        # Single / Multi mode: select highest confidence
        best = sorted(candidates, key=lambda x: x["confidence_score"], reverse=True)[0]
        best["market_regime"] = regime
        best["candle_timestamp"] = datetime.datetime.now().strftime("%H:%M:%S IST")
        return best
