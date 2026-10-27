"""
High-Reliability 1-Minute Historical Candle Downloader for NSE Stocks via Angel One SmartAPI.
Features:
- Respects SmartAPI rate limit (~1 request / 1.5 seconds) with automatic exponential backoff.
- Downloads backwards from today to the earliest available date.
- Real-time chunk appending to CSV (no memory bloat, partial progress is always saved).
- Robust checkpoint tracking in download_progress.json (can be stopped and resumed anytime).
- Multi-token support across all ~2,700 NSE instruments.
"""

import os
import sys
import json
import time
import requests
import datetime
import pandas as pd
from datetime import datetime as dt, timedelta
from SmartApi import SmartConnect
import pyotp

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "historical_data")
PROGRESS_FILE = os.path.join(OUTPUT_DIR, "download_progress.json")
MASTER_FILE = os.path.join(OUTPUT_DIR, "nse_instruments.json")
SECRETS_FILE = os.path.join(PROJECT_ROOT, "trader", "secrets.json")

# Target 10 years or earliest available
END_DATE = dt.now()
START_DATE = END_DATE - timedelta(days=365 * 10)
DAYS_PER_CHUNK = 25  # ~6,000 to 7,500 candles per chunk (well within 10,000 limit)
RATE_LIMIT_DELAY = 1.35  # seconds between requests to prevent HTTP 429

def get_smart_api_session():
    if not os.path.exists(SECRETS_FILE):
        raise FileNotFoundError(f"Secrets file not found at {SECRETS_FILE}")
    with open(SECRETS_FILE, "r") as f:
        secrets = json.load(f)

    smart_api = SmartConnect(api_key=secrets["smartapi_api_key"])
    totp = pyotp.TOTP(secrets["smartapi_totp_secret"]).now()
    session = smart_api.generateSession(
        secrets["smartapi_client_code"],
        secrets["smartapi_pin"],
        totp
    )
    if not session or not session.get("status"):
        raise RuntimeError(f"SmartAPI Authentication failed: {session.get('message')}")
    print(f"Authenticated with Angel One SmartAPI. Client: {secrets['smartapi_client_code']}")
    return smart_api

def load_master_instruments():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    if os.path.exists(MASTER_FILE):
        try:
            with open(MASTER_FILE, "r") as f:
                data = json.load(f)
                if len(data) > 1000:
                    return data
        except Exception:
            pass

    print("Fetching OpenAPIScripMaster from Angel One...")
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    res = requests.get(url, timeout=30)
    data = res.json()

    nse_stocks = {}
    for item in data:
        if item.get("exch_seg") == "NSE" and item.get("symbol", "").endswith("-EQ"):
            name = item.get("name")
            nse_stocks[name] = {
                "token": item.get("token"),
                "symbol": item.get("symbol"),
                "name": name
            }

    with open(MASTER_FILE, "w") as f:
        json.dump(nse_stocks, f, indent=2)
    return nse_stocks

def load_progress():
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"completed": [], "stock_stats": {}}

def save_progress(progress):
    try:
        with open(PROGRESS_FILE, "w") as f:
            json.dump(progress, f, indent=2)
    except Exception as e:
        print(f"Error saving progress: {e}")

def generate_chunks_descending(start_dt, end_dt, days_chunk=DAYS_PER_CHUNK):
    """Generate chunks going backwards from today into the past."""
    chunks = []
    curr_end = end_dt
    while curr_end > start_dt:
        curr_start = max(curr_end - timedelta(days=days_chunk), start_dt)
        chunks.append((
            curr_start.strftime("%Y-%m-%d 09:15"),
            curr_end.strftime("%Y-%m-%d 15:30")
        ))
        curr_end = curr_start - timedelta(minutes=1)
    return chunks

