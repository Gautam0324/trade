import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.append(str(BASE_DIR / "trader"))
import ledger

def square_off_all():
    led = ledger.get_ledger()
    open_positions = led.get("open_positions", [])
    print(f"Current open positions count: {len(open_positions)}")
    
    for pos in list(open_positions):
        sym_or_id = pos.get("id") or pos.get("symbol")
        entry_price = float(pos["entry_price"])
        print(f"Closing position: {pos.get('symbol')} ({pos.get('id')}) at Rs {entry_price}")
        success, res = ledger.record_trade_exit(
            symbol_or_id=sym_or_id,
            exit_price=entry_price,
            reason="Manual User Squareoff / Clear All Holdings"
        )
        print(f"Result: {success} -> {res}")

    updated = ledger.get_ledger()
    print("--------------------------------------------------")
    print(f"Remaining Open Positions: {len(updated.get('open_positions', []))}")
    print(f"Free Cash: Rs {updated.get('free_cash'):,.2f}")
    print(f"Deployed Cash: Rs {updated.get('deployed_cash'):,.2f}")
    print(f"Realized P&L: Rs {updated.get('realized_pnl'):,.2f}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    square_off_all()

