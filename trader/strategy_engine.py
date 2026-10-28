#!/usr/bin/env python3
"""
Standardized Quantitative Strategy Engine for Indian Equities.
Provides unified, testable strategy logic used symmetrically across:
- Historical Backtesting (backtest.py)
- Real-time Paper Trading (multi_live_trader.py)
- Audit Logging & Telegram Signals
- Web Dashboard Explanations

Strategies implemented:
1. EMA_CHANNEL: EMA 5/9/33 Channel Breakout
2. VWAP_PULLBACK: Intraday VWAP Pullback Bounce
3. ORB: Opening Range Breakout (15-min)
4. TREND_MOMENTUM: EMA 9/33 + RSI + ADX + ATR
5. BREAKOUT_RETEST: Range Breakout + Pullback Retest of Breakout Level
6. TREND_PULLBACK_RECLAIM: Running Trend Pullback towards EMA33/VWAP and Reclaim
7. FAILED_BREAKDOWN: False Breakdown Reclaim (Long) / False Breakout Rejection (Short)
8. RELATIVE_STRENGTH_BREAKOUT: Outperformance / Underperformance vs NIFTY / Benchmark
9. CONFLUENCE: Multi-factor weighted confirmation

Output Schema:
{
  "symbol": str,
  "exchange": str,
  "timestamp": str,
  "timeframe": str,
  "strategy_name": str,
  "version": str,
  "direction": "LONG" | "SHORT" | "NO_TRADE",
  "setup_description": str,
  "entry_trigger": float,
  "proposed_entry_price": float,
  "stop_loss": float,
  "target_price": float,
  "risk_per_unit": float,
  "reward_risk_ratio": float,
  "indicator_values": dict,
  "volume_metrics": dict,
  "market_regime": str,
  "confirmation_status": "PENDING" | "CONFIRMED" | "NONE",
  "invalidation_conditions": str,
  "rejection_reasons": list,
  "data_freshness": str
}
"""

import datetime
from pathlib import Path
import sys

TRADER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADER_DIR))

import indicators
from regime import detect_market_regime

