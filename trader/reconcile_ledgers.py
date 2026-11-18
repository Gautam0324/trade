import json
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
from ledger_transaction import locked_ledger_transaction

def reconcile_account(fname):
    lpath = BASE_DIR / "trader" / fname
    def _reconcile(data):
        init = float(data.get("initial_capital", data.get("starting_capital", 1000000.0)))
        pnl = float(data.get("realized_pnl", 0.0))
        open_pos = data.get("open_positions", [])
        calc_dep = round(sum(float(p.get("entry_price", 0.0)) * float(p.get("qty", p.get("quantity", 0.0))) for p in open_pos), 2)
        total_equity = round(init + pnl, 2)
        data["deployed_cash"] = calc_dep
        data["free_cash"] = round(total_equity - calc_dep, 2)
        print(f"Reconciled {fname}: Init={init}, PnL={pnl}, Deployed={calc_dep}, FreeCash={data['free_cash']}")
        return data
    locked_ledger_transaction(lpath, _reconcile)

if __name__ == "__main__":
    reconcile_account("ledger_himanshu.json")
    reconcile_account("ledger_prateek.json")
