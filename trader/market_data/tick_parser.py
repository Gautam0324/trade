"""
Tick Parser for Day Trader Pro V2.
Parses raw SmartWebSocketV2 dictionary messages into normalized tick dictionaries.
"""

from typing import Dict, Any, Optional
import datetime
import zoneinfo

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class TickParser:
    def __init__(self, instrument_manager):
        self.instrument_manager = instrument_manager

    def parse_tick(self, raw_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not isinstance(raw_data, dict):
            return None

        # Extract token
        raw_token = str(raw_data.get("token", "")).strip()
        inst = self.instrument_manager.get_by_token(raw_token)
        if not inst:
            # Fallback check if symbol is provided
            symbol = raw_data.get("symbol")
            if symbol:
                inst = self.instrument_manager.get_by_symbol(symbol)
                raw_token = inst["token"] if inst else raw_token
        
        symbol = inst["symbol"] if inst else raw_data.get("symbol", f"UNKNOWN_{raw_token}")
        exchange = inst["exchange"] if inst else raw_data.get("exchange", "NSE")

        # Parse LTP (SmartWebSocketV2 gives paise if it's integer > 10000 for regular stock price, or integer)
        # SmartAPI binary protocol stores LTP in paise (int). E.g. Rs 1162.90 -> 116290.
        raw_ltp = raw_data.get("last_traded_price") or raw_data.get("ltp") or raw_data.get("last_price")
        if raw_ltp is None:
            return None

        try:
            flt_ltp = float(raw_ltp)
            # SmartWebSocketV2 uses paise format if subscription_mode in [LTP, QUOTE, SNAP_QUOTE]
            # If flt_ltp is large integer (like 116290 for a 1162 rupee stock) or explicit flag
            if "subscription_mode" in raw_data and isinstance(raw_ltp, int):
                ltp = flt_ltp / 100.0
            elif raw_data.get("is_paise", False):
                ltp = flt_ltp / 100.0
            else:
                ltp = flt_ltp
        except (ValueError, TypeError):
            return None

        # Parse timestamp
        raw_ts = raw_data.get("exchange_timestamp") or raw_data.get("timestamp")
        iso_timestamp = None
        raw_exchange_ts = None

        if isinstance(raw_ts, (int, float)):
            raw_exchange_ts = int(raw_ts)
            # Timestamp might be in ms or s
            sec = raw_ts / 1000.0 if raw_ts > 1e11 else raw_ts
            try:
                dt = datetime.datetime.fromtimestamp(sec, tz=IST_TZ)
                iso_timestamp = dt.isoformat()
            except Exception:
                dt = datetime.datetime.now(IST_TZ)
                iso_timestamp = dt.isoformat()
        elif isinstance(raw_ts, str):
            iso_timestamp = raw_ts
        else:
            iso_timestamp = datetime.datetime.now(IST_TZ).isoformat()

        # Parse Volume
        # In QUOTE/SNAP_QUOTE mode, SmartWebSocketV2 provides 'volume_trade_for_the_day' (cumulative)
        cum_volume = raw_data.get("volume_trade_for_the_day") or raw_data.get("volume")
        try:
            vol = float(cum_volume) if cum_volume is not None else None
        except (ValueError, TypeError):
            vol = None

        # Best bid / ask
        best_bid = None
        best_ask = None
        if "best_5_buy_data" in raw_data and raw_data["best_5_buy_data"]:
            first_buy = raw_data["best_5_buy_data"][0]
            if isinstance(first_buy, dict) and "price" in first_buy:
                best_bid = float(first_buy["price"]) / 100.0
        if "best_5_sell_data" in raw_data and raw_data["best_5_sell_data"]:
            first_sell = raw_data["best_5_sell_data"][0]
            if isinstance(first_sell, dict) and "price" in first_sell:
                best_ask = float(first_sell["price"]) / 100.0

        return {
            "symbol": symbol,
            "token": raw_token,
            "exchange": exchange,
            "ltp": round(ltp, 4),
            "timestamp": iso_timestamp,
            "volume": vol,
            "raw_exchange_timestamp": raw_exchange_ts,
            "best_bid": round(best_bid, 4) if best_bid else None,
            "best_ask": round(best_ask, 4) if best_ask else None,
            "sequence_number": raw_data.get("sequence_number")
        }

