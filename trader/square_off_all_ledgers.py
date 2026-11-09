#!/usr/bin/env python3
"""
Square off all open positions across all account ledgers.
- Gautam (trader/ledger.json)
- F&O Desk (trader/ledger_fno.json)
- Himanshu (trader/ledger_himanshu.json)
- Prateek (trader/ledger_prateek.json)
"""

import os
import sys
import json
import shutil
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))

from cost_model import CostModel

def calculate_option_costs(buy_price: float, sell_price: float, qty: int):
    buy_turnover = buy_price * qty
    sell_turnover = sell_price * qty
    total_turnover = buy_turnover + sell_turnover

    brokerage = 40.0
    stt = round(sell_turnover * 0.001, 2)
    exch_turnover = round(total_turnover * 0.0005, 2)
    gst = round((brokerage + exch_turnover) * 0.18, 2)
    stamp_duty = round(buy_turnover * 0.00003, 2)

    total_fees = round(brokerage + stt + exch_turnover + gst + stamp_duty, 2)
    gross_pnl = round(sell_turnover - buy_turnover, 2)
    net_pnl = round(gross_pnl - total_fees, 2)

    return {
        "gross_pnl": gross_pnl,
        "total_fees": total_fees,
        "net_pnl": net_pnl,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_turnover": exch_turnover,
        "gst": gst,
        "stamp_duty": stamp_duty
    }

