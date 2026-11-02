"""
Multi-Account Manager for Day Trader Pro V2.
Manages isolated paper portfolios, ledgers, daily risk states, and targeted Telegram notifications
for Gautam, Himanshu, and Prateek.
"""

import os
import json
import time
from pathlib import Path
from typing import Dict, Any, List, Optional

import telegram_bot
from cost_model import CostModel
from order_manager import OrderManager, OrderState

BASE_DIR = Path(__file__).resolve().parent.parent
ACCOUNTS_PATH = BASE_DIR / "trader" / "accounts.json"
CONFIG_PATH = BASE_DIR / "trader" / "config.json"

class AccountPortfolio:
    def __init__(self, account_id: str, info: Dict[str, Any]):
        self.account_id = account_id
        self.name = info.get("name", account_id.capitalize())
        self.capital = float(info.get("capital", 1000000.0))
        self.telegram_chat_id = info.get("telegram_chat_id")
        self.watchlist = [s.upper() for s in info.get("watchlist", [])]
        self.ledger_file = BASE_DIR / "trader" / info.get("ledger_file", f"ledger_{account_id}.json")
        self.risk_state_file = BASE_DIR / "trader" / info.get("risk_state_file", f"daily_risk_{account_id}.json")
        self.order_manager = OrderManager()
        self.open_positions: Dict[str, Dict[str, Any]] = {}
        self.load_ledger()

    def load_ledger(self):
        if self.ledger_file.exists():
            try:
                with open(self.ledger_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for p in data.get("open_positions", []):
                        self.open_positions[p["symbol"]] = dict(p)
            except Exception as e:
                print(f"[ACCOUNT {self.name}] Ledger load error: {e}")

    def save_ledger(self):
        try:
            current_data = {}
            if self.ledger_file.exists():
                with open(self.ledger_file, "r", encoding="utf-8") as f:
                    current_data = json.load(f)
            current_data["open_positions"] = list(self.open_positions.values())
            with open(self.ledger_file, "w", encoding="utf-8") as f:
                json.dump(current_data, f, indent=2)
        except Exception as e:
            print(f"[ACCOUNT {self.name}] Ledger save error: {e}")

    def send_telegram(self, text: str):
        if self.telegram_chat_id:
            try:
                telegram_bot.send_telegram_message(text, chat_id=self.telegram_chat_id)
            except Exception as e:
                print(f"[TELEGRAM ERROR {self.name}] {e}")


class MultiAccountManager:
    def __init__(self, cost_model: Optional[CostModel] = None):
        self.cost_model = cost_model or CostModel()
        self.accounts: Dict[str, AccountPortfolio] = {}
        self.load_accounts()

    def load_accounts(self):
        if not ACCOUNTS_PATH.exists():
            return
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            for acc_id, info in data.get("accounts", {}).items():
                self.accounts[acc_id] = AccountPortfolio(acc_id, info)
        except Exception as e:
            print(f"[MULTI-ACCOUNT INIT ERROR] {e}")

    def get_account(self, acc_id: str) -> Optional[AccountPortfolio]:
        self.load_accounts()
        return self.accounts.get(acc_id)

    def get_all_accounts(self) -> List[AccountPortfolio]:
        self.load_accounts()
        return list(self.accounts.values())

    def execute_entry_for_all(
        self,
        symbol: str,
        side: str,
        price: float,
        stop_loss: float,
        target_price: float,
        strategy_name: str,
        base_qty: int = 100
    ) -> List[Dict[str, Any]]:
        self.load_accounts()
        results = []

        for acc_id, acc in self.accounts.items():
            if acc_id == "gautam":
                continue  # Handled directly by primary live trader engine

            # Dedicated Watchlist Filter: Only trade symbols configured for this person!
            if acc.watchlist and symbol.upper() not in acc.watchlist:
                continue

            if symbol in acc.open_positions:
                continue

            order_id = f"ORD-{acc_id[:3].upper()}-{int(time.time() * 1000)}-{symbol}"
            pos_id = f"POS-{acc_id[:3].upper()}-{int(time.time() * 1000)}-{symbol}"

            pos_record = {
                "id": pos_id,
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": base_qty,
                "qty": base_qty,
                "entry_price": price,
                "sl_price": stop_loss,
                "target_price": target_price,
                "strategy_name": strategy_name,
                "entry_time": time.strftime("%H:%M"),
                "status": "OPEN"
            }

            acc.open_positions[symbol] = pos_record
            acc.save_ledger()

            # Send targeted Telegram message ONLY to this user
            msg = (
                f"🚀 <b>ENTRY EXECUTED [{acc.name}]</b>\n\n"
                f"📈 <b>Stock:</b> {symbol} ({side})\n"
                f"💰 <b>Price:</b> ₹{price:.2f}\n"
                f"📦 <b>Qty:</b> {base_qty}\n"
                f"🎯 <b>Target:</b> ₹{target_price:.2f}\n"
                f"🛑 <b>Stop Loss:</b> ₹{stop_loss:.2f}\n"
                f"🧠 <b>Strategy:</b> {strategy_name}"
            )
            acc.send_telegram(msg)
            results.append({"account": acc.name, "pos": pos_record})

        return results

    def execute_exit_for_all(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str,
        exchange: str = "NSE"
    ) -> List[Dict[str, Any]]:
        self.load_accounts()
        results = []

        for acc_id, acc in self.accounts.items():
            if acc_id == "gautam":
                continue  # Handled directly by primary live trader engine

            pos = acc.open_positions.pop(symbol, None)
            if not pos:
                continue

            side = pos["side"]
            qty = pos["quantity"]
            entry_price = pos["entry_price"]

            roundtrip = self.cost_model.calculate_roundtrip_cost(
                entry_side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=qty,
                exchange=exchange
            )

            gross_pnl = roundtrip["gross_pnl"]
            fees = roundtrip["total_costs"]
            net_pnl = roundtrip["net_pnl"]

            trade_record = {
                "id": pos["id"],
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "quantity": qty,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "entry_time": pos["entry_time"],
                "exit_time": time.strftime("%H:%M:%S IST"),
                "gross_pnl": round(gross_pnl, 2),
                "fees": round(fees, 2),
                "pnl": round(net_pnl, 2),
                "reason": exit_reason,
                "date": time.strftime("%Y-%m-%d"),
                "exit_date": time.strftime("%Y-%m-%d")
            }

            # Update account ledger trade history
            try:
                led_data = {}
                if acc.ledger_file.exists():
                    with open(acc.ledger_file, "r", encoding="utf-8") as lf:
                        led_data = json.load(lf)
                th = led_data.setdefault("trade_history", [])
                th.append(trade_record)
                led_data["open_positions"] = list(acc.open_positions.values())
                led_data["realized_pnl"] = round(float(led_data.get("realized_pnl", 0.0)) + net_pnl, 2)
                led_data["free_cash"] = round(float(led_data.get("free_cash", acc.capital)) + net_pnl, 2)

                with open(acc.ledger_file, "w", encoding="utf-8") as lf:
                    json.dump(led_data, lf, indent=2)
            except Exception as le:
                print(f"[ACCOUNT {acc.name}] Trade history record error: {le}")

            pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
            exit_msg = (
                f"{pnl_emoji} <b>EXIT EXECUTED [{acc.name}]</b>\n\n"
                f"📉 <b>Stock:</b> {symbol} ({side})\n"
                f"🏁 <b>Exit Price:</b> ₹{exit_price:.2f}\n"
                f"💡 <b>Reason:</b> {exit_reason}\n"
                f"💵 <b>Net P&L:</b> ₹{net_pnl:+,.2f}\n"
                f"📊 <b>Realized Today:</b> ₹{led_data.get('realized_pnl', 0.0):+,.2f}"
            )
            acc.send_telegram(exit_msg)
            results.append({"account": acc.name, "trade": trade_record})

        return results

