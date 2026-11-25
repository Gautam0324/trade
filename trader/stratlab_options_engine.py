"""
StratLab Quantitative Options Trading Framework
Module: trader/stratlab_options_engine.py

Provides:
1. Black-Scholes Pricing & Greeks Engine (Delta, Gamma, Theta, Vega, IV Solver)
2. Option Chain Intelligence Engine (OI Walls, PCR Sentiment, Max Pain)
3. Modular Vertical Debit Spread Resolver (Bull Call / Bear Put Spreads)
4. Institutional Trade Clearance Guards (OI Wall Clearance, IV Rank Filter)
"""

import math
import datetime
from typing import Dict, List, Optional, Tuple

class BlackScholesEngine:
    """Analytical Black-Scholes pricing and Greek calculator."""
    
    @staticmethod
    def norm_cdf(x: float) -> float:
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    @staticmethod
    def norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)

    @classmethod
    def call_price(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return max(0.0, S - K)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return S * cls.norm_cdf(d1) - K * math.exp(-r * T) * cls.norm_cdf(d2)

    @classmethod
    def put_price(cls, S: float, K: float, T: float, r: float, sigma: float) -> float:
        if T <= 0 or sigma <= 0:
            return max(0.0, K - S)
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        return K * math.exp(-r * T) * cls.norm_cdf(-d2) - S * cls.norm_cdf(-d1)

    @classmethod
    def compute_greeks(cls, S: float, K: float, T: float, r: float, sigma: float, is_call: bool = True) -> Dict[str, float]:
        if T <= 0 or sigma <= 0:
            return {"delta": 1.0 if is_call else -1.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0}
            
        d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        
        pdf_d1 = cls.norm_pdf(d1)
        cdf_d1 = cls.norm_cdf(d1)
        cdf_d2 = cls.norm_cdf(d2)
        
        if is_call:
            delta = cdf_d1
            theta = (- (S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) - r * K * math.exp(-r * T) * cdf_d2) / 365.0
        else:
            delta = cdf_d1 - 1.0
            theta = (- (S * pdf_d1 * sigma) / (2.0 * math.sqrt(T)) + r * K * math.exp(-r * T) * (1.0 - cdf_d2)) / 365.0
            
        gamma = pdf_d1 / (S * sigma * math.sqrt(T))
        vega = (S * math.sqrt(T) * pdf_d1) / 100.0  # ₹ change per 1% change in IV
        
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4)
        }

    @classmethod
    def calculate_iv(cls, market_price: float, S: float, K: float, T: float, r: float = 0.07, is_call: bool = True) -> float:
        """Bisection solver for Implied Volatility (returns percentage e.g. 24.50%)."""
        if T <= 0 or market_price <= 0:
            return 0.0
        intrinsic = max(0.0, S - K) if is_call else max(0.0, K - S)
        if market_price <= intrinsic:
            return 5.0  # Floor baseline
            
        low = 0.01
        high = 3.0
        mid = 0.20
        for _ in range(25):
            mid = (low + high) / 2.0
            m_price = cls.call_price(S, K, T, r, mid) if is_call else cls.put_price(S, K, T, r, mid)
            if abs(m_price - market_price) < 0.01:
                return round(mid * 100.0, 2)
            if m_price < market_price:
                low = mid
            else:
                high = mid
        return round(mid * 100.0, 2)

    @staticmethod
    def calculate_iv_rank(current_iv: float, min_iv: float, max_iv: float) -> float:
        """Computes IV Rank as percentage between 0% and 100%."""
        if max_iv <= min_iv:
            return 50.0
        ivr = ((current_iv - min_iv) / (max_iv - min_iv)) * 100.0
        return round(max(0.0, min(100.0, ivr)), 2)


