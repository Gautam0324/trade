"""
Walk-Forward Optimization Analyzer for Day Trader Pro V2.
Evaluates out-of-sample stability across rolling chronological windows.
"""

from typing import List, Dict, Any
from .backtest_engine import BacktestEngine
from .performance_metrics import PerformanceMetrics

class WalkForwardAnalyzer:
    def __init__(self, backtest_engine: BacktestEngine):
        self.engine = backtest_engine

    def run_walk_forward(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        window_size: int = 150,
        step_size: int = 50,
        strategy_name: str = "CONFLUENCE"
    ) -> List[Dict[str, Any]]:
        n = len(candles)
        if n < window_size:
            return []

        results = []
        window_idx = 1
        start = 0

        while start + window_size <= n:
            window_candles = candles[start : start + window_size]
            # Split window into 70% in-sample (train) and 30% out-of-sample (test)
            split_point = int(window_size * 0.7)
            in_sample = window_candles[:split_point]
            out_of_sample = window_candles[split_point:]

            is_res = self.engine.run_backtest(symbol, in_sample, strategy_name)
            oos_res = self.engine.run_backtest(symbol, out_of_sample, strategy_name)

            results.append({
                "window": window_idx,
                "start_time": window_candles[0]["timestamp"],
                "end_time": window_candles[-1]["timestamp"],
                "in_sample_metrics": is_res["metrics"],
                "out_of_sample_metrics": oos_res["metrics"]
            })

            start += step_size
            window_idx += 1

        return results

