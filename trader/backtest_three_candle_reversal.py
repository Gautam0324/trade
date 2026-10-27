#!/usr/bin/env python3
"""
Backtesting Strategy: 3-Candle Bullish Reversal + Next-Day High Breakout
Instrument: TCS (TCS.NS)
Timeframe: 1DAY
Period: 01-10-2023 to 31-12-2025 (plus 10 calendar days warmup)

Setup Conditions:
- C1 = candle[i-2], C2 = candle[i-1], C3 = candle[i]
1. C1 is bearish: C1.close < C1.open
2. C2 is bullish: C2.close > C2.open
3. C3 is bullish: C3.close > C3.open
4. C3 breakout: C3.close >= max(C1.close, C2.close)
5. Volume condition: C3.volume >= C3.VOL_SMA_20 * VOLUME_MULTIPLIER (VOLUME_MULTIPLIER=0)

Entry Execution:
- Pending buy stop trigger at C3.high
- Monitoring begins next session
- If next session reaches or exceeds trigger, filled at trigger (or open if gap-up)
- Target: +5% (entry_price * 1.05)
- Stop loss: Disabled (reference tracked for reporting)
- Exit: When post-entry candle high >= target
- If not exited by end of data: status OPEN_AT_END

Generates:
- setup_check_log.csv
- trade_log.csv
- Performance summary output
"""

import os
import sys
import json
import csv
import datetime
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "trader" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Helper function
def format_date(dt_or_str):
    if isinstance(dt_or_str, (datetime.date, datetime.datetime)):
        return dt_or_str.strftime("%d-%m-%Y")
    return str(dt_or_str)

class AlgrowConnect:
    """
    Data connector abstraction supporting AlgrowConnect interface.
    Fetches daily OHLCV bars via Yahoo Finance or local cache.
    """
    def __init__(self, api_key=None):
        self.api_key = api_key

    def get_data(self, symbol="TCS", start_date="2023-09-20", end_date="2025-12-31", interval="1DAY"):
        # Format symbol for Indian exchange if needed
        yahoo_sym = symbol if "." in symbol else f"{symbol}.NS"
        
        # Parse dates to timestamp
        dt_start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.datetime.strptime(end_date, "%Y-%m-%d") + datetime.timedelta(days=1)
        period1 = int(dt_start.timestamp())
        period2 = int(dt_end.timestamp())

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?period1={period1}&period2={period2}&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                data = json.loads(response.read().decode())
                result = data["chart"]["result"][0]
                timestamps = result["timestamp"]
                quote = result["indicators"]["quote"][0]
                
                bars = []
                for idx, ts in enumerate(timestamps):
                    dt = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
                    date_str = dt.strftime("%Y-%m-%d")
                    o = quote["open"][idx]
                    h = quote["high"][idx]
                    l = quote["low"][idx]
                    c = quote["close"][idx]
                    v = quote["volume"][idx]
                    
                    if None not in (o, h, l, c) and o > 0 and c > 0:
                        bars.append({
                            "date": date_str,
                            "timestamp": ts,
                            "open": round(float(o), 2),
                            "high": round(float(h), 2),
                            "low": round(float(l), 2),
                            "close": round(float(c), 2),
                            "volume": float(v) if v is not None else 0.0
                        })
                return bars
        except Exception as e:
            print(f"[AlgrowConnect] Error fetching data for {symbol}: {e}")
            return []

def calculate_sma(series, period):
    n = len(series)
    out = [None] * n
    if n < period:
        return out
    
    current_sum = sum(series[:period])
    out[period - 1] = round(current_sum / period, 2)
    for i in range(period, n):
        current_sum += series[i] - series[i - period]
        out[i] = round(current_sum / period, 2)
    return out

