import pytest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

import indicators
from indicators import calculate_macd, calculate_supertrend, validate_ohlcv_bars


def test_macd_calculation():
    closes = [100.0 + i for i in range(50)]
    macd_res = calculate_macd(closes)
    assert "macd" in macd_res
    assert "signal" in macd_res
    assert "hist" in macd_res
    assert macd_res["macd"][-1] is not None
    assert macd_res["macd"][-1] > 0

def test_supertrend_bullish():
    # Steadily rising series
    highs = [100.0 + i*2 for i in range(30)]
    lows = [98.0 + i*2 for i in range(30)]
    closes = [99.0 + i*2 for i in range(30)]
    st_res = calculate_supertrend(highs, lows, closes, period=10, multiplier=3.0)
    assert st_res["direction"][-1] == 1
    assert st_res["supertrend"][-1] < closes[-1]

def test_ohlcv_validation_rejects_bad_bars():
    bad_bars = {
        "open": [100.0, 105.0],
        "high": [102.0, 104.0], # High < Open for bar 1!
        "low": [98.0, 99.0],
        "close": [101.0, 102.0],
        "volume": [1000, 2000]
    }
    is_valid, _, err = validate_ohlcv_bars(bad_bars)
    assert is_valid is False
    assert "High" in err
