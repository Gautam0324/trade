"""
Centralized Market Data Quote Validator (F&O V4).
Enforces strict quote validation invariants across Signal Generation, Option Selection,
Paper Execution, Position Marking, and Exit Management:
1. Rejects missing or non-string/float timestamps.
2. Prohibits replacing missing exchange timestamps with local clock timestamps.
3. Rejects stale quotes (LTP/Depth age > max_age_seconds, e.g. 60s).
4. Rejects non-positive or non-finite LTP.
5. Rejects zero bid or ask when execution decisions require order book depth.
6. Validates bid <= ask invariant (rejects crossed markets).
7. Validates correct exchange segment (NFO vs BFO).
8. Differentiates genuine realtime market data from historical candle bars.
9. verified_market_data is set to True ONLY when all validation checks pass.
"""

import math
import re
import datetime
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, field


@dataclass
class ValidationResult:
    is_valid: bool
    verified_market_data: bool
    rejection_reasons: list = field(default_factory=list)
    symbol: str = ""
    exchange: str = "NFO"
    token: str = ""
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    spread_pct: float = 0.0
    exchange_timestamp: Optional[str] = None
    local_receive_timestamp: str = ""
    quote_age_seconds: float = -1.0
    data_source: str = "UNKNOWN"
    is_realtime: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "verified_market_data": self.verified_market_data,
            "rejection_reasons": self.rejection_reasons,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "token": self.token,
            "ltp": self.ltp,
            "bid": self.bid,
            "ask": self.ask,
            "spread": self.spread,
            "spread_pct": self.spread_pct,
            "exchange_timestamp": self.exchange_timestamp,
            "local_receive_timestamp": self.local_receive_timestamp,
            "quote_age_seconds": self.quote_age_seconds,
            "data_source": self.data_source,
            "is_realtime": self.is_realtime
        }


