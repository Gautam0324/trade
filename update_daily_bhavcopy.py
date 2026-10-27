#!/usr/bin/env python3
"""
update_daily_bhavcopy.py
Automatically fetches the official daily NSE Bhavcopy,
extracts clean OHLCV data for all stocks in stocks.csv,
and updates/appends to stocks_daily_ohlc.csv.
"""

import os
import io
import time
import datetime
import requests
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STOCKS_CSV = BASE_DIR / "stocks.csv"
OUTPUT_CSV = BASE_DIR / "stocks_daily_ohlc.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def fetch_bhavcopy_for_date(date_obj):
    """
    Tries to download the full Bhavcopy for a given date from NSE Archives.
    File naming format: sec_bhavdata_full_DDMMYYYY.csv
    """
    d_str = date_obj.strftime("%d%m%Y")
    url = f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d_str}.csv"
    
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=HEADERS, timeout=10)
    except Exception:
        pass

    try:
        r = session.get(url, headers=HEADERS, timeout=25)
        if r.status_code == 200 and len(r.content) > 1000:
            return r.text
        return None
    except Exception as e:
        print(f"[BHAVCOPY] Error downloading from {url}: {e}")
        return None

def update_daily_ohlc():
    today = datetime.date.today()
    print(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}] Checking daily Bhavcopy for: {today.strftime('%Y-%m-%d')}...")
    
    # 1. Try today's date first
    csv_text = fetch_bhavcopy_for_date(today)
    target_date = today
    
    # If today's file is not yet uploaded or market was closed, try prior business days
    if not csv_text:
        for back_day in range(1, 5):
            candidate = today - datetime.timedelta(days=back_day)
            if candidate.weekday() >= 5:  # skip Saturday/Sunday
                continue
            csv_text = fetch_bhavcopy_for_date(candidate)
            if csv_text:
                target_date = candidate
                print(f"[BHAVCOPY] Using latest available trading day archive: {candidate.strftime('%Y-%m-%d')}")
                break

    if not csv_text:
        print("[BHAVCOPY] No Bhavcopy archive available at this time.")
        return False

    # 2. Parse and clean raw data
    raw_df = pd.read_csv(io.StringIO(csv_text))
    raw_df.columns = [c.strip() for c in raw_df.columns]
    for col in ["SYMBOL", "SERIES"]:
        if col in raw_df.columns:
            raw_df[col] = raw_df[col].astype(str).str.strip()

    # 3. Filter symbols that match our master universe (stocks.csv)
    if STOCKS_CSV.exists():
        master_df = pd.read_csv(STOCKS_CSV)
        valid_symbols = set(master_df["SYMBOL"].astype(str).str.strip())
        matched = raw_df[raw_df["SYMBOL"].isin(valid_symbols)].copy()
    else:
        matched = raw_df[raw_df["SERIES"].isin(["EQ", "BE", "BZ"])].copy()

    clean_ohlc = matched[[
        "SYMBOL", "SERIES", "DATE1", "PREV_CLOSE", "OPEN_PRICE", "HIGH_PRICE",
        "LOW_PRICE", "CLOSE_PRICE", "AVG_PRICE", "TTL_TRD_QNTY", "TURNOVER_LACS",
        "NO_OF_TRADES", "DELIV_QTY", "DELIV_PER"
    ]].copy()

    clean_ohlc.rename(columns={
        "DATE1": "DATE",
        "OPEN_PRICE": "OPEN",
        "HIGH_PRICE": "HIGH",
        "LOW_PRICE": "LOW",
        "CLOSE_PRICE": "CLOSE",
        "TTL_TRD_QNTY": "VOLUME"
    }, inplace=True)

    clean_ohlc.sort_values(by=["SYMBOL", "SERIES"], inplace=True)
    clean_ohlc.to_csv(OUTPUT_CSV, index=False)

    print(f"[SUCCESS] Updated {OUTPUT_CSV.name} for {target_date.strftime('%Y-%m-%d')}")
    print(f"Total Stocks Processed: {len(clean_ohlc)} | File Size: {os.path.getsize(OUTPUT_CSV) / 1024:.1f} KB\n")
    return True

if __name__ == "__main__":
    update_daily_ohlc()
