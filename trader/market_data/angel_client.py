"""
Angel Client for Day Trader Pro V2.
Manages secure authentication, TOTP generation, token refresh, and SmartConnect sessions.
Never exposes credentials in logs.
"""

import os
import json
import pyotp
from pathlib import Path
from typing import Dict, Any, Optional
from SmartApi import SmartConnect

class AngelClient:
    def __init__(self, secrets_path: Optional[str] = None):
        self.base_dir = Path(__file__).resolve().parent.parent.parent
        self.secrets_path = Path(secrets_path) if secrets_path else self.base_dir / "trader" / "secrets.json"
        self._smart_connect: Optional[SmartConnect] = None
        self._jwt_token: Optional[str] = None
        self._feed_token: Optional[str] = None
        self._client_code: Optional[str] = None
        self._api_key: Optional[str] = None

    def _load_credentials(self) -> Dict[str, str]:
        # Priority 1: Environment variables
        api_key = os.getenv("ANGEL_API_KEY")
        client_code = os.getenv("ANGEL_CLIENT_CODE")
        pin = os.getenv("ANGEL_PASSWORD") or os.getenv("ANGEL_PIN")
        totp_secret = os.getenv("ANGEL_TOTP_SECRET")
        feed_token = os.getenv("ANGEL_FEED_TOKEN")
        jwt_token = os.getenv("ANGEL_JWT_TOKEN")

        # Priority 2: secrets.json fallback
        if (not api_key or not client_code or not pin or not totp_secret) and self.secrets_path.exists():
            try:
                with open(self.secrets_path, "r", encoding="utf-8") as f:
                    sec = json.load(f)
                    api_key = api_key or sec.get("smartapi_api_key")
                    client_code = client_code or sec.get("smartapi_client_code")
                    pin = pin or sec.get("smartapi_pin")
                    totp_secret = totp_secret or sec.get("smartapi_totp_secret")
            except Exception:
                pass

        return {
            "api_key": api_key or "",
            "client_code": client_code or "",
            "pin": pin or "",
            "totp_secret": totp_secret or "",
            "feed_token": feed_token or "",
            "jwt_token": jwt_token or ""
        }

    def authenticate(self) -> bool:
        creds = self._load_credentials()
        self._api_key = creds["api_key"]
        self._client_code = creds["client_code"]

        if not self._api_key or not self._client_code:
            return False

        # If JWT and Feed token are already supplied in env and valid
        if creds["jwt_token"] and creds["feed_token"]:
            self._jwt_token = creds["jwt_token"]
            self._feed_token = creds["feed_token"]
            self._smart_connect = SmartConnect(api_key=self._api_key)
            self._smart_connect.setAccessToken(self._jwt_token)
            return True

        # Generate session via SmartConnect TOTP
        if not creds["pin"] or not creds["totp_secret"]:
            return False

        try:
            self._smart_connect = SmartConnect(api_key=self._api_key)
            totp = pyotp.TOTP(creds["totp_secret"]).now()
            session = self._smart_connect.generateSession(self._client_code, creds["pin"], totp)

            if session and session.get("status"):
                data = session.get("data", {})
                self._jwt_token = data.get("jwtToken")
                self._feed_token = data.get("feedToken")
                return bool(self._jwt_token and self._feed_token)
            return False
        except Exception:
            return False

    def get_session_info(self) -> Dict[str, Any]:
        return {
            "authenticated": bool(self._jwt_token and self._feed_token),
            "client_code": self._client_code,
            "api_key": self._api_key,
            "jwt_token": self._jwt_token,
            "feed_token": self._feed_token
        }

    @property
    def smart_connect(self) -> Optional[SmartConnect]:
        return self._smart_connect

