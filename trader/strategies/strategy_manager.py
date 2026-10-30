"""
Strategy Manager for Day Trader Pro V2.
Orchestrates strategy execution on completed candles, prevents duplicate signals, and generates atomic signal identifiers.
"""

from typing import Dict, Any, List, Optional
import time
from .confluence import ConfluenceStrategy
from .ema_channel import EMAChannelStrategy
from .trend_momentum import TrendMomentumStrategy
from .vwap_pullback import VWAPPullbackStrategy

class StrategyManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.confluence_strategy = ConfluenceStrategy(self.config)
        self.strategies = {
            "CONFLUENCE": self.confluence_strategy,
            "EMA_CHANNEL": EMAChannelStrategy(self.config),
            "TREND_MOMENTUM": TrendMomentumStrategy(self.config),
            "VWAP_PULLBACK": VWAPPullbackStrategy(self.config)
        }
        self._emitted_signal_ids = set()

    def evaluate_completed_candle(
        self,
        symbol: str,
        timeframe: str,
        candles: Dict[str, Any],
        indicators: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None,
        strategy_mode: str = "CONFLUENCE"
    ) -> Optional[Dict[str, Any]]:
        strat = self.strategies.get(strategy_mode.upper(), self.confluence_strategy)
        sig = strat.evaluate(symbol, timeframe, candles, indicators, market_context)

        if not sig:
            return None

        # Build unique signal ID
        timestamps = candles.get("timestamp", [])
        ts_clean = timestamps[-1].replace(":", "").replace("-", "").replace(" ", "_") if timestamps else str(int(time.time()))
        signal_id = f"SIG-{symbol.upper()}-{sig['signal']}-{timeframe}-{ts_clean}"

        # Deduplication check: Has this signal already been emitted for this candle?
        if signal_id in self._emitted_signal_ids:
            return None

        self._emitted_signal_ids.add(signal_id)
        # Keep set bounded
        if len(self._emitted_signal_ids) > 1000:
            self._emitted_signal_ids.clear()

        sig["signal_id"] = signal_id
        return sig

