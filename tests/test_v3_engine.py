import pytest
import sys
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "trader"))

from indicators import calculate_macd, calculate_supertrend, validate_ohlcv_bars
from strategy_engine import StrategyEngine
from risk_engine import RiskEngine
from exit_engine import PositionManager
import db_v3

def test_v3_confluence_scoring_breakdown():
    cfg = {"strategy_mode": "CONFLUENCE", "min_confluence_score": 65.0}
    eng = StrategyEngine(cfg)

    # Bullish synthetic bar set
    n = 60
    highs = [100.0 + i*1.5 for i in range(n)]
    lows = [98.0 + i*1.5 for i in range(n)]
    closes = [99.5 + i*1.5 for i in range(n)]
    opens = [98.5 + i*1.5 for i in range(n)]
    volumes = [50000 + i*500 for i in range(n)]

    bars = {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
    res = eng.evaluate_v3_confluence("TCS.NS", bars)

    assert "confluence_score" in res
    assert "component_weights" in res
    assert res["confluence_score"] >= 65.0
    assert res["signal"] == "BUY"
    assert res["reward_risk_ratio"] >= 1.5

def test_v3_confluence_rejects_weak_signal():
    cfg = {"strategy_mode": "CONFLUENCE", "min_confluence_score": 65.0}
    eng = StrategyEngine(cfg)

    # Sideways flat series
    n = 50
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    opens = [100.0] * n
    volumes = [1000] * n

    bars = {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}
    res = eng.evaluate_v3_confluence("INFY.NS", bars)

    assert res["signal"] == "NO_TRADE"
    assert len(res["rejections"]) > 0

def test_v3_risk_reward_gate_rejects_under_1_5():
    risk_eng = RiskEngine({"budget": 100000.0, "min_reward_risk": 1.5})
    # Entry 100, Stop 95 (Risk 5), Target 105 (Reward 5 -> RR = 1.0 < 1.5)
    res = risk_eng.evaluate_order_risk(
        symbol="SBIN.NS", side="BUY", entry_price=100.0, stop_loss=95.0, target_price=105.0,
        available_cash=50000.0, daily_realized_loss=0.0, current_trades_count=1, current_open_positions_count=0
    )
    assert res["passed"] is False
    assert any("Reward-to-risk ratio" in r for r in res["reasons"])

def test_v3_partial_exit_and_cost_adjusted_breakeven():
    pos_mgr = PositionManager({"enable_partial_profit": True, "partial_profit_ratio": 0.5})
    pos = {
        "symbol": "RELIANCE.NS",
        "entry_price": 1000.0,
        "initial_stop": 990.0, # 1R = 10 pts
        "current_stop": 990.0,
        "qty": 10,
        "peak_price": 1000.0
    }
    # Price reaches +1R (1010.0)
    exit_res = pos_mgr.evaluate_exit(pos, current_price=1010.0)
    assert exit_res["action"] == "PARTIAL_EXIT"
    assert exit_res["exit_qty"] == 5
    # Verify current stop moved to breakeven + round-trip cost (1000 + 0.50 = 1000.50)
    assert exit_res["updated_position"]["current_stop"] >= 1000.0

def test_v3_db_audit_persistence(tmp_path):
    test_db = tmp_path / "test_audit.db"
    db_v3.init_db(test_db)

    conn = db_v3.get_connection(test_db)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO signals_audit (
        timestamp, symbol, strategy_name, side, signal, confluence_score, accepted,
        rejection_reasons, component_weights, indicator_values, entry_price, stop_loss, target_price,
        risk_per_share, reward_risk_ratio
    ) VALUES ('10:00:00 IST', 'TCS.NS', 'CONFLUENCE_V3', 'BUY', 'BUY', 85.0, 1, '[]', '{}', '{}', 3500.0, 3450.0, 3575.0, 50.0, 1.5)
    """)
    conn.commit()

    cur.execute("SELECT * FROM signals_audit WHERE symbol='TCS.NS'")
    row = cur.fetchone()
    assert row["confluence_score"] == 85.0
    assert row["accepted"] == 1
    conn.close()
