#!/usr/bin/env python3
"""
Non-Destructive Ledger Migration & Accounting Reconciliation Script.
Audits historical records in trader/ledger.json and marks COST_DATA_INCOMPLETE where fees are absent.
Calculates exact discrepancy reports without mutating data unless --dry-run is False.
"""

import sys
import io
import json
import shutil
from pathlib import Path

# UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

TRADER_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TRADER_DIR))

from accounting_engine import AccountingEngine

LEDGER_FILES = [
    {"name": "Gautam (Cash)", "file": TRADER_DIR / "ledger.json", "capital": 1000000.0},
    {"name": "Himanshu (Cash)", "file": TRADER_DIR / "ledger_himanshu.json", "capital": 1000000.0},
    {"name": "Prateek (Cash)", "file": TRADER_DIR / "ledger_prateek.json", "capital": 1000000.0},
    {"name": "F&O Desk", "file": TRADER_DIR / "ledger_fno.json", "capital": 200000.0}
]

def audit_and_reconcile_all(apply_fixes=False):
    engine = AccountingEngine()
    print("=" * 75)
    print("LEDGER AUDIT & ACCOUNTING RECONCILIATION")
    print("=" * 75)

    reports = {}
    for item in LEDGER_FILES:
        path = item["file"]
        name = item["name"]
        capital = item["capital"]

        if not path.exists():
            print(f"[{name}] File not found: {path.name}")
            continue

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        history = data.get("trade_history", [])
        incomplete_count = 0
        canonical_fixed = 0

        for t in history:
            raw_sym = t.get("symbol", "")
            can_sym = engine.canonical_symbol(raw_sym)
            if raw_sym != can_sym:
                canonical_fixed += 1
                if apply_fixes:
                    t["symbol"] = can_sym
                    t["instrument"] = can_sym

            # Check fees and gross_pnl
            if t.get("gross_pnl") is None or t.get("fees") is None:
                incomplete_count += 1
                if "accounting_status" not in t:
                    if apply_fixes:
                        t["accounting_status"] = "COST_DATA_INCOMPLETE"

        recon = engine.reconcile_account(data, capital)
        reports[name] = {
            "total_trades": len(history),
            "incomplete_trades": incomplete_count,
            "canonical_fixed": canonical_fixed,
            "reconciliation": recon
        }

        print(f"\n--- {name} ({path.name}) ---")
        print(f"Total Completed Trades: {len(history)}")
        print(f"Cost Data Incomplete  : {incomplete_count}")
        print(f"Reported Realized P&L : ₹{recon['reported_realized_pnl']:+,.2f}")
        print(f"Calculated Net P&L    : ₹{recon['calculated_realized_pnl']:+,.2f}")
        print(f"P&L Discrepancy       : ₹{recon['pnl_discrepancy']:,.2f}")
        print(f"Reported Free Cash    : ₹{recon['reported_free_cash']:,.2f}")
        print(f"Calculated Free Cash  : ₹{recon['calculated_free_cash']:,.2f}")
        print(f"Reconciled Status     : {'✅ RECONCILED' if recon['is_reconciled'] else '⚠️ DISCREPANCY DETECTED'}")

        if apply_fixes and (canonical_fixed > 0 or incomplete_count > 0):
            # Backup original first
            backup_path = path.with_suffix(".json.bak")
            shutil.copyfile(path, backup_path)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Saved normalized ledger with backup at {backup_path.name}")

    return reports

if __name__ == "__main__":
    apply = "--apply" in sys.argv
    audit_and_reconcile_all(apply_fixes=apply)
