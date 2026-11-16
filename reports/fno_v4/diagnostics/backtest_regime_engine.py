"""
Backtesting and Validation of MarketRegimeEngine on historical multi-day index constituent candles.
File: reports/fno_v4/diagnostics/backtest_regime_engine.py
"""

import json
import sys
from pathlib import Path
import pandas as pd
import numpy as np
from datetime import datetime, timezone

BASE_DIR = Path("d:/Trade-agent/day-trader-skill")
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from trader.fno_v4.market_regime import MarketRegimeEngine, RegimeConfig
HISTORICAL_DIR = BASE_DIR / "historical_data"

def resample_to_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Aggregates 1-minute OHLCV to completed 5-minute OHLCV."""
    if 'timestamp' in df_1m.columns:
        df_1m['dt'] = pd.to_datetime(df_1m['timestamp'])
    else:
        df_1m['dt'] = pd.to_datetime(df_1m['datetime'])
    
    df_1m = df_1m.set_index('dt').sort_index()
    
    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }
    
    df_5m = df_1m.resample('5min').agg(agg_dict).dropna().reset_index()
    df_5m.rename(columns={'dt': 'timestamp'}, inplace=True)
    return df_5m

def run_regime_backtest():
    engine = MarketRegimeEngine()
    files = list(HISTORICAL_DIR.glob("*_4days.csv"))
    
    summary = {}
    total_evaluated_candles = 0
    regime_counts = {
        "STRONG_TREND": 0,
        "WEAK_TREND": 0,
        "SIDEWAYS": 0,
        "HIGH_VOLATILITY": 0,
        "NO_TRADE": 0
    }
    total_transitions = 0
    
    for fpath in files:
        symbol = fpath.stem.replace("_4days", "")
        df_1m = pd.read_csv(fpath)
        df_5m = resample_to_5m(df_1m)
        
        n_bars = len(df_5m)
        if n_bars < 60:
            continue
            
        sym_regimes = []
        transitions = 0
        last_regime = None
        
        # Rolling evaluation of 5m bars starting at bar 55
        for i in range(55, n_bars):
            window = df_5m.iloc[:i+1].copy()
            ref_time = window['timestamp'].iloc[-1]
            if ref_time.tzinfo is None:
                ref_time = ref_time.replace(tzinfo=timezone.utc)
                
            res = engine.evaluate(window, reference_time=ref_time)
            regime = res.regime
            sym_regimes.append(regime)
            
            if last_regime is not None and regime != last_regime:
                transitions += 1
            last_regime = regime
            
            regime_counts[regime] = regime_counts.get(regime, 0) + 1
            total_evaluated_candles += 1
            
        total_transitions += transitions
        
        series = pd.Series(sym_regimes)
        counts = series.value_counts().to_dict()
        summary[symbol] = {
            "total_5m_bars_evaluated": len(sym_regimes),
            "counts": counts,
            "transitions": transitions,
            "pct_no_trade": round((counts.get("NO_TRADE", 0) / len(sym_regimes)) * 100.0, 2) if len(sym_regimes) > 0 else 0.0
        }
        
    overall = {
        "total_candles_evaluated": total_evaluated_candles,
        "regime_distribution": {
            k: {
                "count": v,
                "percentage": round((v / total_evaluated_candles) * 100.0, 2) if total_evaluated_candles > 0 else 0.0
            } for k, v in regime_counts.items()
        },
        "total_transitions": total_transitions,
        "avg_transitions_per_100_bars": round((total_transitions / total_evaluated_candles) * 100.0, 2) if total_evaluated_candles > 0 else 0.0,
        "per_symbol_summary": summary
    }
    
    print(json.dumps(overall, indent=2))
    return overall

if __name__ == "__main__":
    run_regime_backtest()
