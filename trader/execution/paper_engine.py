"""
Paper Trading Execution Engine for Day Trader Pro V2.
Enforces strict paper-trading isolation boundary, realistic slippage, cost model accounting, and ledger logging.
"""

from typing import Dict, Any, Optional
import time
import os
import zoneinfo
import datetime

from .paper_order_manager import PaperOrderManager, OrderLifecycleState
from .position_manager import PositionManager
from .trade_ledger import TradeLedger
try:
    from cost_model import CostModel
except ImportError:
    from trader.cost_model import CostModel

IST_TZ = zoneinfo.ZoneInfo("Asia/Kolkata")

class PaperTradingIsolationError(RuntimeError):
    pass

class PaperExecutionEngine:
    def __init__(
        self,
        paper_order_manager: PaperOrderManager,
        position_manager: PositionManager,
        trade_ledger: TradeLedger,
        cost_model: Optional[CostModel] = None,
        slippage_pct: float = 0.0002  # 0.02%
    ):
        self.paper_order_manager = paper_order_manager
        self.position_manager = position_manager
        self.trade_ledger = trade_ledger
        self.cost_model = cost_model or CostModel()
        self.slippage_pct = slippage_pct
        self._enforce_paper_isolation()

    def _enforce_paper_isolation(self):
        live_flag = os.getenv("LIVE_TRADING", "false").lower()
        if live_flag in ("true", "1", "yes"):
            raise PaperTradingIsolationError("CRITICAL SAFETY BLOCK: Live trading flag is strictly disabled in PaperExecutionEngine!")

    def execute_paper_entry(
        self,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        stop_loss: float,
        target_price: float,
        signal_id: str,
        strategy_name: str
    ) -> Dict[str, Any]:
        self._enforce_paper_isolation()

        # 1. Apply simulated slippage
        # Buy fills slightly higher, Sell fills slightly lower
        slip_mult = 1.0 + self.slippage_pct if side.upper() == "BUY" else 1.0 - self.slippage_pct
        fill_price = round(price * slip_mult, 4)

        # 2. Create paper order
        order = self.paper_order_manager.create_order(
            signal_id=signal_id,
            symbol=symbol,
            side=side,
            qty=quantity,
            price=fill_price,
            stop_loss=stop_loss,
            target_price=target_price,
            strategy_name=strategy_name
        )

        now_str = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S")
        pos_id = f"POS-{int(time.time() * 1000)}-{symbol.upper()}"

        position_dict = {
            "id": pos_id,
            "order_id": order["order_id"],
            "symbol": symbol.upper(),
            "side": side.upper(),
            "quantity": quantity,
            "qty": quantity,
            "entry_price": fill_price,
            "sl_price": stop_loss,
            "target_price": target_price,
            "strategy_name": strategy_name,
            "entry_time": now_str,
            "entry_time_epoch": time.time(),
            "status": "OPEN"
        }

        # 3. Transition order state to POSITION_OPEN
        self.paper_order_manager.update_state(order["order_id"], OrderLifecycleState.POSITION_OPEN)
        self.position_manager.open_position(position_dict)

        return position_dict

    def execute_paper_exit(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        exchange: str = "NSE"
    ) -> Optional[Dict[str, Any]]:
        self._enforce_paper_isolation()

        pos = self.position_manager.close_position(symbol)
        if not pos:
            return None

        side = pos["side"]
        qty = pos["quantity"]
        entry_price = pos["entry_price"]

        # Apply exit slippage
        slip_mult = 1.0 - self.slippage_pct if side.upper() == "BUY" else 1.0 + self.slippage_pct
        real_exit_price = round(exit_price * slip_mult, 4)

        # Calculate exact transaction costs & statutory fees
        roundtrip = self.cost_model.calculate_roundtrip_cost(
            entry_side=side,
            entry_price=entry_price,
            exit_price=real_exit_price,
            qty=qty,
            exchange=exchange
        )

        gross_pnl = roundtrip["gross_pnl"]
        fees = roundtrip["total_costs"]
        net_pnl = roundtrip["net_pnl"]

        now_str = datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S")

        trade_record = {
            "id": pos["id"],
            "order_id": pos.get("order_id"),
            "symbol": symbol.upper(),
            "side": side,
            "qty": qty,
            "quantity": qty,
            "entry_price": entry_price,
            "exit_price": real_exit_price,
            "entry_time": pos["entry_time"],
            "exit_time": now_str,
            "gross_pnl": round(gross_pnl, 2),
            "fees": round(fees, 2),
            "pnl": round(net_pnl, 2),
            "reason": exit_reason,
            "status": "CLOSED"
        }

        # Update order state & record to ledger
        order_id = pos.get("order_id")
        if order_id:
            self.paper_order_manager.update_state(order_id, OrderLifecycleState.POSITION_CLOSED)

        self.trade_ledger.record_closed_trade(trade_record)
        return trade_record