class OptionChainStructureEngine:
    """
    Analyzes Option Chain positioning:
    - Call OI Walls (Resistance)
    - Put OI Walls (Support)
    - Put-Call Ratio (PCR)
    - Max Pain Strike (Expiry Gravity)
    """

    @staticmethod
    def analyze_chain(strikes_data: List[Dict]) -> Dict:
        """
        Input strikes_data format:
        [
            {"strike": 1280.0, "call_oi": 150000, "put_oi": 80000, "call_price": 25.0, "put_price": 10.0},
            ...
        ]
        """
        if not strikes_data:
            return {
                "call_wall": 0.0,
                "put_wall": 0.0,
                "pcr": 1.0,
                "max_pain": 0.0,
                "total_call_oi": 0,
                "total_put_oi": 0
            }

        total_call_oi = sum(s.get("call_oi", 0) for s in strikes_data)
        total_put_oi = sum(s.get("put_oi", 0) for s in strikes_data)
        pcr = round(total_put_oi / total_call_oi, 2) if total_call_oi > 0 else 1.0

        # Call Wall = strike with maximum Call OI
        best_call_strike = max(strikes_data, key=lambda s: s.get("call_oi", 0))
        call_wall = best_call_strike.get("strike", 0.0)

        # Put Wall = strike with maximum Put OI
        best_put_strike = max(strikes_data, key=lambda s: s.get("put_oi", 0))
        put_wall = best_put_strike.get("strike", 0.0)

        # Max Pain calculation: strike that minimizes total intrinsic loss of option buyers
        all_strikes = sorted([s["strike"] for s in strikes_data])
        min_total_payout = float("inf")
        max_pain_strike = all_strikes[0] if all_strikes else 0.0

        for test_s in all_strikes:
            payout = 0.0
            for row in strikes_data:
                k = row["strike"]
                c_oi = row.get("call_oi", 0)
                p_oi = row.get("put_oi", 0)
                call_loss = max(0.0, test_s - k) * c_oi
                put_loss = max(0.0, k - test_s) * p_oi
                payout += (call_loss + put_loss)

            if payout < min_total_payout:
                min_total_payout = payout
                max_pain_strike = test_s

        return {
            "call_wall": call_wall,
            "put_wall": put_wall,
            "pcr": pcr,
            "max_pain": max_pain_strike,
            "total_call_oi": total_call_oi,
            "total_put_oi": total_put_oi
        }

    @staticmethod
    def validate_call_clearance(spot_price: float, call_wall: float, tolerance_pct: float = 0.0075) -> Tuple[bool, str]:
        """
        Blocks BUY Call if spot is immediately under a massive Call OI Wall (Resistance Trap).
        Allows BUY Call if:
        1. Spot is well below Call Wall (plenty of runway, > tolerance_pct distance), OR
        2. Spot has cleanly broken and trades ABOVE Call Wall.
        """
        if call_wall <= 0:
            return True, "NO_CALL_WALL_RESTRICTION"

        # Distance to Call Wall
        dist = (call_wall - spot_price) / spot_price

        # Case 1: Sitting immediately underneath (e.g. within 0.75% of wall) -> TRAP RISK
        if 0.0 < dist <= tolerance_pct:
            return False, f"TRAP_BLOCKED: Spot {spot_price:.1f} is sitting right below Call OI Wall @ {call_wall:.1f} (within {dist*100:.2f}%)"

        # Case 2: Clean runway or already broken above
        return True, "CALL_CLEARANCE_PASSED"

    @staticmethod
    def validate_put_clearance(spot_price: float, put_wall: float, tolerance_pct: float = 0.0075) -> Tuple[bool, str]:
        """
        Blocks BUY Put if spot is immediately above a massive Put OI Wall (Support Trap).
        """
        if put_wall <= 0:
            return True, "NO_PUT_WALL_RESTRICTION"

        dist = (spot_price - put_wall) / spot_price
        if 0.0 < dist <= tolerance_pct:
            return False, f"TRAP_BLOCKED: Spot {spot_price:.1f} is sitting right above Put OI Wall @ {put_wall:.1f} (within {dist*100:.2f}%)"

        return True, "PUT_CLEARANCE_PASSED"


class VerticalSpreadResolver:
    """
    Constructs defined-risk, theta-neutral Vertical Debit Spreads:
    - Bull Call Spread: Buy ATM Call + Sell OTM Call (+1 step above)
    - Bear Put Spread: Buy ATM Put + Sell OTM Put (-1 step below)
    """

    @staticmethod
    def resolve_bull_call_spread(cached_contracts: List[Dict], underlying: str, spot_price: float) -> Optional[Dict]:
        """
        Finds ATM Call (Long) and nearest OTM Call (Short) for nearest expiry.
        """
        today_dt = datetime.datetime.now().date()

        def parse_exp(c):
            try:
                return datetime.datetime.strptime(c["expiry"], "%d%b%Y").date()
            except Exception:
                return datetime.date.max

        opts = [
            c for c in cached_contracts
            if c.get("name") == underlying
            and c.get("instrumenttype") in ("OPTIDX", "OPTSTK")
            and c.get("symbol", "").endswith("CE")
            and parse_exp(c) >= today_dt
        ]
        if not opts:
            return None

        # Filter for nearest expiry
        nearest_exp = min(parse_exp(c) for c in opts)
        exp_opts = [c for c in opts if parse_exp(c) == nearest_exp]

        # Sort strikes
        strikes = sorted(list(set(float(c["strike"]) / 100.0 for c in exp_opts)))
        if len(strikes) < 2:
            return None

        # Find closest strike to spot -> ATM Long Leg
        atm_strike = min(strikes, key=lambda s: abs(s - spot_price))
        atm_idx = strikes.index(atm_strike)

        # OTM Short Leg is the next strike above
        otm_idx = min(len(strikes) - 1, atm_idx + 1)
        if otm_idx == atm_idx:
            return None
        otm_strike = strikes[otm_idx]

        long_contract = next(c for c in exp_opts if float(c["strike"]) / 100.0 == atm_strike)
        short_contract = next(c for c in exp_opts if float(c["strike"]) / 100.0 == otm_strike)

        lot = int(long_contract.get("lotsize", 1))
        strike_width = round(otm_strike - atm_strike, 2)

        return {
            "strategy": "BULL_CALL_SPREAD",
            "underlying": underlying,
            "expiry": long_contract["expiry"],
            "lotsize": lot,
            "exchange": long_contract.get("exch_seg", "NFO"),
            "long_leg": {
                "symbol": long_contract["symbol"],
                "token": long_contract["token"],
                "strike": atm_strike,
                "side": "BUY",
                "type": "CE"
            },
            "short_leg": {
                "symbol": short_contract["symbol"],
                "token": short_contract["token"],
                "strike": otm_strike,
                "side": "SELL",
                "type": "CE"
            },
            "strike_width": strike_width,
            "max_loss_cap": "NET_DEBIT_PAID",
            "max_profit_cap": f"WIDTH({strike_width}) - NET_DEBIT"
        }
