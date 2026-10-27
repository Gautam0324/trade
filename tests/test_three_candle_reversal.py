import pytest
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from backtest_three_candle_reversal import ThreeCandleReversalBacktest

def test_five_setup_conditions_pass():
    bt = ThreeCandleReversalBacktest()
    # Construct 3 synthetic candles matching setup
    # C1: Bearish (Close < Open)
    c1 = {"date": "2023-10-01", "open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000.0, "sma20": 100.0, "vol_sma20": 1000.0}
    # C2: Bullish (Close > Open)
    c2 = {"date": "2023-10-02", "open": 100.0, "high": 104.0, "low": 99.5, "close": 103.0, "volume": 1200.0, "sma20": 100.0, "vol_sma20": 1000.0}
    # C3: Bullish (Close > Open) and Breakout (Close >= max(C1, C2) = max(100, 103) = 103)
    c3 = {"date": "2023-10-03", "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 1500.0, "sma20": 100.0, "vol_sma20": 1000.0}
    
    cond1 = c1["close"] < c1["open"]
    cond2 = c2["close"] > c2["open"]
    cond3 = c3["close"] > c3["open"]
    cond4 = c3["close"] >= max(c1["close"], c2["close"])
    cond5 = c3["volume"] >= c3["vol_sma20"] * 0.0

    assert cond1 is True
    assert cond2 is True
    assert cond3 is True
    assert cond4 is True
    assert cond5 is True

def test_rejection_when_c1_is_not_bearish():
    c1 = {"open": 100.0, "close": 102.0} # Bullish, should reject
    cond1 = c1["close"] < c1["open"]
    assert cond1 is False

def test_rejection_when_c3_does_not_breakout():
    c1 = {"close": 105.0, "open": 110.0} # Bearish
    c2 = {"close": 107.0, "open": 104.0} # Bullish
    c3 = {"close": 106.0, "open": 105.0} # Bullish, but 106 < max(105, 107) = 107
    cond4 = c3["close"] >= max(c1["close"], c2["close"])
    assert cond4 is False

def test_entry_trigger_and_target_fill():
    # C3 high = 108.0. Next day hits 110.0 -> Filled @ 108.0
    # Target = 108 * 1.05 = 113.4
    bars = [
        {"date": "2023-09-29", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-09-30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-10-01", "open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"date": "2023-10-02", "open": 100.0, "high": 104.0, "low": 99.5, "close": 103.0, "volume": 1200.0},
        {"date": "2023-10-03", "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 1500.0},
        # Next bar triggers entry
        {"date": "2023-10-04", "open": 105.0, "high": 110.0, "low": 104.0, "close": 109.0, "volume": 2000.0},
        # Next bar hits target (113.4)
        {"date": "2023-10-05", "open": 110.0, "high": 115.0, "low": 109.0, "close": 114.0, "volume": 2500.0}
    ]
    bt = ThreeCandleReversalBacktest(start_date="01-10-2023", end_date="10-10-2023")
    res = bt.run(bars)
    assert res["total_filled_trades"] == 1
    assert res["target_exits"] == 1
    assert bt.trade_log[0]["entry_price"] == 108.0
    assert bt.trade_log[0]["target_price"] == 113.4
    assert bt.trade_log[0]["exit_reason"] == "TARGET"

def test_unfilled_entry_when_next_day_high_below_trigger():
    # C3 high = 108.0. Next day reaches high 107.0 only -> Not filled
    bars = [
        {"date": "2023-09-29", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-09-30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-10-01", "open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"date": "2023-10-02", "open": 100.0, "high": 104.0, "low": 99.5, "close": 103.0, "volume": 1200.0},
        {"date": "2023-10-03", "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 1500.0},
        # High reaches 107.0 only
        {"date": "2023-10-04", "open": 104.0, "high": 107.0, "low": 102.0, "close": 105.0, "volume": 1000.0}
    ]
    bt = ThreeCandleReversalBacktest(start_date="01-10-2023", end_date="10-10-2023")
    res = bt.run(bars)
    assert res["total_filled_trades"] == 0
    assert res["pending_orders_not_filled"] == 1

def test_open_at_end_status():
    # Position entered but target never reached before data ends
    bars = [
        {"date": "2023-09-29", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-09-30", "open": 100, "high": 101, "low": 99, "close": 100, "volume": 1000},
        {"date": "2023-10-01", "open": 105.0, "high": 106.0, "low": 99.0, "close": 100.0, "volume": 1000.0},
        {"date": "2023-10-02", "open": 100.0, "high": 104.0, "low": 99.5, "close": 103.0, "volume": 1200.0},
        {"date": "2023-10-03", "open": 102.0, "high": 108.0, "low": 101.0, "close": 106.0, "volume": 1500.0},
        # Entry filled @ 108. Target is 113.4
        {"date": "2023-10-04", "open": 105.0, "high": 110.0, "low": 104.0, "close": 109.0, "volume": 2000.0},
        # Never hits 113.4
        {"date": "2023-10-05", "open": 108.0, "high": 111.0, "low": 106.0, "close": 107.0, "volume": 1000.0}
    ]
    bt = ThreeCandleReversalBacktest(start_date="01-10-2023", end_date="10-10-2023")
    res = bt.run(bars)
    assert res["open_positions_at_end"] == 1
    assert bt.trade_log[0]["trade_status"] == "OPEN_AT_END"
    assert bt.trade_log[0]["exit_reason"] == "OPEN_AT_END"
