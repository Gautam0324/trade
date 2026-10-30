"""
Candle Recovery for Day Trader Pro V2.
Reconciles missing historical candles across disconnections using Angel One SmartAPI.
"""

from typing import Dict, Any, List, Optional
import datetime
import zoneinfo

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class CandleRecovery:
    def __init__(self, smart_connect, instrument_manager, candle_store):
        self.smart_connect = smart_connect
        self.instrument_manager = instrument_manager
        self.candle_store = candle_store

    def recover_missing_candles(self, symbol: str, from_time: str, to_time: str, timeframe: str = "1m") -> int:
        if not self.smart_connect:
            return 0

        inst = self.instrument_manager.get_by_symbol(symbol)
        if not inst:
            return 0

        token = inst["token"]
        exch = inst["exchange"]

        interval = "ONE_MINUTE" if timeframe == "1m" else "FIVE_MINUTE"

        try:
            resp = self.smart_connect.getCandleData({
                "exchange": exch,
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_time,
                "todate": to_time
            })

            if not resp or not resp.get("status") or not resp.get("data"):
                return 0

            recovered = 0
            for bar in resp["data"]:
                # Bar format: [timestamp, open, high, low, close, volume]
                raw_ts = bar[0]
                # Format to YYYY-MM-DD HH:MM
                ts_str = raw_ts.replace("T", " ")[:16]

                c_dict = {
                    "symbol": symbol.upper(),
                    "timeframe": timeframe,
                    "timestamp": ts_str,
                    "open": float(bar[1]),
                    "high": float(bar[2]),
                    "low": float(bar[3]),
                    "close": float(bar[4]),
                    "volume": float(bar[5]) if len(bar) > 5 else 0.0,
                    "ticks_count": 0,
                    "status": "completed"
                }
                self.candle_store.add_candle(c_dict)
                recovered += 1

            return recovered
        except Exception as e:
            print(f"[CANDLE RECOVERY ERROR] {symbol} {from_time} -> {to_time}: {e}")
            return 0

