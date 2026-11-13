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
import math
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
from ledger_transaction import (
    locked_ledger_transaction,
    generate_order_id,
    generate_fill_id,
    FileLock
)
from order_manager import OrderManager, OrderState

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

def parse_and_validate_quote_timestamp(ts_val, max_age_seconds: float, reference_dt=None):
    """
    Parses and strictly validates quote timestamp against max allowed age.
    Rejects malformed timestamps, future clock skew anomalies (>60s), and stale quotes.
    Returns: (is_valid: bool, age_seconds: float, formatted_str: str)
    """
    if not ts_val:
        return False, 0.0, "MISSING_TIMESTAMP"
    ref = reference_dt or datetime.datetime.now()
    if isinstance(ts_val, (int, float)):
        try:
            parsed_dt = datetime.datetime.fromtimestamp(ts_val)
        except Exception:
            return False, 0.0, "MALFORMED_EPOCH"
    elif isinstance(ts_val, str):
        clean_ts = ts_val.replace("IST", "").strip()
        if "+" in clean_ts:
            clean_ts = clean_ts.split("+")[0].strip()
        elif clean_ts.endswith("Z"):
            clean_ts = clean_ts[:-1].strip()

        parsed_dt = None
        formats = [
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M",
            "%Y-%m-%d %H:%M",
            "%d-%m-%Y %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
            "%d-%m-%Y %H:%M",
            "%d/%m/%Y %H:%M",
            "%H:%M:%S",
            "%H:%M"
        ]
        for fmt in formats:
            try:
                parsed = datetime.datetime.strptime(clean_ts, fmt)
                if fmt in ("%H:%M:%S", "%H:%M"):
                    parsed = parsed.replace(year=ref.year, month=ref.month, day=ref.day)
                parsed_dt = parsed
                break
            except ValueError:
                continue

        if parsed_dt is None:
            return False, 0.0, "MALFORMED_TIMESTAMP"
    else:
        return False, 0.0, "INVALID_TIMESTAMP_TYPE"

    age = (ref - parsed_dt).total_seconds()
    if age < -60.0:
        return False, age, "FUTURE_TIMESTAMP_ANOMALY"
    if age > max_age_seconds:
        return False, age, "STALE_QUOTE"

    return True, max(0.0, age), parsed_dt.strftime("%Y-%m-%d %H:%M:%S IST")

