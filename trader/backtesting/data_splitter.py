"""
Chronological Data Splitter for Day Trader Pro V2.
Partitions historical OHLCV candles chronologically without data leakage or random shuffling.
"""

from typing import List, Dict, Any, Tuple

class ChronologicalDataSplitter:
    @staticmethod
    def split(
        candles: List[Dict[str, Any]],
        train_ratio: float = 0.60,
        val_ratio: float = 0.20,
        test_ratio: float = 0.20
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        n = len(candles)
        if n == 0:
            return [], [], []

        train_end = int(n * train_ratio)
        val_end = int(n * (train_ratio + val_ratio))

        train_data = candles[:train_end]
        val_data = candles[train_end:val_end]
        test_data = candles[val_end:]

        return train_data, val_data, test_data