class ThreeCandleReversalBacktest:
    def __init__(self,
                 symbol="TCS",
                 start_date="01-10-2023",
                 end_date="31-12-2025",
                 target_pct=0.05,
                 volume_multiplier=0.0,
                 quantity=1,
                 gap_fill_policy="OPEN_IF_GAP",  # "TRIGGER" or "OPEN_IF_GAP"
                 brokerage_cost_pct=0.0005,       # 0.05% round-trip costs
                 max_monitoring_sessions=1):
        self.symbol = symbol
        self.start_date_str = start_date
        self.end_date_str = end_date
        self.dt_start = datetime.datetime.strptime(start_date, "%d-%m-%Y").date()
        self.dt_end = datetime.datetime.strptime(end_date, "%d-%m-%Y").date()
        self.target_pct = target_pct
        self.volume_multiplier = volume_multiplier
        self.quantity = quantity
        self.gap_fill_policy = gap_fill_policy
        self.cost_pct = brokerage_cost_pct
        self.max_monitoring_sessions = max_monitoring_sessions
        
        # Reports
        self.setup_log = []
        self.trade_log = []

    def run(self, bars=None):
        if bars is None:
            # 10 calendar days warmup before start date
            warmup_start = (self.dt_start - datetime.timedelta(days=35)).strftime("%Y-%m-%d")
            end_fetch = self.dt_end.strftime("%Y-%m-%d")
            algrow = AlgrowConnect()
            bars = algrow.get_data(self.symbol, warmup_start, end_fetch, interval="1DAY")

        if not bars or len(bars) < 3:
            print("[ERROR] Insufficient bars returned for backtesting.")
            return {}

        # Calculate indicators: SMA 20 of Close, SMA 20 of Volume
        closes = [b["close"] for b in bars]
        volumes = [b["volume"] for b in bars]
        sma20_close = calculate_sma(closes, 20)
        sma20_vol = calculate_sma(volumes, 20)

        for i in range(len(bars)):
            bars[i]["sma20"] = sma20_close[i]
            bars[i]["vol_sma20"] = sma20_vol[i]

        active_position = None
        pending_order = None
        trade_id_counter = 1

        total_setup_candles = 0
        valid_setups = 0
        rejections_by_reason = {
            "C1 not bearish": 0,
            "C2 not bullish": 0,
            "C3 not bullish": 0,
            "C3 not breakout of max(C1, C2)": 0,
            "Volume below threshold": 0,
            "Indicator warm-up NaN": 0,
            "Already in active position": 0
        }
        unfilled_pending = 0

        # Iterate chronologically
        for i in range(2, len(bars)):
            cur_bar = bars[i]
            cur_date = datetime.datetime.strptime(cur_bar["date"], "%Y-%m-%d").date()

            # 1. Manage Active Position Exit
            if active_position is not None:
                # Update highest/lowest after entry
                if cur_bar["high"] > active_position["highest_price"]:
                    active_position["highest_price"] = cur_bar["high"]
                if cur_bar["low"] < active_position["lowest_price"]:
                    active_position["lowest_price"] = cur_bar["low"]

                # Target check
                target_p = active_position["target_price"]
                if cur_bar["high"] >= target_p:
                    # Filled at target or open if gap up above target
                    exit_p = max(target_p, cur_bar["open"]) if self.gap_fill_policy == "OPEN_IF_GAP" and cur_bar["open"] > target_p else target_p
                    exit_date = cur_bar["date"]
                    entry_p = active_position["entry_price"]
                    qty = active_position["qty"]
                    
                    gross_pnl = round((exit_p - entry_p) * qty, 2)
                    costs = round((entry_p + exit_p) * qty * self.cost_pct, 2)
                    net_pnl = round(gross_pnl - costs, 2)
                    
                    entry_dt = datetime.datetime.strptime(active_position["entry_fill_date"], "%Y-%m-%d").date()
                    exit_dt = datetime.datetime.strptime(exit_date, "%Y-%m-%d").date()
                    holding_days = (exit_dt - entry_dt).days
                    
                    mfm = round(((active_position["highest_price"] - entry_p) / entry_p) * 100, 2)

                    active_position.update({
                        "exit_date": exit_date,
                        "exit_price": exit_p,
                        "exit_reason": "TARGET",
                        "gross_pnl": gross_pnl,
                        "costs": costs,
                        "net_pnl": net_pnl,
                        "holding_period_days": holding_days,
                        "max_favorable_movement_pct": mfm,
                        "trade_status": "CLOSED"
                    })
                    self.trade_log.append(dict(active_position))
                    active_position = None

            # 2. Check Pending Buy Stop Entry
            if pending_order is not None and active_position is None:
                trigger_p = pending_order["trigger_price"]
                setup_idx = pending_order["setup_bar_index"]
                sessions_passed = i - setup_idx

                # Check if next session confirmed
                if sessions_passed <= self.max_monitoring_sessions:
                    if cur_bar["high"] >= trigger_p:
                        # Entry triggered!
                        if cur_bar["open"] > trigger_p and self.gap_fill_policy == "OPEN_IF_GAP":
                            fill_p = cur_bar["open"]
                        else:
                            fill_p = trigger_p
                        
                        target_p = round(fill_p * (1.0 + self.target_pct), 2)
                        stop_loss_ref = round(pending_order["c1_low"], 2) # Reference stop for reporting
                        
                        active_position = {
                            "trade_id": f"TRD-{trade_id_counter:03d}",
                            "setup_date": pending_order["setup_date"],
                            "entry_trigger_date": pending_order["setup_date"],
                            "entry_fill_date": cur_bar["date"],
                            "entry_price": fill_p,
                            "target_price": target_p,
                            "stop_loss_ref": stop_loss_ref,
                            "qty": self.quantity,
                            "highest_price": max(fill_p, cur_bar["high"]),
                            "lowest_price": min(fill_p, cur_bar["low"]),
                            "exit_date": None,
                            "exit_price": None,
                            "exit_reason": None,
                            "gross_pnl": 0.0,
                            "costs": 0.0,
                            "net_pnl": 0.0,
                            "holding_period_days": 0,
                            "max_favorable_movement_pct": 0.0,
                            "trade_status": "OPEN"
                        }
                        trade_id_counter += 1
                        pending_order = None
                    else:
                        if sessions_passed == self.max_monitoring_sessions:
                            # Monitoring expired without fill
                            unfilled_pending += 1
                            pending_order = None
                else:
                    unfilled_pending += 1
                    pending_order = None

            # 3. Evaluate Setup Conditions if within requested backtest period
            if self.dt_start <= cur_date <= self.dt_end:
                total_setup_candles += 1
                c1 = bars[i - 2]
                c2 = bars[i - 1]
                c3 = bars[i]

                # Setup conditions
                cond1_bearish = c1["close"] < c1["open"]
                cond2_bullish = c2["close"] > c2["open"]
                cond3_bullish = c3["close"] > c3["open"]
                cond4_breakout = c3["close"] >= max(c1["close"], c2["close"])
                
                vol_threshold = (c3["vol_sma20"] or 0.0) * self.volume_multiplier
                cond5_volume = c3["volume"] >= vol_threshold

                reasons = []
                if not cond1_bearish:
                    reasons.append("C1 not bearish")
                    rejections_by_reason["C1 not bearish"] += 1
                if not cond2_bullish:
                    reasons.append("C2 not bullish")
                    rejections_by_reason["C2 not bullish"] += 1
                if not cond3_bullish:
                    reasons.append("C3 not bullish")
                    rejections_by_reason["C3 not bullish"] += 1
                if not cond4_breakout:
                    reasons.append("C3 not breakout of max(C1, C2)")
                    rejections_by_reason["C3 not breakout of max(C1, C2)"] += 1
                if not cond5_volume:
                    reasons.append("Volume below threshold")
                    rejections_by_reason["Volume below threshold"] += 1

                is_setup_valid = (cond1_bearish and cond2_bullish and cond3_bullish and cond4_breakout and cond5_volume)

                # Record in setup check log
                self.setup_log.append({
                    "setup_date": c3["date"],
                    "c1_date": c1["date"],
                    "c1_open": c1["open"], "c1_high": c1["high"], "c1_low": c1["low"], "c1_close": c1["close"], "c1_volume": c1["volume"], "c1_sma20": c1["sma20"],
                    "c2_date": c2["date"],
                    "c2_open": c2["open"], "c2_high": c2["high"], "c2_low": c2["low"], "c2_close": c2["close"], "c2_volume": c2["volume"], "c2_sma20": c2["sma20"],
                    "c3_date": c3["date"],
                    "c3_open": c3["open"], "c3_high": c3["high"], "c3_low": c3["low"], "c3_close": c3["close"], "c3_volume": c3["volume"], "c3_sma20": c3["sma20"], "c3_vol_sma20": c3["vol_sma20"],
                    "cond1_c1_bearish": cond1_bearish,
                    "cond2_c2_bullish": cond2_bullish,
                    "cond3_c3_bullish": cond3_bullish,
                    "cond4_c3_breakout": cond4_breakout,
                    "cond5_volume": cond5_volume,
                    "setup_accepted": is_setup_valid,
                    "rejection_reasons": "; ".join(reasons) if reasons else "None"
                })

                if is_setup_valid:
                    valid_setups += 1
                    # Only register pending order if not already in position
                    if active_position is None and pending_order is None:
                        pending_order = {
                            "setup_date": c3["date"],
                            "setup_bar_index": i,
                            "trigger_price": c3["high"],
                            "c1_low": c1["low"]
                        }
                    elif active_position is not None:
                        rejections_by_reason["Already in active position"] += 1

        # Check if any position remains open at end of data
        if active_position is not None:
            last_bar = bars[-1]
            entry_p = active_position["entry_price"]
            entry_dt = datetime.datetime.strptime(active_position["entry_fill_date"], "%Y-%m-%d").date()
            last_dt = datetime.datetime.strptime(last_bar["date"], "%Y-%m-%d").date()
            holding_days = (last_dt - entry_dt).days
            mfm = round(((active_position["highest_price"] - entry_p) / entry_p) * 100, 2)
            cur_val = last_bar["close"]
            gross_pnl = round((cur_val - entry_p) * self.quantity, 2)
            costs = round((entry_p + cur_val) * self.quantity * self.cost_pct, 2)

            active_position.update({
                "exit_date": "OPEN_AT_END",
                "exit_price": cur_val, # Marked to market
                "exit_reason": "OPEN_AT_END",
                "gross_pnl": gross_pnl,
                "costs": costs,
                "net_pnl": round(gross_pnl - costs, 2),
                "holding_period_days": holding_days,
                "max_favorable_movement_pct": mfm,
                "trade_status": "OPEN_AT_END"
            })
            self.trade_log.append(dict(active_position))

        # Save CSV reports
        setup_csv_path = REPORTS_DIR / "setup_check_log.csv"
        trade_csv_path = REPORTS_DIR / "trade_log.csv"
        
        self.save_csv(setup_csv_path, self.setup_log)
        self.save_csv(trade_csv_path, self.trade_log)

        # Performance summary metrics
        closed_trades = [t for t in self.trade_log if t["trade_status"] == "CLOSED"]
        open_trades = [t for t in self.trade_log if t["trade_status"] == "OPEN_AT_END"]
        
        wins = [t for t in closed_trades if t["net_pnl"] > 0]
        win_rate = round((len(wins) / len(closed_trades)) * 100, 2) if closed_trades else 0.0
        avg_holding = round(sum(t["holding_period_days"] for t in closed_trades) / len(closed_trades), 1) if closed_trades else 0.0
        tot_gross_pnl = round(sum(t["gross_pnl"] for t in closed_trades), 2)
        tot_net_pnl = round(sum(t["net_pnl"] for t in closed_trades), 2)

        summary = {
            "symbol": self.symbol,
            "backtest_start": self.start_date_str,
            "backtest_end": self.end_date_str,
            "setup_candles_evaluated": total_setup_candles,
            "valid_setups": valid_setups,
            "rejected_setups_by_reason": rejections_by_reason,
            "pending_orders_not_filled": unfilled_pending,
            "total_filled_trades": len(self.trade_log),
            "target_exits": len(closed_trades),
            "open_positions_at_end": len(open_trades),
            "total_gross_pnl": tot_gross_pnl,
            "total_net_pnl": tot_net_pnl,
            "win_rate_closed_pct": win_rate,
            "average_holding_period_days": avg_holding,
            "setup_log_csv": str(setup_csv_path),
            "trade_log_csv": str(trade_csv_path)
        }
        return summary

    def save_csv(self, filepath, records):
        if not records:
            return
        keys = records[0].keys()
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(records)

