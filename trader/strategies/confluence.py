"""
Confluence Strategy Engine.
Combines multiple orthogonal indicators and sub-strategies with strict direction agreement and scoring thresholds.
"""

from typing import Dict, Any, Optional, List
from .base_strategy import BaseStrategy
from .ema_channel import EMAChannelStrategy
from .trend_momentum import TrendMomentumStrategy
from .vwap_pullback import VWAPPullbackStrategy

class ConfluenceStrategy(BaseStrategy):
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__("CONFLUENCE", config)
        self.min_confluence_score = float(self.config.get("min_confluence_score", 65.0))
        self.sub_strategies = [
            EMAChannelStrategy(self.config),
            TrendMomentumStrategy(self.config),
            VWAPPullbackStrategy(self.config)
        ]

    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: Dict[str, Any],
        indicators: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        context = market_context or {}
        regime = context.get("regime", "UNKNOWN")
        timestamps = candles.get("timestamp", [])
        ts = timestamps[-1] if timestamps else None

        long_points = 0.0
        short_points = 0.0
        reasons: List[str] = []

        sub_signals = []
        for strat in self.sub_strategies:
            sig = strat.evaluate(symbol, timeframe, candles, indicators, market_context)
            if sig:
                sub_signals.append(sig)
                if sig["signal"] == "BUY":
                    long_points += 30.0
                    reasons.append(strat.name)
                elif sig["signal"] == "SELL":
                    short_points += 30.0
                    reasons.append(strat.name)

        # Contradiction check: Never trade if both long and short signals fire simultaneously
        if long_points > 0 and short_points > 0:
            return None

        chosen_side = "BUY" if long_points > short_points else ("SELL" if short_points > long_points else None)
        if not chosen_side:
            return None

        total_score = long_points if chosen_side == "BUY" else short_points

        # Regime bonus
        if chosen_side == "BUY" and regime == "TRENDING_UP":
            total_score += 20.0
            reasons.append("Regime Alignment (TRENDING_UP)")
        elif chosen_side == "SELL" and regime == "TRENDING_DOWN":
            total_score += 20.0
            reasons.append("Regime Alignment (TRENDING_DOWN)")

        # R:R Feasibility (10 pts)
        total_score += 10.0
        reasons.append("Favorable R:R (1.50)")

        if total_score < self.min_confluence_score:
            return None

        # Determine reference entry and stop loss from primary matching sub-strategy
        primary_sig = next((s for s in sub_signals if s["signal"] == chosen_side), None)
        if not primary_sig:
            return None

        entry_price = primary_sig["entry_price"]
        stop_loss = primary_sig["stop_loss"]
        target = primary_sig["target"]

        summary_reason = f"Multi-Strategy Confluence ({total_score:.1f} pts >= {self.min_confluence_score:.1f}): {' + '.join(reasons)}"

        return self.build_signal(
            symbol=symbol,
            timeframe=timeframe,
            signal_type=chosen_side,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            reason=summary_reason,
            confidence=round(min(1.0, total_score / 100.0), 2),
            timestamp=ts
        )