class QuoteValidator:
    """
    Centralized validation engine for equity and options market quotes.
    Fail-closed by design: any uncertainty returns is_valid=False and verified_market_data=False.
    """

    MAX_LTP_AGE_SECONDS = 60.0
    MAX_DEPTH_AGE_SECONDS = 60.0
    MAX_CANDLE_AGE_SECONDS = 180.0
    MAX_ALLOWED_SPREAD_PCT = 15.0  # Max bid-ask spread % allowed for liquid options

    @staticmethod
    def parse_exchange_timestamp(raw_ts: Any, reference_dt: Optional[datetime.datetime] = None) -> Tuple[bool, float, Optional[str], Optional[str]]:
        """
        Parses exchange feed timestamp without local clock substitution.
        Returns: (is_valid, age_seconds, formatted_ts_str, error_reason)
        """
        if raw_ts is None or raw_ts == "":
            return False, -1.0, None, "MISSING_EXCHANGE_TIMESTAMP"

        if reference_dt is None:
            ref = datetime.datetime.now()
        else:
            ref = reference_dt

        parsed_dt = None
        if isinstance(raw_ts, (int, float)):
            try:
                # Millisecond epoch vs second epoch
                if raw_ts > 1e11:
                    parsed_dt = datetime.datetime.fromtimestamp(raw_ts / 1000.0)
                else:
                    parsed_dt = datetime.datetime.fromtimestamp(raw_ts)
            except (ValueError, OverflowError, OSError):
                return False, -1.0, None, "INVALID_NUMERIC_TIMESTAMP"
        elif isinstance(raw_ts, str):
            clean_ts = re.sub(r"\b(IST|UTC|GMT)\b", "", raw_ts).replace("T", " ").replace("Z", "").strip()
            formats = [
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d-%m-%Y %H:%M:%S",
                "%d/%m/%Y %H:%M:%S",
                "%d-%m-%Y %H:%M",
                "%d/%m/%Y %H:%M",
                "%H:%M:%S",
                "%H:%M"
            ]
            for fmt in formats:
                try:
                    parsed = datetime.datetime.strptime(clean_ts, fmt)
                    if fmt in ("%H:%M:%S", "%H:%M"):
                        parsed = parsed.replace(year=ref.year, month=ref.month, day=ref.day)
                    parsed_dt = parsed
                    break
                except ValueError:
                    continue

            if parsed_dt is None:
                return False, -1.0, None, "MALFORMED_TIMESTAMP_FORMAT"
        else:
            return False, -1.0, None, "UNSUPPORTED_TIMESTAMP_TYPE"

        age = (ref - parsed_dt).total_seconds()
        # Tolerance for minor local-broker clock drift: 60s in the future
        if age < -60.0:
            return False, age, None, "FUTURE_TIMESTAMP_ANOMALY"

        formatted_str = parsed_dt.strftime("%Y-%m-%d %H:%M:%S IST")
        return True, max(0.0, age), formatted_str, None

    @classmethod
    def validate_quote(
        cls,
        quote: Optional[Dict[str, Any]],
        require_depth: bool = False,
        expected_exchange: Optional[str] = None,
        reference_dt: Optional[datetime.datetime] = None,
        max_age_seconds: Optional[float] = None
    ) -> ValidationResult:
        """
        Validates quote data strictly against all empirical integrity criteria.
        """
        now_str = (reference_dt or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S IST")
        if not quote or not isinstance(quote, dict):
            return ValidationResult(
                is_valid=False,
                verified_market_data=False,
                rejection_reasons=["NULL_OR_INVALID_QUOTE_PAYLOAD"],
                local_receive_timestamp=now_str
            )

        rejection_reasons = []

        symbol = str(quote.get("symbol") or quote.get("tradingsymbol") or "")
        token = str(quote.get("token") or quote.get("symboltoken") or "")
        exchange = str(quote.get("exchange") or quote.get("exch_seg") or "NFO").upper()
        data_source = str(quote.get("quote_source") or quote.get("source") or "UNKNOWN")
        is_realtime = bool(quote.get("is_realtime_quote", quote.get("is_realtime", True)))

        # If require_depth is True (execution decision), historical candle close data is strictly rejected
        if require_depth and ("CANDLE" in data_source or not is_realtime):
            rejection_reasons.append("HISTORICAL_CANDLE_CANNOT_BE_USED_FOR_EXECUTION")
        if expected_exchange:
            exp_clean = expected_exchange.upper()
            if exchange != exp_clean:
                rejection_reasons.append(f"EXCHANGE_MISMATCH_EXPECTED_{exp_clean}_GOT_{exchange}")

        # If symbol starts with BSE or is a BSE equity derivative, it must be BFO
        if symbol.startswith("BSE") and exchange == "NFO":
            rejection_reasons.append("INVALID_EXCHANGE_ROUTING_BSE_MUST_BE_BFO")

        # 2. LTP sanity check
        raw_ltp = quote.get("ltp")
        try:
            ltp = float(raw_ltp)
            if not math.isfinite(ltp) or ltp <= 0:
                rejection_reasons.append("NON_POSITIVE_OR_NON_FINITE_LTP")
        except (TypeError, ValueError):
            ltp = 0.0
            rejection_reasons.append("INVALID_LTP_TYPE")

        # 3. Bid / Ask sanity check
        try:
            bid = float(quote.get("bid", 0.0) or 0.0)
            ask = float(quote.get("ask", 0.0) or 0.0)
        except (TypeError, ValueError):
            bid = 0.0
            ask = 0.0
            rejection_reasons.append("INVALID_BID_ASK_TYPES")

        if not math.isfinite(bid) or bid < 0:
            rejection_reasons.append("INVALID_BID_VALUE")
        if not math.isfinite(ask) or ask < 0:
            rejection_reasons.append("INVALID_ASK_VALUE")

        if bid > 0 and ask > 0 and bid > ask:
            rejection_reasons.append("CROSSED_MARKET_BID_GREATER_THAN_ASK")

        spread = 0.0
        spread_pct = 0.0
        if ask > 0 and bid > 0:
            spread = round(ask - bid, 2)
            mid = (ask + bid) / 2.0
            spread_pct = round((spread / mid) * 100.0, 2) if mid > 0 else 0.0

        if require_depth:
            if bid <= 0.0 or ask <= 0.0:
                rejection_reasons.append("ZERO_BID_OR_ASK_DEPTH_REQUIRED")
            elif spread_pct > cls.MAX_ALLOWED_SPREAD_PCT:
                rejection_reasons.append(f"EXCESSIVE_SPREAD_PCT_{spread_pct}_EXCEEDS_{cls.MAX_ALLOWED_SPREAD_PCT}")

        # 4. Exchange Timestamp and Freshness validation
        raw_ts = (
            quote.get("feed_time")
            or quote.get("exch_feed_time")
            or quote.get("quote_timestamp")
            or quote.get("timestamp")
        )

        max_age = max_age_seconds
        if max_age is None:
            if "CANDLE" in data_source or not is_realtime:
                max_age = cls.MAX_CANDLE_AGE_SECONDS
            else:
                max_age = cls.MAX_LTP_AGE_SECONDS

        is_valid_ts, age_secs, formatted_ts, ts_err = cls.parse_exchange_timestamp(raw_ts, reference_dt)
        if not is_valid_ts:
            rejection_reasons.append(f"TIMESTAMP_REJECTED_{ts_err}")
        elif age_secs > max_age:
            rejection_reasons.append(f"STALE_QUOTE_AGE_{age_secs:.1f}S_EXCEEDS_{max_age:.1f}S")

        is_valid = len(rejection_reasons) == 0
        verified_market_data = is_valid and (raw_ts is not None and is_valid_ts)

        return ValidationResult(
            is_valid=is_valid,
            verified_market_data=verified_market_data,
            rejection_reasons=rejection_reasons,
            symbol=symbol,
            exchange=exchange,
            token=token,
            ltp=ltp,
            bid=bid,
            ask=ask,
            spread=spread,
            spread_pct=spread_pct,
            exchange_timestamp=formatted_ts if is_valid_ts else None,
            local_receive_timestamp=now_str,
            quote_age_seconds=age_secs if is_valid_ts else -1.0,
            data_source=data_source,
            is_realtime=is_realtime
        )
