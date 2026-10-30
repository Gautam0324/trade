"""
EMA Channel Strategy (EMA 5 / 9 / 33 Channel Breakout & Pullback).
"""

from typing import Dict, Any, Optional
from .base_strategy import BaseStrategy

class EMAChannelStrategy(BaseStrategy):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("EMA_CHANNEL", config)

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

        if len(closes) < 35:
            return None

        c, h, l = closes[-1], highs[-1], lows[-1]
        prev_c, prev_l, prev_h = closes[-2], lows[-2], highs[-2]
        ts = timestamps[-1] if timestamps else None

        ema5 = indicators.get("ema5", [])
        ema9 = indicators.get("ema9", [])
        ema33 = indicators.get("ema33", [])

        if not ema5 or not ema9 or not ema33 or None in (ema5[-1], ema9[-1], ema33[-1]):
            return None

        e5, e9, e33 = ema5[-1], ema9[-1], ema33[-1]

        # 1. Bullish Setup: EMA5 > EMA9 > EMA33 and close above EMA5
        if e5 > e9 > e33:
            # Pullback to EMA9 or bounce
            if prev_l <= ema9[-2] and c > e5:
                sl = min(prev_l, l, e33) - 0.05
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
                        reason=f"EMA Channel Bullish Alignment (EMA5 {e5:.2f} > EMA9 {e9:.2f} > EMA33 {e33:.2f}) with bounce",
                        confidence=0.85,
                        timestamp=ts
                    )

        # 2. Bearish Setup: EMA5 < EMA9 < EMA33 and close below EMA5
        elif e5 < e9 < e33:
            if prev_h >= ema9[-2] and c < e5:
                sl = max(prev_h, h, e33) + 0.05
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
                        reason=f"EMA Channel Bearish Alignment (EMA5 {e5:.2f} < EMA9 {e9:.2f} < EMA33 {e33:.2f}) with rejection",
                        confidence=0.85,
                        timestamp=ts
                    )

        return None