def get_option_market_quote(api, symbol: str, token: str, exchange: str = "NFO") -> dict:
    """
    Retrieves a verified market quote for an option contract from Angel One SmartAPI.
    Applies rigorous Phase 3.2 validation invariants:
    - LTP must be positive and finite
    - Bid and ask must be non-negative, finite, and internally consistent (bid <= ask)
    - Rejects malformed and stale timestamps based on source-specific max age
    - Source-specific max age: LTP/Depth <= 60s, Historical Candles <= 180s
    - Incomplete / in-progress candles are strictly rejected
    - Historical candle data is explicitly distinguished from live quotes (is_realtime_quote=False)
    - Full price provenance is recorded on every valid quote
    - Stale or invalid quotes return None (NEVER substitutes fallback or spot price)
    """
    if not api:
        return None

    now_dt = datetime.datetime.now()
    now_ist = now_dt.strftime("%Y-%m-%d %H:%M:%S IST")
    time_str = now_dt.strftime("%H:%M")
    today_str = now_dt.strftime("%Y-%m-%d")

    # Tier 1: getLtpData (fastest real-time tick price)
    try:
        ltp_resp = api.getLtpData(exchange=exchange, tradingsymbol=symbol, symboltoken=str(token))
        if ltp_resp and ltp_resp.get("status") and ltp_resp.get("data"):
            data = ltp_resp["data"]
            ltp = float(data.get("ltp", 0.0))
            bid = float(data.get("bid", 0.0) or 0.0)
            ask = float(data.get("ask", 0.0) or 0.0)

            # Check explicit quote timestamp if present in data
            ts_key = next((k for k in ("feed_time", "timestamp", "quote_timestamp", "exch_feed_time") if k in data and data[k]), None)
            quote_age = 0.0
            quote_ts_str = now_ist
            if ts_key:
                is_valid_ts, quote_age, formatted_ts = parse_and_validate_quote_timestamp(data[ts_key], max_age_seconds=60.0, reference_dt=now_dt)
                if not is_valid_ts:
                    data = None  # Invalidate tier 1 response due to malformed or stale timestamp
                else:
                    quote_ts_str = formatted_ts

            if data and math.isfinite(ltp) and ltp > 0:
                if math.isfinite(bid) and math.isfinite(ask) and bid >= 0 and ask >= 0:
                    if bid > 0 and ask > 0 and bid > ask:
                        # Crossed book anomaly -> Reject as corrupted data
                        pass
                    else:
                        return {
                            "valid": True,
                            "is_realtime_quote": True,
                            "quote_type": "LIVE_QUOTE",
                            "ltp": ltp,
                            "bid": bid,
                            "ask": ask,
                            "quote_source": "ANGEL_LTP_API",
                            "quote_timestamp": quote_ts_str,
                            "quote_age_seconds": quote_age,
                            "exchange": exchange,
                            "symbol": symbol,
                            "token": str(token),
                            "price_provenance": {
                                "source": "ANGEL_LTP_API",
                                "quote_type": "LIVE_QUOTE",
                                "is_realtime": True,
                                "timestamp": quote_ts_str,
                                "age_seconds": quote_age
                            },
                            "raw_data": data
                        }
    except Exception:
        pass

    # Tier 2: getMarketData (FULL mode for depth bid/ask)
    try:
        mkt_resp = api.getMarketData(mode="FULL", exchangeTokens={exchange: [str(token)]})
        if mkt_resp and mkt_resp.get("status") and mkt_resp.get("data"):
            fetched = mkt_resp["data"].get("fetched", [])
            if fetched:
                f0 = fetched[0]
                ltp = float(f0.get("ltp", 0.0))
                depth = f0.get("depth", {})
                best_bid = float(depth.get("buy", [{}])[0].get("price", 0.0)) if depth.get("buy") else 0.0
                best_ask = float(depth.get("sell", [{}])[0].get("price", 0.0)) if depth.get("sell") else 0.0

                ts_key = next((k for k in ("feed_time", "timestamp", "quote_timestamp", "exch_feed_time") if k in f0 and f0[k]), None)
                quote_age = 0.0
                quote_ts_str = now_ist
                if ts_key:
                    is_valid_ts, quote_age, formatted_ts = parse_and_validate_quote_timestamp(f0[ts_key], max_age_seconds=60.0, reference_dt=now_dt)
                    if not is_valid_ts:
                        f0 = None
                    else:
                        quote_ts_str = formatted_ts

                if f0 and math.isfinite(ltp) and ltp > 0:
                    if math.isfinite(best_bid) and math.isfinite(best_ask) and best_bid >= 0 and best_ask >= 0:
                        if best_bid > 0 and best_ask > 0 and best_bid > best_ask:
                            pass
                        else:
                            return {
                                "valid": True,
                                "is_realtime_quote": True,
                                "quote_type": "LIVE_QUOTE",
                                "ltp": ltp,
                                "bid": best_bid,
                                "ask": best_ask,
                                "quote_source": "ANGEL_MARKET_DATA",
                                "quote_timestamp": quote_ts_str,
                                "quote_age_seconds": quote_age,
                                "exchange": exchange,
                                "symbol": symbol,
                                "token": str(token),
                                "price_provenance": {
                                "source": "ANGEL_MARKET_DATA",
                                "quote_type": "LIVE_QUOTE",
                                "is_realtime": True,
                                "timestamp": quote_ts_str,
                                "age_seconds": quote_age
                            },
                                "raw_data": f0
                            }
    except Exception:
        pass

    # Tier 3: getCandleData (most recent completed 1-minute bar close)
    try:
        candle_resp = api.getCandleData({
            "exchange": exchange,
            "symboltoken": str(token),
            "interval": "ONE_MINUTE",
            "fromdate": f"{today_str} 09:15",
            "todate": f"{today_str} {time_str}"
        })
        if candle_resp and candle_resp.get("status") and candle_resp.get("data"):
            bars = candle_resp["data"]
            if bars and len(bars) > 0:
                completed_bars = []
                for b in bars:
                    bar_time_str = b[0].split("T")[1][:5] if "T" in b[0] else b[0].split()[1][:5]
                    if bar_time_str < time_str:
                        completed_bars.append(b)

                if completed_bars:
                    last_bar = completed_bars[-1]
                    raw_bar_ts = last_bar[0]
                    bar_time_str = raw_bar_ts.split("T")[1][:5] if "T" in raw_bar_ts else raw_bar_ts.split()[1][:5]
                    bar_close = float(last_bar[4])

                    # Validate candle timestamp and freshness (max 180 seconds)
                    is_valid_ts, age_seconds, formatted_ts = parse_and_validate_quote_timestamp(raw_bar_ts, max_age_seconds=180.0, reference_dt=now_dt)

                    if is_valid_ts and math.isfinite(bar_close) and bar_close > 0:
                        return {
                            "valid": True,
                            "is_realtime_quote": False,
                            "quote_type": "HISTORICAL_CANDLE",
                            "ltp": bar_close,
                            "bid": 0.0,
                            "ask": 0.0,
                            "quote_source": "ANGEL_CANDLE_CLOSE",
                            "quote_timestamp": formatted_ts,
                            "quote_age_seconds": age_seconds,
                            "exchange": exchange,
                            "symbol": symbol,
                            "token": str(token),
                            "price_provenance": {
                                "source": "ANGEL_CANDLE_CLOSE",
                                "quote_type": "HISTORICAL_CANDLE",
                                "is_realtime": False,
                                "timestamp": formatted_ts,
                                "age_seconds": age_seconds
                            },
                            "raw_data": {"bar_time": raw_bar_ts, "close": bar_close, "age_seconds": age_seconds}
                        }
    except Exception:
        pass

    return None

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

        def _update_entry(led_data):
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
                return False

            cur_free = float(led_data.get("free_cash", acc_info.get("capital", 1000000.0)))
            if cur_free < investment:
                print(f"[F&O {user_name}] Skipped: Insufficient free cash (Need Rs {investment}, have Rs {cur_free})")
                return False

            open_positions = led_data.setdefault("open_positions", [])
            if not any(p.get("symbol") == pos_record["symbol"] for p in open_positions):
                user_pos = dict(pos_record)
                user_pos["id"] = f"OPT-{acc_id[:3].upper()}-{int(time.time()*1000)}-{pos_record['symbol']}"
                open_positions.append(user_pos)
                led_data["free_cash"] = round(cur_free - investment, 2)
                led_data["deployed_cash"] = round(float(led_data.get("deployed_cash", 0.0)) + investment, 2)
                led_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
                print(f"[F&O {user_name}] Position recorded in {ledger_fname}")
                return True
            return False

        try:
            locked_ledger_transaction(ledger_file, _update_entry)
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

        net_pnl_holder = [0.0]
        def _update_exit(led_data):
            open_positions = led_data.get("open_positions", [])
            pos_idx = next((i for i, p in enumerate(open_positions) if p.get("symbol") == opt_sym), None)
            if pos_idx is None:
                return None

            pos = open_positions.pop(pos_idx)
            entry_p = float(pos.get("entry_price", exit_price))
            qty = int(pos.get("qty", 1))

            costs = calculate_option_costs(entry_p, exit_price, qty)
            net_pnl = costs["net_pnl"]
            fees = costs["total_fees"]
            gross_pnl = costs["gross_pnl"]
            net_pnl_holder[0] = net_pnl

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
                "exit_date": datetime.datetime.now().strftime("%Y-%m-%d"),
                # Audit & verification metadata preserved from position
                "price_source": pos.get("price_source", "VERIFIED_MARKET_QUOTE"),
                "verified_market_data": pos.get("verified_market_data", True),
                "quote_source": pos.get("quote_source", "UNKNOWN"),
                "quote_timestamp": pos.get("quote_timestamp", ""),
                "slippage_applied": pos.get("slippage_applied", 0.0),
                "contract_details": pos.get("contract_details", {})
            }

            th = led_data.setdefault("trade_history", [])
            th.append(trade_record)
            led_data["realized_pnl"] = round(float(led_data.get("realized_pnl", 0.0)) + net_pnl, 2)
            returned_cash = (exit_price * qty) - fees
            led_data["free_cash"] = round(float(led_data.get("free_cash", acc_info.get("capital", 1000000.0))) + returned_cash, 2)
            led_data["deployed_cash"] = max(0.0, round(float(led_data.get("deployed_cash", 0.0)) - (entry_p * qty), 2))
            led_data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")
            print(f"[F&O {user_name}] Exit recorded in {ledger_fname} | Net: Rs {net_pnl:+.2f}")
            return trade_record

        try:
            locked_ledger_transaction(ledger_file, _update_exit)
        except Exception as e:
            print(f"[F&O {user_name}] Ledger exit error: {e}")

        if cid:
            try:
                net_pnl = net_pnl_holder[0]
                pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
                exit_msg = (
                    f"{pnl_emoji} <b>[F&O EXIT EXECUTED - {user_name}]</b>\n\n"
                    f"🎯 <b>Contract:</b> <code>{opt_sym}</code>\n"
                    f"🏁 <b>Exit Premium:</b> ₹{exit_price:.2f}\n"
                    f"💡 <b>Reason:</b> {reason}\n"
                    f"💵 <b>Net P&L:</b> ₹{net_pnl:+,.2f}"
                )
                telegram_bot.send_telegram_message(exit_msg, chat_id=cid)
            except Exception as te:
                print(f"[TELEGRAM ERROR {user_name}] {te}")

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
    def __init__(self, order_manager=None):
        self.api = None
        self.open_positions = {}
        self.in_flight_orders = set()
        self.in_flight_exits = set()
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
        self.order_manager = order_manager or OrderManager(db_path=BASE_DIR / "trader" / "orders_lifecycle.db", ledger_path=LEDGER_PATH)
        self.load_ledger()
        try:
            self.order_manager.recover_pending_orders(broker_adapter=self.broker_adapter)
        except Exception as e:
            print(f"[ORDER RECOVERY ERROR] {e}")

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
        def _update(data):
            data["initial_capital"] = self.capital
            data["free_cash"] = round(self.free_cash, 2)
            data["deployed_cash"] = round(sum(p["qty"] * p["entry_price"] for p in self.open_positions.values()), 2)
            data["realized_pnl"] = round(self.realized_pnl, 2)
            data["open_positions"] = list(self.open_positions.values())
            data["trade_history"] = self.completed_trades
            data["last_updated"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S IST")

        try:
            locked_ledger_transaction(LEDGER_PATH, _update)
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

                    time.sleep(4.0)  # Rate-limit safety spacing between indices

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
                            time.sleep(2.5)
                        except Exception as ce:
                            err_str = str(ce).lower()
                            if "access rate" in err_str or "rate" in err_str:
                                time.sleep(3.5)
                            elif attempt < 2:
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

    def fetch_option_quote(self, opt_sym: str, opt_token: str, exchange: str = "NFO") -> dict:
        """Helper method calling verified market quote fetcher."""
        return get_option_market_quote(self.api, opt_sym, opt_token, exchange=exchange)

    def enter_option_trade(self, contract: dict, spot_price: float, option_type: str, reason: str, signal_id: str = None):
        opt_sym = contract.get("symbol", "")
        opt_token = str(contract.get("token", ""))
        qty = contract.get("lotsize", 0)
        underlying = contract.get("underlying", "")
        expiry_str = contract.get("expiry", "")
        strike = contract.get("strike", 0.0)

        # 0. Phase 3.1 Contract Validation Invariants
        if not opt_sym or not opt_token or not opt_token.isdigit() or qty <= 0:
            print(f"[F&O CONTRACT INVALID] Token or symbol malformed for {opt_sym}. Entry aborted.")
            return

        if not math.isfinite(strike) or strike <= 0:
            print(f"[F&O CONTRACT INVALID] Strike price {strike} is invalid. Entry aborted.")
            return

        # Check for expired contract
        try:
            exp_date = datetime.datetime.strptime(expiry_str, "%d%b%Y").date()
            if exp_date < datetime.datetime.now().date():
                print(f"[F&O CONTRACT EXPIRED] Contract {opt_sym} expired on {expiry_str}. Entry aborted.")
                return
        except Exception:
            pass

        # Persistent Order Registry & Duplicate Order Prevention
        today_str = datetime.datetime.now().strftime("%Y-%m-%d")
        if not signal_id:
            signal_id = f"SIG-FNO-{opt_sym}-{today_str}-{int(time.time()*1000)}"

        if hasattr(self, "order_manager") and self.order_manager:
            can_admit, admit_reason = self.order_manager.can_admit_order(opt_sym, signal_id)
            if not can_admit:
                print(f"[F&O DUPLICATE BLOCKED] {admit_reason}")
                return

        if opt_sym in self.open_positions or (hasattr(self, "in_flight_orders") and opt_sym in self.in_flight_orders):
            print(f"[F&O DUPLICATE BLOCKED] Contract {opt_sym} is already active or in-flight. Skipping.")
            return

        order_id = None
        if hasattr(self, "order_manager") and self.order_manager:
            order_res = self.order_manager.create_order(
                symbol=opt_sym,
                side="BUY",
                quantity=qty,
                price=spot_price,
                stop_loss=round(spot_price * 0.95, 2),
                target_price=round(spot_price * 1.15, 2),
                signal_id=signal_id,
                strategy_name="FNO_OPTIONS_CONFLUENCE"
            )
            order_id = order_res.get("order_id")
            if order_res.get("status") != OrderState.CREATED:
                print(f"[F&O ORDER ADMISSION REJECTED] {order_res.get('reason')}")
                return
            self.order_manager.update_order_status(order_id, OrderState.SUBMITTED)

        if not hasattr(self, "in_flight_orders"):
            self.in_flight_orders = set()
        self.in_flight_orders.add(opt_sym)
        try:
            self._execute_option_entry(contract, spot_price, option_type, reason, order_id=order_id)
        finally:
            self.in_flight_orders.discard(opt_sym)

    def _execute_option_entry(self, contract: dict, spot_price: float, option_type: str, reason: str, order_id: str = None):
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
            if order_id and hasattr(self, "order_manager") and self.order_manager:
                self.order_manager.update_order_status(order_id, OrderState.REJECTED, rejection_reason="Daily trade limit reached")
            print(f"[F&O BLOCKED] Maximum daily trades quota reached ({total_today_entries}/{max_trades}). Halting new F&O entries for today.")
            return

        # 2. Enforce 15-min cooldown per underlying index after position exit
        underlying = contract.get("underlying")
        cooldown_secs = 900
        time_since_exit = time.time() - self.last_exit_times.get(underlying, 0)
        if time_since_exit < cooldown_secs:
            rem_mins = int((cooldown_secs - time_since_exit) / 60)
            if order_id and hasattr(self, "order_manager") and self.order_manager:
                self.order_manager.update_order_status(order_id, OrderState.REJECTED, rejection_reason=f"Underlying in cooldown ({rem_mins}m remaining)")
            print(f"[F&O COOLDOWN] {underlying} in exit cooldown ({rem_mins}m remaining). Skipping entry.")
            return

        # 3. Retrieve and verify live option market quote (STRICT PRICE VALIDATION)
        time.sleep(0.5)
        quote = self.fetch_option_quote(opt_sym, opt_token, exchange="NFO")

        # Reject/defer entry if valid fresh market quote is not obtained
        if not quote or quote.get("ltp", 0.0) <= 0:
            if order_id and hasattr(self, "order_manager") and self.order_manager:
                self.order_manager.update_order_status(order_id, OrderState.FAILED, rejection_reason="No verified market quote available")
            print(
                f"[F&O ENTRY REJECTED] Live option quote unavailable for {opt_sym} "
                f"(Exchange: NFO, Token: {opt_token}, Strike: {contract['strike']}, Expiry: {contract['expiry']}). "
                f"Entry deferred to protect capital. Fallback pricing is strictly prohibited."
            )
            return

        ltp = float(quote["ltp"])
        ask = float(quote.get("ask", 0.0))
        bid = float(quote.get("bid", 0.0))

        # 4. Determine execution fill price with documented slippage for paper simulation
        slippage = 0.0
        if ask > 0 and ask >= ltp:
            # Order book ask taking provides realistic market order fill
            premium = ask
            slippage = round(ask - ltp, 2)
        else:
            # Documented 0.2% slippage model over LTP
            slippage = round(ltp * 0.002, 2)
            premium = round(ltp + slippage, 2)

        # Log exact contract specification and quote telemetry
        print(
            f"[F&O QUOTE VERIFIED] Contract: {opt_sym} | Exchange: NFO | Expiry: {contract['expiry']} | "
            f"Strike: {contract['strike']} {option_type} | Token: {opt_token} | "
            f"LTP: Rs {ltp:.2f} | Bid: Rs {bid:.2f} | Ask: Rs {ask:.2f} | "
            f"Source: {quote.get('quote_source')} @ {quote.get('quote_timestamp')} | "
            f"Simulated Fill: Rs {premium:.2f} (Slippage: Rs {slippage:.2f})"
        )

        investment = round(premium * qty, 2)
        if self.free_cash < investment:
            if order_id and hasattr(self, "order_manager") and self.order_manager:
                self.order_manager.update_order_status(order_id, OrderState.REJECTED, rejection_reason="Insufficient cash")
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
                if order_id and hasattr(self, "order_manager") and self.order_manager:
                    self.order_manager.update_order_status(order_id, OrderState.FAILED, rejection_reason=order_res.get("reason"))
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

        fill_id = generate_fill_id(opt_sym, prefix="FILL-BUY")
        pos_record = {
            "id": fill_id,
            "order_id": order_id,
            "fill_id": fill_id,
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
            "status": "OPEN",
            # Phase 3 Audit & Price Verification Metadata
            "price_source": "VERIFIED_MARKET_QUOTE",
            "verified_market_data": True,
            "quote_source": quote.get("quote_source", "ANGEL_LTP_API"),
            "quote_timestamp": quote.get("quote_timestamp", ""),
            "base_ltp": ltp,
            "bid": bid,
            "ask": ask,
            "slippage_applied": slippage,
            "contract_details": {
                "exchange": "NFO",
                "trading_symbol": opt_sym,
                "instrument_token": str(opt_token),
                "underlying": contract["underlying"],
                "expiry": contract["expiry"],
                "strike": contract["strike"],
                "option_type": option_type
            }
        }

        self.open_positions[opt_sym] = pos_record
        self.free_cash -= investment

        # Update persistent order manager if active
        if order_id and hasattr(self, "order_manager") and self.order_manager:
            try:
                self.order_manager.record_simulated_fill(order_id, fill_price=premium, fill_qty=qty)
            except Exception as e:
                print(f"[ORDER_MANAGER FILL ERROR] {e}")

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

        # Fetch verified current option quote
        quote = self.fetch_option_quote(opt_sym, opt_token, exchange="NFO")
        if quote and quote.get("ltp", 0.0) > 0:
            current_premium = float(quote["ltp"])
            pos["price_source"] = "VERIFIED_MARKET_QUOTE"
            pos["verified_market_data"] = True
            pos["quote_source"] = quote.get("quote_source")
            pos["quote_timestamp"] = quote.get("quote_timestamp")
        else:
            # Quote temporarily unavailable; maintain last verified price (no fabricated delta)
            current_premium = float(pos.get("current_price", entry_p))

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

    def exit_position(self, opt_sym: str, reason: str, exit_price: float = None, max_retries: int = 3, backoff_base: float = 0.5):
        """
        Executes bounded simulated exit with retry, duplicate exit prevention, and operator alert on exhaustion.
        """
        pos = self.open_positions.get(opt_sym)
        if not pos:
            return False

        # Duplicate exit order prevention
        if not hasattr(self, "in_flight_exits"):
            self.in_flight_exits = set()

        if opt_sym in self.in_flight_exits:
            print(f"[F&O DUPLICATE EXIT BLOCKED] Exit order for {opt_sym} is already in-flight.")
            return False

        self.in_flight_exits.add(opt_sym)
        try:
            return self._execute_exit_with_retry(opt_sym, pos, reason, exit_price, max_retries, backoff_base)
        finally:
            self.in_flight_exits.discard(opt_sym)

    def _execute_exit_with_retry(self, opt_sym: str, pos: dict, reason: str, exit_price: float, max_retries: int, backoff_base: float):
        entry_p = pos["entry_price"]
        opt_token = pos.get("token", "")
        qty = pos.get("qty", 1)

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
            if not exit_res.get("success") or not exit_res.get("order_id"):
                print(f"[LIVE F&O EXIT FAILED] Failed to place exit order for {opt_sym}: {exit_res.get('reason')}. Position kept OPEN.")
                return False

            time.sleep(1.0)
            exit_status = self.broker_adapter.get_order_status(exit_res["order_id"])
            if exit_status.get("status") != "COMPLETE" or exit_status.get("fill_price", 0) <= 0:
                print(f"[LIVE F&O EXIT UNCONFIRMED] Order {exit_res['order_id']} status is {exit_status.get('status')}. Position kept OPEN.")
                return False
            exit_price = float(exit_status["fill_price"])

        # Paper Mode: Bounded simulated exit retry loop
        confirmed_exit_price = exit_price
        if confirmed_exit_price is None or confirmed_exit_price <= 0:
            for attempt in range(1, max_retries + 1):
                print(f"[F&O EXIT ATTEMPT {attempt}/{max_retries}] Fetching refreshed market quote for {opt_sym}...")
                quote = self.fetch_option_quote(opt_sym, opt_token, exchange="NFO")
                if quote and quote.get("ltp", 0.0) > 0:
                    bid = float(quote.get("bid", 0.0))
                    ltp = float(quote["ltp"])
                    if bid > 0 and bid <= ltp:
                        confirmed_exit_price = bid
                    else:
                        confirmed_exit_price = round(ltp * 0.998, 2)  # 0.2% slippage on exit taker
                    print(f"[F&O EXIT QUOTE CONFIRMED] Attempt {attempt} obtained valid exit price @ Rs {confirmed_exit_price:.2f}")
                    break
                else:
                    print(f"[F&O EXIT ATTEMPT {attempt}/{max_retries} FAILED] Market quote unavailable or invalid.")
                    if attempt < max_retries and backoff_base > 0:
                        sleep_time = backoff_base * (2 ** (attempt - 1))
                        time.sleep(sleep_time)

        # Retry exhaustion check: Never fabricate successful exit
        if confirmed_exit_price is None or confirmed_exit_price <= 0:
            print(f"[F&O EXIT RETRY EXHAUSTION] Retries exhausted ({max_retries}/{max_retries}) for {opt_sym}. Position preserved as OPEN.")
            alert_msg = (
                f"🚨 <b>[OPERATOR ALERT - EXIT RETRY EXHAUSTED]</b>\n\n"
                f"⚠️ <b>Contract:</b> <code>{opt_sym}</code>\n"
                f"📦 <b>Preserved Open Quantity:</b> {qty} Shares\n"
                f"❌ <b>Failure:</b> Quote unavailable after {max_retries} attempts.\n"
                f"🛡️ <b>Action:</b> Position remains OPEN. Immediate operator intervention required."
            )
            self.send_telegram(alert_msg)
            return False

        # Fill confirmed -> Safely pop position from open_positions
        self.open_positions.pop(opt_sym, None)
        costs = calculate_option_costs(entry_p, confirmed_exit_price, qty)
        net_pnl = costs["net_pnl"]
        fees = costs["total_fees"]
        gross_pnl = costs["gross_pnl"]

        returned_cash = (confirmed_exit_price * qty) - fees
        self.free_cash += returned_cash
        self.realized_pnl += net_pnl

        fill_id = generate_fill_id(opt_sym, prefix="FILL-EXIT")
        trade_record = {
            "id": pos.get("id", fill_id),
            "fill_id": fill_id,
            "symbol": opt_sym,
            "side": pos.get("side", "BUY"),
            "qty": qty,
            "quantity": qty,
            "entry_price": entry_p,
            "exit_price": confirmed_exit_price,
            "entry_time": pos.get("entry_time", ""),
            "exit_time": datetime.datetime.now().strftime("%H:%M:%S IST"),
            "gross_pnl": gross_pnl,
            "fees": fees,
            "pnl": net_pnl,
            "reason": reason,
            "date": datetime.datetime.now().strftime("%Y-%m-%d"),
            "exit_date": datetime.datetime.now().strftime("%Y-%m-%d"),
            # Audit & verification metadata preserved from position
            "price_source": pos.get("price_source", "VERIFIED_MARKET_QUOTE"),
            "verified_market_data": pos.get("verified_market_data", True),
            "quote_source": pos.get("quote_source", "UNKNOWN"),
            "quote_timestamp": pos.get("quote_timestamp", ""),
            "slippage_applied": pos.get("slippage_applied", 0.0),
            "contract_details": pos.get("contract_details", {})
        }

        # Record exit time for underlying cooldown
        underlying = pos.get("underlying")
        if underlying:
            self.last_exit_times[underlying] = time.time()

        self.completed_trades.append(trade_record)
        self.save_ledger()
        # Broadcast F&O Exit to all active accounts (Gautam, Himanshu, Prateek)
        broadcast_option_exit_to_accounts(opt_sym, confirmed_exit_price, reason)

        pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
        exit_msg = (
            f"{pnl_emoji} <b>[F&O EXIT EXECUTED] {opt_sym}</b>\n\n"
            f"🏁 <b>Exit Premium:</b> ₹{confirmed_exit_price:.2f}\n"
            f"📍 <b>Entry Premium:</b> ₹{entry_p:.2f}\n"
            f"📦 <b>Lotsize:</b> {qty} Shares\n"
            f"💵 <b>Net P&L:</b> ₹{net_pnl:+,.2f}\n"
            f"🧾 <b>Statutory Fees:</b> ₹{fees:.2f}\n"
            f"🔍 <b>Exit Reason:</b> {reason}\n"
            f"💳 <b>Updated Free Cash:</b> ₹{self.free_cash:,.2f}"
        )
        self.send_telegram(exit_msg)
        print(f"[F&O EXIT] {opt_sym} @ Rs {confirmed_exit_price:.2f} | Net PnL: Rs {net_pnl:+,.2f} | Reason: {reason}")
        return True

if __name__ == "__main__":
    trader = FnoOptionsTrader()
    trader.run()
