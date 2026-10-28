#!/usr/bin/env python3
"""
Institutional Transaction Cost & Slippage Model for Indian Equities & Derivatives.
Computes precise regulatory and statutory charges for NSE/BSE intraday trading:
1. Brokerage: min(0.03%, ₹20.00) per order leg (discount broker model)
2. STT (Securities Transaction Tax): 0.025% on SELL leg (Equity Intraday)
3. Exchange Transaction Charges: 0.00345% on turnover (NSE) / 0.00375% (BSE)
4. GST: 18% on (Brokerage + Exchange Charges)
5. SEBI Turnover Charges: ₹10 per crore (0.0001%)
6. Stamp Duty: 0.003% on BUY leg
7. Conservative Modeled Slippage: configurable (default 0.02% of turnover)
"""

class CostModel:
    def __init__(self, config=None):
        cfg = config or {}
        # Allow overrides from config
        self.brokerage_rate = float(cfg.get("brokerage_rate", 0.0003)) # 0.03%
        self.max_brokerage_per_order = float(cfg.get("max_brokerage_per_order", 20.0)) # ₹20 cap
        self.stt_sell_rate = float(cfg.get("stt_sell_rate", 0.00025)) # 0.025%
        self.nse_txn_rate = float(cfg.get("nse_txn_rate", 0.0000345)) # 0.00345%
        self.bse_txn_rate = float(cfg.get("bse_txn_rate", 0.0000375)) # 0.00375%
        self.gst_rate = float(cfg.get("gst_rate", 0.18)) # 18%
        self.sebi_rate = float(cfg.get("sebi_rate", 0.000001)) # ₹10 / crore (0.0001%)
        self.stamp_duty_buy_rate = float(cfg.get("stamp_duty_buy_rate", 0.00003)) # 0.003%
        self.slippage_rate = float(cfg.get("slippage_rate", 0.0002)) # 0.02% modeled slippage

    def calculate_turnover(self, qty, price):
        return round(float(qty) * float(price), 2)

    def calculate_order_cost(self, side, qty, price, exchange="NSE"):
        """
        Calculates all fees and charges for a single order leg.
        """
        turnover = self.calculate_turnover(qty, price)
        if turnover <= 0:
            return {
                "turnover": 0.0, "brokerage": 0.0, "stt": 0.0, "exchange_txn": 0.0,
                "gst": 0.0, "sebi": 0.0, "stamp_duty": 0.0, "slippage": 0.0,
                "total_statutory": 0.0, "total_cost": 0.0
            }

        # 1. Brokerage
        brokerage = min(self.max_brokerage_per_order, round(turnover * self.brokerage_rate, 2))

        # 2. STT (only on SELL for equity intraday)
        side_upper = side.upper()
        stt = round(turnover * self.stt_sell_rate, 2) if side_upper == "SELL" else 0.0

        # 3. Exchange Txn Fee
        txn_rate = self.bse_txn_rate if "BSE" in exchange.upper() else self.nse_txn_rate
        exchange_txn = round(turnover * txn_rate, 2)

        # 4. GST (18% on Brokerage + Exchange Txn)
        gst = round((brokerage + exchange_txn) * self.gst_rate, 2)

        # 5. SEBI turnover fee
        sebi = round(turnover * self.sebi_rate, 4)

        # 6. Stamp duty (only on BUY)
        stamp_duty = round(turnover * self.stamp_duty_buy_rate, 2) if side_upper == "BUY" else 0.0

        # 7. Conservative slippage
        slippage = round(turnover * self.slippage_rate, 2)

        statutory = round(brokerage + stt + exchange_txn + gst + sebi + stamp_duty, 2)
        total_cost = round(statutory + slippage, 2)

        return {
            "turnover": turnover,
            "brokerage": brokerage,
            "stt": stt,
            "exchange_txn": exchange_txn,
            "gst": gst,
            "sebi": round(sebi, 2),
            "stamp_duty": stamp_duty,
            "slippage": slippage,
            "total_statutory": statutory,
            "total_cost": total_cost
        }

    def calculate_leg_cost(self, side, price, qty, exchange="NSE"):
        return self.calculate_order_cost(side, qty, price, exchange)

    def simulate_fill_price(self, side, price):
        if side.upper() == "BUY":
            return round(float(price) * (1.0 + self.slippage_rate), 2)
        else:
            return round(float(price) * (1.0 - self.slippage_rate), 2)

    def calculate_roundtrip_cost(self, entry_side, entry_price, exit_price, qty, exchange="NSE"):
        """
        Calculates complete round-trip costs across entry and exit legs.
        Returns:
            gross_pnl, total_costs, net_pnl, breakdown
        """
        exit_side = "SELL" if entry_side.upper() == "BUY" else "BUY"
        entry_costs = self.calculate_order_cost(entry_side, qty, entry_price, exchange)
        exit_costs = self.calculate_order_cost(exit_side, qty, exit_price, exchange)

        if entry_side.upper() == "BUY":
            gross_pnl = round((float(exit_price) - float(entry_price)) * int(qty), 2)
        else:
            gross_pnl = round((float(entry_price) - float(exit_price)) * int(qty), 2)

        total_costs = round(entry_costs["total_cost"] + exit_costs["total_cost"], 2)
        net_pnl = round(gross_pnl - total_costs, 2)

        return {
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "total_costs": total_costs,
            "entry_costs": entry_costs,
            "exit_costs": exit_costs
        }