if __name__ == "__main__":
    bt = ThreeCandleReversalBacktest(
        symbol="TCS",
        start_date="01-10-2023",
        end_date="31-12-2025",
        target_pct=0.05,
        volume_multiplier=0.0,
        quantity=1
    )
    res = bt.run()
    print("\n" + "="*70)
    print(" 3-CANDLE BULLISH REVERSAL + NEXT-DAY HIGH BREAKOUT BACKTEST REPORT")
    print("="*70)
    print(f"Symbol:                   {res.get('symbol')}")
    print(f"Period:                   {res.get('backtest_start')} to {res.get('backtest_end')}")
    print(f"Candles Evaluated:        {res.get('setup_candles_evaluated')}")
    print(f"Valid Setups Detected:    {res.get('valid_setups')}")
    print(f"Pending Unfilled Orders:  {res.get('pending_orders_not_filled')}")
    print(f"Total Filled Trades:      {res.get('total_filled_trades')}")
    print(f"Target Exits (Closed):    {res.get('target_exits')}")
    print(f"Open Positions at End:    {res.get('open_positions_at_end')}")
    print(f"Win Rate (Closed Trades): {res.get('win_rate_closed_pct')}%")
    print(f"Total Net P&L:            Rs. {res.get('total_net_pnl'):,.2f}")
    print(f"Average Holding Period:   {res.get('average_holding_period_days')} calendar days")
    print(f"Setup Log CSV:            {res.get('setup_log_csv')}")
    print(f"Trade Log CSV:            {res.get('trade_log_csv')}")
    print("="*70)
    print("DISCLAIMER: No-Stop-Loss Strategy. Positions can experience drawdowns.\n")
