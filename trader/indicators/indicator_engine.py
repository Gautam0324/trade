"""
Indicator Engine for Day Trader Pro V2.
Orchestrates calculation and caching of standard quantitative indicators on completed OHLCV candles.
"""

from typing import Dict, List, Any, Optional
from .ema import calculate_ema, calculate_sma
from .macd import calculate_macd
from .supertrend import calculate_supertrend, calculate_atr
from .vwap import calculate_vwap
from .rsi import calculate_rsi
from .highest_lowest import calculate_highest_high, calculate_lowest_low

class IndicatorEngine:
    def __init__(self):
        self._cache: Dict[str, Dict[str, Any]] = {}

    def calculate_all(self, symbol: str, bars_dict: Dict[str, List[Any]], force_recalculate: bool = False) -> Dict[str, Any]:
        timestamps = bars_dict.get("timestamp", [])
        closes = [float(x) for x in bars_dict.get("close", [])]
        highs = [float(x) for x in bars_dict.get("high", [])]
        lows = [float(x) for x in bars_dict.get("low", [])]
        volumes = [float(x) for x in bars_dict.get("volume", [])]

        n = len(closes)
        if n == 0:
            return {}

        last_ts = timestamps[-1] if timestamps else str(n)
        cache_key = f"{symbol.upper()}_{last_ts}_{n}"

        if not force_recalculate and cache_key in self._cache:
            return self._cache[cache_key]

        # 1. EMAs
        ema5 = calculate_ema(closes, 5)
        ema9 = calculate_ema(closes, 9)
        ema33 = calculate_ema(closes, 33)

        # 2. HH33 / LL33
        hh33 = calculate_highest_high(highs, 33)
        ll33 = calculate_lowest_low(lows, 33)

        # 3. Supertrend
        st_res = calculate_supertrend(highs, lows, closes, 10, 3.0)

        # 4. MACD
        macd_res = calculate_macd(closes, 12, 26, 9)

        # 5. VWAP
        vwap_res = calculate_vwap(highs, lows, closes, volumes)

        # 6. RSI
        rsi_res = calculate_rsi(closes, 14)

        # 7. ATR
        atr_res = calculate_atr(highs, lows, closes, 14)

        result = {
            "ema5": ema5,
            "ema9": ema9,
            "ema33": ema33,
            "hh33": hh33,
            "ll33": ll33,
            "supertrend": st_res["supertrend"],
            "supertrend_direction": st_res["direction"],
            "macd": macd_res["macd"],
            "macd_signal": macd_res["signal"],
            "macd_hist": macd_res["hist"],
            "vwap": vwap_res,
            "rsi": rsi_res,
            "atr": atr_res,
            "latest": {
                "close": closes[-1],
                "high": highs[-1],
                "low": lows[-1],
                "volume": volumes[-1] if volumes else 0.0,
                "ema5": ema5[-1],
                "ema9": ema9[-1],
                "ema33": ema33[-1],
                "hh33": hh33[-1],
                "ll33": ll33[-1],
                "supertrend": st_res["supertrend"][-1],
                "supertrend_dir": st_res["direction"][-1],
                "macd": macd_res["macd"][-1],
                "macd_signal": macd_res["signal"][-1],
                "vwap": vwap_res[-1] if vwap_res else None,
                "rsi": rsi_res[-1] if rsi_res else None,
                "atr": atr_res[-1] if atr_res else None
            }
        }

        # Keep cache bounded
        if len(self._cache) > 200:
            self._cache.clear()

        self._cache[cache_key] = result
        return result

