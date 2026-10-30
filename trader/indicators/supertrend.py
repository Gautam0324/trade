"""
Supertrend indicator calculation.
"""

from typing import List, Dict, Optional, Any

def calculate_tr(highs: List[float], lows: List[float], closes: List[float]) -> List[float]:
    tr = [highs[0] - lows[0]]
    for i in range(1, len(highs)):
        h, l, c_prev = highs[i], lows[i], closes[i - 1]
        tr.append(max(h - l, abs(h - c_prev), abs(l - c_prev)))
    return tr

def calculate_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 10) -> List[Optional[float]]:
    if len(highs) < period:
        return [None] * len(highs)
    tr = calculate_tr(highs, lows, closes)
    atr = [None] * (period - 1)
    atr.append(sum(tr[:period]) / period)
    for i in range(period, len(tr)):
        atr.append((atr[-1] * (period - 1) + tr[i]) / period)
    return atr

def calculate_supertrend(highs: List[float], lows: List[float], closes: List[float], period: int = 10, multiplier: float = 3.0) -> Dict[str, List[Any]]:
    n = len(highs)
    if n < period:
        return {"supertrend": [None] * n, "direction": [0] * n}

    atr = calculate_atr(highs, lows, closes, period)
    basic_upper = []
    basic_lower = []
    for i in range(n):
        if atr[i] is None:
            basic_upper.append(None)
            basic_lower.append(None)
        else:
            hl2 = (highs[i] + lows[i]) / 2.0
            basic_upper.append(hl2 + multiplier * atr[i])
            basic_lower.append(hl2 - multiplier * atr[i])

    final_upper = [None] * n
    final_lower = [None] * n
    supertrend = [None] * n
    direction = [0] * n

    for i in range(period - 1, n):
        if i == period - 1:
            final_upper[i] = basic_upper[i]
            final_lower[i] = basic_lower[i]
            direction[i] = 1 if closes[i] > final_upper[i] else -1
            supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]
            continue

        prev_final_upper = final_upper[i - 1]
        if basic_upper[i] < prev_final_upper or closes[i - 1] > prev_final_upper:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = prev_final_upper

        prev_final_lower = final_lower[i - 1]
        if basic_lower[i] > prev_final_lower or closes[i - 1] < prev_final_lower:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = prev_final_lower

        if direction[i - 1] == 1:
            direction[i] = -1 if closes[i] < final_lower[i] else 1
        else:
            direction[i] = 1 if closes[i] > final_upper[i] else -1

        supertrend[i] = final_lower[i] if direction[i] == 1 else final_upper[i]

    return {"supertrend": supertrend, "direction": direction}
