"""
Rolling Highest High and Lowest Low calculations (e.g. HH 33, LL 33).
"""

from typing import List, Optional

def calculate_highest_high(highs: List[float], period: int = 33) -> List[Optional[float]]:
    n = len(highs)
    if n < period:
        return [None] * n
    res = [None] * (period - 1)
    for i in range(period - 1, n):
        res.append(max(highs[i - period + 1 : i + 1]))
    return res

def calculate_lowest_low(lows: List[float], period: int = 33) -> List[Optional[float]]:
    n = len(lows)
    if n < period:
        return [None] * n
    res = [None] * (period - 1)
    for i in range(period - 1, n):
        res.append(min(lows[i - period + 1 : i + 1]))
    return res

