"""
Strategies Package for Day Trader Pro V2.
Standardized strategy interface, standalone strategy modules, and confluence aggregator.
"""

from .base_strategy import BaseStrategy
from .ema_channel import EMAChannelStrategy
from .trend_momentum import TrendMomentumStrategy
from .vwap_pullback import VWAPPullbackStrategy
from .confluence import ConfluenceStrategy
from .strategy_manager import StrategyManager

__all__ = [
    "BaseStrategy",
    "EMAChannelStrategy",
    "TrendMomentumStrategy",
    "VWAPPullbackStrategy",
    "ConfluenceStrategy",
    "StrategyManager"
]

