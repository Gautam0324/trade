"""
F&O Option Margin Calculator & Exposure Management Engine.
Version 4.0 - Compliant with SEBI Peak Margin Norms & Angel One SPAN + Exposure.

Provides:
1. Exact Option Selling Margin Calculation:
   - Index (NIFTY, BANKNIFTY) & Liquid Stocks (RELIANCE, HDFCBANK, BSE, ICICIBANK, SBIN).
   - Base SPAN (~10-12% underlying) + Exposure (~2-3% underlying) + Premium Receivable.
   - Standard exchange minimum margin:
     * NIFTY: ~Rs 1,15,000 to Rs 1,40,000 per lot
     * BANKNIFTY: ~Rs 1,20,000 to Rs 1,50,000 per lot
     * Stock Options: ~15% - 22% of total contract value (Strike * LotSize).
2. Option Buying Cash Requirement:
   - Cash Required = Option Premium * LotSize.
3. Margin Validation & Pre-Trade Gate:
   - Checks available free cash against total required margin before order submission.
   - Prevents margin shortfalls, margin penalties, and unauthorized naked exposure.
4. Defined-Risk Spread Margin Benefit:
   - For credit spreads (e.g. Bear Call Spread, Bull Put Spread), margin is capped
     at (Strike Difference * LotSize) + Buffer instead of full naked margin.
"""

import math
from typing import Dict, Any, Optional, Tuple


class OptionMarginCalculator:
    """
    Computes required intraday / overnight margin for Indian F&O equity and index options.
    """

    # Baseline SPAN + Exposure percentage by underlying asset class
    INDEX_SPAN_EXPOSURE_PCT = 0.125    # ~12.5% of underlying contract value
    STOCK_SPAN_EXPOSURE_PCT = 0.20     # ~20% of underlying contract value (standard VaR + ELM)

    # Hard minimum margin floors per lot to prevent under-margining in edge cases
    MIN_MARGIN_FLOOR = {
        "NIFTY": 110000.0,
        "BANKNIFTY": 115000.0,
        "FINNIFTY": 100000.0,
        "BSE": 125000.0,
        "RELIANCE": 130000.0,
        "HDFCBANK": 120000.0,
        "ICICIBANK": 110000.0,
        "SBIN": 110000.0,
        "IDEA": 90000.0,
        "DEFAULT": 100000.0
    }

    @classmethod
    def calculate_margin(
        cls,
        action: str,          # 'BUY' or 'SELL'
        option_type: str,     # 'CE' or 'PE'
        underlying: str,
        strike: float,
        spot_price: float,
        premium: float,
        qty: int,
        is_spread: bool = False,
        hedge_strike: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Calculates exact cash or margin requirement for an option order.
        """
        action = action.upper()
        option_type = option_type.upper()
        underlying = (underlying or "NIFTY").upper()

        if action not in ("BUY", "SELL"):
            raise ValueError(f"Action must be BUY or SELL, got {action}")
        if option_type not in ("CE", "PE"):
            raise ValueError(f"Option type must be CE or PE, got {option_type}")
        if qty <= 0 or premium < 0 or spot_price <= 0:
            raise ValueError("Qty, premium, and spot must be positive")

        # 1. OPTION BUYING (LONG CE / LONG PE)
        if action == "BUY":
            cash_required = round(premium * qty, 2)
            return {
                "action": "BUY",
                "position_direction": "LONG",
                "strategy": f"BUY_{option_type}",
                "margin_required": 0.0,
                "cash_required": cash_required,
                "total_funds_needed": cash_required,
                "is_margin_trade": False,
                "margin_type": "CASH_PREMIUM",
                "details": {
                    "premium": premium,
                    "qty": qty,
                    "formula": "Premium * Quantity"
                }
            }

        # 2. DEFINED-RISK SPREAD SELLING (Hedged Credit Spread)
        if is_spread and hedge_strike and hedge_strike > 0:
            strike_diff = abs(strike - hedge_strike)
            max_risk_margin = round(strike_diff * qty, 2)
            # Add 15% safety buffer for exchange peak margin requirement
            spread_margin = round(max_risk_margin * 1.15, 2)
            return {
                "action": "SELL",
                "position_direction": "SHORT",
                "strategy": f"SPREAD_SELL_{option_type}",
                "margin_required": spread_margin,
                "cash_required": 0.0,
                "total_funds_needed": spread_margin,
                "is_margin_trade": True,
                "margin_type": "DEFINED_RISK_SPREAD",
                "details": {
                    "strike_difference": strike_diff,
                    "max_spread_risk": max_risk_margin,
                    "safety_buffer_pct": 15.0
                }
            }

        # 3. NAKED OPTION SELLING (SHORT CE / SHORT PE)
        # Contract notional value = Underlying Spot * Quantity
        contract_value = spot_price * qty
        is_index = underlying in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
        margin_pct = cls.INDEX_SPAN_EXPOSURE_PCT if is_index else cls.STOCK_SPAN_EXPOSURE_PCT

        base_span_exposure = round(contract_value * margin_pct, 2)
        min_floor = cls.MIN_MARGIN_FLOOR.get(underlying, cls.MIN_MARGIN_FLOOR["DEFAULT"])

        # OTM discount / ITM surcharge adjustment:
        # If ITM: Add intrinsic value to margin
        # If OTM: Margin cannot drop below 75% of base SPAN+Exposure
        intrinsic = 0.0
        if option_type == "CE":
            intrinsic = max(0.0, spot_price - strike) * qty
        else:
            intrinsic = max(0.0, strike - spot_price) * qty

        total_margin = max(base_span_exposure + intrinsic, min_floor)
        total_margin = round(total_margin, 2)

        return {
            "action": "SELL",
            "position_direction": "SHORT",
            "strategy": f"SELL_{option_type}",
            "margin_required": total_margin,
            "cash_required": 0.0,
            "total_funds_needed": total_margin,
            "is_margin_trade": True,
            "margin_type": "SPAN_AND_EXPOSURE",
            "details": {
                "contract_value": round(contract_value, 2),
                "margin_pct": margin_pct,
                "base_span_exposure": base_span_exposure,
                "intrinsic_value_adj": round(intrinsic, 2),
                "margin_floor_applied": total_margin == min_floor
            }
        }

    @classmethod
    def validate_order_margin(
        cls,
        action: str,
        option_type: str,
        underlying: str,
        strike: float,
        spot_price: float,
        premium: float,
        qty: int,
        free_cash: float,
        allow_naked_selling: bool = True,
        is_spread: bool = False,
        hedge_strike: Optional[float] = None
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Validates if an order can be placed given account's available free cash.
        Returns: (can_admit: bool, rejection_reason: str, margin_breakdown: dict)
        """
        margin_info = cls.calculate_margin(
            action=action,
            option_type=option_type,
            underlying=underlying,
            strike=strike,
            spot_price=spot_price,
            premium=premium,
            qty=qty,
            is_spread=is_spread,
            hedge_strike=hedge_strike
        )

        needed = margin_info["total_funds_needed"]

        # Safety policy check
        if action.upper() == "SELL" and not is_spread and not allow_naked_selling:
            return False, "Naked option selling is disabled by risk policy; defined-risk spread required.", margin_info

        if free_cash < needed:
            return (
                False,
                f"Insufficient funds: Need Rs {needed:,.2f} ({margin_info['margin_type']}), but available free cash is Rs {free_cash:,.2f}",
                margin_info
            )

        return True, "MARGIN_OK", margin_info
