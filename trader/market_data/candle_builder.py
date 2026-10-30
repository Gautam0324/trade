"""
Live Candle Builder for Day Trader Pro V2.
Constructs rigorous 1-minute and 5-minute OHLCV candles from real-time tick streams.
Aligned to Asia/Kolkata market clock.
"""

from typing import Dict, Any, List, Optional, Callable
import datetime
import zoneinfo

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class Candle:
    def __init__(self, symbol: str, timeframe: str, start_time: datetime.datetime):
        self.symbol = symbol
        self.timeframe = timeframe  # "1m" or "5m"
        self.start_time = start_time
        self.timestamp_str = start_time.strftime("%Y-%m-%d %H:%M")
        self.open: float = 0.0
        self.high: float = 0.0
        self.low: float = 0.0
        self.close: float = 0.0
        self.volume: float = 0.0
        self.ticks_count: int = 0
        self.status: str = "forming"  # "forming" or "completed"
        self._initial_cum_vol: Optional[float] = None
        self._last_cum_vol: Optional[float] = None

    def update_tick(self, ltp: float, cum_volume: Optional[float] = None, inc_volume: Optional[float] = None):
        if self.ticks_count == 0:
            self.open = ltp
            self.high = ltp
            self.low = ltp
            self.close = ltp
            if cum_volume is not None:
                self._initial_cum_vol = cum_volume
                self._last_cum_vol = cum_volume
        else:
            if ltp > self.high:
                self.high = ltp
            if ltp < self.low:
                self.low = ltp
            self.close = ltp

        self.ticks_count += 1

        # Handle Volume aggregation
        if inc_volume is not None and inc_volume > 0:
            self.volume += inc_volume
        elif cum_volume is not None and self._initial_cum_vol is not None:
            # Cumulative volume delta
            delta = cum_volume - self._initial_cum_vol
            if delta >= 0:
                self.volume = delta
            self._last_cum_vol = cum_volume

    def complete(self):
        self.status = "completed"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp_str,
            "open": round(self.open, 4),
            "high": round(self.high, 4),
            "low": round(self.low, 4),
            "close": round(self.close, 4),
            "volume": round(self.volume, 2),
            "ticks_count": self.ticks_count,
            "status": self.status
        }

    def to_bar_tuple(self) -> List[Any]:
        # Compatible with legacy multi_live_trader format: [timestamp, open, high, low, close, volume]
        return [self.timestamp_str, self.open, self.high, self.low, self.close, self.volume]


class CandleBuilder:
    def __init__(self, on_candle_completed: Optional[Callable[[Dict[str, Any]], None]] = None):
        self.on_candle_completed = on_candle_completed
        # Current forming candles: {symbol: {"1m": Candle, "5m": Candle}}
        self._forming: Dict[str, Dict[str, Candle]] = {}

    def _get_interval_start(self, dt: datetime.datetime, minutes: int) -> datetime.datetime:
        discard = datetime.timedelta(
            minutes=dt.minute % minutes,
            seconds=dt.second,
            microseconds=dt.microsecond
        )
        return dt - discard

    def process_tick(self, tick: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Processes a validated tick. Returns list of completed candles (if boundary crossed).
        """
        symbol = tick.get("symbol")
        ltp = float(tick.get("ltp", 0.0))
        vol = tick.get("volume")
        raw_ts = tick.get("raw_exchange_timestamp")

        # Parse timestamp
        if raw_ts and isinstance(raw_ts, (int, float)):
            sec = raw_ts / 1000.0 if raw_ts > 1e11 else raw_ts
            try:
                dt = datetime.datetime.fromtimestamp(sec, tz=IST_TZ)
            except Exception:
                dt = datetime.datetime.now(IST_TZ)
        else:
            dt = datetime.datetime.now(IST_TZ)

        completed_candles = []

        if symbol not in self._forming:
            self._forming[symbol] = {}

        for tf, interval_mins in [("1m", 1), ("5m", 5)]:
            interval_start = self._get_interval_start(dt, interval_mins)
            current_candle = self._forming[symbol].get(tf)

            if current_candle is None:
                # First candle for this symbol & timeframe
                new_candle = Candle(symbol, tf, interval_start)
                new_candle.update_tick(ltp, cum_volume=vol)
                self._forming[symbol][tf] = new_candle
            else:
                if interval_start > current_candle.start_time:
                    # Previous candle completed!
                    current_candle.complete()
                    completed_dict = current_candle.to_dict()
                    completed_candles.append(completed_dict)

                    if self.on_candle_completed:
                        try:
                            self.on_candle_completed(completed_dict)
                        except Exception as e:
                            print(f"[CANDLE CALLBACK ERROR] {symbol} {tf}: {e}")

                    # Start new forming candle
                    new_candle = Candle(symbol, tf, interval_start)
                    new_candle.update_tick(ltp, cum_volume=vol)
                    self._forming[symbol][tf] = new_candle
                elif interval_start == current_candle.start_time:
                    # Update current candle
                    current_candle.update_tick(ltp, cum_volume=vol)
                else:
                    # Out-of-order tick arriving after candle boundary closed; ignore for completed candle integrity
                    pass

        return completed_candles

    def get_forming_candle(self, symbol: str, timeframe: str = "1m") -> Optional[Dict[str, Any]]:
        c = self._forming.get(symbol, {}).get(timeframe)
        return c.to_dict() if c else None

