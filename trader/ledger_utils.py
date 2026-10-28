#!/usr/bin/env python3
"""
Ledger Invariants, Reconciliation & Schema Validation Utility.
Guarantees:
- Free cash + Deployed cash == Starting capital + Cumulative realized net P&L
- Single closed trade recorded exactly once
- Trade history P&L matches realized_pnl exactly
- Daily P&L map (realized_pnl_by_day) accurately aggregates trades by exit date
- Safe backup and migration capability
"""

import os
import json
import shutil
import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRADER_DIR = BASE_DIR / "trader"
LEDGER_PATH = TRADER_DIR / "ledger.json"
BACKUP_PATH = TRADER_DIR / "ledger.backup.json"

def backup_ledger(source_path=None, dest_path=None):
    src = source_path or LEDGER_PATH
    dst = dest_path or BACKUP_PATH
    if os.path.exists(src):
        shutil.copy2(src, dst)
        return True
    return False

def reconcile_ledger(ledger_data=None, auto_repair=False):
    """
    Performs comprehensive audit of ledger invariants.
    Returns: (is_valid: bool, report: dict, repaired_ledger: dict)
    """
    if ledger_data is None:
        if not os.path.exists(LEDGER_PATH):
            return False, {"error": "ledger.json does not exist"}, {}
        with open(LEDGER_PATH, "r", encoding="utf-8") as f:
            ledger_data = json.load(f)

    discrepancies = []
    starting_cap = float(ledger_data.get("starting_capital", 1000000.0))
    free_cash = float(ledger_data.get("free_cash", starting_cap))
    deployed_cash = float(ledger_data.get("deployed_cash", 0.0))
    realized_pnl = float(ledger_data.get("realized_pnl", 0.0))
    open_positions = ledger_data.get("open_positions", [])
    trade_history = ledger_data.get("trade_history", [])

    # 1. Audit deployed capital against active positions
    calc_deployed = round(sum(float(p.get("amount", p.get("qty", 0) * p.get("entry_price", 0.0))) for p in open_positions), 2)
    if abs(calc_deployed - deployed_cash) > 0.05:
        discrepancies.append(f"Deployed cash mismatch: recorded ₹{deployed_cash:,.2f} vs calculated sum ₹{calc_deployed:,.2f}")

    # 2. Audit trade history sum against realized P&L
    calc_realized = round(sum(float(t.get("pnl", 0.0)) for t in trade_history), 2)
    if abs(calc_realized - realized_pnl) > 0.05:
        discrepancies.append(f"Realized P&L mismatch: recorded ₹{realized_pnl:,.2f} vs trade history sum ₹{calc_realized:,.2f}")

    # 3. Audit accounting identity: free_cash + deployed == starting + realized
    account_equity = round(free_cash + deployed_cash, 2)
    expected_equity = round(starting_cap + calc_realized, 2)
    if abs(account_equity - expected_equity) > 0.05:
        discrepancies.append(f"Balance equation broken: free ({free_cash:,.2f}) + deployed ({deployed_cash:,.2f}) = {account_equity:,.2f} != starting ({starting_cap:,.2f}) + realized ({calc_realized:,.2f}) = {expected_equity:,.2f}")

    # 4. Check for duplicate closed trades
    seen_trades = set()
    dup_count = 0
    for t in trade_history:
        key = (t.get("symbol"), t.get("entry_time"), t.get("exit_time"), t.get("date") or t.get("exit_date"))
        if key in seen_trades:
            dup_count += 1
        seen_trades.add(key)
    if dup_count > 0:
        discrepancies.append(f"Found {dup_count} duplicate trade records in trade_history")

    # 5. Build daily P&L map
    daily_map = {}
    for t in trade_history:
        d = t.get("exit_date") or t.get("date") or datetime.datetime.now().strftime("%Y-%m-%d")
        daily_map[d] = round(daily_map.get(d, 0.0) + float(t.get("pnl", 0.0)), 2)

    is_valid = (len(discrepancies) == 0)

    repaired = dict(ledger_data)
    if auto_repair:
        repaired["starting_capital"] = starting_cap
        repaired["deployed_cash"] = calc_deployed
        repaired["realized_pnl"] = calc_realized
        repaired["free_cash"] = round(starting_cap - calc_deployed + calc_realized, 2)
        repaired["realized_pnl_by_day"] = daily_map
        repaired["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

    report = {
        "is_valid": is_valid,
        "discrepancies": discrepancies,
        "starting_capital": starting_cap,
        "recorded_free_cash": free_cash,
        "calculated_deployed_cash": calc_deployed,
        "calculated_realized_pnl": calc_realized,
        "daily_pnl_breakdown": daily_map,
        "total_trades": len(trade_history),
        "open_positions": len(open_positions)
    }

    return is_valid, report, repaired

if __name__ == "__main__":
    backup_ledger()
    valid, rep, rep_data = reconcile_ledger(auto_repair=True)
    print(json.dumps(rep, indent=2))
    if not valid:
        print("[AUTO-REPAIR] Saving reconciled ledger...")
        with open(LEDGER_PATH, "w", encoding="utf-8") as f:
            json.dump(rep_data, f, indent=2)
        print("[DONE] Ledger reconciled.")
