"""
Indicators Package for Day Trader Pro V2.
Deterministic mathematical indicator engine for completed OHLCV candles.
Full backward compatibility with legacy indicators.py.
"""

from pathlib import Path
import importlib.util

# 1. Load all functions from legacy indicators.py
base_trader = Path(__file__).resolve().parent.parent
legacy_path = base_trader / "indicators.py"
if legacy_path.exists():
    spec = importlib.util.spec_from_file_location("legacy_indicators", str(legacy_path))
    legacy_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy_mod)
    for attr in dir(legacy_mod):
        if not attr.startswith("_"):
            globals()[attr] = getattr(legacy_mod, attr)

# 2. Expose V2 modular additions
from .ema import calculate_ema, calculate_sma
from .macd import calculate_macd
from .supertrend import calculate_supertrend
from .vwap import calculate_vwap
from .rsi import calculate_rsi
from .highest_lowest import calculate_highest_high, calculate_lowest_low
from .indicator_engine import IndicatorEngine

__all__ = [
    "calculate_ema",
    "calculate_sma",
    "calculate_macd",
    "calculate_supertrend",
    "calculate_vwap",
    "calculate_rsi",
    "calculate_highest_high",
    "calculate_lowest_low",
    "calculate_adx",
    "calculate_atr",
    "validate_ohlcv_bars",
    "calculate_relative_volume",
    "calculate_tr",
    "IndicatorEngine"
]