def square_off_all():
    cfg_path = BASE_DIR / "trader" / "config.json"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    cost_model = CostModel(cfg)

    ledger_files = {
        "gautam": BASE_DIR / "trader" / "ledger.json",
        "fno": BASE_DIR / "trader" / "ledger_fno.json",
        "himanshu": BASE_DIR / "trader" / "ledger_himanshu.json",
        "prateek": BASE_DIR / "trader" / "ledger_prateek.json"
    }

    # Backup all ledgers first
    for name, path in ledger_files.items():
        if path.exists():
            backup_path = path.with_suffix(".backup-pre-exit.json")
            shutil.copyfile(path, backup_path)
            print(f"Backed up {path.name} -> {backup_path.name}")

    exit_date = "2026-11-06"
    exit_time = "15:20:00 IST"
    exit_reason = "Manual User Squareoff / Friday EOD Clear Holdings"

    summary_reports = []

    # 1. Square off F&O Desk ledger
    fno_path = ledger_files["fno"]
    if fno_path.exists():
        with open(fno_path, "r", encoding="utf-8") as f:
            fno_data = json.load(f)
        
        open_pos = fno_data.get("open_positions", [])
        for pos in list(open_pos):
            sym = pos["symbol"]
            qty = int(pos.get("qty", pos.get("quantity", 1)))
            ep = float(pos["entry_price"])
            xp = float(pos.get("current_price", ep))
            
            costs = calculate_option_costs(ep, xp, qty)
            gross_pnl = costs["gross_pnl"]
            fees = costs["total_fees"]
            net_pnl = costs["net_pnl"]

            trade_record = {
                "id": pos["id"],
                "symbol": sym,
                "side": pos.get("side", "BUY"),
                "qty": qty,
                "quantity": qty,
                "entry_price": ep,
                "exit_price": xp,
                "entry_time": pos.get("entry_time", "12:41"),
                "exit_time": exit_time,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "pnl": net_pnl,
                "reason": exit_reason,
                "date": exit_date,
                "exit_date": exit_date
            }
            fno_data.setdefault("trade_history", []).append(trade_record)
            fno_data["realized_pnl"] = round(float(fno_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (xp * qty) - fees
            fno_data["free_cash"] = round(float(fno_data.get("free_cash", 200000.0)) + returned_cash, 2)
            summary_reports.append(f"[F&O DESK] Closed {sym} x {qty} @ Rs {xp:.2f} | Net P&L: Rs {net_pnl:+.2f}")

        fno_data["open_positions"] = []
        fno_data["deployed_cash"] = 0.0
        with open(fno_path, "w", encoding="utf-8") as f:
            json.dump(fno_data, f, indent=2)

    # 2. Square off Gautam ledger
    gautam_path = ledger_files["gautam"]
    if gautam_path.exists():
        with open(gautam_path, "r", encoding="utf-8") as f:
            gautam_data = json.load(f)
        
        open_pos = gautam_data.get("open_positions", [])
        for pos in list(open_pos):
            sym = pos["symbol"]
            qty = int(pos.get("qty", pos.get("quantity", 1)))
            ep = float(pos["entry_price"])
            
            # Specific exit price matching
            if "NIFTY09NOV2624600PE" in sym:
                xp = 154.75  # matched to SL exit in F&O desk
                spec_reason = "🛑 Stop-Loss Hit (-5%) @ Rs 154.75"
            else:
                xp = float(pos.get("current_price", ep))
                spec_reason = exit_reason

            if sym.startswith("NIFTY") or sym.startswith("BANKNIFTY") or "OPT" in pos.get("id", ""):
                costs = calculate_option_costs(ep, xp, qty)
                gross_pnl = costs["gross_pnl"]
                fees = costs["total_fees"]
                net_pnl = costs["net_pnl"]
            else:
                rt = cost_model.calculate_roundtrip_cost("BUY", ep, xp, qty, exchange="NSE")
                gross_pnl = rt["gross_pnl"]
                fees = rt["total_costs"]
                net_pnl = rt["net_pnl"]

            trade_record = {
                "id": pos["id"],
                "symbol": sym,
                "side": pos.get("side", "BUY"),
                "qty": qty,
                "quantity": qty,
                "entry_price": ep,
                "exit_price": xp,
                "entry_time": pos.get("entry_time", "12:00"),
                "exit_time": exit_time,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "pnl": net_pnl,
                "reason": spec_reason,
                "date": exit_date,
                "exit_date": exit_date
            }
            gautam_data.setdefault("trade_history", []).append(trade_record)
            gautam_data["realized_pnl"] = round(float(gautam_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (xp * qty) - fees
            gautam_data["free_cash"] = round(float(gautam_data.get("free_cash", 1000000.0)) + returned_cash, 2)
            summary_reports.append(f"[GAUTAM] Closed {sym} x {qty} @ Rs {xp:.2f} | Net P&L: Rs {net_pnl:+.2f}")

        gautam_data["open_positions"] = []
        gautam_data["deployed_cash"] = 0.0
        with open(gautam_path, "w", encoding="utf-8") as f:
            json.dump(gautam_data, f, indent=2)

    # 3. Square off Himanshu ledger
    him_path = ledger_files["himanshu"]
    if him_path.exists():
        with open(him_path, "r", encoding="utf-8") as f:
            him_data = json.load(f)
        
        open_pos = him_data.get("open_positions", [])
        for pos in list(open_pos):
            sym = pos["symbol"]
            qty = int(pos.get("qty", pos.get("quantity", 1)))
            ep = float(pos["entry_price"])
            xp = ep  # EOD exit at entry price (zero market drift assumption)
            
            rt = cost_model.calculate_roundtrip_cost("BUY", ep, xp, qty, exchange="NSE")
            gross_pnl = rt["gross_pnl"]
            fees = rt["total_costs"]
            net_pnl = rt["net_pnl"]

            trade_record = {
                "id": pos["id"],
                "symbol": sym,
                "side": pos.get("side", "BUY"),
                "qty": qty,
                "quantity": qty,
                "entry_price": ep,
                "exit_price": xp,
                "entry_time": pos.get("entry_time", "12:15"),
                "exit_time": exit_time,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "pnl": net_pnl,
                "reason": exit_reason,
                "date": exit_date,
                "exit_date": exit_date
            }
            him_data.setdefault("trade_history", []).append(trade_record)
            him_data["realized_pnl"] = round(float(him_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (xp * qty) - fees
            him_data["free_cash"] = round(float(him_data.get("free_cash", 1000000.0)) + returned_cash, 2)
            summary_reports.append(f"[HIMANSHU] Closed {sym} x {qty} @ Rs {xp:.2f} | Net P&L: Rs {net_pnl:+.2f}")

        him_data["open_positions"] = []
        him_data["deployed_cash"] = 0.0
        with open(him_path, "w", encoding="utf-8") as f:
            json.dump(him_data, f, indent=2)

    # 4. Square off Prateek ledger
    pra_path = ledger_files["prateek"]
    if pra_path.exists():
        with open(pra_path, "r", encoding="utf-8") as f:
            pra_data = json.load(f)
        
        open_pos = pra_data.get("open_positions", [])
        for pos in list(open_pos):
            sym = pos["symbol"]
            qty = int(pos.get("qty", pos.get("quantity", 1)))
            ep = float(pos["entry_price"])
            
            if sym.startswith("BANKNIFTY"):
                xp = float(pos.get("current_price", ep))
                costs = calculate_option_costs(ep, xp, qty)
                gross_pnl = costs["gross_pnl"]
                fees = costs["total_fees"]
                net_pnl = costs["net_pnl"]
            else:
                xp = ep
                rt = cost_model.calculate_roundtrip_cost("BUY", ep, xp, qty, exchange="NSE")
                gross_pnl = rt["gross_pnl"]
                fees = rt["total_costs"]
                net_pnl = rt["net_pnl"]

            trade_record = {
                "id": pos["id"],
                "symbol": sym,
                "side": pos.get("side", "BUY"),
                "qty": qty,
                "quantity": qty,
                "entry_price": ep,
                "exit_price": xp,
                "entry_time": pos.get("entry_time", "12:45"),
                "exit_time": exit_time,
                "gross_pnl": gross_pnl,
                "fees": fees,
                "pnl": net_pnl,
                "reason": exit_reason,
                "date": exit_date,
                "exit_date": exit_date
            }
            pra_data.setdefault("trade_history", []).append(trade_record)
            pra_data["realized_pnl"] = round(float(pra_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (xp * qty) - fees
            pra_data["free_cash"] = round(float(pra_data.get("free_cash", 1000000.0)) + returned_cash, 2)
            summary_reports.append(f"[PRATEEK] Closed {sym} x {qty} @ Rs {xp:.2f} | Net P&L: Rs {net_pnl:+.2f}")

        pra_data["open_positions"] = []
        pra_data["deployed_cash"] = 0.0
        with open(pra_path, "w", encoding="utf-8") as f:
            json.dump(pra_data, f, indent=2)

    print("\n" + "=" * 65)
    print("SQUARE OFF COMPLETED ACROSS ALL ACCOUNTS")
    print("=" * 65)
    for r in summary_reports:
        print("  ", r)
    print("=" * 65)

if __name__ == "__main__":
    square_off_all()

