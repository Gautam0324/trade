"""
Candle Store for Day Trader Pro V2.
In-memory multi-timeframe candle cache with SQLite persistent storage and deduplication.
"""

from typing import Dict, List, Any, Optional
import sqlite3
import threading
from pathlib import Path

class CandleStore:
    def __init__(self, db_path: Optional[str] = None, max_in_memory: int = 500):
        base_dir = Path(__file__).resolve().parent.parent.parent
        self.db_path_str = db_path if db_path else str(base_dir / "trader" / "ticks_market_data.db")
        self.is_memory = (self.db_path_str == ":memory:")
        self.max_in_memory = max_in_memory
        self._lock = threading.RLock()
        # In-memory storage: {symbol: {timeframe: [candle_dict, ...]}}
        self._history: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._mem_conn = None
        if self.is_memory:
            self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._init_db()

    def _get_connection(self):
        if self.is_memory:
            return self._mem_conn
        return sqlite3.connect(self.db_path_str, timeout=5)

    def _init_db(self):
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS market_candles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    volume REAL NOT NULL,
                    ticks_count INTEGER DEFAULT 0,
                    UNIQUE(symbol, timeframe, timestamp)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_candles_lookup ON market_candles (symbol, timeframe, timestamp)")
            conn.commit()
            if not self.is_memory:
                conn.close()
        except Exception as e:
            print(f"[CANDLE STORE DB INIT ERROR] {e}")

    def add_candle(self, candle: Dict[str, Any]):
        symbol = candle.get("symbol")
        tf = candle.get("timeframe", "1m")
        ts = candle.get("timestamp")

        if not symbol or not ts:
            return

        with self._lock:
            if symbol not in self._history:
                self._history[symbol] = {}
            if tf not in self._history[symbol]:
                self._history[symbol][tf] = []

            # Check if this candle timestamp already exists in memory
            candles = self._history[symbol][tf]
            if not any(c.get("timestamp") == ts for c in candles):
                candles.append(candle)
                if len(candles) > self.max_in_memory:
                    candles.pop(0)

        # Store in SQLite idempotently
        self._persist_candle(candle)

    def _persist_candle(self, c: Dict[str, Any]):
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT OR IGNORE INTO market_candles 
                (symbol, timeframe, timestamp, open, high, low, close, volume, ticks_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                c["symbol"], c["timeframe"], c["timestamp"],
                c["open"], c["high"], c["low"], c["close"], c["volume"],
                c.get("ticks_count", 0)
            ))
            conn.commit()
            if not self.is_memory:
                conn.close()
        except Exception as e:
            print(f"[CANDLE PERSIST ERROR] {c.get('symbol')} {c.get('timestamp')}: {e}")

    def get_candles(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> List[Dict[str, Any]]:
        with self._lock:
            candles = self._history.get(symbol.upper(), {}).get(timeframe, [])
            return [dict(c) for c in candles[-limit:]]

    def get_bars_dict(self, symbol: str, timeframe: str = "1m", limit: int = 100) -> Dict[str, List[Any]]:
        candles = self.get_candles(symbol, timeframe, limit=limit)
        return {
            "timestamp": [c["timestamp"] for c in candles],
            "open": [c["open"] for c in candles],
            "high": [c["high"] for c in candles],
            "low": [c["low"] for c in candles],
            "close": [c["close"] for c in candles],
            "volume": [c["volume"] for c in candles]
        }

    def load_from_db(self, symbol: str, timeframe: str = "1m", limit: int = 100):
        """Loads historical candles from SQLite into in-memory store."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=5)
            cur = conn.cursor()
            cur.execute("""
                SELECT symbol, timeframe, timestamp, open, high, low, close, volume, ticks_count
                FROM market_candles
                WHERE symbol = ? AND timeframe = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (symbol.upper(), timeframe, limit))
            rows = cur.fetchall()
            conn.close()

            with self._lock:
                if symbol.upper() not in self._history:
                    self._history[symbol.upper()] = {}
                if timeframe not in self._history[symbol.upper()]:
                    self._history[symbol.upper()][timeframe] = []

                # Rows are DESC, reverse to chronological ASC
                for r in reversed(rows):
                    c_dict = {
                        "symbol": r[0], "timeframe": r[1], "timestamp": r[2],
                        "open": r[3], "high": r[4], "low": r[5], "close": r[6],
                        "volume": r[7], "ticks_count": r[8], "status": "completed"
                    }
                    if not any(c.get("timestamp") == r[2] for c in self._history[symbol.upper()][timeframe]):
                        self._history[symbol.upper()][timeframe].append(c_dict)
        except Exception as e:
            print(f"[CANDLE LOAD ERROR] {symbol} {timeframe}: {e}")
