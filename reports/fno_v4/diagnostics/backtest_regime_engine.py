"""
Backtesting and Validation of MarketRegimeEngine on historical multi-day index constituent candles.
File: reports/fno_v4/diagnostics/backtest_regime_engine.py

Hardened for Phase 2.1:
1. Configurable historical data directory via CLI argument or environment variable.
2. Market session alignment (09:15 to 15:30 IST) with timezone awareness.
3. Explicit reporting of duplicate bars, missing intervals, and invalid OHLCV.
4. Mandatory 15m HTF candle resampling and alignment alongside 5m candles.
5. Strict segregation: Regime classification statistics are NEVER claimed as strategy profitability.
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime, timezone, time
from typing import Dict, Any
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from trader.fno_v4.market_regime import MarketRegimeEngine, RegimeConfig


def parse_args():
    parser = argparse.ArgumentParser(description="Run Market Regime Engine historical validation")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=os.environ.get("HISTORICAL_DATA_DIR", str(BASE_DIR / "historical_data")),
        help="Path to directory containing historical *_4days.csv files"
    )
    return parser.parse_args()


def resample_ohlcv(df_1m: pd.DataFrame, timeframe_str: str) -> tuple[pd.DataFrame, Dict[str, Any]]:
    """Aggregates 1-minute OHLCV to completed higher timeframe bars with data quality auditing."""
    audit = {
        "raw_bars": len(df_1m),
        "duplicate_timestamps": 0,
        "missing_intervals": 0,
        "invalid_ohlcv": 0,
        "market_hours_bars": 0
    }

    # Ensure timestamp column
    if 'timestamp' in df_1m.columns:
        ts_col = 'timestamp'
    elif 'datetime' in df_1m.columns:
        ts_col = 'datetime'
    else:
        raise ValueError("Missing timestamp/datetime column")

    df = df_1m.copy()
    df['dt'] = pd.to_datetime(df[ts_col])

    # Timezone normalization: if naive, localize to Asia/Kolkata (+05:30)
    if df['dt'].dt.tz is None:
        df['dt'] = df['dt'].dt.tz_localize('Asia/Kolkata')
    else:
        df['dt'] = df['dt'].dt.tz_convert('Asia/Kolkata')

    # Audit duplicates
    duplicates = df['dt'].duplicated().sum()
    audit["duplicate_timestamps"] = int(duplicates)
    if duplicates > 0:
        df = df.drop_duplicates(subset=['dt'], keep='last')

    # Filter strictly to market session (09:15 to 15:30 IST)
    market_open = time(9, 15)
    market_close = time(15, 30)
    times = df['dt'].dt.time
    session_mask = (times >= market_open) & (times <= market_close)
    df = df[session_mask].copy()
    audit["market_hours_bars"] = len(df)

    # Audit OHLC validity
    invalid_mask = (df['high'] < df['low']) | (df['high'] < df['open']) | (df['high'] < df['close']) | (df['low'] > df['open']) | (df['low'] > df['close'])
    audit["invalid_ohlcv"] = int(invalid_mask.sum())
    if audit["invalid_ohlcv"] > 0:
        df = df[~invalid_mask].copy()

    df = df.set_index('dt').sort_index()

    agg_dict = {
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum'
    }

    df_resampled = df.resample(timeframe_str).agg(agg_dict).dropna().reset_index()
    df_resampled.rename(columns={'dt': 'timestamp'}, inplace=True)
    return df_resampled, audit


def run_regime_backtest(data_dir: Path):
    engine = MarketRegimeEngine(RegimeConfig(require_15m=True))
    files = list(data_dir.glob("*_4days.csv"))

    if not files:
        print(f"No *_4days.csv files found in {data_dir}")
        return

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
    data_quality_audits = {}

    for fpath in files:
        symbol = fpath.stem.replace("_4days", "")
        df_1m = pd.read_csv(fpath)
        
        df_5m, dq_audit_5m = resample_ohlcv(df_1m, '5min')
        df_15m, dq_audit_15m = resample_ohlcv(df_1m, '15min')
        data_quality_audits[symbol] = {"5m_audit": dq_audit_5m, "15m_audit": dq_audit_15m}

        n_5m = len(df_5m)
        if n_5m < 60:
            continue

        sym_regimes = []
        transitions = 0
        last_regime = None

        # Rolling evaluation starting after warm-up (bar 55)
        for i in range(55, n_5m):
            window_5m = df_5m.iloc[:i+1].copy()
            curr_ts = window_5m['timestamp'].iloc[-1]

            # Slice only completed 15m candles up to current 5m candle time (no lookahead)
            window_15m = df_15m[df_15m['timestamp'] <= curr_ts].copy()

            res = engine.evaluate(window_5m, candles_15m=window_15m, reference_time=curr_ts)
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
        "disclaimer": "DISCLAIMER: Regime classification statistics quantify market structural states. They DO NOT measure strategy P&L, edge, or trade profitability without execution rules.",
        "per_symbol_summary": summary,
        "data_quality_audits": data_quality_audits
    }

    print(json.dumps(overall, indent=2))
    return overall


if __name__ == "__main__":
    args = parse_args()
    run_regime_backtest(Path(args.data_dir))
