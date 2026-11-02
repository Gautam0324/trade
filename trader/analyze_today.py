import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import json
from collections import defaultdict

today = '2026-11-02'

def analyze(name, path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            d = json.load(f)
        trades = [t for t in d.get('trade_history', []) if t.get('date') == today or t.get('exit_date') == today]
        print(f"==================================================")
        print(f"=== {name.upper()} (Trades Today: {len(trades)}) ===")
        print(f"==================================================")
        strat_stats = defaultdict(lambda: {'count': 0, 'wins': 0, 'losses': 0, 'pnl': 0.0, 'gross': 0.0, 'fees': 0.0, 'symbols': set(), 'reasons': defaultdict(int)})
        
        for t in trades:
            strat = t.get('strategy_name') or t.get('strategy') or ('FNO_CONFLUENCE' if 'OPT' in str(t.get('id', '')) else 'UNKNOWN')
            pnl = float(t.get('pnl', 0.0))
            gross = float(t.get('gross_pnl', pnl))
            fees = float(t.get('fees', 0.0))
            reason = t.get('reason') or t.get('exit_reason', 'N/A')
            sym = t.get('symbol', 'N/A')
            
            strat_stats[strat]['count'] += 1
            strat_stats[strat]['pnl'] += pnl
            strat_stats[strat]['gross'] += gross
            strat_stats[strat]['fees'] += fees
            strat_stats[strat]['symbols'].add(sym)
            strat_stats[strat]['reasons'][reason] += 1
            if pnl > 0:
                strat_stats[strat]['wins'] += 1
            else:
                strat_stats[strat]['losses'] += 1

        for s, data in strat_stats.items():
            wr = round(data['wins'] / data['count'] * 100, 1) if data['count'] else 0
            print(f"Strategy: {s}")
            print(f"  Trades: {data['count']} | Wins: {data['wins']} | Losses: {data['losses']} | WinRate: {wr}%")
            print(f"  Net PnL: Rs {round(data['pnl'], 2)} (Gross: Rs {round(data['gross'], 2)}, Fees: Rs {round(data['fees'], 2)})")
            print(f"  Symbols: {', '.join(data['symbols'])}")
            print("  Exit Reasons:")
            for r, c in data['reasons'].items():
                print(f"    - {r}: {c} times")
        
        # Print individual trades
        print("\n  Trade Log:")
        for t in trades:
            sym = t.get('symbol')
            pnl = float(t.get('pnl', 0.0))
            entry_t = t.get('entry_time', '')
            exit_t = t.get('exit_time', '')
            entry_p = t.get('entry_price', '')
            exit_p = t.get('exit_price', '')
            reason = t.get('reason') or t.get('exit_reason', '')
            print(f"    [{entry_t} -> {exit_t}] {sym}: Entry={entry_p}, Exit={exit_p} | PnL=Rs {pnl:.2f} | Reason={reason}")
        print()
    except Exception as e:
        print(f"Error analyzing {name}: {e}")

analyze("Gautam (Cash)", "trader/ledger.json")
analyze("Himanshu (Cash)", "trader/ledger_himanshu.json")
analyze("Prateek (Cash)", "trader/ledger_prateek.json")
analyze("F&O Options Desk", "trader/ledger_fno.json")
