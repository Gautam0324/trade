"""
Square off the 3 stale/penny positions requested by the user:
1. NIFTY26NOV24200CE
2. BANKNIFTY26NOV51500PE
3. IDEA23NOV2613PE

Leaves the 2 active fresh Bull Run trades open:
- BSE23NOV263950CE
- HDFCBANK23NOV26820CE
"""

import json
import datetime
from pathlib import Path

BASE_DIR = Path("d:/Trade-agent/day-trader-skill")
TRADER_DIR = BASE_DIR / "trader"

SYMBOLS_TO_CLOSE = [
    "SBIN26NOV1150CE",
    "BHARTIARTL26NOV2140PE",
    "TATASTEEL26NOV162.5PE"
]

def square_off():
    files = ["ledger.json", "ledger_fno.json", "ledger_himanshu.json", "ledger_prateek.json"]
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    for fname in files:
        fpath = TRADER_DIR / fname
        if not fpath.exists():
            continue

        with open(fpath, "r", encoding="utf-8") as f:
            d = json.load(f)

        open_pos = d.get("open_positions", [])
        th = d.get("trade_history", [])
        kept_open = []
        closed_now = []

        for p in open_pos:
            sym = p.get("symbol", "")
            if sym in SYMBOLS_TO_CLOSE:
                entry_p = float(p.get("entry_price", 0.0))
                exit_p = float(p.get("current_price", entry_p) or entry_p)
                qty = float(p.get("qty", p.get("quantity", 0.0)))
                gross = round((exit_p - entry_p) * qty, 2)
                fees = round(float(p.get("fees", 20.0)), 2)
                net_pnl = round(gross - fees, 2)

                closed_record = dict(p)
                closed_record["exit_price"] = exit_p
                closed_record["exit_time"] = now_str
                closed_record["exit_date"] = today_str
                closed_record["date"] = p.get("entry_date", today_str)
                closed_record["gross_pnl"] = gross
                closed_record["fees"] = fees
                closed_record["pnl"] = net_pnl
                closed_record["status"] = "CLOSED"
                closed_record["reason"] = "EOD Mandatory Square-Off (Overnight Session Reconciled)"

                th.append(closed_record)
                closed_now.append(sym)
            else:
                kept_open.append(p)

        # Recalculate accounting
        init_cap = float(d.get("initial_capital", d.get("starting_capital", 1000000.0)))
        total_realized_pnl = round(sum(float(t.get("pnl", 0.0)) for t in th), 2)
        calc_dep = round(sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in kept_open), 2)
        calc_free = round(init_cap + total_realized_pnl - calc_dep, 2)

        d["open_positions"] = kept_open
        d["trade_history"] = th
        d["deployed_cash"] = calc_dep
        d["realized_pnl"] = total_realized_pnl
        d["free_cash"] = calc_free
        d["last_updated"] = now_str

        with open(fpath, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2)

        print(f"[{fname}] Closed {len(closed_now)} positions: {closed_now}")
        print(f"   Kept open {len(kept_open)}: {[p.get('symbol') for p in kept_open]}")
        print(f"   Accounting: Deployed=Rs {calc_dep}, Free=Rs {calc_free}, Realized PnL=Rs {total_realized_pnl}")

if __name__ == "__main__":
    square_off()

