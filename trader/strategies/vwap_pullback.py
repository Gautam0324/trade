"""
VWAP Pullback Reclaim Strategy.
"""

from typing import Dict, Any, Optional
from .base_strategy import BaseStrategy

class VWAPPullbackStrategy(BaseStrategy):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("VWAP_PULLBACK", config)

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: Dict[str, Any],
        indicators: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        closes = candles.get("close", [])
        highs = candles.get("high", [])
        lows = candles.get("low", [])
        timestamps = candles.get("timestamp", [])

        if len(closes) < 15:
            return None

        c, h, l = closes[-1], highs[-1], lows[-1]
        prev_c, prev_l, prev_h = closes[-2], lows[-2], highs[-2]
        ts = timestamps[-1] if timestamps else None

        vwap = indicators.get("vwap", [])
        if not vwap or None in (vwap[-1], vwap[-2] if len(vwap) > 1 else None):
            return None

        current_vwap = vwap[-1]
        prev_vwap = vwap[-2]

        # Bullish Reclaim: Previous low dipped below or touched VWAP, current close crosses cleanly above VWAP
        if prev_l <= prev_vwap and c > current_vwap and prev_c <= prev_vwap:
            sl = min(prev_l, l) - 0.05
            risk = c - sl
            if risk > 0:
                target = c + (risk * 1.5)
                return self.build_signal(
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type="BUY",
                    entry_price=c,
                    stop_loss=sl,
                    target=target,
                    reason=f"VWAP Pullback Reclaim: Prior dip to {prev_l:.2f} <= VWAP ({prev_vwap:.2f}) reclaimed with Close {c:.2f}",
                    confidence=0.85,
                    timestamp=ts
                )

        # Bearish Rejection: Prior high popped above VWAP, current close crosses below VWAP
        elif prev_h >= prev_vwap and c < current_vwap and prev_c >= prev_vwap:
            sl = max(prev_h, h) + 0.05
            risk = sl - c
            if risk > 0:
                target = c - (risk * 1.5)
                return self.build_signal(
                    symbol=symbol,
                    timeframe=timeframe,
                    signal_type="SELL",
                    entry_price=c,
                    stop_loss=sl,
                    target=target,
                    reason=f"VWAP Rejection: Prior rally to {prev_h:.2f} >= VWAP ({prev_vwap:.2f}) rejected with Close {c:.2f}",
                    confidence=0.85,
                    timestamp=ts
                )

        return None

