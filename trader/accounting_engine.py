#!/usr/bin/env python3
"""
Authoritative Accounting Engine for Day Trader Pro.
Standardized financial accounting and trade reconciliation across Cash Equities & F&O Derivatives.

Ensures:
- Exact Indian statutory fee calculation (Brokerage, STT, Exchange Txn, GST, SEBI, Stamp Duty).
- Strict separation of Statutory Taxes vs Simulated Slippage.
- Canonical instrument symbol formatting (strips .NS, .BO, whitespace).
- Audit tracking with explicit status (VERIFIED vs COST_DATA_INCOMPLETE).
- Reconciles Daily Realized P&L and Account Free Cash mathematically.
"""

import math
import datetime
from typing import Dict, Any, Optional

class AccountingEngine:
    VERSION = "3.0.0"

    # Default Statutory & Brokerage Rates for Indian Markets (Discount Broker Model)
    BROKERAGE_PER_ORDER_CAP = 20.0  # Max ₹20 per executed order leg
    BROKERAGE_PCT_CASH = 0.0003     # 0.03%
    BROKERAGE_PCT_FNO = 0.0003      # ₹20 flat or 0.03%
    STT_CASH_INTRADAY_SELL = 0.00025 # 0.025% on Sell side only
    STT_OPTIONS_SELL = 0.000625     # 0.0625% on option premium (Sell leg)
    NSE_TXN_CASH = 0.0000345        # 0.00345% on turnover
    NSE_TXN_OPTIONS = 0.00053       # 0.053% on premium turnover
    BSE_TXN_CASH = 0.0000375        # 0.00375% on turnover
    GST_RATE = 0.18                 # 18% on (Brokerage + Exchange Txn Fees)
    SEBI_TURNOVER_RATE = 0.000001   # ₹10 per crore (0.0001%)
    STAMP_DUTY_CASH_BUY = 0.00003   # 0.003% on Buy leg only
    STAMP_DUTY_OPTIONS_BUY = 0.00003 # 0.003% on Buy leg
    DEFAULT_SLIPPAGE_RATE = 0.0002  # 0.02% simulated slippage

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.slippage_rate = float(self.config.get("slippage_rate", self.DEFAULT_SLIPPAGE_RATE))

    @staticmethod
    def canonical_symbol(symbol: str) -> str:
        """Normalizes symbol to canonical format, removing exchange suffixes."""
        if not symbol:
            return ""
        return str(symbol).strip().upper().replace(".NS", "").replace(".BO", "").replace("-EQ", "")

    def calculate_trade_costs(
        self,
        segment: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: int,
        exchange: str = "NSE"
    ) -> Dict[str, Any]:
        """
        Calculates exact granular statutory charges, brokerage, and simulated slippage.
        Segment: 'EQUITY' or 'OPTIONS'
        Side: 'BUY' (Long) or 'SELL' (Short)
        """
        qty = int(quantity)
        entry_p = float(entry_price)
        exit_p = float(exit_price)

        if qty <= 0 or entry_p <= 0 or exit_p <= 0:
            return {
                "status": "COST_DATA_INCOMPLETE",
                "gross_pnl": 0.0, "total_statutory": 0.0, "slippage": 0.0,
                "total_costs": 0.0, "net_pnl": 0.0, "brokerage": 0.0,
                "stt": 0.0, "exchange_charges": 0.0, "gst": 0.0,
                "stamp_duty": 0.0, "sebi_charges": 0.0
            }

        entry_turnover = round(qty * entry_p, 2)
        exit_turnover = round(qty * exit_p, 2)
        total_turnover = round(entry_turnover + exit_turnover, 2)

        # 1. Gross PnL
        is_buy = side.upper() == "BUY"
        if is_buy:
            gross_pnl = round((exit_p - entry_p) * qty, 2)
            buy_turnover = entry_turnover
            sell_turnover = exit_turnover
        else:
            gross_pnl = round((entry_p - exit_p) * qty, 2)
            buy_turnover = exit_turnover
            sell_turnover = entry_turnover

        # 2. Granular Charges
        is_options = "OPT" in segment.upper()

        if is_options:
            # Options: Flat ₹20 per leg
            brokerage_entry = min(self.BROKERAGE_PER_ORDER_CAP, round(entry_turnover * self.BROKERAGE_PCT_FNO, 2))
            brokerage_exit = min(self.BROKERAGE_PER_ORDER_CAP, round(exit_turnover * self.BROKERAGE_PCT_FNO, 2))
            brokerage = round(brokerage_entry + brokerage_exit, 2)

            stt = round(sell_turnover * self.STT_OPTIONS_SELL, 2)
            exchange_txn = round(total_turnover * self.NSE_TXN_OPTIONS, 2)
            stamp_duty = round(buy_turnover * self.STAMP_DUTY_OPTIONS_BUY, 2)
        else:
            # Cash Equity
            brokerage_entry = min(self.BROKERAGE_PER_ORDER_CAP, round(entry_turnover * self.BROKERAGE_PCT_CASH, 2))
            brokerage_exit = min(self.BROKERAGE_PER_ORDER_CAP, round(exit_turnover * self.BROKERAGE_PCT_CASH, 2))
            brokerage = round(brokerage_entry + brokerage_exit, 2)

            stt = round(sell_turnover * self.STT_CASH_INTRADAY_SELL, 2)
            txn_rate = self.BSE_TXN_CASH if "BSE" in exchange.upper() else self.NSE_TXN_CASH
            exchange_txn = round(total_turnover * txn_rate, 2)
            stamp_duty = round(buy_turnover * self.STAMP_DUTY_CASH_BUY, 2)

        gst = round((brokerage + exchange_txn) * self.GST_RATE, 2)
        sebi_charges = round(total_turnover * self.SEBI_TURNOVER_RATE, 2)
        slippage = round(total_turnover * self.slippage_rate, 2)

        total_statutory = round(brokerage + stt + exchange_txn + gst + stamp_duty + sebi_charges, 2)
        total_costs = round(total_statutory + slippage, 2)
        net_pnl = round(gross_pnl - total_costs, 2)

        return {
            "status": "VERIFIED",
            "gross_pnl": gross_pnl,
            "brokerage": brokerage,
            "stt": stt,
            "exchange_charges": exchange_txn,
            "gst": gst,
            "stamp_duty": stamp_duty,
            "sebi_charges": sebi_charges,
            "slippage": slippage,
            "total_statutory": total_statutory,
            "total_costs": total_costs,
            "net_pnl": net_pnl
        }

    def create_completed_trade_record(
        self,
        trade_id: str,
        position_id: str,
        account_id: str,
        strategy_id: str,
        instrument: str,
        segment: str,
        side: str,
        quantity: int,
        entry_timestamp: str,
        exit_timestamp: str,
        entry_price: float,
        exit_price: float,
        exit_reason: str,
        entry_signal_id: Optional[str] = None,
        data_source: str = "LIVE_DATA",
        strategy_version: str = "3.0.0",
        exchange: str = "NSE"
    ) -> Dict[str, Any]:
        """
        Builds a strictly schema-compliant completed trade record with full fee isolation.
        """
        can_sym = self.canonical_symbol(instrument)
        costs = self.calculate_trade_costs(segment, side, entry_price, exit_price, quantity, exchange)

        date_str = entry_timestamp.split("T")[0] if "T" in entry_timestamp else entry_timestamp.split(" ")[0]
        exit_date_str = exit_timestamp.split("T")[0] if "T" in exit_timestamp else exit_timestamp.split(" ")[0]

        return {
            "trade_id": str(trade_id),
            "position_id": str(position_id),
            "account_id": str(account_id).lower(),
            "strategy_id": str(strategy_id),
            "strategy_version": str(strategy_version),
            "instrument": can_sym,
            "symbol": can_sym,  # For backward-compatibility with UI
            "exchange": str(exchange).upper(),
            "segment": str(segment).upper(),
            "side": str(side).upper(),
            "quantity": int(quantity),
            "qty": int(quantity),  # Backward compatibility
            "entry_timestamp": str(entry_timestamp),
            "entry_time": str(entry_timestamp),
            "exit_timestamp": str(exit_timestamp),
            "exit_time": str(exit_timestamp),
            "entry_price": round(float(entry_price), 2),
            "exit_price": round(float(exit_price), 2),
            "gross_pnl": costs["gross_pnl"],
            "brokerage": costs["brokerage"],
            "stt": costs["stt"],
            "exchange_charges": costs["exchange_charges"],
            "gst": costs["gst"],
            "stamp_duty": costs["stamp_duty"],
            "sebi_charges": costs["sebi_charges"],
            "slippage": costs["slippage"],
            "total_statutory": costs["total_statutory"],
            "total_costs": costs["total_costs"],
            "fees": costs["total_costs"],  # Backward compatibility
            "net_pnl": costs["net_pnl"],
            "pnl": costs["net_pnl"],  # Backward compatibility
            "exit_reason": str(exit_reason),
            "reason": str(exit_reason),  # Backward compatibility
            "entry_signal_id": str(entry_signal_id or ""),
            "accounting_status": costs["status"],
            "data_source": str(data_source),
            "date": date_str,
            "exit_date": exit_date_str
        }

    @staticmethod
    def reconcile_account(ledger: Dict[str, Any], starting_capital: float) -> Dict[str, Any]:
        """
        Performs authoritative reconciliation of trade history vs reported realized P&L & cash.
        """
        trade_history = ledger.get("trade_history", [])
        open_positions = ledger.get("open_positions", [])

        # Sum of net PnL from verified trades
        calculated_realized_pnl = 0.0
        incomplete_trades_count = 0
        pnl_by_date: Dict[str, float] = {}

        for t in trade_history:
            net_pnl = t.get("net_pnl", t.get("pnl"))
            if net_pnl is None:
                incomplete_trades_count += 1
                continue
            pnl_val = float(net_pnl)
            calculated_realized_pnl += pnl_val

            d = t.get("date", t.get("exit_date", "UNKNOWN"))
            pnl_by_date[d] = round(pnl_by_date.get(d, 0.0) + pnl_val, 2)

        calculated_realized_pnl = round(calculated_realized_pnl, 2)
        reported_realized_pnl = round(float(ledger.get("realized_pnl", 0.0)), 2)

        deployed_cash = round(sum(
            float(p.get("qty", p.get("quantity", 0))) * float(p.get("entry_price", 0.0))
            for p in open_positions
        ), 2)

        calculated_free_cash = round(starting_capital + calculated_realized_pnl - deployed_cash, 2)
        reported_free_cash = round(float(ledger.get("free_cash", calculated_free_cash)), 2)

        pnl_discrepancy = round(abs(calculated_realized_pnl - reported_realized_pnl), 2)
        cash_discrepancy = round(abs(calculated_free_cash - reported_free_cash), 2)

        is_reconciled = (pnl_discrepancy <= 0.05) and (cash_discrepancy <= 0.05) and (incomplete_trades_count == 0)

        return {
            "is_reconciled": is_reconciled,
            "starting_capital": starting_capital,
            "calculated_realized_pnl": calculated_realized_pnl,
            "reported_realized_pnl": reported_realized_pnl,
            "pnl_discrepancy": pnl_discrepancy,
            "deployed_cash": deployed_cash,
            "calculated_free_cash": calculated_free_cash,
            "reported_free_cash": reported_free_cash,
            "cash_discrepancy": cash_discrepancy,
            "incomplete_trades_count": incomplete_trades_count,
            "pnl_by_date": pnl_by_date
        }

