"""
Performance Metrics for Day Trader Pro V2.
Calculates mathematically consistent risk-adjusted metrics: Win Rate, Profit Factor, Expectancy, Max Drawdown.
"""

from typing import List, Dict, Any

class PerformanceMetrics:
    @staticmethod
    def calculate(trades: List[Dict[str, Any]], initial_capital: float = 1000000.0) -> Dict[str, Any]:
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "gross_pnl": 0.0,
                "total_fees": 0.0,
                "net_pnl": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0,
                "avg_trade_pnl": 0.0,
                "expectancy": 0.0,
                "wins": 0,
                "losses": 0
            }

        wins = [t for t in trades if float(t.get("pnl", 0.0)) > 0]
        losses = [t for t in trades if float(t.get("pnl", 0.0)) < 0]

        win_count = len(wins)
        loss_count = len(losses)
        win_rate = (win_count / total_trades) * 100.0

        gross_wins = sum(float(t.get("pnl", 0.0)) for t in wins)
        gross_losses = abs(sum(float(t.get("pnl", 0.0)) for t in losses))

        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)

        total_gross_pnl = sum(float(t.get("gross_pnl", t.get("pnl", 0.0))) for t in trades)
        total_fees = sum(float(t.get("fees", 0.0)) for t in trades)
        total_net_pnl = sum(float(t.get("pnl", 0.0)) for t in trades)

        # Max Drawdown calculation
        equity = initial_capital
        peak = equity
        max_dd = 0.0
        max_dd_pct = 0.0

        for t in trades:
            equity += float(t.get("pnl", 0.0))
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd
                max_dd_pct = (max_dd / peak) * 100.0 if peak > 0 else 0.0

        avg_win = (gross_wins / win_count) if win_count > 0 else 0.0
        avg_loss = (gross_losses / loss_count) if loss_count > 0 else 0.0

        # Expectancy = (Win% * AvgWin) - (Loss% * AvgLoss)
        win_prob = win_count / total_trades
        loss_prob = loss_count / total_trades
        expectancy = (win_prob * avg_win) - (loss_prob * avg_loss)

        return {
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "win_rate": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "gross_pnl": round(total_gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "net_pnl": round(total_net_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2)
        }

