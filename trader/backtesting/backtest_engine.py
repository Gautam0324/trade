"""
Backtesting Engine for Day Trader Pro V2.
Deterministic historical simulation with zero look-ahead bias, conservative collision handling, and realistic Indian costs.
"""

from typing import List, Dict, Any, Optional
import math
from trader.indicators.indicator_engine import IndicatorEngine
from trader.strategies.strategy_manager import StrategyManager
from trader.cost_model import CostModel
from .performance_metrics import PerformanceMetrics

class BacktestEngine:
    def __init__(
        self,
        strategy_manager: Optional[StrategyManager] = None,
        indicator_engine: Optional[IndicatorEngine] = None,
        cost_model: Optional[CostModel] = None,
        initial_capital: float = 1000000.0,
        slippage_pct: float = 0.0002,
        risk_pct: float = 1.0
    ):
        self.strategy_manager = strategy_manager or StrategyManager()
        self.indicator_engine = indicator_engine or IndicatorEngine()
        self.cost_model = cost_model or CostModel()
        self.initial_capital = initial_capital
        self.slippage_pct = slippage_pct
        self.risk_pct = risk_pct

    def run_backtest(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        strategy_name: str = "CONFLUENCE"
    ) -> Dict[str, Any]:
        if len(candles) < 35:
            return {"trades": [], "metrics": PerformanceMetrics.calculate([])}

        trades: List[Dict[str, Any]] = []
        open_position: Optional[Dict[str, Any]] = None

        # Build rolling bar structure
        rolling_bars = {
            "timestamp": [],
            "open": [],
            "high": [],
            "low": [],
            "close": [],
            "volume": []
        }

        for idx, bar in enumerate(candles):
            rolling_bars["timestamp"].append(bar["timestamp"])
            rolling_bars["open"].append(float(bar["open"]))
            rolling_bars["high"].append(float(bar["high"]))
            rolling_bars["low"].append(float(bar["low"]))
            rolling_bars["close"].append(float(bar["close"]))
            rolling_bars["volume"].append(float(bar.get("volume", 0.0)))

            c = rolling_bars["close"][-1]
            h = rolling_bars["high"][-1]
            l = rolling_bars["low"][-1]
            ts = rolling_bars["timestamp"][-1]

            # 1. Evaluate open position exit on current candle
            if open_position:
                side = open_position["side"]
                sl = open_position["stop_loss"]
                tgt = open_position["target"]
                pos_qty = open_position["qty"]
                pos_entry = open_position["entry_price"]

                exit_triggered = False
                exit_price = 0.0
                exit_reason = ""

                if side == "BUY":
                    # Conservative collision rule: if both hit in the same bar, assume stop loss triggered first
                    if l <= sl and h >= tgt:
                        exit_triggered = True
                        exit_price = sl * (1.0 - self.slippage_pct)
                        exit_reason = "STOP_LOSS_HIT_SAME_BAR_COLLISION"
                    elif l <= sl:
                        exit_triggered = True
                        exit_price = sl * (1.0 - self.slippage_pct)
                        exit_reason = "STOP_LOSS_HIT"
                    elif h >= tgt:
                        exit_triggered = True
                        exit_price = tgt * (1.0 - self.slippage_pct)
                        exit_reason = "TARGET_HIT"
                elif side == "SELL":
                    if h >= sl and l <= tgt:
                        exit_triggered = True
                        exit_price = sl * (1.0 + self.slippage_pct)
                        exit_reason = "STOP_LOSS_HIT_SAME_BAR_COLLISION"
                    elif h >= sl:
                        exit_triggered = True
                        exit_price = sl * (1.0 + self.slippage_pct)
                        exit_reason = "STOP_LOSS_HIT"
                    elif l <= tgt:
                        exit_triggered = True
                        exit_price = tgt * (1.0 + self.slippage_pct)
                        exit_reason = "TARGET_HIT"

                if exit_triggered:
                    cost_info = self.cost_model.calculate_roundtrip_cost(
                        entry_side=side,
                        entry_price=pos_entry,
                        exit_price=exit_price,
                        qty=pos_qty,
                        exchange="NSE"
                    )
                    trade_record = {
                        "symbol": symbol,
                        "side": side,
                        "entry_time": open_position["entry_time"],
                        "exit_time": ts,
                        "entry_price": round(pos_entry, 2),
                        "exit_price": round(exit_price, 2),
                        "qty": pos_qty,
                        "gross_pnl": round(cost_info["gross_pnl"], 2),
                        "fees": round(cost_info["total_costs"], 2),
                        "pnl": round(cost_info["net_pnl"], 2),
                        "reason": exit_reason
                    }
                    trades.append(trade_record)
                    open_position = None
                    continue

            # 2. If flat, evaluate completed candle indicators and strategy for new entry
            if not open_position and len(rolling_bars["close"]) >= 35:
                indicators = self.indicator_engine.calculate_all(symbol, rolling_bars)
                signal = self.strategy_manager.evaluate_completed_candle(
                    symbol=symbol,
                    timeframe="1m",
                    candles=rolling_bars,
                    indicators=indicators,
                    strategy_mode=strategy_name
                )

                if signal and signal.get("signal") in ("BUY", "SELL"):
                    entry_side = signal["signal"]
                    raw_entry = signal["entry_price"]
                    sl_level = signal["stop_loss"]
                    tgt_level = signal["target"]

                    # Slippage on entry
                    fill_entry = raw_entry * (1.0 + self.slippage_pct if entry_side == "BUY" else 1.0 - self.slippage_pct)

                    # Position sizing
                    per_share_risk = abs(fill_entry - sl_level)
                    if per_share_risk > 0:
                        risk_cap = self.initial_capital * (self.risk_pct / 100.0)
                        max_val = self.initial_capital * 0.35
                        qty_risk = risk_cap / per_share_risk
                        qty_cap = max_val / fill_entry
                        final_qty = max(1, int(math.floor(min(qty_risk, qty_cap))))

                        open_position = {
                            "symbol": symbol,
                            "side": entry_side,
                            "entry_price": fill_entry,
                            "stop_loss": sl_level,
                            "target": tgt_level,
                            "qty": final_qty,
                            "entry_time": ts
                        }

        # End of session square-off if position still open
        if open_position:
            last_c = rolling_bars["close"][-1]
            last_ts = rolling_bars["timestamp"][-1]
            side = open_position["side"]
            exit_price = last_c * (1.0 - self.slippage_pct if side == "BUY" else 1.0 + self.slippage_pct)
            cost_info = self.cost_model.calculate_roundtrip_cost(
                entry_side=side,
                entry_price=open_position["entry_price"],
                exit_price=exit_price,
                qty=open_position["qty"],
                exchange="NSE"
            )
            trades.append({
                "symbol": symbol,
                "side": side,
                "entry_time": open_position["entry_time"],
                "exit_time": last_ts,
                "entry_price": round(open_position["entry_price"], 2),
                "exit_price": round(exit_price, 2),
                "qty": open_position["qty"],
                "gross_pnl": round(cost_info["gross_pnl"], 2),
                "fees": round(cost_info["total_costs"], 2),
                "pnl": round(cost_info["net_pnl"], 2),
                "reason": "EOD_SQUARE_OFF"
            })

        metrics = PerformanceMetrics.calculate(trades, initial_capital=self.initial_capital)
        return {"trades": trades, "metrics": metrics}

