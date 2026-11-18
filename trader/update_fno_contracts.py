"""
Fetch and update F&O contracts cache from Angel One OpenAPIScripMaster.
Focuses on:
- Major Indices: NIFTY, BANKNIFTY, FINNIFTY, SENSEX, BANKEX
- Top High-Volume Liquid F&O Stocks:
  RELIANCE, HDFCBANK, ICICIBANK, SBIN, BHARTIARTL, TATASTEEL, TCS, INFY,
  BAJFINANCE, LT, AXISBANK, MARUTI, TITAN, KOTAKBANK, BSE
"""

import json
import os
import sys
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = BASE_DIR / "historical_data" / "fno_contracts.json"

TARGET_SYMBOLS = {
    # Indices
    "NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX",
    # Top Liquid Stocks
    "RELIANCE", "HDFCBANK", "ICICIBANK", "SBIN", "BHARTIARTL",
    "TATASTEEL", "TCS", "INFY", "BAJFINANCE", "LT",
    "AXISBANK", "MARUTI", "TITAN", "KOTAKBANK", "BSE"
}

def update_contracts():
    print("[1/3] Downloading Angel One Scrip Master JSON...")
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }
    
    resp = requests.get(url, headers=headers, timeout=60)
    if resp.status_code != 200:
        print(f"[ERROR] Failed to fetch scrip master, HTTP {resp.status_code}")
        sys.exit(1)
        
    raw_data = resp.json()
    print(f"[2/3] Downloaded {len(raw_data)} total instruments. Filtering for Target Universe...")
    
    filtered_contracts = []
    symbol_counts = {}
    
    for item in raw_data:
        name = item.get("name", "")
        exch = item.get("exch_seg", "")
        itype = item.get("instrumenttype", "")
        
        # Only options on NSE (NFO) and BSE (BFO)
        if exch in ("NFO", "BFO") and itype in ("OPTSTK", "OPTIDX"):
            if name in TARGET_SYMBOLS:
                contract = {
                    "token": item.get("token"),
                    "symbol": item.get("symbol"),
                    "name": name,
                    "expiry": item.get("expiry"),
                    "strike": item.get("strike"),
                    "lotsize": item.get("lotsize"),
                    "instrumenttype": itype,
                    "exch_seg": exch,
                    "tick_size": item.get("tick_size")
                }
                filtered_contracts.append(contract)
                symbol_counts[name] = symbol_counts.get(name, 0) + 1
                
    print(f"[3/3] Found {len(filtered_contracts)} contracts across {len(symbol_counts)} symbols:")
    for sym, count in sorted(symbol_counts.items()):
        print(f"  - {sym}: {count} contracts")
        
    os.makedirs(OUTPUT_FILE.parent, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered_contracts, f, indent=2)
        
    print(f"[SUCCESS] Saved {len(filtered_contracts)} contracts to {OUTPUT_FILE}")

if __name__ == "__main__":
    update_contracts()
