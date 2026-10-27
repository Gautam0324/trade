#!/usr/bin/env python3
"""
Quantitative technical indicator calculation library.
Computes:
- EMA (Exponential Moving Average)
- SMA (Simple Moving Average)
- True Range & ATR (Average True Range - Wilder's smoothing)
- RSI (Relative Strength Index - Wilder's smoothing)
- ADX, +DI, -DI (Average Directional Index - Wilder's 14)
- VWAP (Session-anchored Volume Weighted Average Price)
- Volume moving averages and Relative Volume (RVol)

Pure Python standard library only. Deterministic and zero lookahead.
"""

from decimal import Decimal
import math

def calculate_sma(series, period):
    """
    Simple Moving Average.
    """
    clean = [float(x) for x in series if x is not None]
    if len(clean) < period:
        return [None] * len(series)
    
    out = [None] * (period - 1)
    window_sum = sum(clean[:period])
    out.append(window_sum / period)
    for i in range(period, len(clean)):
        window_sum += clean[i] - clean[i - period]
        out.append(window_sum / period)
    return out

def calculate_ema(series, period):
    """
    Exponential Moving Average.
    Initial seed is SMA of first 'period' elements.
    Multiplier = 2 / (period + 1)
    """
    clean = [float(x) for x in series if x is not None]
    if len(clean) < period:
        return [None] * len(series)

    multiplier = 2.0 / (period + 1.0)
    sma_seed = sum(clean[:period]) / period

    ema_vals = [sma_seed]
    for p in clean[period:]:
        ema_vals.append((p - ema_vals[-1]) * multiplier + ema_vals[-1])

    full_ema = [None] * (period - 1) + ema_vals
    return full_ema

def calculate_tr(highs, lows, closes):
    """
    True Range for each bar:
    max(H - L, abs(H - C_prev), abs(L - C_prev))
    """
    tr = []
    for i in range(len(highs)):
        h = float(highs[i])
        l = float(lows[i])
        if i == 0:
            tr.append(h - l)
        else:
            prev_c = float(closes[i - 1])
            tr.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))
    return tr

