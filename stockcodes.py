import pandas as pd
import requests
from io import StringIO

url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5"
}

try:
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    df = pd.read_csv(StringIO(response.text))
    STOCKCODES = sorted(df["SYMBOL"].dropna().unique().tolist())

    print(f"Total stock codes: {len(STOCKCODES)}")
    print(STOCKCODES)
except Exception as e:
    # If NSE blocks direct request, fallback to session with cookie or print error
    print(f"Error fetching from NSE: {e}")
    s = requests.Session()
    s.get("https://www.nseindia.com", headers=headers, timeout=15)
    response = s.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    df = pd.read_csv(StringIO(response.text))
    STOCKCODES = sorted(df["SYMBOL"].dropna().unique().tolist())
    print(f"Total stock codes: {len(STOCKCODES)}")
    print(STOCKCODES)
