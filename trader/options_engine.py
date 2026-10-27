#!/usr/bin/env python3
"""
Options Engine Module for Indian Index Derivatives (NSE).
Supports:
- NIFTY 50 and BANKNIFTY
- Weekly / Monthly strike selection (ATM, ITM, OTM)
- Lot sizing compliance (NIFTY: 25, BANKNIFTY: 15)
- CE / PE Option Contract symbol construction
- Synthetic / Black-Scholes estimate or Broker Option Chain premium resolution
"""

import math
import datetime

# Standard Lot Sizes for NSE Index Derivatives
INDEX_LOT_SIZES = {
    "NIFTY": 25,
    "NIFTY 50": 25,
    "BANKNIFTY": 15,
    "NIFTY BANK": 15,
    "FINNIFTY": 25
}

# Standard Strike Step Intervals
STRIKE_INTERVALS = {
    "NIFTY": 50,
    "NIFTY 50": 50,
    "BANKNIFTY": 100,
    "NIFTY BANK": 100,
    "FINNIFTY": 50
}

class OptionsEngine:
    def __init__(self, config=None):
        self.config = config or {}
        self.default_expiry = self.config.get("option_expiry", "WEEKLY") # "WEEKLY" or "MONTHLY"
        self.strike_preference = self.config.get("strike_preference", "ITM1") # "ATM", "ITM1", "OTM1"

    @staticmethod
    def get_lot_size(symbol: str) -> int:
        sym = symbol.upper().replace(" ", "").replace("_", "")
        for k, v in INDEX_LOT_SIZES.items():
            if k.replace(" ", "") in sym:
                return v
        return 25 # Default fallback

    @staticmethod
    def get_strike_interval(symbol: str) -> int:
        sym = symbol.upper().replace(" ", "").replace("_", "")
        for k, v in STRIKE_INTERVALS.items():
            if k.replace(" ", "") in sym:
                return v
        return 50 # Default fallback

    def select_strike(self, spot_price: float, option_type: str, symbol: str = "NIFTY") -> int:
        """
        Determines the strike price based on current spot and strike preference.
        option_type: "CE" (Call) or "PE" (Put)
        preference:
          - "ATM": Nearest At-The-Money strike
          - "ITM1": 1 strike In-The-Money (higher delta ~0.55-0.65, less theta decay)
          - "OTM1": 1 strike Out-Of-The-Money
        """
        step = self.get_strike_interval(symbol)
        atm_strike = int(round(spot_price / step) * step)

        opt_type = option_type.upper()
        pref = self.strike_preference.upper()

        if pref == "ATM":
            return atm_strike
        elif pref == "ITM1":
            if opt_type == "CE":
                return atm_strike - step
            else: # PE
                return atm_strike + step
        elif pref == "OTM1":
            if opt_type == "CE":
                return atm_strike + step
            else: # PE
                return atm_strike - step
        return atm_strike

    def construct_option_symbol(self, underlying: str, strike: int, option_type: str, expiry_date: datetime.date = None) -> str:
        """
        Generates standard NSE option contract name.
        Example: NIFTY24OCT24500CE
        """
        if expiry_date is None:
            today = datetime.date.today()
            days_ahead = (3 - today.weekday() + 7) % 7
            if days_ahead == 0 and datetime.datetime.now().hour >= 15:
                days_ahead = 7
            expiry_date = today + datetime.timedelta(days=days_ahead)

        clean_underlying = "NIFTY" if "NIFTY" in underlying.upper() and "BANK" not in underlying.upper() else "BANKNIFTY"
        exp_str = expiry_date.strftime("%y%b").upper()
        return f"{clean_underlying}{exp_str}{strike}{option_type.upper()}"

    def estimate_option_premium(self, spot_price: float, strike: int, option_type: str, iv: float = 0.14, days_to_expiry: float = 3.0) -> float:
        """
        Calculates an intrinsic + time-value approximation for paper trading simulations
        when live option chain is not streaming.
        """
        opt_type = option_type.upper()
        if opt_type == "CE":
            intrinsic = max(0.0, spot_price - strike)
        else:
            intrinsic = max(0.0, strike - spot_price)

        t_years = max(days_to_expiry, 0.2) / 365.0
        time_value = 0.4 * spot_price * iv * math.sqrt(t_years)
        dist = abs(spot_price - strike) / spot_price
        time_value_dampened = time_value * math.exp(-dist * 15)

        total_premium = intrinsic + time_value_dampened
        return max(5.0, round(total_premium, 2))
