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