def calculate_atr(highs, lows, closes, period=14):
    """
    Average True Range using Wilder's Smoothing.
    Initial ATR = SMA of first 14 TRs.
    Subsequent ATR = (Prior ATR * (period - 1) + Current TR) / period.
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return [None] * len(highs)

    tr = calculate_tr(highs, lows, closes)
    if len(tr) < period:
        return [None] * len(tr)

    atr = [None] * (period - 1)
    seed = sum(tr[:period]) / period
    atr.append(seed)

    for i in range(period, len(tr)):
        val = (atr[-1] * (period - 1) + tr[i]) / period
        atr.append(val)

    return atr

def calculate_rsi(closes, period=14):
    """
    Relative Strength Index (RSI) using Wilder's smoothed moving average.
    """
    if len(closes) < period + 1:
        return [None] * len(closes)

    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = float(closes[i]) - float(closes[i - 1])
        if diff >= 0:
            gains.append(diff)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(diff))

    if len(gains) < period:
        return [None] * len(closes)

    rsi = [None] * period  # Pad for indices 0 to period-1
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0.0:
        rsi.append(100.0)
    else:
        rs = avg_gain / avg_loss
        rsi.append(100.0 - (100.0 / (1.0 + rs)))

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0.0:
            rsi.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi.append(100.0 - (100.0 / (1.0 + rs)))

    return rsi

def calculate_adx(highs, lows, closes, period=14):
    """
    Average Directional Index (ADX) with +DI, -DI using Wilder's smoothing.
    Returns: dict of list { 'adx': [...], 'plus_di': [...], 'minus_di': [...] }
    """
    n = len(closes)
    if n < period * 2:
        return {
            "adx": [None] * n,
            "plus_di": [None] * n,
            "minus_di": [None] * n
        }

    tr = calculate_tr(highs, lows, closes)
    plus_dm = [0.0]
    minus_dm = [0.0]

    for i in range(1, n):
        up_move = float(highs[i]) - float(highs[i - 1])
        down_move = float(lows[i - 1]) - float(lows[i])

        if up_move > down_move and up_move > 0:
            plus_dm.append(up_move)
        else:
            plus_dm.append(0.0)

        if down_move > up_move and down_move > 0:
            minus_dm.append(down_move)
        else:
            minus_dm.append(0.0)

    # Wilder smoothing for TR, +DM, -DM
    smooth_tr = [None] * (period - 1) + [sum(tr[:period])]
    smooth_pdm = [None] * (period - 1) + [sum(plus_dm[:period])]
    smooth_mdm = [None] * (period - 1) + [sum(minus_dm[:period])]

    for i in range(period, n):
        smooth_tr.append(smooth_tr[-1] - (smooth_tr[-1] / period) + tr[i])
        smooth_pdm.append(smooth_pdm[-1] - (smooth_pdm[-1] / period) + plus_dm[i])
        smooth_mdm.append(smooth_mdm[-1] - (smooth_mdm[-1] / period) + minus_dm[i])

    plus_di = [None] * n
    minus_di = [None] * n
    dx = [None] * n

    for i in range(period - 1, n):
        str_val = smooth_tr[i]
        if str_val and str_val > 0:
            p_di = 100.0 * (smooth_pdm[i] / str_val)
            m_di = 100.0 * (smooth_mdm[i] / str_val)
            plus_di[i] = p_di
            minus_di[i] = m_di
            di_sum = p_di + m_di
            if di_sum > 0:
                dx[i] = 100.0 * (abs(p_di - m_di) / di_sum)
            else:
                dx[i] = 0.0

    # Calculate ADX as Wilder smoothed average of DX (starts at 2*period - 1)
    adx = [None] * n
    dx_valid = [x for x in dx if x is not None]
    if len(dx_valid) >= period:
        first_adx_idx = (period - 1) + period - 1
        seed_adx = sum(dx[period - 1 : first_adx_idx + 1]) / period
        adx[first_adx_idx] = seed_adx
        for i in range(first_adx_idx + 1, n):
            if dx[i] is not None and adx[i - 1] is not None:
                adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return {
        "adx": adx,
        "plus_di": plus_di,
        "minus_di": minus_di
    }

def calculate_vwap(highs, lows, closes, volumes, session_starts=None):
    """
    Session-anchored Volume Weighted Average Price (VWAP).
    Typical Price = (High + Low + Close) / 3
    VWAP = Cumulative(Typical Price * Volume) / Cumulative(Volume)
    session_starts is a boolean list indicating where a new session begins (True on first bar of day).
    If session_starts is None, calculates continuously.
    """
    n = len(closes)
    vwap = []
    cum_pv = 0.0
    cum_vol = 0.0

    for i in range(n):
        if session_starts and session_starts[i]:
            cum_pv = 0.0
            cum_vol = 0.0

        h = float(highs[i])
        l = float(lows[i])
        c = float(closes[i])
        v = float(volumes[i]) if volumes[i] is not None and volumes[i] > 0 else 0.0

        tp = (h + l + c) / 3.0
        cum_pv += tp * v
        cum_vol += v

        if cum_vol > 0:
            vwap.append(cum_pv / cum_vol)
        else:
            # Fallback to typical price if no volume
            vwap.append(tp)

    return vwap

def calculate_relative_volume(volumes, period=20):
    """
    Relative Volume (RVol) = Current Volume / Average Volume of prior N bars.
    """
    rvol = [1.0] * len(volumes)
    vol_sma = calculate_sma(volumes, period)
    for i in range(len(volumes)):
        if vol_sma[i] and vol_sma[i] > 0:
            rvol[i] = float(volumes[i]) / float(vol_sma[i])
        else:
            rvol[i] = 1.0
    return rvol

def calculate_macd(closes, fast_period=12, slow_period=26, signal_period=9):
    """
    Moving Average Convergence Divergence (MACD).
    MACD Line = Fast EMA - Slow EMA
    Signal Line = EMA of MACD Line
    Histogram = MACD Line - Signal Line
    """
    n = len(closes)
    fast_ema = calculate_ema(closes, fast_period)
    slow_ema = calculate_ema(closes, slow_period)

    macd_line = [None] * n
    for i in range(n):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    # Compute Signal Line (EMA of valid MACD values)
    valid_macd_indices = [i for i, v in enumerate(macd_line) if v is not None]
    signal_line = [None] * n
    hist = [None] * n

    if len(valid_macd_indices) >= signal_period:
        valid_vals = [macd_line[i] for i in valid_macd_indices]
        sig_sub = calculate_ema(valid_vals, signal_period)
        for sub_idx, orig_idx in enumerate(valid_macd_indices):
            signal_line[orig_idx] = sig_sub[sub_idx]
            if macd_line[orig_idx] is not None and signal_line[orig_idx] is not None:
                hist[orig_idx] = macd_line[orig_idx] - signal_line[orig_idx]

    return {
        "macd": macd_line,
        "signal": signal_line,
        "hist": hist
    }

def calculate_supertrend(highs, lows, closes, period=10, multiplier=3.0):
    """
    Supertrend Indicator (period=10, multiplier=3.0).
    Upper Band = (High + Low) / 2 + Multiplier * ATR
    Lower Band = (High + Low) / 2 - Multiplier * ATR
    Direction: 1 for Bullish (Price > Upper Band), -1 for Bearish (Price < Lower Band)
    """
    n = len(closes)
    atr = calculate_atr(highs, lows, closes, period)

    supertrend = [None] * n
    direction = [None] * n  # 1 = Bullish, -1 = Bearish
    upper_band = [None] * n
    lower_band = [None] * n

    for i in range(n):
        if atr[i] is None:
            continue

        hl2 = (float(highs[i]) + float(lows[i])) / 2.0
        basic_upper = hl2 + (multiplier * atr[i])
        basic_lower = hl2 - (multiplier * atr[i])

        if i == 0 or upper_band[i - 1] is None:
            upper_band[i] = basic_upper
            lower_band[i] = basic_lower
            direction[i] = 1 if float(closes[i]) >= basic_upper else -1
            supertrend[i] = lower_band[i] if direction[i] == 1 else upper_band[i]
            continue

        # Smooth upper band
        if basic_upper < upper_band[i - 1] or float(closes[i - 1]) > upper_band[i - 1]:
            upper_band[i] = basic_upper
        else:
            upper_band[i] = upper_band[i - 1]

        # Smooth lower band
        if basic_lower > lower_band[i - 1] or float(closes[i - 1]) < lower_band[i - 1]:
            lower_band[i] = basic_lower
        else:
            lower_band[i] = lower_band[i - 1]

        # Determine direction
        if direction[i - 1] == 1:
            if float(closes[i]) < lower_band[i]:
                direction[i] = -1
                supertrend[i] = upper_band[i]
            else:
                direction[i] = 1
                supertrend[i] = lower_band[i]
        else:
            if float(closes[i]) > upper_band[i]:
                direction[i] = 1
                supertrend[i] = lower_band[i]
            else:
                direction[i] = -1
                supertrend[i] = upper_band[i]

    return {
        "supertrend": supertrend,
        "direction": direction,
        "upper_band": upper_band,
        "lower_band": lower_band
    }

def validate_ohlcv_bars(bars_dict):
    """
    Validates completed OHLCV candle bars:
    - High >= Low
    - High >= Open, High >= Close
    - Low <= Open, Low <= Close
    - Volume >= 0
    - No None/NaN values in completed sequence
    - Rejects invalid bars instead of silently forward-filling
    Returns: (is_valid: bool, cleaned_dict: dict, error_msg: str)
    """
    opens = bars_dict.get("open", [])
    highs = bars_dict.get("high", [])
    lows = bars_dict.get("low", [])
    closes = bars_dict.get("close", [])
    volumes = bars_dict.get("volume", [])

    length = len(closes)
    if length == 0 or len(opens) != length or len(highs) != length or len(lows) != length:
        return False, {}, "Mismatched or empty bar series lengths"

    valid_o, valid_h, valid_l, valid_c, valid_v = [], [], [], [], []

    for i in range(length):
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        v = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0

        if None in (o, h, l, c):
            return False, {}, f"Invalid None value at bar index {i}"

        o, h, l, c, v = float(o), float(h), float(l), float(c), float(v)

        if h < l:
            return False, {}, f"High ({h}) < Low ({l}) at bar {i}"
        if h < max(o, c) - 1e-4:
            return False, {}, f"High ({h}) < max(Open, Close) at bar {i}"
        if l > min(o, c) + 1e-4:
            return False, {}, f"Low ({l}) > min(Open, Close) at bar {i}"
        if v < 0:
            return False, {}, f"Negative volume ({v}) at bar {i}"

        valid_o.append(o)
        valid_h.append(h)
        valid_l.append(l)
        valid_c.append(c)
        valid_v.append(v)

    return True, {
        "open": valid_o,
        "high": valid_h,
        "low": valid_l,
        "close": valid_c,
        "volume": valid_v
    }, ""

