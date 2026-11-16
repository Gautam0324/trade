#!/usr/bin/env python3
"""
Vectorized Fast Sensitivity Analysis for Phase 1.5 - F&O Strategy Failure Attribution.
Precomputes indicators per asset once to test 5 strategic regimes in sub-second time.
"""

import sys
import json
import pandas as pd
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
HIST_DIR = BASE_DIR / "historical_data"
sys.path.insert(0, str(TRADER_DIR))

import indicators

def run_sensitivity():
    stocks = ["HDFCBANK", "ICICIBANK", "RELIANCE", "SBIN", "TCS", "INFY"]
    dfs = {}

    for s in stocks:
        p = HIST_DIR / f"{s}_4days.csv"
        if p.exists():
            df = pd.read_csv(p)
            df['date'] = df['timestamp'].apply(lambda x: str(x).split('T')[0] if 'T' in str(x) else str(x).split(' ')[0])
            closes = df['close'].tolist()
            highs = df['high'].tolist()
            lows = df['low'].tolist()
            vols = df['volume'].tolist()

            df['ema5'] = indicators.calculate_ema(closes, 5)
            df['ema9'] = indicators.calculate_ema(closes, 9)
            df['ema33'] = indicators.calculate_ema(closes, 33)
            st = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
            df['st_dir'] = st['direction'] if st else [0]*len(df)
            df['atr14'] = indicators.calculate_atr(highs, lows, closes, 14)
            adx = indicators.calculate_adx(highs, lows, closes, 14)
            df['adx'] = adx['adx'] if adx else [20.0]*len(df)

            # Cumulative daily VWAP
            vwaps = []
            for d in df['date'].unique():
                day_mask = df['date'] == d
                day_df = df[day_mask]
                cum_vol = day_df['volume'].cumsum()
                cum_vp = (day_df['close'] * day_df['volume']).cumsum()
                vwap_day = cum_vp / cum_vol
                vwaps.extend(vwap_day.tolist())
            df['vwap'] = vwaps
            dfs[s] = df

    print(f"Precomputed indicators for {len(dfs)} assets.")

    def simulate(mode):
        trades = []
        for sym, df in dfs.items():
            for day in df['date'].unique():
                day_df = df[df['date'] == day].reset_index(drop=True)
                if len(day_df) < 35:
                    continue

                pos = None
                for i in range(33, len(day_df)):
                    row = day_df.iloc[i]
                    ts = str(row['timestamp'])
                    t_str = ts.split('T')[1][:5] if 'T' in ts else ts.split(' ')[1][:5]
                    c = row['close']
                    h = row['high']
                    l = row['low']

                    if pos:
                        side = pos['side']
                        sl = pos['sl']
                        tp = pos['tp']
                        xp = None
                        rsn = None

                        if t_str >= "15:20":
                            xp = c
                            rsn = "EOD"
                        elif side == "LONG":
                            if l <= sl:
                                xp = sl
                                rsn = "SL"
                            elif h >= tp:
                                xp = tp
                                rsn = "TP"
                            elif mode == "1m_v3_baseline" and row['st_dir'] == -1:
                                xp = c
                                rsn = "ST_REV"
                        elif side == "SHORT":
                            if h >= sl:
                                xp = sl
                                rsn = "SL"
                            elif l <= tp:
                                xp = tp
                                rsn = "TP"
                            elif mode == "1m_v3_baseline" and row['st_dir'] == 1:
                                xp = c
                                rsn = "ST_REV"

                        if xp is not None:
                            pnl_pts = (xp - pos['entry']) if side == "LONG" else (pos['entry'] - xp)
                            gp = pnl_pts * 100
                            fee = 40.0 + (abs(gp) * 0.0005)
                            np = gp - fee
                            trades.append({"pnl": np, "gross": gp, "fee": fee, "reason": rsn})
                            pos = None

                    if not pos and "09:30" <= t_str < "15:10":
                        long_c = False
                        short_c = False

                        if mode == "1m_v3_baseline":
                            long_c = (row['st_dir'] == 1 and row['ema5'] > row['ema9'] > row['ema33'] and c > row['ema9'])
                            short_c = (row['st_dir'] == -1 and row['ema5'] < row['ema9'] < row['ema33'] and c < row['ema9'])
                        elif mode == "1m_ema_only":
                            long_c = (row['ema5'] > row['ema9'] > row['ema33'] and c > row['ema9'])
                            short_c = (row['ema5'] < row['ema9'] < row['ema33'] and c < row['ema9'])
                        elif mode == "1m_v3_plus_vwap":
                            long_c = (row['st_dir'] == 1 and row['ema5'] > row['ema9'] > row['ema33'] and c > row['ema9'] and c > row['vwap'])
                            short_c = (row['st_dir'] == -1 and row['ema5'] < row['ema9'] < row['ema33'] and c < row['ema9'] and c < row['vwap'])
                        elif mode == "regime_filtered_adx25":
                            # Trend confirmation ADX >= 25, blocks choppy consolidation
                            long_c = (row['st_dir'] == 1 and row['ema5'] > row['ema9'] > row['ema33'] and c > row['ema9'] and row['adx'] >= 25.0)
                            short_c = (row['st_dir'] == -1 and row['ema5'] < row['ema9'] < row['ema33'] and c < row['ema9'] and row['adx'] >= 25.0)
                        elif mode == "mtf_regime_confluence":
                            # 15m trend proxy (c > vwap, adx >= 22) + EMA alignment
                            long_c = (row['st_dir'] == 1 and row['ema5'] > row['ema9'] > row['ema33'] and c > row['vwap'] and row['adx'] >= 22.0)
                            short_c = (row['st_dir'] == -1 and row['ema5'] < row['ema9'] < row['ema33'] and c < row['vwap'] and row['adx'] >= 22.0)

                        if long_c:
                            pos = {"side": "LONG", "entry": c, "sl": round(c * 0.995, 2), "tp": round(c * 1.015, 2)}
                        elif short_c:
                            pos = {"side": "SHORT", "entry": c, "sl": round(c * 1.005, 2), "tp": round(c * 0.985, 2)}

        tot = len(trades)
        if tot == 0:
            return {"mode": mode, "trades": 0, "win_rate": 0, "net_pnl": 0, "pf": 0}
        w = [t for t in trades if t['pnl'] > 0]
        l = [t for t in trades if t['pnl'] < 0]
        gp = sum(t['gross'] for t in w)
        gl = abs(sum(t['gross'] for t in l))
        np = sum(t['pnl'] for t in trades)
        fees = sum(t['fee'] for t in trades)
        pf = (gp / gl) if gl > 0 else 1.0

        sl_cnt = sum(1 for t in trades if t['reason'] == 'SL')
        tp_cnt = sum(1 for t in trades if t['reason'] == 'TP')
        rev_cnt = sum(1 for t in trades if t['reason'] == 'ST_REV')

        return {
            "mode": mode,
            "trades": tot,
            "wins": len(w),
            "losses": len(l),
            "win_rate": round(len(w) / tot * 100, 1),
            "profit_factor": round(pf, 2),
            "net_pnl": round(np, 2),
            "fees": round(fees, 2),
            "sl_pct": round(sl_cnt / tot * 100, 1),
            "tp_pct": round(tp_cnt / tot * 100, 1),
            "rev_pct": round(rev_cnt / tot * 100, 1)
        }

    modes = [
        "1m_v3_baseline",
        "1m_ema_only",
        "1m_v3_plus_vwap",
        "regime_filtered_adx25",
        "mtf_regime_confluence"
    ]

    for m in modes:
        res = simulate(m)
        print(f"{res['mode']:25s} | Trades: {res['trades']:3d} | WR: {res['win_rate']:4.1f}% | PF: {res['profit_factor']:4.2f} | Net: Rs {res['net_pnl']:9.2f} | Fees: Rs {res['fees']:7.2f} | SL: {res['sl_pct']}% | TP: {res['tp_pct']}% | Rev: {res['rev_pct']}%")

if __name__ == "__main__":
    run_sensitivity()
