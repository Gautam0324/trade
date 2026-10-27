#!/usr/bin/env python3
"""
generate_unified_1m_dataset.py
Downloads 1-minute (1m) intraday candles for all watchlist stocks,
calculates all quantitative indicators:
- EMA (5, 9, 20, 33 High, 33 Low, 50, 200)
- SMA (20, 50)
- VWAP (Session-anchored)
- RSI (14)
- ATR (14)
- MACD (12, 26, 9 line, signal, hist)
- Supertrend (10, 3.0)
- ADX / +DI / -DI (14)
- Relative Volume (RVol 20)

And merges everything into ONE SINGLE comprehensive CSV file.
"""

import os
import csv
import json
import time
import datetime
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / "trader" / "historical_data"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
UNIFIED_CSV = OUTPUT_DIR / "unified_all_stocks_1m_with_indicators.csv"

# Watchlist symbols
WATCHLIST = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "TCS.BO"
]

import sys
sys.path.insert(0, str(BASE_DIR / "trader"))
import indicators

def fetch_1m_candles(symbol):
    """
    Downloads available 1-minute candles (7-day window from Yahoo Finance).
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=7d&interval=1m"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
            result = data.get("chart", {}).get("result", [])
            if not result:
                return None
            res = result[0]
            timestamps = res.get("timestamp", [])
            quotes = res.get("indicators", {}).get("quote", [{}])[0]
            
            raw_o = quotes.get("open", [])
            raw_h = quotes.get("high", [])
            raw_l = quotes.get("low", [])
            raw_c = quotes.get("close", [])
            raw_v = quotes.get("volume", [])
            
            clean_ts, clean_o, clean_h, clean_l, clean_c, clean_v = [], [], [], [], [], []
            for i in range(len(timestamps)):
                o = raw_o[i] if i < len(raw_o) else None
                h = raw_h[i] if i < len(raw_h) else None
                l = raw_l[i] if i < len(raw_l) else None
                c = raw_c[i] if i < len(raw_c) else None
                v = raw_v[i] if i < len(raw_v) else 0
                if None not in (o, h, l, c):
                    clean_ts.append(timestamps[i])
                    clean_o.append(float(o))
                    clean_h.append(float(h))
                    clean_l.append(float(l))
                    clean_c.append(float(c))
                    clean_v.append(float(v) if v is not None else 0.0)

            return {
                "timestamp": clean_ts,
                "open": clean_o,
                "high": clean_h,
                "low": clean_l,
                "close": clean_c,
                "volume": clean_v
            }
    except Exception as e:
        print(f"[ERROR] Failed fetching 1m data for {symbol}: {e}")
        return None

def compute_all_indicators(bars):
    o = bars["open"]
    h = bars["high"]
    l = bars["low"]
    c = bars["close"]
    v = bars["volume"]
    ts = bars["timestamp"]
    n = len(c)
    
    # 1. EMAs & SMAs
    ema5 = indicators.calculate_ema(c, 5)
    ema9 = indicators.calculate_ema(c, 9)
    ema20 = indicators.calculate_ema(c, 20)
    ema33_high = indicators.calculate_ema(h, 33)
    ema33_low = indicators.calculate_ema(l, 33)
    ema50 = indicators.calculate_ema(c, 50)
    ema200 = indicators.calculate_ema(c, 200)
    sma20 = indicators.calculate_sma(c, 20)
    sma50 = indicators.calculate_sma(c, 50)
    
    # 2. Session-anchored VWAP (Detect daily session start)
    dates = [datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d") for t in ts]
    session_starts = [i == 0 or dates[i] != dates[i-1] for i in range(n)]
    vwap = indicators.calculate_vwap(h, l, c, v, session_starts=session_starts)
    
    # 3. Oscillators & Volatility
    rsi14 = indicators.calculate_rsi(c, 14)
    atr14 = indicators.calculate_atr(h, l, c, 14)
    rvol20 = indicators.calculate_relative_volume(v, 20)
    macd_res = indicators.calculate_macd(c)
    adx_res = indicators.calculate_adx(h, l, c, 14)
    supertrend_res = indicators.calculate_supertrend(h, l, c, 10, 3.0)
    
    rows = []
    for i in range(n):
        dt = datetime.datetime.fromtimestamp(ts[i])
        dt_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        
        def _round(val, decimals=2):
            return round(val, decimals) if val is not None else ""

        rows.append({
            "timestamp": ts[i],
            "datetime": dt_str,
            "open": round(o[i], 2),
            "high": round(h[i], 2),
            "low": round(l[i], 2),
            "close": round(c[i], 2),
            "volume": int(v[i]),
            "ema_5": _round(ema5[i]),
            "ema_9": _round(ema9[i]),
            "ema_20": _round(ema20[i]),
            "ema_33_high": _round(ema33_high[i]),
            "ema_33_low": _round(ema33_low[i]),
            "ema_50": _round(ema50[i]),
            "ema_200": _round(ema200[i]),
            "sma_20": _round(sma20[i]),
            "sma_50": _round(sma50[i]),
            "vwap": _round(vwap[i]),
            "rsi_14": _round(rsi14[i]),
            "atr_14": _round(atr14[i]),
            "rvol_20": _round(rvol20[i]),
            "macd": _round(macd_res["macd"][i]),
            "macd_signal": _round(macd_res["signal"][i]),
            "macd_hist": _round(macd_res["hist"][i]),
            "adx_14": _round(adx_res["adx"][i]),
            "plus_di_14": _round(adx_res["plus_di"][i]),
            "minus_di_14": _round(adx_res["minus_di"][i]),
            "supertrend": _round(supertrend_res["supertrend"][i]),
            "supertrend_direction": supertrend_res["direction"][i] if supertrend_res["direction"][i] is not None else ""
        })
    return rows

def main():
    print(f"=== Generating Unified 1-Minute Dataset with Indicators ===")
    fieldnames = [
        "symbol", "timestamp", "datetime", "open", "high", "low", "close", "volume",
        "ema_5", "ema_9", "ema_20", "ema_33_high", "ema_33_low", "ema_50", "ema_200",
        "sma_20", "sma_50", "vwap", "rsi_14", "atr_14", "rvol_20",
        "macd", "macd_signal", "macd_hist", "adx_14", "plus_di_14", "minus_di_14",
        "supertrend", "supertrend_direction"
    ]
    
    total_records = 0
    all_rows = []

    for sym in WATCHLIST:
        print(f"Downloading 1m bars & computing indicators for {sym}...")
        bars = fetch_1m_candles(sym)
        if bars and len(bars["close"]) > 0:
            computed_rows = compute_all_indicators(bars)
            for r in computed_rows:
                r["symbol"] = sym
                all_rows.append(r)
            print(f"[OK] {sym}: {len(computed_rows)} bars processed.")
            total_records += len(computed_rows)
        time.sleep(1)

    # Sort unified dataset chronologically, then by symbol
    all_rows.sort(key=lambda x: (x["timestamp"], x["symbol"]))

    with open(UNIFIED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"\n[COMPLETE] Unified dataset saved: {UNIFIED_CSV.name}")
    print(f"Total Combined Rows: {total_records}")
    print(f"File Size: {os.path.getsize(UNIFIED_CSV) / 1024 / 1024:.2f} MB")

if __name__ == "__main__":
    main()
