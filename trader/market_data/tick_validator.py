"""
Tick Validator for Day Trader Pro V2.
Validates normalized tick messages against data integrity, bounds, ordering, and duplication rules.
"""

from typing import Dict, Any, Tuple, Optional
import time

class TickValidator:
    def __init__(self, instrument_manager, max_allowed_price: float = 1000000.0):
        self.instrument_manager = instrument_manager
        self.max_allowed_price = max_allowed_price
        self._last_seen_seq: Dict[str, int] = {}
        self._last_seen_ts: Dict[str, float] = {}
        self._last_seen_ltp: Dict[str, float] = {}

    def validate_tick(self, tick: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
        if not tick or not isinstance(tick, dict):
            return False, "Malformed tick: Not a dictionary"

        token = tick.get("token")
        symbol = tick.get("symbol")
        ltp = tick.get("ltp")

        if not token or not symbol:
            return False, "Missing token or symbol"

        # Validate that token is registered
        inst = self.instrument_manager.get_by_token(token)
        if not inst:
            # Check by symbol
            inst = self.instrument_manager.get_by_symbol(symbol)
            if not inst:
                return False, f"Unregistered instrument: token={token}, symbol={symbol}"

        # LTP validity
        if ltp is None or not isinstance(ltp, (int, float)):
            return False, f"Invalid LTP type: {type(ltp)}"

        if ltp <= 0.0:
            return False, f"Non-positive LTP: {ltp}"

        if ltp > self.max_allowed_price:
            return False, f"LTP exceeds maximum threshold: {ltp}"

        # Check sequence number for duplication / ordering if present
        seq = tick.get("sequence_number")
        if seq is not None and isinstance(seq, int) and seq > 0:
            last_seq = self._last_seen_seq.get(symbol)
            if last_seq is not None:
                if seq == last_seq:
                    return False, f"Duplicate tick sequence {seq} for {symbol}"
                elif seq < last_seq:
                    # Out-of-order packet
                    return False, f"Out-of-order sequence: received {seq} after {last_seq} for {symbol}"
            self._last_seen_seq[symbol] = seq

        # Check raw timestamp ordering if available
        raw_ts = tick.get("raw_exchange_timestamp")
        if raw_ts is not None and isinstance(raw_ts, (int, float)):
            last_ts = self._last_seen_ts.get(symbol)
            if last_ts is not None:
                # If timestamp is strictly in the past by more than 5 seconds, drop as stale
                if raw_ts < last_ts - 5000:
                    return False, f"Stale/out-of-order exchange timestamp: {raw_ts} < {last_ts} for {symbol}"
            self._last_seen_ts[symbol] = max(self._last_seen_ts.get(symbol, 0), float(raw_ts))

        self._last_seen_ltp[symbol] = float(ltp)
        return True, "VALID"

    def reset_instrument(self, symbol: str):
        self._last_seen_seq.pop(symbol, None)
        self._last_seen_ts.pop(symbol, None)
        self._last_seen_ltp.pop(symbol, None)