def download_stock_historical(smart_api, symbol, token):
    csv_file = os.path.join(OUTPUT_DIR, f"{symbol}_1min.csv")
    chunks = generate_chunks_descending(START_DATE, END_DATE)
    
    total_candles_added = 0
    header_written = False

    # Check if CSV exists to resume or deduplicate
    if os.path.exists(csv_file):
        try:
            df = pd.read_csv(csv_file)
            if len(df) > 0 and "timestamp" in df.columns:
                header_written = True
                total_candles_added = len(df)
        except Exception:
            pass

    empty_consecutive = 0

    for idx, (fdate, tdate) in enumerate(chunks, 1):
        params = {
            "exchange": "NSE",
            "symboltoken": str(token),
            "interval": "ONE_MINUTE",
            "fromdate": fdate,
            "todate": tdate
        }

        candles = None
        for attempt in range(4):
            try:
                time.sleep(RATE_LIMIT_DELAY)
                res = smart_api.getCandleData(params)
                if res and res.get("status"):
                    candles = res.get("data") or []
                    break
                else:
                    msg = str(res.get("message") if res else "")
                    if "exceeding access rate" in msg.lower():
                        backoff = 3.0 * (attempt + 1)
                        # print(f"Rate limited, sleeping {backoff}s...")
                        time.sleep(backoff)
                    else:
                        time.sleep(1.0)
            except Exception as e:
                err_str = str(e).lower()
                if "exceeding access rate" in err_str:
                    backoff = 4.0 * (attempt + 1)
                    # print(f"Rate limited, backing off {backoff}s...")
                    time.sleep(backoff)
                else:
                    time.sleep(2.0)

        if candles:
            empty_consecutive = 0
            df_chunk = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
            df_chunk.to_csv(csv_file, mode="a", header=not header_written, index=False)
            header_written = True
            total_candles_added += len(candles)
            if idx % 10 == 0 or idx == len(chunks):
                print(f"[{symbol}] Progress: Chunk {idx}/{len(chunks)} ({fdate[:7]}) -> Total Candles: {total_candles_added}")
        else:
            empty_consecutive += 1
            # If 6 consecutive chunks (~150 days) return 0 candles in the past, stock wasn't listed yet
            if empty_consecutive >= 6:
                # print(f"[{symbol}] Reached earliest listing date at {fdate}.")
                break

    # Final cleanup & sort by timestamp ascending
    if os.path.exists(csv_file) and total_candles_added > 0:
        try:
            df_final = pd.read_csv(csv_file)
            df_final.drop_duplicates(subset=["timestamp"], keep="last", inplace=True)
            df_final.sort_values(by="timestamp", inplace=True)
            df_final.to_csv(csv_file, index=False)
            total_candles_added = len(df_final)
        except Exception:
            pass

    return total_candles_added

def run_downloader():
    print("=" * 65)
    print("ANGEL ONE SMARTAPI — 10-YEAR 1-MINUTE CANDLE DOWNLOADER")
    print(f"Destination: {OUTPUT_DIR}")
    print("=" * 65)

    smart_api = get_smart_api_session()
    instruments = load_master_instruments()
    progress = load_progress()

    ohlc_file = os.path.join(PROJECT_ROOT, "stocks_daily_ohlc.csv")
    symbols_order = []
    if os.path.exists(ohlc_file):
        df_ohlc = pd.read_csv(ohlc_file)
        symbols = df_ohlc.iloc[:, 0].dropna().unique().tolist()
        symbols_order = [s.strip() for s in symbols if s.strip() in instruments]

    for s in instruments:
        if s not in symbols_order:
            symbols_order.append(s)

    completed = set(progress.get("completed", []))
    remaining = [s for s in symbols_order if s not in completed]

    print(f"Total Universe: {len(symbols_order)} | Completed: {len(completed)} | Remaining: {len(remaining)}")

    for idx, symbol in enumerate(remaining, 1):
        token = instruments[symbol]["token"]
        print(f"\n[{idx}/{len(remaining)}] Starting {symbol} (Token: {token})...")
        try:
            candles_count = download_stock_historical(smart_api, symbol, token)
            progress.setdefault("completed", []).append(symbol)
            progress.setdefault("stock_stats", {})[symbol] = {
                "candles": candles_count,
                "file": f"{symbol}_1min.csv",
                "completed_at": dt.now().strftime("%Y-%m-%d %H:%M:%S")
            }
            save_progress(progress)
            print(f"[{symbol}] DONE. Total Candles: {candles_count}")
        except Exception as e:
            print(f"[{symbol}] Error occurred: {e}")
            # Try re-authenticating if session expired
            try:
                smart_api = get_smart_api_session()
            except Exception:
                pass

    print("\nAll downloads completed!")

if __name__ == "__main__":
    run_downloader()
