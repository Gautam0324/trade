"""
Moving Average Convergence Divergence (MACD) calculation.
"""

from typing import List, Dict, Optional
from .ema import calculate_ema

def calculate_macd(data: List[float], fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> Dict[str, List[Optional[float]]]:
    n = len(data)
    if n < slow_period + signal_period:
        return {"macd": [None] * n, "signal": [None] * n, "hist": [None] * n}

    fast_ema = calculate_ema(data, fast_period)
    slow_ema = calculate_ema(data, slow_period)

    macd_line: List[Optional[float]] = []
    for f, s in zip(fast_ema, slow_ema):
        if f is not None and s is not None:
            macd_line.append(f - s)
        else:
            macd_line.append(None)

    valid_macd = [v for v in macd_line if v is not None]
    if len(valid_macd) < signal_period:
        return {"macd": macd_line, "signal": [None] * n, "hist": [None] * n}

    raw_signal = calculate_ema(valid_macd, signal_period)
    signal_line: List[Optional[float]] = [None] * (n - len(valid_macd)) + raw_signal

    hist_line: List[Optional[float]] = []
    for m, s in zip(macd_line, signal_line):
        if m is not None and s is not None:
            hist_line.append(m - s)
        else:
            hist_line.append(None)

    return {"macd": macd_line, "signal": signal_line, "hist": hist_line}

