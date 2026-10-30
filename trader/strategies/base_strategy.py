"""
Base Strategy Interface for Day Trader Pro V2.
Every strategy implements evaluate(symbol, timeframe, candles, indicators, market_context).
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

class BaseStrategy(ABC):
    def __init__(self, name: str, config: Optional[Dict[str, Any]] = None):
        self.name = name
        self.config = config or {}

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candles: Dict[str, Any],
        indicators: Dict[str, Any],
        market_context: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates candles and indicators.
        Returns a standardized signal dictionary if conditions are met, otherwise None.
        """
        pass

    def build_signal(
        self,
        symbol: str,
        timeframe: str,
        signal_type: str,  # "BUY" or "SELL"
        entry_price: float,
        stop_loss: float,
        target: float,
        reason: str,
        confidence: float = 1.0,
        timestamp: Optional[str] = None
    ) -> Dict[str, Any]:
        return {
            "symbol": symbol.upper(),
            "strategy": self.name,
            "timeframe": timeframe,
            "signal": signal_type.upper(),
            "timestamp": timestamp,
            "entry_price": round(entry_price, 4),
            "stop_loss": round(stop_loss, 4),
            "target": round(target, 4),
            "reason": reason,
            "confidence": round(confidence, 2)
        }