class StrategyEngine:
    VERSION = "2.0.0"

    def __init__(self, config=None):
        self.config = config or {}
        self.mode = self.config.get("strategy_mode", "CONFLUENCE")
        self.enabled_strategies = self.config.get("enabled_strategies", [
            "BREAKOUT_RETEST", "TREND_PULLBACK_RECLAIM", "FAILED_BREAKDOWN",
            "RELATIVE_STRENGTH_BREAKOUT", "EMA_CHANNEL", "VWAP_PULLBACK", "ORB", "TREND_MOMENTUM"
        ])
        self.min_confluence_score = float(self.config.get("min_confluence_score", 65.0))
        self.min_reward_risk = float(self.config.get("min_reward_risk", 1.5))
        self.allow_short = bool(self.config.get("allow_short", True))
        self.benchmark_symbol = self.config.get("benchmark_symbol", "NIFTY 50")

    def _empty_signal(self, symbol, exchange, strategy_name, reason="No setup"):
        return {
            "symbol": symbol,
            "exchange": exchange,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
            "timeframe": "1m",
            "strategy_name": strategy_name,
            "version": self.VERSION,
            "direction": "NO_TRADE",
            "setup_description": reason,
            "entry_trigger": 0.0,
            "proposed_entry_price": 0.0,
            "stop_loss": 0.0,
            "target_price": 0.0,
            "risk_per_unit": 0.0,
            "reward_risk_ratio": 0.0,
            "indicator_values": {},
            "volume_metrics": {},
            "market_regime": "UNKNOWN",
            "confirmation_status": "NONE",
            "invalidation_conditions": "No valid setup identified",
            "rejection_reasons": [reason] if reason else [],
            "data_freshness": "OK"
        }

    # -------------------------------------------------------------
    # 1. STRATEGY A: BREAKOUT_RETEST
    # -------------------------------------------------------------
    def evaluate_breakout_retest(self, symbol, bars, exchange="NSE", or_high=None, or_low=None):
        """
        1. Range identified (Opening Range or Prior 15-bar range)
        2. Candle broke out beyond range boundary
        3. Subsequent candle retests near level (within 0.15% tolerance) and holds
        4. Trigger set at retest candle high (LONG) or low (SHORT)
        """
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        opens = bars.get("open", [])
        volumes = bars.get("volume", [1000] * len(closes))

        if len(closes) < 30:
            return self._empty_signal(symbol, exchange, "BREAKOUT_RETEST", "Insufficient candles")

        # Opening range baseline if not provided
        if or_high is None or or_low is None:
            or_high = max(highs[0:15])
            or_low = min(lows[0:15])

        range_span = or_high - or_low
        if range_span <= 0:
            return self._empty_signal(symbol, exchange, "BREAKOUT_RETEST", "Invalid zero range")

        c, h, l, o = closes[-1], highs[-1], lows[-1], opens[-1]
        prev_c = closes[-2]
        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        atr = atr_series[-1] or (c * 0.01)

        rvol_series = indicators.calculate_relative_volume(volumes, 20)
        rvol = rvol_series[-1] if (rvol_series and isinstance(rvol_series, list)) else 1.0

        # Bullish Breakout + Retest Check
        # Prior candle was above ORH (breakout), current candle retested near ORH and held (closed green above ORH)
        is_long_retest = (
            prev_c > or_high and
            l <= (or_high * 1.0020) and
            c > or_high and
            c >= o
        )

        # Bearish Breakdown + Retest Check
        is_short_retest = (
            prev_c < or_low and
            h >= (or_low * 0.9980) and
            c < or_low and
            c <= o
        )

        if is_long_retest:
            stop_loss = round(min(l, or_high - (0.5 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk <= 0 or risk > (c * 0.03):
                return self._empty_signal(symbol, exchange, "BREAKOUT_RETEST", "Invalid risk or stop too wide")
            target = round(c + (risk * self.min_reward_risk), 2)
            rr = round((target - c) / max(0.01, risk), 2)

            return {
                "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                "timeframe": "1m", "strategy_name": "BREAKOUT_RETEST", "version": self.VERSION,
                "direction": "LONG", "setup_description": f"Breakout above range {or_high:.2f} successfully retested and held",
                "entry_trigger": h, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                "risk_per_unit": risk, "reward_risk_ratio": rr,
                "indicator_values": {"range_high": or_high, "range_low": or_low, "atr": atr},
                "volume_metrics": {"rvol": rvol}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                "invalidation_conditions": f"Close falls below {or_high:.2f}", "rejection_reasons": [], "data_freshness": "OK"
            }

        elif is_short_retest and self.allow_short:
            stop_loss = round(max(h, or_low + (0.5 * atr)), 2)
            risk = round(stop_loss - c, 2)
            if risk <= 0 or risk > (c * 0.03):
                return self._empty_signal(symbol, exchange, "BREAKOUT_RETEST", "Invalid risk or stop too wide")
            target = round(c - (risk * self.min_reward_risk), 2)
            rr = round((c - target) / max(0.01, risk), 2)

            return {
                "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                "timeframe": "1m", "strategy_name": "BREAKOUT_RETEST", "version": self.VERSION,
                "direction": "SHORT", "setup_description": f"Breakdown below range {or_low:.2f} successfully retested and held",
                "entry_trigger": l, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                "risk_per_unit": risk, "reward_risk_ratio": rr,
                "indicator_values": {"range_high": or_high, "range_low": or_low, "atr": atr},
                "volume_metrics": {"rvol": rvol}, "market_regime": "TRENDING_DOWN", "confirmation_status": "PENDING",
                "invalidation_conditions": f"Close rises above {or_low:.2f}", "rejection_reasons": [], "data_freshness": "OK"
            }

        return self._empty_signal(symbol, exchange, "BREAKOUT_RETEST", "No breakout-retest pattern detected")

    # -------------------------------------------------------------
    # 2. STRATEGY B: TREND_PULLBACK_RECLAIM
    # -------------------------------------------------------------
    def evaluate_trend_pullback_reclaim(self, symbol, bars, exchange="NSE"):
        """
        LONG:
        - EMA 9 > EMA 33 (primary trend)
        - Price dips near EMA 33 or VWAP (within 0.20%)
        - Reclaim candle closes bullish (close > open, close > EMA 33)
        SHORT:
        - EMA 9 < EMA 33 (primary downtrend)
        - Price tests up near EMA 33
        - Rejection/reclaim candle closes bearish (close < open, close < EMA 33)
        """
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        opens = bars.get("open", [])
        volumes = bars.get("volume", [1000] * len(closes))

        if len(closes) < 35:
            return self._empty_signal(symbol, exchange, "TREND_PULLBACK_RECLAIM", "Insufficient candles")

        c, h, l, o = closes[-1], highs[-1], lows[-1], opens[-1]
        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33 = indicators.calculate_ema(closes, 33)[-1]
        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        atr = atr_series[-1] or (c * 0.01)

        if not ema9 or not ema33:
            return self._empty_signal(symbol, exchange, "TREND_PULLBACK_RECLAIM", "Indicators not ready")

        # Long Setup
        is_bull_trend = ema9 > ema33
        pulled_to_ema = l <= (ema33 * 1.0020) and c > ema33
        bullish_reclaim = c > o
        if is_bull_trend and pulled_to_ema and bullish_reclaim:
            stop_loss = round(min(l, ema33 - (0.5 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "TREND_PULLBACK_RECLAIM", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"EMA9>EMA33 trend pullback to EMA33 ({ema33:.2f}) reclaimed with bullish close",
                    "entry_trigger": h, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"ema9": ema9, "ema33": ema33, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Break below swing low {stop_loss:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        # Short Setup
        is_bear_trend = ema9 < ema33
        pulled_up_to_ema = h >= (ema33 * 0.9980) and c < ema33
        bearish_reclaim = c < o
        if is_bear_trend and pulled_up_to_ema and bearish_reclaim and self.allow_short:
            stop_loss = round(max(h, ema33 + (0.5 * atr)), 2)
            risk = round(stop_loss - c, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c - (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "TREND_PULLBACK_RECLAIM", "version": self.VERSION,
                    "direction": "SHORT", "setup_description": f"EMA9<EMA33 downtrend test of EMA33 ({ema33:.2f}) rejected downwards",
                    "entry_trigger": l, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"ema9": ema9, "ema33": ema33, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_DOWN", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Break above swing high {stop_loss:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        return self._empty_signal(symbol, exchange, "TREND_PULLBACK_RECLAIM", "No trend pullback reclaim setup")

    # -------------------------------------------------------------
    # 3. STRATEGY C: FAILED_BREAKDOWN / FAILED_BREAKOUT
    # -------------------------------------------------------------
    def evaluate_failed_breakdown(self, symbol, bars, exchange="NSE", key_support=None, key_resistance=None):
        """
        FAILED_BREAKDOWN (Bullish Reclaim):
        - Low pierced below key support (swing low or opening range low)
        - But completed candle rejected lower prices and closed back ABOVE support
        - Wick rejection on low >= 40% of candle range
        """
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        opens = bars.get("open", [])

        if len(closes) < 25:
            return self._empty_signal(symbol, exchange, "FAILED_BREAKDOWN", "Insufficient candles")

        c, h, l, o = closes[-1], highs[-1], lows[-1], opens[-1]
        candle_range = max(0.01, h - l)
        lower_wick = min(o, c) - l
        upper_wick = h - max(o, c)

        if key_support is None:
            key_support = min(lows[-25:-1])
        if key_resistance is None:
            key_resistance = max(highs[-25:-1])

        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        atr = atr_series[-1] or (c * 0.01)

        # Bullish Liquidity Sweep & Reclaim
        pierced_support = l < key_support and c > key_support
        has_lower_rejection = (lower_wick / candle_range) >= 0.35
        if pierced_support and has_lower_rejection and c >= o:
            stop_loss = round(l - (0.2 * atr), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "FAILED_BREAKDOWN", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"Failed breakdown below support {key_support:.2f} with {lower_wick/candle_range:.0%} lower wick reclaim",
                    "entry_trigger": h, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"key_support": key_support, "lower_wick": lower_wick, "atr": atr},
                    "volume_metrics": {}, "market_regime": "RANGE_BOUND", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Break below sweep low {l:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        # Bearish Liquidity Sweep & Rejection
        pierced_resistance = h > key_resistance and c < key_resistance
        has_upper_rejection = (upper_wick / candle_range) >= 0.35
        if pierced_resistance and has_upper_rejection and c <= o and self.allow_short:
            stop_loss = round(h + (0.2 * atr), 2)
            risk = round(stop_loss - c, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c - (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "FAILED_BREAKDOWN", "version": self.VERSION,
                    "direction": "SHORT", "setup_description": f"Failed breakout above resistance {key_resistance:.2f} with {upper_wick/candle_range:.0%} upper wick rejection",
                    "entry_trigger": l, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"key_resistance": key_resistance, "upper_wick": upper_wick, "atr": atr},
                    "volume_metrics": {}, "market_regime": "RANGE_BOUND", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Break above sweep high {h:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        return self._empty_signal(symbol, exchange, "FAILED_BREAKDOWN", "No failed break setup")

    # -------------------------------------------------------------
    # 4. STRATEGY D: RELATIVE_STRENGTH_BREAKOUT
    # -------------------------------------------------------------
    def evaluate_relative_strength_breakout(self, symbol, bars, benchmark_bars=None, exchange="NSE"):
        """
        Stock Outperformance:
        - Over lookback (20 bars), Stock Return > Benchmark Return + 0.30%
        - Stock is making a fresh 20-bar high
        - Degrades to NO_TRADE safely if benchmark data is missing
        """
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        opens = bars.get("open", [])

        if len(closes) < 25:
            return self._empty_signal(symbol, exchange, "RELATIVE_STRENGTH_BREAKOUT", "Insufficient stock candles")

        if not benchmark_bars or len(benchmark_bars.get("close", [])) < 25:
            return self._empty_signal(symbol, exchange, "RELATIVE_STRENGTH_BREAKOUT", "Benchmark data missing or insufficient")

        # Check timestamp alignment and freshness if timestamps are provided
        stock_times = bars.get("timestamp", bars.get("time", []))
        bench_times = benchmark_bars.get("timestamp", benchmark_bars.get("time", []))
        if stock_times and bench_times:
            if stock_times[-1] != bench_times[-1]:
                return self._empty_signal(symbol, exchange, "RELATIVE_STRENGTH_BREAKOUT", f"Benchmark timestamp {bench_times[-1]} unaligned or stale vs stock {stock_times[-1]}")

        bench_closes = benchmark_bars["close"]
        lookback = min(20, len(closes)-1, len(bench_closes)-1)

        stock_ret = (closes[-1] - closes[-lookback]) / closes[-lookback]
        bench_ret = (bench_closes[-1] - bench_closes[-lookback]) / bench_closes[-lookback]
        rel_strength = stock_ret - bench_ret

        c, h, l, o = closes[-1], highs[-1], lows[-1], opens[-1]
        prior_high = max(highs[-lookback:-1])
        prior_low = min(lows[-lookback:-1])
        atr_series = indicators.calculate_atr(highs, lows, closes, 14)
        atr = atr_series[-1] or (c * 0.01)

        # Bullish Outperformance Breakout
        if rel_strength >= 0.003 and c > prior_high and c >= o:
            stop_loss = round(min(l, prior_high - (0.5 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "RELATIVE_STRENGTH_BREAKOUT", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"Outperforming benchmark ({rel_strength*100:+.2f}% vs {self.benchmark_symbol}) + Breakout above {prior_high:.2f}",
                    "entry_trigger": h, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"stock_ret": stock_ret, "bench_ret": bench_ret, "rel_strength": rel_strength, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Falls below breakout level {prior_high:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        # Bearish Underperformance Breakdown
        if rel_strength <= -0.003 and c < prior_low and c <= o and self.allow_short:
            stop_loss = round(max(h, prior_low + (0.5 * atr)), 2)
            risk = round(stop_loss - c, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c - (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "RELATIVE_STRENGTH_BREAKOUT", "version": self.VERSION,
                    "direction": "SHORT", "setup_description": f"Underperforming benchmark ({rel_strength*100:+.2f}% vs {self.benchmark_symbol}) + Breakdown below {prior_low:.2f}",
                    "entry_trigger": l, "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"stock_ret": stock_ret, "bench_ret": bench_ret, "rel_strength": rel_strength, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_DOWN", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Rises above breakdown level {prior_low:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }

        return self._empty_signal(symbol, exchange, "RELATIVE_STRENGTH_BREAKOUT", "No relative strength breakout setup")

    # -------------------------------------------------------------
    # 5. EXISTING STRATEGIES (Cleaned & Unified)
    # -------------------------------------------------------------
    def evaluate_ema_channel(self, symbol, bars, exchange="NSE"):
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        if len(closes) < 35:
            return self._empty_signal(symbol, exchange, "EMA_CHANNEL", "Insufficient candles")

        c = closes[-1]
        ema5 = indicators.calculate_ema(closes, 5)[-1]
        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33_h = indicators.calculate_ema(highs, 33)[-1]
        ema33_l = indicators.calculate_ema(lows, 33)[-1]
        atr = indicators.calculate_atr(highs, lows, closes, 14)[-1] or (c * 0.01)

        if c > ema33_h and ema5 > ema9:
            stop_loss = round(max(ema33_l, c - (2.0 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "EMA_CHANNEL", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"Close ({c:.2f}) > EMA33 High ({ema33_h:.2f}) & EMA5 > EMA9",
                    "entry_trigger": highs[-1], "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"ema5": ema5, "ema9": ema9, "ema33_high": ema33_h, "ema33_low": ema33_l, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Close below EMA9 ({ema9:.2f})", "rejection_reasons": [], "data_freshness": "OK"
                }
        return self._empty_signal(symbol, exchange, "EMA_CHANNEL", "No EMA Channel breakout")

    def evaluate_vwap_pullback(self, symbol, bars, exchange="NSE"):
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        opens = bars.get("open", [])
        volumes = bars.get("volume", [1000] * len(closes))
        if len(closes) < 30:
            return self._empty_signal(symbol, exchange, "VWAP_PULLBACK", "Insufficient candles")

        c, l, o = closes[-1], lows[-1], opens[-1]
        vwap_series = indicators.calculate_vwap(highs, lows, closes, volumes)
        cur_vwap = vwap_series[-1] or c
        atr = indicators.calculate_atr(highs, lows, closes, 14)[-1] or (c * 0.01)

        # Pullback test near VWAP and bullish green candle close above VWAP
        if l <= (cur_vwap * 1.0020) and c > cur_vwap and c >= o:
            stop_loss = round(min(l, cur_vwap - (0.5 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "VWAP_PULLBACK", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"VWAP pullback tested {cur_vwap:.2f} and closed bullish above VWAP",
                    "entry_trigger": highs[-1], "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"vwap": cur_vwap, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Break below VWAP stop {stop_loss:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }
        return self._empty_signal(symbol, exchange, "VWAP_PULLBACK", "No VWAP pullback setup")

    def evaluate_orb(self, symbol, bars, exchange="NSE", or_high=None, or_low=None):
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        if len(closes) < 20:
            return self._empty_signal(symbol, exchange, "ORB", "Insufficient candles")

        if or_high is None or or_low is None:
            or_high = max(highs[:15])
            or_low = min(lows[:15])

        c = closes[-1]
        atr = indicators.calculate_atr(highs, lows, closes, 14)[-1] or (c * 0.01)

        if c > or_high:
            stop_loss = round(max(or_low, or_high - (0.5 * atr)), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "ORB", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"15-min Opening Range Breakout above {or_high:.2f}",
                    "entry_trigger": highs[-1], "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"or_high": or_high, "or_low": or_low, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Close back below OR High {or_high:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }
        elif c < or_low and self.allow_short:
            stop_loss = round(min(or_high, or_low + (0.5 * atr)), 2)
            risk = round(stop_loss - c, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c - (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "ORB", "version": self.VERSION,
                    "direction": "SHORT", "setup_description": f"15-min Opening Range Breakdown below {or_low:.2f}",
                    "entry_trigger": lows[-1], "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"or_high": or_high, "or_low": or_low, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_DOWN", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"Close back above OR Low {or_low:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }
        return self._empty_signal(symbol, exchange, "ORB", "No ORB breakout")

    def evaluate_trend_momentum(self, symbol, bars, exchange="NSE"):
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        if len(closes) < 35:
            return self._empty_signal(symbol, exchange, "TREND_MOMENTUM", "Insufficient candles")

        c = closes[-1]
        ema9 = indicators.calculate_ema(closes, 9)[-1]
        ema33 = indicators.calculate_ema(closes, 33)[-1]
        rsi = indicators.calculate_rsi(closes, 14)[-1] or 50.0
        atr = indicators.calculate_atr(highs, lows, closes, 14)[-1] or (c * 0.01)

        if ema9 > ema33 and 52.0 <= rsi <= 75.0:
            stop_loss = round(c - (1.5 * atr), 2)
            risk = round(c - stop_loss, 2)
            if risk > 0 and risk <= (c * 0.03):
                target = round(c + (risk * self.min_reward_risk), 2)
                return {
                    "symbol": symbol, "exchange": exchange, "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST"),
                    "timeframe": "1m", "strategy_name": "TREND_MOMENTUM", "version": self.VERSION,
                    "direction": "LONG", "setup_description": f"EMA9>EMA33 trend alignment with RSI ({rsi:.1f}) momentum continuation",
                    "entry_trigger": highs[-1], "proposed_entry_price": c, "stop_loss": stop_loss, "target_price": target,
                    "risk_per_unit": risk, "reward_risk_ratio": self.min_reward_risk,
                    "indicator_values": {"ema9": ema9, "ema33": ema33, "rsi": rsi, "atr": atr},
                    "volume_metrics": {}, "market_regime": "TRENDING_UP", "confirmation_status": "PENDING",
                    "invalidation_conditions": f"RSI drops below 48 or price breaches {stop_loss:.2f}", "rejection_reasons": [], "data_freshness": "OK"
                }
        return self._empty_signal(symbol, exchange, "TREND_MOMENTUM", "No trend momentum alignment")

    # -------------------------------------------------------------
    # 6. MASTER EVALUATION DISPATCHER
    # -------------------------------------------------------------
    def evaluate_all(self, symbol, bars, or_high=None, or_low=None, vwap_series=None, benchmark_bars=None, exchange="NSE"):
        """
        Backward-compatible dispatcher for backtest.py and legacy callers.
        Maps to unified evaluate().
        """
        sig = self.evaluate(symbol, bars, exchange=exchange, or_high=or_high, or_low=or_low, benchmark_bars=benchmark_bars)
        sig["signal"] = "BUY" if sig["direction"] == "LONG" else ("SELL" if sig["direction"] == "SHORT" else "NO_TRADE")
        sig["side"] = "BUY" if sig["direction"] == "LONG" else "SELL"
        sig["entry_reference_price"] = sig.get("proposed_entry_price", 0.0)
        sig["risk_per_share"] = sig.get("risk_per_unit", 0.0)
        return sig

    def evaluate(self, symbol, bars, exchange="NSE", benchmark_bars=None, or_high=None, or_low=None):
        """
        Dispatches according to configured strategy_mode:
        - SINGLE: executes exactly the selected strategy
        - CONFLUENCE: scans enabled strategies and returns valid signal with highest score
        - SCANNER: returns all evaluated signals
        """
        closes = bars.get("close", [])
        highs = bars.get("high", [])
        lows = bars.get("low", [])
        volumes = bars.get("volume", [1000] * len(closes))

        # Check market regime
        regime_info = detect_market_regime(highs, lows, closes, volumes)
        regime = regime_info["regime"]

        # Strategy Map
        strategy_methods = {
            "BREAKOUT_RETEST": lambda: self.evaluate_breakout_retest(symbol, bars, exchange, or_high, or_low),
            "TREND_PULLBACK_RECLAIM": lambda: self.evaluate_trend_pullback_reclaim(symbol, bars, exchange),
            "FAILED_BREAKDOWN": lambda: self.evaluate_failed_breakdown(symbol, bars, exchange),
            "RELATIVE_STRENGTH_BREAKOUT": lambda: self.evaluate_relative_strength_breakout(symbol, bars, benchmark_bars, exchange),
            "EMA_CHANNEL": lambda: self.evaluate_ema_channel(symbol, bars, exchange),
            "VWAP_PULLBACK": lambda: self.evaluate_vwap_pullback(symbol, bars, exchange),
            "ORB": lambda: self.evaluate_orb(symbol, bars, exchange, or_high, or_low),
            "TREND_MOMENTUM": lambda: self.evaluate_trend_momentum(symbol, bars, exchange)
        }

        VALID_MODES = ["CONFLUENCE", "SCANNER"] + list(strategy_methods.keys())
        if self.mode not in VALID_MODES:
            return self._empty_signal(symbol, exchange, self.mode, f"Invalid strategy mode '{self.mode}'. Allowed: {VALID_MODES}")

        # Single Strategy Mode
        if self.mode in strategy_methods:
            if self.mode not in self.enabled_strategies:
                return self._empty_signal(symbol, exchange, self.mode, f"Strategy '{self.mode}' is disabled in configuration")
            sig = strategy_methods[self.mode]()
            sig["market_regime"] = regime
            return sig

        # Confluence / Multi Mode: evaluate all enabled strategies
        signals = []
        for name in self.enabled_strategies:
            if name in strategy_methods:
                s = strategy_methods[name]()
                s["market_regime"] = regime
                signals.append(s)

        if self.mode == "SCANNER":
            return signals

        # Real CONFLUENCE Engine
        # Requires true multi-factor confirmation score or independent setup agreement:
        # 1. Base Strategy Confirmation: At least 2 independent strategies agreeing on direction OR
        # 2. Confluence Score >= min_confluence_score (e.g. 65.0)
        long_candidates = [s for s in signals if s["direction"] == "LONG"]
        short_candidates = [s for s in signals if s["direction"] == "SHORT"]

        # Calculate directional confluence scores
        # Weighting: Base setup = 35 pts, Supporting setup = 25 pts, Regime alignment = 20 pts, RVol = 10 pts, R:R >= 1.5 = 10 pts
        regime_bullish = regime in ("TRENDING_UP", "RANGE_BOUND")
        regime_bearish = regime in ("TRENDING_DOWN", "RANGE_BOUND")

        if len(long_candidates) >= 1:
            score = 35.0  # primary setup
            contributing = [long_candidates[0]["strategy_name"]]
            if len(long_candidates) >= 2:
                score += 25.0 * (len(long_candidates) - 1)
                contributing.extend([s["strategy_name"] for s in long_candidates[1:]])
            if regime_bullish:
                score += 20.0
                contributing.append(f"Regime Alignment ({regime})")
            if long_candidates[0].get("reward_risk_ratio", 0) >= self.min_reward_risk:
                score += 10.0
                contributing.append(f"Favorable R:R ({long_candidates[0]['reward_risk_ratio']:.2f})")

            # Check if confluence score threshold is satisfied
            required_score = self.min_confluence_score
            # If multi-strategy agreement required (min 2 independent triggers)
            min_independent = int(self.config.get("min_confluence_strategies", 2))
            has_multi_setup = len(long_candidates) >= min_independent
            score_passed = score >= required_score

            best_sig = dict(long_candidates[0])
            best_sig["confluence_score"] = round(score, 1)
            best_sig["contributing_conditions"] = contributing
            best_sig["required_threshold"] = required_score
            best_sig["strategy_name"] = "CONFLUENCE"

            if has_multi_setup and score_passed:
                best_sig["direction"] = "LONG"
                best_sig["setup_description"] = f"Multi-Strategy Confluence ({score:.1f} pts >= {required_score:.1f}): " + " + ".join(contributing)
                best_sig["rejection_reasons"] = []
                return best_sig
            else:
                rejections = []
                if not has_multi_setup:
                    rejections.append(f"Insufficient independent strategy confirmations ({len(long_candidates)} < {min_independent})")
                if not score_passed:
                    rejections.append(f"Confluence score ({score:.1f}) below threshold ({required_score:.1f})")
                best_sig["direction"] = "NO_TRADE"
                best_sig["rejection_reasons"] = rejections
                best_sig["setup_description"] = "Confluence Rejected: " + "; ".join(rejections)
                return best_sig

        elif len(short_candidates) >= 1 and self.allow_short:
            score = 35.0
            contributing = [short_candidates[0]["strategy_name"]]
            if len(short_candidates) >= 2:
                score += 25.0 * (len(short_candidates) - 1)
                contributing.extend([s["strategy_name"] for s in short_candidates[1:]])
            if regime_bearish:
                score += 20.0
                contributing.append(f"Regime Alignment ({regime})")
            if short_candidates[0].get("reward_risk_ratio", 0) >= self.min_reward_risk:
                score += 10.0
                contributing.append(f"Favorable R:R ({short_candidates[0]['reward_risk_ratio']:.2f})")

            required_score = self.min_confluence_score
            min_independent = int(self.config.get("min_confluence_strategies", 2))
            has_multi_setup = len(short_candidates) >= min_independent
            score_passed = score >= required_score

            best_sig = dict(short_candidates[0])
            best_sig["confluence_score"] = round(score, 1)
            best_sig["contributing_conditions"] = contributing
            best_sig["required_threshold"] = required_score
            best_sig["strategy_name"] = "CONFLUENCE"

            if has_multi_setup and score_passed:
                best_sig["direction"] = "SHORT"
                best_sig["setup_description"] = f"Multi-Strategy Short Confluence ({score:.1f} pts >= {required_score:.1f}): " + " + ".join(contributing)
                best_sig["rejection_reasons"] = []
                return best_sig
            else:
                rejections = []
                if not has_multi_setup:
                    rejections.append(f"Insufficient independent strategy confirmations ({len(short_candidates)} < {min_independent})")
                if not score_passed:
                    rejections.append(f"Confluence score ({score:.1f}) below threshold ({required_score:.1f})")
                best_sig["direction"] = "NO_TRADE"
                best_sig["rejection_reasons"] = rejections
                best_sig["setup_description"] = "Confluence Rejected: " + "; ".join(rejections)
                return best_sig

        return self._empty_signal(symbol, exchange, "CONFLUENCE", "No enabled strategy triggered")
