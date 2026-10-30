"""
Structured System Logger for Day Trader Pro V2.
Logs events with timestamps, components, and payload attributes while masking any sensitive fields.
"""

import datetime
import json
import zoneinfo
from typing import Dict, Any, Optional

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class SystemLogger:
    SENSITIVE_KEYS = {"api_key", "password", "pin", "totp_secret", "jwt_token", "feed_token", "token"}

    @classmethod
    def log(cls, level: str, component: str, message: str, data: Optional[Dict[str, Any]] = None):
        ts = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S IST")
        safe_data = cls._mask_sensitive(data) if data else {}
        prefix = f"[{ts}] [{level.upper()}] [{component}]"
        if safe_data:
            print(f"{prefix} {message} | {json.dumps(safe_data)}")
        else:
            print(f"{prefix} {message}")

    @classmethod
    def _mask_sensitive(cls, d: Dict[str, Any]) -> Dict[str, Any]:
        masked = {}
        for k, v in d.items():
            if any(s in k.lower() for s in cls.SENSITIVE_KEYS):
                masked[k] = "***MASKED***"
            elif isinstance(v, dict):
                masked[k] = cls._mask_sensitive(v)
            else:
                masked[k] = v
        return masked

