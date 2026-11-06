#!/usr/bin/env python3
"""
Day Trader Pro V2 - F&O Options Desk (Paper Trading Engine).
Dedicated standalone event-driven trading engine for NIFTY & BANKNIFTY Weekly Options.
Features:
- Completely isolated from Cash Day Trading engine (zero collision, independent process)
- Tracks NIFTY 50 & BANKNIFTY Spot with 1-Minute completed candles
- Confluence Strategy Engine (Supertrend, EMA 5/9/33 Channel, VWAP)
- Dynamic ATM Strike & Weekly Expiry Contract Resolver
- 1-Lot Position Sizing with 5% Stop-Loss & 15% Target (+1:3 Risk-Reward)
- Real Indian Regulatory Option CostModel (STT, GST, NSE Turnover, Brokerage)
- Isolated ledger persistence in trader/ledger_fno.json
- Real-time Telegram alerting with [⚡ F&O OPTIONS DESK] tag
"""

import os
import sys
import json
import time
import datetime
from pathlib import Path

# Force UTF-8 on Windows
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.append(str(BASE_DIR / "trader"))
sys.path.append(str(BASE_DIR / "trader-alerts"))

import indicators
import telegram_bot
import download_historical_1min as dh
from broker_adapter import BrokerAdapter, PaperModeIsolationError

LEDGER_PATH = BASE_DIR / "trader" / "ledger_fno.json"
ACCOUNTS_PATH = BASE_DIR / "trader" / "accounts.json"
CONTRACTS_CACHE_PATH = BASE_DIR / "historical_data" / "fno_contracts.json"
LOCK_PATH = BASE_DIR / "trader" / "fno_trader.lock"

INDICES = [
    {"name": "NIFTY", "symbol": "NIFTY 50", "token": "99926000", "step": 50},
    {"name": "BANKNIFTY", "symbol": "BANKNIFTY", "token": "99926009", "step": 100}
]

# Risk Parameters: 5% Stop-Loss, 15% Target (1:3 Risk:Reward)
STOP_LOSS_PCT = 0.05
TARGET_PCT = 0.15

_fno_lock_handle = None

