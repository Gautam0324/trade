#!/usr/bin/env python3
"""
EMA Strategy Calculator for Day Trader Skill.
Computes:
  - EMA 5 (Close)  : Ultra-fast momentum trigger
  - EMA 9 (Close)  : Trend confirmation baseline
  - EMA 33 (High)  : Upper dynamic resistance / breakout envelope channel
  - EMA 33 (Low)   : Lower dynamic support / pullback envelope channel
Standard library only.
"""

def calculate_ema(series, period):
    """
    Exponential Moving Average calculation.
    series: list of numeric prices.
    period: integer period (e.g. 5, 9, 33).
    """
    clean_series = [float(x) for x in series if x is not None]
    if len(clean_series) < period:
        return [None] * len(series)

    # Initial SMA
    sma = sum(clean_series[:period]) / period
    multiplier = 2.0 / (period + 1.0)

    ema_values = [sma]
    for price in clean_series[period:]:
        new_val = (price - ema_values[-1]) * multiplier + ema_values[-1]
        ema_values.append(new_val)

    # Pad with None for initial points
    full_ema = [None] * (period - 1) + ema_values
    return full_ema

def evaluate_ema_setup(bars):
    """
    Evaluates EMA 5, 9, 33 High, 33 Low strategy.
    bars is a dict containing:
      'close': list of close prices,
      'high': list of high prices,
      'low': list of low prices,
      'open': list of open prices

    Strategy Rules:
    - Long (BUY):
      1. Close > EMA 33 High (Bullish trend channel filter)
      2. EMA 5 crosses or holds above EMA 9
      3. Stop-Loss at EMA 33 Low (or previous candle low)
    - Short / Flat:
      Below EMA 33 Low
    """
    closes = [c for c in bars.get("close", []) if c is not None]
    highs = [h for h in bars.get("high", []) if h is not None]
    lows = [l for l in bars.get("low", []) if l is not None]

    if len(closes) < 40:
        return {"status": "INSUFFICIENT_DATA"}

    ema5 = calculate_ema(closes, 5)
    ema9 = calculate_ema(closes, 9)
    ema33_high = calculate_ema(highs, 33)
    ema33_low = calculate_ema(lows, 33)

    cur_close = closes[-1]
    cur_ema5 = ema5[-1]
    cur_ema9 = ema9[-1]
    cur_ema33_h = ema33_high[-1]
    cur_ema33_l = ema33_low[-1]

    prev_close = closes[-2]
    prev_ema5 = ema5[-2]
    prev_ema9 = ema9[-2]

    # Signal logic
    is_bullish_cross = (prev_ema5 <= prev_ema9) and (cur_ema5 > cur_ema9)
    is_channel_breakout = cur_close > cur_ema33_h
    is_above_channel = cur_close > cur_ema33_h and cur_ema5 > cur_ema9

    signal = "HOLD"
    if is_channel_breakout and (is_bullish_cross or cur_ema5 > cur_ema9):
        signal = "BUY"
    elif cur_close < cur_ema5 or cur_close < cur_ema33_l:
        signal = "EXIT"

    return {
        "signal": signal,
        "close": round(cur_close, 2),
        "ema5": round(cur_ema5, 2),
        "ema9": round(cur_ema9, 2),
        "ema33_high": round(cur_ema33_h, 2),
        "ema33_low": round(cur_ema33_l, 2),
        "is_above_33h": cur_close > cur_ema33_h,
        "is_above_33l": cur_close > cur_ema33_l,
        "ema5_gt_ema9": cur_ema5 > cur_ema9
    }

def check_trailing_profit_exit(entry_price, peak_price, current_price, current_ema5, current_ema9):
    """
    Trailing Profit Locking Rule:
    As soon as the trade is in profit and price turns back down:
    1. If price drops below EMA 5 (fastest momentum drop)
    2. OR if price drops 0.1% - 0.2% below its highest peak (Toffee example)
    ➔ Trigger EXIT immediately and lock the profit.
    """
    entry = float(entry_price)
    peak = float(peak_price)
    cur = float(current_price)

    is_in_profit = cur > entry

    # Condition 1: Dropped below EMA 5
    below_ema5 = cur < current_ema5
    # Condition 2: Dropped below EMA 9
    below_ema9 = cur < current_ema9
    # Condition 3: Reversal from peak (0.2% drop from high after gaining)
    peak_reversal = (peak > entry) and (cur < (peak * 0.998))

    should_exit = False
    reason = ""

    if is_in_profit and (below_ema5 or peak_reversal):
        should_exit = True
        if below_ema5:
            reason = f"Price ({cur}) crossed below EMA 5 ({current_ema5}) — Profit Locked"
        else:
            reason = f"Price reversed from peak {peak} to {cur} — Profit Locked"
    elif cur < current_ema9:
        should_exit = True
        reason = f"Price dropped below EMA 9 baseline ({current_ema9})"

    return {
        "should_exit": should_exit,
        "reason": reason,
        "is_profit": is_in_profit,
        "profit_per_share": round(cur - entry, 2)
    }
