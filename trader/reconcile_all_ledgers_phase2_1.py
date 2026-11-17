"""
Reconciles all ledger accounts for Phase 2.1.
Enforces:
1. Stop-loss and target for options expressed in option premium price units.
2. Exact accounting conservation: free_cash = initial_capital + realized_pnl - deployed_cash.
3. deployed_cash matches exact sum of open positions (0.0 if empty).
"""

import json
from pathlib import Path

BASE_DIR = Path("d:/Trade-agent/day-trader-skill")
TRADER_DIR = BASE_DIR / "trader"

def reconcile_ledgers():
    report = []

    # 1. Reconcile ledger.json
    ledger_path = TRADER_DIR / "ledger.json"
    with open(ledger_path, "r", encoding="utf-8") as f:
        ledger = json.load(f)

    for p in ledger.get("open_positions", []):
        sym = p.get("symbol", "")
        entry = float(p.get("entry_price", 0.0))
        sl = float(p.get("sl_price", 0.0))
        tgt = float(p.get("target_price", 0.0))

        # Check if option contract has spot prices mistakenly assigned
        if ("CE" in sym or "PE" in sym) and (sl > 1000.0 or tgt > 1000.0):
            old_sl, old_tgt = sl, tgt
            new_sl = round(entry * 0.95, 2)
            new_tgt = round(entry * 1.15, 2)
            p["sl_price"] = new_sl
            p["target_price"] = new_tgt
            p["current_stop"] = new_sl
            report.append(f"[ledger.json] Corrected {sym} (Entry {entry}): SL {old_sl} -> {new_sl}, Target {old_tgt} -> {new_tgt}")

    init_cap = float(ledger.get("initial_capital", ledger.get("starting_capital", 1000000.0)))
    real_pnl = float(ledger.get("realized_pnl", 0.0))
    calc_dep = sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in ledger.get("open_positions", []))
    ledger["deployed_cash"] = round(calc_dep, 2)
    ledger["free_cash"] = round(init_cap + real_pnl - calc_dep, 2)

    with open(ledger_path, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)
    report.append(f"[ledger.json] Balance reconciled: Initial={init_cap}, Realized={real_pnl}, Deployed={ledger['deployed_cash']}, Free={ledger['free_cash']}")

    # 2. Reconcile ledger_fno.json
    fno_path = TRADER_DIR / "ledger_fno.json"
    with open(fno_path, "r", encoding="utf-8") as f:
        fno = json.load(f)

    fno_init = float(fno.get("initial_capital", 200000.0))
    fno_open = fno.get("open_positions", [])
    fno_dep = sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in fno_open)
    fno_th = fno.get("trade_history", [])
    fno_real_pnl = round(sum(float(t.get("pnl", 0.0)) for t in fno_th), 2)

    old_fno_dep = fno.get("deployed_cash")
    old_fno_free = fno.get("free_cash")
    fno["deployed_cash"] = round(fno_dep, 2)
    fno["realized_pnl"] = fno_real_pnl
    fno["free_cash"] = round(fno_init + fno_real_pnl - fno_dep, 2)

    with open(fno_path, "w", encoding="utf-8") as f:
        json.dump(fno, f, indent=2)
    report.append(f"[ledger_fno.json] Reconciled: Deployed {old_fno_dep} -> {fno['deployed_cash']}, Free {old_fno_free} -> {fno['free_cash']}, Realized={fno_real_pnl}")

    # 3. Reconcile ledger_himanshu.json
    him_path = TRADER_DIR / "ledger_himanshu.json"
    with open(him_path, "r", encoding="utf-8") as f:
        him = json.load(f)

    him_init = float(him.get("initial_capital", 1000000.0))
    him_open = him.get("open_positions", [])
    him_dep = sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in him_open)
    him_th = him.get("trade_history", [])
    him_real_pnl = round(sum(float(t.get("pnl", 0.0)) for t in him_th), 2)

    old_him_dep = him.get("deployed_cash")
    old_him_free = him.get("free_cash")
    him["deployed_cash"] = round(him_dep, 2)
    him["realized_pnl"] = him_real_pnl
    him["free_cash"] = round(him_init + him_real_pnl - him_dep, 2)

    with open(him_path, "w", encoding="utf-8") as f:
        json.dump(him, f, indent=2)
    report.append(f"[ledger_himanshu.json] Reconciled: Deployed {old_him_dep} -> {him['deployed_cash']}, Free {old_him_free} -> {him['free_cash']}, Realized={him_real_pnl}")

    # 4. Reconcile ledger_prateek.json
    pra_path = TRADER_DIR / "ledger_prateek.json"
    with open(pra_path, "r", encoding="utf-8") as f:
        pra = json.load(f)

    pra_init = float(pra.get("initial_capital", 1000000.0))
    pra_open = pra.get("open_positions", [])
    pra_dep = sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in pra_open)
    pra_th = pra.get("trade_history", [])
    pra_real_pnl = round(sum(float(t.get("pnl", 0.0)) for t in pra_th), 2)

    pra["deployed_cash"] = round(pra_dep, 2)
    pra["realized_pnl"] = pra_real_pnl
    pra["free_cash"] = round(pra_init + pra_real_pnl - pra_dep, 2)

    with open(pra_path, "w", encoding="utf-8") as f:
        json.dump(pra, f, indent=2)
    report.append(f"[ledger_prateek.json] Reconciled: Deployed={pra['deployed_cash']}, Free={pra['free_cash']}, Realized={pra_real_pnl}")

    for line in report:
        print(line)

if __name__ == "__main__":
    reconcile_ledgers()