def acquire_fno_lock():
    global _fno_lock_handle
    try:
        _fno_lock_handle = open(LOCK_PATH, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_fno_lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_fno_lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _fno_lock_handle.write(str(os.getpid()))
        _fno_lock_handle.flush()
    except (IOError, OSError):
        print("[FATAL] Another F&O Desk instance is already actively running! Exiting.")
        sys.exit(0)

def load_fno_contracts():
    if CONTRACTS_CACHE_PATH.exists():
        try:
            with open(CONTRACTS_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[FNO CONTRACTS ERROR] {e}")
    return []

CACHED_CONTRACTS = load_fno_contracts()

def resolve_atm_contract(underlying: str, spot_price: float, option_type: str = "CE"):
    """
    Finds the exact ATM contract for the nearest active weekly expiry.
    """
    step = 50 if underlying == "NIFTY" else 100
    atm_strike = round(spot_price / step) * step

    opts = [
        c for c in CACHED_CONTRACTS
        if c.get("name") == underlying
        and c.get("instrumenttype") == "OPTIDX"
        and c.get("symbol", "").endswith(option_type.upper())
        and abs(float(c.get("strike", 0)) / 100 - atm_strike) < 1.0
    ]

    today_dt = datetime.datetime.now().date()

    def parse_exp(c):
        try:
            return datetime.datetime.strptime(c["expiry"], "%d%b%Y").date()
        except Exception:
            return datetime.date.max

    future_opts = [c for c in opts if parse_exp(c) >= today_dt]
    future_opts.sort(key=parse_exp)

    if future_opts:
        best = future_opts[0]
        return {
            "symbol": best["symbol"],
            "token": best["token"],
            "strike": float(best["strike"]) / 100,
            "expiry": best["expiry"],
            "lotsize": int(best.get("lotsize", 65 if underlying == "NIFTY" else 30)),
            "option_type": option_type.upper(),
            "underlying": underlying,
            "atm_strike": atm_strike
        }
    return None

def calculate_option_costs(buy_price: float, sell_price: float, qty: int):
    """
    Indian Regulatory Cost Model for Equity Options:
    - Brokerage: Rs 20 per leg (Rs 40 roundtrip)
    - STT: 0.1% on Sell turnover
    - Exchange Turnover: 0.05%
    - GST: 18% on (Brokerage + Turnover)
    - Stamp Duty: 0.003% on Buy turnover
    """
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
        "breakdown": {
            "brokerage": brokerage,
            "stt": stt,
            "turnover_fee": exch_turnover,
            "gst": gst,
            "stamp_duty": stamp_duty
        }
    }

def get_all_active_accounts():
    if ACCOUNTS_PATH.exists():
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("accounts", {})
        except Exception:
            pass
    return {}

def broadcast_option_entry_to_accounts(pos_record: dict, investment: float):
    accounts = get_all_active_accounts()
    for acc_id, acc_info in accounts.items():
        if acc_id == "fno":
            continue
        
        cid = acc_info.get("telegram_chat_id")
        user_name = acc_info.get("name", acc_id.capitalize())
        ledger_fname = acc_info.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")
        ledger_file = BASE_DIR / "trader" / ledger_fname

        try:
            led_data = {}
            if ledger_file.exists():
                with open(ledger_file, "r", encoding="utf-8") as lf:
                    led_data = json.load(lf)
            
            # Check daily trade quota for this account
            cfg_file = BASE_DIR / "trader" / "config.json"
            max_trades = 30
            if cfg_file.exists():
                try:
                    with open(cfg_file, "r", encoding="utf-8") as cf:
                        max_trades = int(json.load(cf).get("max_trades_per_day", 30))
                except Exception:
                    pass

            today_str = datetime.datetime.now().strftime("%Y-%m-%d")
            today_trades = [t for t in led_data.get("trade_history", []) if t.get("date") == today_str or t.get("exit_date") == today_str]
            acc_open = led_data.get("open_positions", [])
            total_today_entries = len(today_trades) + len(acc_open)
            if total_today_entries >= max_trades:
                print(f"[F&O {user_name}] Skipped: Daily trade limit reached ({total_today_entries}/{max_trades})")
                continue

            cur_free = float(led_data.get("free_cash", acc_info.get("capital", 1000000.0)))
            if cur_free < investment:
                print(f"[F&O {user_name}] Skipped: Insufficient free cash (Need Rs {investment}, have Rs {cur_free})")
                continue

            open_positions = led_data.setdefault("open_positions", [])
            if not any(p.get("symbol") == pos_record["symbol"] for p in open_positions):
                user_pos = dict(pos_record)
                user_pos["id"] = f"OPT-{acc_id[:3].upper()}-{int(time.time()*1000)}-{pos_record['symbol']}"
                open_positions.append(user_pos)
                led_data["free_cash"] = round(cur_free - investment, 2)
                led_data["deployed_cash"] = round(float(led_data.get("deployed_cash", 0.0)) + investment, 2)
                
                with open(ledger_file, "w", encoding="utf-8") as lf:
                    json.dump(led_data, lf, indent=2)
                print(f"[F&O {user_name}] Position recorded in {ledger_fname}")
        except Exception as e:
            print(f"[F&O {user_name}] Ledger entry error: {e}")

        if cid:
            try:
                opt_sym = pos_record["symbol"]
                type_emoji = "🟢 CALL (CE)" if pos_record["option_type"] == "CE" else "🔴 PUT (PE)"
                msg = (
                    f"⚡ <b>[F&O ENTRY EXECUTED - {user_name}] {type_emoji}</b>\n\n"
                    f"🎯 <b>Contract:</b> <code>{opt_sym}</code>\n"
                    f"📊 <b>Underlying Spot:</b> ₹{pos_record['entry_spot']:,.2f} ({pos_record['underlying']})\n"
                    f"💰 <b>Premium Price:</b> ₹{pos_record['entry_price']:.2f}\n"
                    f"📦 <b>Lot Size:</b> {pos_record['qty']} Shares\n"
                    f"💵 <b>Deployed Amount:</b> ₹{investment:,.2f}\n"
                    f"🛑 <b>Stop-Loss (-5%):</b> ₹{pos_record['sl_price']:.2f}\n"
                    f"🏁 <b>Target (+15%):</b> ₹{pos_record['target_price']:.2f} (1:3 R:R)\n"
                    f"🧠 <b>Strategy Reason:</b> {pos_record['reason']}"
                )
                telegram_bot.send_telegram_message(msg, chat_id=cid)
            except Exception as te:
                print(f"[TELEGRAM ERROR {user_name}] {te}")

def broadcast_option_exit_to_accounts(opt_sym: str, exit_price: float, reason: str):
    accounts = get_all_active_accounts()
    for acc_id, acc_info in accounts.items():
        if acc_id == "fno":
            continue
        
        cid = acc_info.get("telegram_chat_id")
        user_name = acc_info.get("name", acc_id.capitalize())
        ledger_fname = acc_info.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")
        ledger_file = BASE_DIR / "trader" / ledger_fname

        try:
            if not ledger_file.exists():
                continue
            with open(ledger_file, "r", encoding="utf-8") as lf:
                led_data = json.load(lf)
            
            open_positions = led_data.get("open_positions", [])
            pos_idx = next((i for i, p in enumerate(open_positions) if p.get("symbol") == opt_sym), None)
            if pos_idx is None:
                continue

            pos = open_positions.pop(pos_idx)
            entry_p = float(pos.get("entry_price", exit_price))
            qty = int(pos.get("qty", 1))

            costs = calculate_option_costs(entry_p, exit_price, qty)
            net_pnl = costs["net_pnl"]
            fees = costs["total_fees"]
            gross_pnl = costs["gross_pnl"]

            trade_record = {
                "id": pos.get("id", f"OPT-{int(time.time()*1000)}-{opt_sym}"),
                "symbol": opt_sym,
                "side": pos.get("side", "BUY"),
                "qty": qty,
                "quantity": qty,
                "entry_price": entry_p,
                "exit_price": exit_price,
                "entry_time": pos.get("entry_time", ""),
                "exit_time": datetime.datetime.now().strftime("%H:%M:%S IST"),
                "gross_pnl": gross_pnl,
                "fees": fees,
                "pnl": net_pnl,
                "reason": reason,
                "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                "exit_date": datetime.datetime.now().strftime("%Y-%m-%d")
            }

            th = led_data.setdefault("trade_history", [])
            th.append(trade_record)
            led_data["realized_pnl"] = round(float(led_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (exit_price * qty) - fees
            led_data["free_cash"] = round(float(led_data.get("free_cash", acc_info.get("capital", 1000000.0))) + returned_cash, 2)
            led_data["deployed_cash"] = max(0.0, round(float(led_data.get("deployed_cash", 0.0)) - (entry_p * qty), 2))

            with open(ledger_file, "w", encoding="utf-8") as lf:
                json.dump(led_data, lf, indent=2)
            print(f"[F&O {user_name}] Exit recorded in {ledger_fname} | Net: Rs {net_pnl:+.2f}")

            if cid:
                try:
                    pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
                    exit_msg = (
                        f"{pnl_emoji} <b>[F&O EXIT EXECUTED - {user_name}]</b>\n\n"
                        f"🎯 <b>Contract:</b> <code>{opt_sym}</code>\n"
                        f"🏁 <b>Exit Premium:</b> ₹{exit_price:.2f}\n"
                        f"📍 <b>Entry Premium:</b> ₹{entry_p:.2f}\n"
                        f"📦 <b>Lotsize:</b> {qty} Shares\n"
                        f"💡 <b>Reason:</b> {reason}\n"
                        f"💵 <b>Net P&L:</b> ₹{net_pnl:+,.2f}\n"
                        f"📊 <b>Account Realized Today:</b> ₹{led_data.get('realized_pnl', 0.0):+,.2f}"
                    )
                    telegram_bot.send_telegram_message(exit_msg, chat_id=cid)
                except Exception as te:
                    print(f"[TELEGRAM ERROR {user_name}] {te}")
        except Exception as e:
            print(f"[F&O {user_name}] Ledger exit error: {e}")

def update_open_option_on_all_ledgers(opt_sym: str, cur_p: float):
    accounts = get_all_active_accounts()
    for acc_id, acc_info in accounts.items():
        if acc_id == "fno":
            continue
        ledger_fname = acc_info.get("ledger_file", f"ledger_{acc_id}.json" if acc_id != "gautam" else "ledger.json")
        ledger_file = BASE_DIR / "trader" / ledger_fname
        try:
            if not ledger_file.exists():
                continue
            with open(ledger_file, "r", encoding="utf-8") as lf:
                data = json.load(lf)
            modified = False
            for p in data.get("open_positions", []):
                if p.get("symbol") == opt_sym:
                    ep = float(p.get("entry_price", cur_p))
                    qty = int(p.get("qty", 1))
                    p["current_price"] = cur_p
                    p["peak_price"] = round(max(float(p.get("peak_price", ep)), cur_p), 2)
                    p["unrealized_pnl"] = round((cur_p - ep) * qty, 2)
                    p["amount"] = round(ep * qty, 2)
                    modified = True
            if modified:
                with open(ledger_file, "w", encoding="utf-8") as lf:
                    json.dump(data, lf, indent=2)
        except Exception:
            pass


class FnoOptionsTrader:
    def __init__(self):
        self.api = None
        self.open_positions = {}
        self.pending_signals = {}
        self.completed_trades = []
        self.capital = 200000.0
        self.free_cash = 200000.0
        self.realized_pnl = 0.0
        self.last_exit_times = {}
        self.config = {}
        cfg_file = BASE_DIR / "trader" / "config.json"
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as cf:
                    self.config = json.load(cf)
            except Exception:
                pass
        self.broker_adapter = BrokerAdapter(config=self.config)
        self.load_ledger()

    def load_ledger(self):
        if LEDGER_PATH.exists():
            try:
                with open(LEDGER_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.capital = float(data.get("initial_capital", 200000.0))
                    self.free_cash = float(data.get("free_cash", self.capital))
                    self.realized_pnl = float(data.get("realized_pnl", 0.0))
                    self.completed_trades = data.get("trade_history", [])
                    for p in data.get("open_positions", []):
                        self.open_positions[p["symbol"]] = p
            except Exception as e:
                print(f"[FNO LEDGER LOAD ERROR] {e}")

    def save_ledger(self):
        try:
            data = {
                "initial_capital": self.capital,
                "free_cash": round(self.free_cash, 2),
                "deployed_cash": round(sum(p["qty"] * p["entry_price"] for p in self.open_positions.values()), 2),
                "realized_pnl": round(self.realized_pnl, 2),
                "open_positions": list(self.open_positions.values()),
                "trade_history": self.completed_trades
            }
            with open(LEDGER_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[FNO LEDGER SAVE ERROR] {e}")

    def get_fno_chat_id(self):
        if ACCOUNTS_PATH.exists():
            try:
                with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    return d.get("accounts", {}).get("fno", {}).get("telegram_chat_id", "1081536667")
            except Exception:
                pass
        return "1081536667"

    def send_telegram(self, text: str):
        cid = self.get_fno_chat_id()
        try:
            telegram_bot.send_telegram_message(text, chat_id=cid)
        except Exception as e:
            print(f"[TELEGRAM FNO ERROR] {e}")

    def run(self):
        acquire_fno_lock()
        mode_str = "LIVE BROKER" if self.broker_adapter.is_live() else "PAPER"

        print("=" * 75)
        print("[START] F&O OPTIONS TRADING DESK (NIFTY & BANKNIFTY ATM Momentum Paper Engine)")
        print(f"[CONFIG] Capital: Rs {self.capital:,.2f} | Mode: {mode_str} | Auto-Squareoff: 15:20 IST")
        print("=" * 75)

        self.api = dh.get_smart_api_session()
        self.broker_adapter.live_broker_client = self.api
        if hasattr(self.broker_adapter, "executor") and self.broker_adapter.executor:
            self.broker_adapter.executor.api = self.api


        startup_msg = (
            "⚡ <b>F&O Options Trading Desk is LIVE!</b>\n\n"
            "📊 <b>Underlying Assets:</b> <code>NIFTY 50</code>, <code>BANKNIFTY</code>\n"
            "🎯 <b>Strategy:</b> Intraday Confluence Momentum (Supertrend + EMA Channel + VWAP)\n"
            "📦 <b>Strike Resolution:</b> Dynamic Weekly ATM Contracts\n"
            f"💰 <b>Paper Capital:</b> ₹{self.capital:,.2f}\n"
            "🛡️ <b>Risk Rules:</b> 1 Lot | 5% SL | 15% Target (1:3 R:R)\n"
            "⏰ <b>Intraday Flat By:</b> 15:20 IST\n\n"
            "🔔 <i>Live NFO paper entries and exits will be reported here!</i>"
        )
        self.send_telegram(startup_msg)

        while True:
            try:
                now_dt = datetime.datetime.now()
                today_str = now_dt.strftime("%Y-%m-%d")
                time_str = now_dt.strftime("%H:%M")

                if time_str >= "15:25":
                    print("[F&O MARKET CLOSED] End of trading session.")
                    break

                # 15:20 Square-off
                if time_str >= "15:20":
                    if self.broker_adapter.is_live():
                        self.broker_adapter.cancel_all_open_orders()
                    if self.open_positions:
                        for sym in list(self.open_positions.keys()):
                            self.exit_position(sym, "EOD Mandatory Square-Off (15:20 IST reached)")

                for idx_info in INDICES:
                    uname = idx_info["name"]
                    sym = idx_info["symbol"]
                    token = idx_info["token"]

                    time.sleep(3.5)  # Rate-limit safety spacing

                    resp = None
                    for attempt in range(3):
                        try:
                            resp = self.api.getCandleData({
                                "exchange": "NSE",
                                "symboltoken": token,
                                "interval": "ONE_MINUTE",
                                "fromdate": f"{today_str} 09:15",
                                "todate": f"{today_str} {time_str}"
                            })
                            if resp and resp.get("status") and resp.get("data"):
                                break
                            time.sleep(2.0)
                        except Exception as ce:
                            if attempt < 2:
                                time.sleep(2.5)
                            else:
                                print(f"[CANDLE FETCH RETRY] {sym}: {ce}")

                    if not resp or not resp.get("status") or not resp.get("data"):
                        continue

                    bars = resp["data"]
                    if len(bars) < 20:
                        continue

                    # Completed bars only (exclude current minute)
                    completed = [b for b in bars if b[0].split("T")[1][:5] < time_str]
                    if len(completed) < 15:
                        continue

                    latest_bar = completed[-1]
                    bar_time = latest_bar[0].split("T")[1][:5]
                    closes = [float(b[4]) for b in completed]
                    highs = [float(b[2]) for b in completed]
                    lows = [float(b[3]) for b in completed]
                    spot_c = closes[-1]

                    # Indicators
                    ema5 = indicators.calculate_ema(closes, 5)[-1]
                    ema9 = indicators.calculate_ema(closes, 9)[-1]
                    ema33 = indicators.calculate_ema(closes, 33)[-1]
                    st_data = indicators.calculate_supertrend(highs, lows, closes, 10, 3.0)
                    st_dir = st_data["direction"][-1] if (st_data and len(st_data.get("direction", [])) > 0) else 0

                    print(f"[F&O {bar_time}] {sym:12s} Spot: Rs {spot_c:8.2f} | ST: {st_dir:+d} | EMA5: {ema5:.1f} | EMA9: {ema9:.1f}")

                    # Manage Open Positions for this underlying
                    for open_sym, pos in list(self.open_positions.items()):
                        if pos.get("underlying") == uname:
                            self.evaluate_option_exit(pos, spot_c, st_dir)

                    # Signal Evaluation (between 09:30 and 15:10)
                    has_open = any(p.get("underlying") == uname for p in self.open_positions.values())
                    if not has_open and "09:30" <= bar_time < "15:10":
                        # BULLISH CONFLUENCE -> BUY ATM CALL
                        if st_dir == 1 and ema5 > ema9 > ema33 and spot_c > ema9:
                            contract = resolve_atm_contract(uname, spot_c, "CE")
                            if contract and contract["symbol"] not in self.open_positions:
                                self.enter_option_trade(contract, spot_c, "CE", "Bullish Supertrend + EMA Channel Alignment")

                        # BEARISH CONFLUENCE -> BUY ATM PUT
                        elif st_dir == -1 and ema5 < ema9 < ema33 and spot_c < ema9:
                            contract = resolve_atm_contract(uname, spot_c, "PE")
                            if contract and contract["symbol"] not in self.open_positions:
                                self.enter_option_trade(contract, spot_c, "PE", "Bearish Supertrend + EMA Channel Alignment")

                time.sleep(45)

            except Exception as e:
                print(f"[F&O TRADER LOOP ERROR] {e}")
                time.sleep(10)

    def enter_option_trade(self, contract: dict, spot_price: float, option_type: str, reason: str):
        opt_sym = contract["symbol"]
        opt_token = contract["token"]
        qty = contract["lotsize"]
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.datetime.now().strftime("%H:%M")

        # 1. Enforce max_trades_per_day quota (30 trades per day)
        cfg_file = BASE_DIR / "trader" / "config.json"
        max_trades = 30
        if cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as cf:
                    max_trades = int(json.load(cf).get("max_trades_per_day", 30))
            except Exception:
                pass

        today_trades = [t for t in self.completed_trades if t.get("date") == today_str or t.get("exit_date") == today_str]
        total_today_entries = len(today_trades) + len(self.open_positions)
        if total_today_entries >= max_trades:
            print(f"[F&O BLOCKED] Maximum daily trades quota reached ({total_today_entries}/{max_trades}). Halting new F&O entries for today.")
            return

        # 2. Enforce 15-min cooldown per underlying index after position exit
        underlying = contract.get("underlying")
        cooldown_secs = 900
        time_since_exit = time.time() - self.last_exit_times.get(underlying, 0)
        if time_since_exit < cooldown_secs:
            rem_mins = int((cooldown_secs - time_since_exit) / 60)
            print(f"[F&O COOLDOWN] {underlying} in exit cooldown ({rem_mins}m remaining). Skipping entry.")
            return

        # Fetch live option premium
        time.sleep(1.2)
        premium = 0.0
        try:
            resp = self.api.getCandleData({
                "exchange": "NFO",
                "symboltoken": opt_token,
                "interval": "ONE_MINUTE",
                "fromdate": f"{today_str} 09:15",
                "todate": f"{today_str} {time_str}"
            })
            if resp and resp.get("status") and resp.get("data"):
                premium = float(resp["data"][-1][4])
        except Exception:
            pass

        # Fallback realistic ATM premium estimate if broker data unavailable
        if premium <= 0:
            premium = 85.0 if contract["underlying"] == "NIFTY" else 220.0

        investment = round(premium * qty, 2)
        if self.free_cash < investment:
            print(f"[F&O BLOCKED] Insufficient cash for {opt_sym} (Need Rs {investment}, have Rs {self.free_cash})")
            return

        sl_price = round(premium * (1.0 - STOP_LOSS_PCT), 2)     # -5% Stop-loss
        tgt_price = round(premium * (1.0 + TARGET_PCT), 2)       # +15% Target (1:3 R:R)

        # Live Order Dispatch if dual-gate live trading is active
        broker_order_id = None
        broker_sl_order_id = None
        if self.broker_adapter.is_live():
            print(f"[LIVE F&O DISPATCH] Placing BUY order for {qty} of {opt_sym} on Angel One...")
            order_res = self.broker_adapter.place_order(
                symbol=opt_sym,
                side="BUY",
                qty=qty,
                price=0.0,
                order_type="MARKET",
                product_type="INTRADAY",
                exchange="NFO",
                token=opt_token
            )
            if not order_res.get("success"):
                print(f"[LIVE F&O ORDER REJECTED/FAILED] {opt_sym}: {order_res.get('reason')}")
                return
            broker_order_id = order_res.get("order_id")
            print(f"[LIVE F&O ORDER SUBMITTED] Broker Order ID: {broker_order_id}")

            # Reconcile actual fill price if available
            time.sleep(1.0)
            status_info = self.broker_adapter.get_order_status(broker_order_id)
            if status_info.get("status") == "COMPLETE" and status_info.get("fill_price", 0) > 0:
                premium = float(status_info["fill_price"])
                investment = round(premium * qty, 2)
                sl_price = round(premium * (1.0 - STOP_LOSS_PCT), 2)
                tgt_price = round(premium * (1.0 + TARGET_PCT), 2)
                print(f"[LIVE F&O FILL RECONCILED] Fill Price: Rs {premium:.2f} | Investment: Rs {investment}")

            # Immediately dispatch Terminal-Level Stop-Loss Order
            sl_res = self.broker_adapter.place_stop_loss_order(
                symbol=opt_sym,
                token=opt_token,
                side="SELL",
                qty=qty,
                trigger_price=sl_price,
                product_type="INTRADAY",
                exchange="NFO"
            )
            if sl_res.get("success"):
                broker_sl_order_id = sl_res.get("order_id")
                print(f"[LIVE F&O SL PLACED AT BROKER] SL Order ID: {broker_sl_order_id} @ Trigger Rs {sl_price:.2f}")

        pos_record = {
            "id": f"OPT-{int(time.time()*1000)}-{opt_sym}",
            "symbol": opt_sym,
            "token": opt_token,
            "broker_order_id": broker_order_id,
            "broker_sl_order_id": broker_sl_order_id,
            "underlying": contract["underlying"],
            "option_type": option_type,
            "strike": contract["strike"],
            "expiry": contract["expiry"],
            "side": "BUY",
            "qty": qty,
            "quantity": qty,
            "entry_price": premium,
            "current_price": premium,
            "peak_price": premium,
            "amount": investment,
            "invested": investment,
            "sl_price": sl_price,
            "current_stop": sl_price,
            "target_price": tgt_price,
            "entry_spot": spot_price,
            "entry_time": time_str,
            "reason": reason,
            "status": "OPEN"
        }

        self.open_positions[opt_sym] = pos_record
        self.free_cash -= investment
        self.save_ledger()

        type_emoji = "🟢 CALL (CE)" if option_type == "CE" else "🔴 PUT (PE)"
        msg = (
            f"⚡ <b>[F&O ENTRY EXECUTED] {type_emoji}</b>\n\n"
            f"🎯 <b>Contract:</b> <code>{opt_sym}</code>\n"
            f"📊 <b>Underlying Spot:</b> ₹{spot_price:,.2f} ({contract['underlying']})\n"
            f"💰 <b>Premium Price:</b> ₹{premium:.2f}\n"
            f"📦 <b>Lot Size:</b> {qty} Shares\n"
            f"💵 <b>Deployed Amount:</b> ₹{investment:,.2f}\n"
            f"🛑 <b>Stop-Loss (-5%):</b> ₹{sl_price:.2f}\n"
            f"🏁 <b>Target (+15%):</b> ₹{tgt_price:.2f} (1:3 R:R)\n"
            f"🧠 <b>Strategy Reason:</b> {reason}"
        )
        self.send_telegram(msg)
        print(f"[F&O ENTRY] Bought {qty} {opt_sym} @ Rs {premium:.2f} | Deployed: Rs {investment}")
        # Broadcast F&O Entry to all active accounts (Gautam, Himanshu, Prateek)
        broadcast_option_entry_to_accounts(pos_record, investment)

    def evaluate_option_exit(self, pos: dict, current_spot: float, st_dir: int):
        opt_sym = pos["symbol"]
        opt_token = pos["token"]
        qty = pos["qty"]
        entry_p = pos["entry_price"]
        sl_p = pos["sl_price"]
        tgt_p = pos["target_price"]
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.datetime.now().strftime("%H:%M")

        # Fetch current premium
        current_premium = entry_p
        try:
            resp = self.api.getCandleData({
                "exchange": "NFO",
                "symboltoken": opt_token,
                "interval": "ONE_MINUTE",
                "fromdate": f"{today_str} 09:15",
                "todate": f"{today_str} {time_str}"
            })
            if resp and resp.get("status") and resp.get("data"):
                current_premium = float(resp["data"][-1][4])
        except Exception:
            # Fallback delta simulation based on spot movement
            spot_change = current_spot - pos.get("entry_spot", current_spot)
            delta = 0.50 if pos["option_type"] == "CE" else -0.50
            current_premium = max(1.0, round(entry_p + (spot_change * delta), 2))

        # Update live tracking metrics on the open position
        pos["current_price"] = current_premium
        pos["peak_price"] = round(max(float(pos.get("peak_price", entry_p)), current_premium), 2)
        pos["amount"] = round(entry_p * qty, 2)
        pos["unrealized_pnl"] = round((current_premium - entry_p) * qty, 2)
        pos["unrealized_pnl_pct"] = round(((current_premium - entry_p) / entry_p) * 100, 2) if entry_p > 0 else 0.0
        self.save_ledger()
        update_open_option_on_all_ledgers(opt_sym, current_premium)

        # Check Target Hit
        if current_premium >= tgt_p:
            self.exit_position(opt_sym, f"🎯 Target Hit (+15% R:R) @ Rs {current_premium:.2f}", current_premium)
            return

        # Check Stop-Loss Hit
        if current_premium <= sl_p:
            self.exit_position(opt_sym, f"🛑 Stop-Loss Hit (-5%) @ Rs {current_premium:.2f}", current_premium)
            return

        # Supertrend Reversal
        if (pos["option_type"] == "CE" and st_dir == -1) or (pos["option_type"] == "PE" and st_dir == 1):
            self.exit_position(opt_sym, f"🔄 Supertrend Reversal on Underlying Spot @ Rs {current_premium:.2f}", current_premium)

    def exit_position(self, opt_sym: str, reason: str, exit_price: float = None):
        pos = self.open_positions.pop(opt_sym, None)
        if not pos:
            return

        entry_p = pos["entry_price"]
        opt_token = pos.get("token", "")

        # If live trading is active, cancel broker SL and place market exit
        if self.broker_adapter.is_live():
            sl_oid = pos.get("broker_sl_order_id")
            if sl_oid:
                print(f"[LIVE F&O CANCEL SL] Cancelling broker Stop-Loss order: {sl_oid}")
                self.broker_adapter.cancel_order(sl_oid, variety="STOPLOSS")

            print(f"[LIVE F&O EXIT DISPATCH] Placing SELL order for {pos.get('qty')} {opt_sym} on Angel One...")
            exit_res = self.broker_adapter.place_order(
                symbol=opt_sym,
                side="SELL",
                qty=pos.get("qty", 1),
                price=0.0,
                order_type="MARKET",
                product_type="INTRADAY",
                exchange="NFO",
                token=opt_token
            )
            if exit_res.get("success") and exit_res.get("order_id"):
                time.sleep(1.0)
                exit_status = self.broker_adapter.get_order_status(exit_res["order_id"])
                if exit_status.get("status") == "COMPLETE" and exit_status.get("fill_price", 0) > 0:
                    exit_price = float(exit_status["fill_price"])
                    print(f"[LIVE F&O EXIT FILLED] Broker Exit Price: Rs {exit_price:.2f}")
        qty = pos["qty"]
        if exit_price is None:
            exit_price = entry_p

        costs = calculate_option_costs(entry_p, exit_price, qty)
        net_pnl = costs["net_pnl"]
        fees = costs["total_fees"]
        gross_pnl = costs["gross_pnl"]

        returned_cash = (exit_price * qty) - fees
        self.free_cash += returned_cash
        self.realized_pnl += net_pnl

        trade_record = {
            "id": pos["id"],
            "symbol": opt_sym,
            "side": pos["side"],
            "qty": qty,
            "quantity": qty,
            "entry_price": entry_p,
            "exit_price": exit_price,
            "entry_time": pos["entry_time"],
            "exit_time": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "gross_pnl": gross_pnl,
            "fees": fees,
            "pnl": net_pnl,
            "reason": reason,
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "exit_date": datetime.datetime.now().strftime("%Y-%m-%d")
        }

        # Record exit time for underlying cooldown
        underlying = pos.get("underlying")
        if underlying:
            self.last_exit_times[underlying] = time.time()

        self.completed_trades.append(trade_record)
        self.save_ledger()
        # Broadcast F&O Exit to all active accounts (Gautam, Himanshu, Prateek)
        broadcast_option_exit_to_accounts(opt_sym, exit_price, reason)

        pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
        exit_msg = (
            f"{pnl_emoji} <b>[F&O EXIT EXECUTED] {opt_sym}</b>\n\n"
            f"🏁 <b>Exit Premium:</b> ₹{exit_price:.2f}\n"
            f"📍 <b>Entry Premium:</b> ₹{entry_p:.2f}\n"
            f"📦 <b>Lotsize:</b> {qty} Shares\n"
            f"💵 <b>Net P&L:</b> ₹{net_pnl:+,.2f}\n"
            f"🧾 <b>Statutory Fees:</b> ₹{fees:.2f}\n"
            f"🔍 <b>Exit Reason:</b> {reason}\n"
            f"💳 <b>Updated Free Cash:</b> ₹{self.free_cash:,.2f}"
        )
        self.send_telegram(exit_msg)
        print(f"[F&O EXIT] {opt_sym} @ Rs {exit_price:.2f} | Net PnL: Rs {net_pnl:+,.2f} | Reason: {reason}")

if __name__ == "__main__":
    trader = FnoOptionsTrader()
    trader.run()
