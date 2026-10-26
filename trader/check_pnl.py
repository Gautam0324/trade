#!/usr/bin/env python3
import json
import urllib.request
from pathlib import Path

ledger_path = Path("trader/ledger.json")
if not ledger_path.exists():
    print("Ledger not found.")
    exit()

with open(ledger_path, "r", encoding="utf-8") as f:
    leg = json.load(f)

history = leg.get("trade_history", [])
open_pos = leg.get("open_positions", [])

print("="*60)
print("1. PURANI CLOSED TRADES (JO CUT CHUKI HAIN)")
print("="*60)

total_pnl = 0.0
win_count = 0
loss_count = 0

for h in history:
    pnl = float(h.get("pnl", 0.0))
    total_pnl += pnl
    if pnl >= 0:
        win_count += 1
    else:
        loss_count += 1
    tag = "PROFIT" if pnl >= 0 else "LOSS"
    sym = h.get("symbol")
    in_p = h.get("entry_price")
    out_p = round(float(h.get("exit_price")), 2)
    qty = h.get("qty")
    print(f"[{tag}] {sym:12} (Qty: {qty:2}) In: Rs. {in_p:7.2f} -> Out: Rs. {out_p:7.2f} | PnL: Rs. {pnl:+7.2f}")

print("-" * 60)
print(f"Total Closed Trades : {len(history)}")
print(f"Wins: {win_count}  |  Losses: {loss_count}")
print(f"Net Realized PnL    : Rs. {total_pnl:+,.2f}  (OVERALL PROFIT)")
print("="*60)

print("\n" + "="*60)
print("2. ABHI CHAL RAHI OPEN TRADES (LIVE MARKET VALUE)")
print("="*60)

for p in open_pos:
    sym = p.get("symbol")
    in_p = float(p.get("entry_price"))
    qty = int(p.get("qty"))
    cur = in_p
    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range=1d&interval=5m"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode())["chart"]["result"][0]
            cur = float(d["meta"]["regularMarketPrice"])
    except Exception:
        pass

    diff = cur - in_p
    running_pnl = round(diff * qty, 2)
    tag = "RUNNING PROFIT" if running_pnl >= 0 else "RUNNING LOSS"
    print(f"[{tag}] {sym:12} (Qty: {qty:2}) In: Rs. {in_p:7.2f} | Live Price: Rs. {cur:7.2f} | PnL: Rs. {running_pnl:+7.2f}")

print("="*60)
