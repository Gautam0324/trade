"""
Exponential Moving Average (EMA) and Simple Moving Average (SMA) calculations.
"""

from typing import List, Optional

def calculate_sma(data: List[float], period: int) -> List[Optional[float]]:
    if not data or period <= 0 or len(data) < period:
        return [None] * len(data)
    sma = [None] * (period - 1)
    current_sum = sum(data[:period])
    sma.append(current_sum / period)
    for i in range(period, len(data)):
        current_sum += data[i] - data[i - period]
        sma.append(current_sum / period)
    return sma

def calculate_ema(data: List[float], period: int) -> List[Optional[float]]:
    if not data or period <= 0 or len(data) < period:
        return [None] * len(data)
    ema = [None] * (period - 1)
    # Seed with SMA
    sma = sum(data[:period]) / period
    ema.append(sma)
    multiplier = 2.0 / (period + 1)
    for i in range(period, len(data)):
        current_val = (data[i] - ema[-1]) * multiplier + ema[-1]
        ema.append(current_val)
    return ema

