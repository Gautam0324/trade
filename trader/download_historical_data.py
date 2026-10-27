#!/usr/bin/env python3
"""
historical_data_downloader.py
Fetches and saves clean OHLCV historical data for backtesting Indian Equities (NSE/BSE).
Saves data in:
1. CSV format (standard for pandas / backtrader / Excel)
2. JSON format (for fast standard-library python ingestion)

Usage:
    python trader/download_historical_data.py
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

# Primary Watchlist stocks + Major Nifty Index
SYMBOLS = [
    "RELIANCE.NS",
    "TCS.NS",
    "HDFCBANK.NS",
    "INFY.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "TATAMOTORS.NS",
    "^NSEI",         # Nifty 50 Index benchmark
    "TCS.BO"
]

def fetch_yahoo_chart_data(symbol, range_str="1y", interval="1d"):
    """
    Downloads chart data from Yahoo Finance API without third-party dependencies.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_str}&interval={interval}"
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
            
            opens = quotes.get("open", [])
            highs = quotes.get("high", [])
            lows = quotes.get("low", [])
            closes = quotes.get("close", [])
            volumes = quotes.get("volume", [])
            
            rows = []
            for i in range(len(timestamps)):
                ts = timestamps[i]
                o = opens[i] if i < len(opens) else None
                h = highs[i] if i < len(highs) else None
                l = lows[i] if i < len(lows) else None
                c = closes[i] if i < len(closes) else None
                v = volumes[i] if i < len(volumes) else None
                
                # Filter out null bars from market pauses/holidays
                if None in (o, h, l, c):
                    continue
                
                dt = datetime.datetime.fromtimestamp(ts)
                date_str = dt.strftime("%Y-%m-%d %H:%M:%S") if interval != "1d" else dt.strftime("%Y-%m-%d")
                
                rows.append({
                    "timestamp": ts,
                    "date": date_str,
                    "open": round(float(o), 2),
                    "high": round(float(h), 2),
                    "low": round(float(l), 2),
                    "close": round(float(c), 2),
                    "volume": int(v) if v is not None else 0
                })
            return rows
    except Exception as e:
        print(f"[ERROR] Failed fetching {symbol} ({interval}): {e}")
        return None

def save_data(symbol, interval, rows):
    clean_sym = symbol.replace("^", "").replace(".NS", "_NS").replace(".BO", "_BO")
    
    # 1. Save CSV
    csv_file = OUTPUT_DIR / f"{clean_sym}_{interval}.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "date", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)
        
    # 2. Save JSON
    json_file = OUTPUT_DIR / f"{clean_sym}_{interval}.json"
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)
        
    print(f"[OK] Saved {len(rows)} bars for {symbol} ({interval}) -> {csv_file.name}")

def main():
    print(f"=== Downloading Historical Data to: {OUTPUT_DIR} ===\n")
    
    for symbol in SYMBOLS:
        print(f"Downloading 1-Year Daily (1d) data for {symbol}...")
        daily_rows = fetch_yahoo_chart_data(symbol, range_str="1y", interval="1d")
        if daily_rows:
            save_data(symbol, "1d", daily_rows)
        time.sleep(1)
        
        print(f"Downloading 60-Day Intraday (5m) data for {symbol}...")
        intraday_rows = fetch_yahoo_chart_data(symbol, range_str="60d", interval="5m")
        if intraday_rows:
            save_data(symbol, "5m", intraday_rows)
        time.sleep(1)

    print("\n[COMPLETE] All historical dataset files generated successfully.")

if __name__ == "__main__":
    main()
