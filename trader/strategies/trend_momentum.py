"""
Trend Momentum Strategy (Supertrend + EMA + Momentum Volume).
"""

from typing import Dict, Any, Optional
from .base_strategy import BaseStrategy

class TrendMomentumStrategy(BaseStrategy):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("TREND_MOMENTUM", config)

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
        volumes = candles.get("volume", [])
        timestamps = candles.get("timestamp", [])

        if len(closes) < 20:
            return None

        c, h, l = closes[-1], highs[-1], lows[-1]
        ts = timestamps[-1] if timestamps else None

        st_dir = indicators.get("supertrend_direction", [])
        st_val = indicators.get("supertrend", [])
        ema9 = indicators.get("ema9", [])

        if not st_dir or not st_val or not ema9 or None in (st_dir[-1], st_val[-1], ema9[-1]):
            return None

        current_st_dir = st_dir[-1]
        prev_st_dir = st_dir[-2] if len(st_dir) > 1 else current_st_dir

        # Supertrend Bullish
        if current_st_dir == 1 and c > ema9[-1]:
            # Trigger on fresh flip or continuation
            sl = min(st_val[-1], l) - 0.05
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
                    reason=f"Supertrend Bullish (+1) above EMA9 with momentum",
                    confidence=0.80,
                    timestamp=ts
                )

        # Supertrend Bearish
        elif current_st_dir == -1 and c < ema9[-1]:
            sl = max(st_val[-1], h) + 0.05
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
                    reason=f"Supertrend Bearish (-1) below EMA9 with momentum",
                    confidence=0.80,
                    timestamp=ts
                )

        return None

