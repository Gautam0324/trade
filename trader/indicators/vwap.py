"""
Volume Weighted Average Price (VWAP) calculation.
"""

from typing import List, Optional

def calculate_vwap(highs: List[float], lows: List[float], closes: List[float], volumes: List[float]) -> List[Optional[float]]:
    n = len(closes)
    if n == 0:
        return []
    vwap = []
    cum_pv = 0.0
    cum_vol = 0.0
    for i in range(n):
        typical_price = (highs[i] + lows[i] + closes[i]) / 3.0
        v = volumes[i] if (volumes and i < len(volumes)) else 0.0
        cum_pv += typical_price * v
        cum_vol += v
        if cum_vol > 0:
            vwap.append(cum_pv / cum_vol)
        else:
            # Fallback to cumulative average typical price
            vwap.append(typical_price)
    return vwap

