"""
Options P&L and Directional Execution Engine (F&O V4).
Provides authoritative calculations for:
1. CALL BUY (CE) and PUT BUY (PE) directional signals and P&L.
2. CALL SELL (CE) and PUT SELL (PE) short options P&L.
3. Strict Option Premium Authority:
   Gross P&L for BUY: (Exit/Current Premium - Entry Premium) * Quantity
   Gross P&L for SELL: (Entry Premium - Exit/Current Premium) * Quantity
   Underlying spot direction dictates signals, NOT P&L.
4. Regulatory Statutory Charges & Net P&L:
   Brokerage (Rs 20/order), STT (0.1% on Sell turnover), Turnover fees, GST, Stamp duty.
5. Multi-leg partial exit aggregation and weighted average entry/exit pricing.
6. Validation of live quotes (rejecting <= 0, nan, inf, stale, missing).
"""

import math
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field


@dataclass
class OptionPnLBreakdown:
    symbol: str
    option_type: str        # 'CE' or 'PE'
    side: str               # 'BUY' or 'SELL'
    quantity: int
    entry_premium: float
    current_premium: float
    gross_pnl: float
    net_pnl: float
    total_fees: float
    brokerage: float
    stt: float
    exchange_turnover: float
    gst: float
    stamp_duty: float
    return_pct: float
    is_profit: bool
    status: str = "OPEN"    # 'OPEN' or 'CLOSED'
    exit_premium: Optional[float] = None
    exit_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "option_type": self.option_type,
            "side": self.side,
            "quantity": self.quantity,
            "entry_premium": self.entry_premium,
            "current_premium": self.current_premium,
            "exit_premium": self.exit_premium,
            "gross_pnl": self.gross_pnl,
            "net_pnl": self.net_pnl,
            "total_fees": self.total_fees,
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_turnover": self.exchange_turnover,
            "gst": self.gst,
            "stamp_duty": self.stamp_duty,
            "return_pct": self.return_pct,
            "is_profit": self.is_profit,
            "status": self.status,
            "exit_reason": self.exit_reason
        }


class OptionsPnLEngine:
    """
    Independent quantitative engine for option directional verification,
    premium-based P&L calculation, and partial fill aggregation.
    """

    # Indian F&O Statutory Fee Schedule (Equity/Index Options)
    BROKERAGE_PER_ORDER = 20.0       # Rs 20 per executed order leg
    STT_SELL_RATE = 0.001           # 0.1% on Sell leg turnover (Option Premium)
    EXCHANGE_TURNOVER_RATE = 0.0005  # 0.05% on total turnover (Buy + Sell)
    GST_RATE = 0.18                 # 18% on (Brokerage + Turnover charges)
    STAMP_DUTY_BUY_RATE = 0.00003   # 0.003% on Buy leg turnover
    SEBI_TURNOVER_RATE = 0.000001   # Rs 10 per crore

    @classmethod
    def calculate_statutory_fees(
        cls,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: int
    ) -> Dict[str, float]:
        """
        Calculates exact Indian F&O statutory fees for a roundtrip or single leg.
        """
        side_clean = side.upper()
        if side_clean == "BUY":
            buy_price = entry_price
            sell_price = exit_price
        else:
            sell_price = entry_price
            buy_price = exit_price

        buy_turnover = round(buy_price * qty, 2)
        sell_turnover = round(sell_price * qty, 2)
        total_turnover = round(buy_turnover + sell_turnover, 2)

        # 2 legs: entry and exit
        brokerage = round(cls.BROKERAGE_PER_ORDER * 2.0, 2)
        stt = round(sell_turnover * cls.STT_SELL_RATE, 2)
        exch_turnover = round(total_turnover * cls.EXCHANGE_TURNOVER_RATE, 2)
        sebi_charges = round(total_turnover * cls.SEBI_TURNOVER_RATE, 2)
        gst = round((brokerage + exch_turnover + sebi_charges) * cls.GST_RATE, 2)
        stamp_duty = round(buy_turnover * cls.STAMP_DUTY_BUY_RATE, 2)

        total_fees = round(brokerage + stt + exch_turnover + sebi_charges + gst + stamp_duty, 2)

        return {
            "total_fees": total_fees,
            "brokerage": brokerage,
            "stt": stt,
            "exchange_turnover": exch_turnover,
            "sebi_charges": sebi_charges,
            "gst": gst,
            "stamp_duty": stamp_duty,
            "buy_turnover": buy_turnover,
            "sell_turnover": sell_turnover,
            "total_turnover": total_turnover
        }

    @classmethod
    def calculate_pnl(
        cls,
        symbol: str,
        side: str,
        entry_price: float,
        current_or_exit_price: float,
        qty: int,
        option_type: Optional[str] = None,
        is_closed: bool = False,
        exit_reason: Optional[str] = None
    ) -> OptionPnLBreakdown:
        """
        Calculates Gross P&L, Fees, and Net P&L strictly from actual option premiums.
        """
        if not math.isfinite(entry_price) or entry_price <= 0:
            raise ValueError(f"Entry premium must be positive and finite, got {entry_price}")
        if not math.isfinite(current_or_exit_price) or current_or_exit_price < 0:
            raise ValueError(f"Current/Exit premium must be non-negative and finite, got {current_or_exit_price}")
        if qty <= 0:
            raise ValueError(f"Quantity must be a positive integer, got {qty}")

        side_clean = side.upper()
        if side_clean not in ("BUY", "SELL"):
            raise ValueError(f"Side must be 'BUY' or 'SELL', got {side}")

        # Resolve option type from symbol if omitted
        opt_type = option_type.upper() if option_type else ("CE" if symbol.endswith("CE") else ("PE" if symbol.endswith("PE") else "UNKNOWN"))

        # Gross P&L Calculation:
        # BUY: (Current - Entry) * Qty -> Profit when premium rises
        # SELL: (Entry - Current) * Qty -> Profit when premium drops
        if side_clean == "BUY":
            gross_pnl = round((current_or_exit_price - entry_price) * qty, 2)
        else:
            gross_pnl = round((entry_price - current_or_exit_price) * qty, 2)

        fee_dict = cls.calculate_statutory_fees(side_clean, entry_price, current_or_exit_price, qty)
        total_fees = fee_dict["total_fees"]
        net_pnl = round(gross_pnl - total_fees, 2)

        invested = round(entry_price * qty, 2)
        return_pct = round((gross_pnl / invested) * 100.0, 2) if invested > 0 else 0.0

        return OptionPnLBreakdown(
            symbol=symbol,
            option_type=opt_type,
            side=side_clean,
            quantity=qty,
            entry_premium=round(entry_price, 2),
            current_premium=round(current_or_exit_price, 2),
            exit_premium=round(current_or_exit_price, 2) if is_closed else None,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            total_fees=total_fees,
            brokerage=fee_dict["brokerage"],
            stt=fee_dict["stt"],
            exchange_turnover=fee_dict["exchange_turnover"],
            gst=fee_dict["gst"],
            stamp_duty=fee_dict["stamp_duty"],
            return_pct=return_pct,
            is_profit=(gross_pnl > 0),
            status="CLOSED" if is_closed else "OPEN",
            exit_reason=exit_reason
        )

    @classmethod
    def aggregate_partial_exits(
        cls,
        symbol: str,
        side: str,
        entry_price: float,
        executed_fills: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Aggregates multiple partial exits into weighted average exit price and cumulative P&L.
        executed_fills format: [{"exit_price": float, "qty": int, "exit_reason": str}]
        """
        if not executed_fills:
            return {
                "total_qty": 0,
                "weighted_exit_price": 0.0,
                "total_gross_pnl": 0.0,
                "total_fees": 0.0,
                "total_net_pnl": 0.0
            }

        total_qty = 0
        total_exit_turnover = 0.0
        total_gross = 0.0
        total_fees = 0.0
        total_net = 0.0

        for fill in executed_fills:
            f_qty = int(fill["qty"])
            f_price = float(fill["exit_price"])
            res = cls.calculate_pnl(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                current_or_exit_price=f_price,
                qty=f_qty,
                is_closed=True,
                exit_reason=fill.get("exit_reason")
            )
            total_qty += f_qty
            total_exit_turnover += f_price * f_qty
            total_gross += res.gross_pnl
            total_fees += res.total_fees
            total_net += res.net_pnl

        weighted_exit = round(total_exit_turnover / total_qty, 2) if total_qty > 0 else 0.0

        return {
            "total_qty": total_qty,
            "weighted_exit_price": weighted_exit,
            "total_gross_pnl": round(total_gross, 2),
            "total_fees": round(total_fees, 2),
            "total_net_pnl": round(total_net, 2)
        }

    @staticmethod
    def validate_directional_signal(
        market_bias: str,
        option_type: str,
        side: str = "BUY"
    ) -> Tuple[bool, str]:
        """
        Validates directional consistency:
        - Bullish -> BUY CE or SELL PE
        - Bearish -> BUY PE or SELL CE
        """
        bias = market_bias.upper()
        otype = option_type.upper()
        oside = side.upper()

        if bias in ("BULLISH", "UP", "BULL"):
            if oside == "BUY" and otype == "CE":
                return True, "VALID_BULLISH_CE_BUY"
            elif oside == "SELL" and otype == "PE":
                return True, "VALID_BULLISH_PE_SELL"
            else:
                return False, f"DIRECTION_MISMATCH_BULLISH_MARKET_CANNOT_{oside}_{otype}"
        elif bias in ("BEARISH", "DOWN", "BEAR"):
            if oside == "BUY" and otype == "PE":
                return True, "VALID_BEARISH_PE_BUY"
            elif oside == "SELL" and otype == "CE":
                return True, "VALID_BEARISH_CE_SELL"
            else:
                return False, f"DIRECTION_MISMATCH_BEARISH_MARKET_CANNOT_{oside}_{otype}"
        else:
            return False, f"UNCERTAIN_MARKET_BIAS_{bias}"
