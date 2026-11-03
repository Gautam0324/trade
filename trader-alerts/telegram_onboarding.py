"""
Telegram Onboarding & Account Linker for Day Trader Pro V2.
Interactive self-registration: When users send /start, offers inline buttons:
[ 👤 Himanshu ]  [ 👤 Prateek ]  [ 👤 Gautam ]
On click, automatically captures their Telegram Chat ID and registers it to trader/accounts.json!
"""

import os
import sys
import json
import time
import threading
import urllib.request
import urllib.parse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRETS_PATH = BASE_DIR / "trader" / "secrets.json"
ACCOUNTS_PATH = BASE_DIR / "trader" / "accounts.json"

def get_bot_token():
    try:
        from dotenv import load_dotenv
        env_path = BASE_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)
    except Exception:
        pass

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token

    if SECRETS_PATH.exists():
        try:
            with open(SECRETS_PATH, "r", encoding="utf-8") as f:
                d = json.load(f)
                return d.get("telegram_bot_token")
        except Exception:
            pass
    return None

def send_telegram_request(token, endpoint, payload):
    url = f"https://api.telegram.org/bot{token}/{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"[TELEGRAM API ERROR] {endpoint}: {e}")
        return None

def load_accounts():
    if ACCOUNTS_PATH.exists():
        try:
            with open(ACCOUNTS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"accounts": {}}

def save_accounts(data):
    try:
        with open(ACCOUNTS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        print("[ACCOUNTS] Updated accounts.json successfully.")
    except Exception as e:
        print(f"[ACCOUNTS SAVE ERROR] {e}")

class TelegramOnboardingListener:
    def __init__(self):
        self.token = get_bot_token()
        self._stop_requested = False
        self._thread = None
        self._last_update_id = 0

    def start(self):
        if not self.token:
            print("[ONBOARDING] No Telegram bot token configured. Skipping listener.")
            return

        self._stop_requested = False
        self._thread = threading.Thread(target=self._poll_loop, name="TelegramOnboardingThread", daemon=True)
        self._thread.start()
        print("[ONBOARDING] Interactive Telegram Onboarding Listener started in background.")

    def stop(self):
        self._stop_requested = True

    def _poll_loop(self):
        while not self._stop_requested:
            try:
                url = f"https://api.telegram.org/bot{self.token}/getUpdates?offset={self._last_update_id + 1}&timeout=20"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=25) as resp:
                    res = json.loads(resp.read().decode("utf-8"))

                if res.get("ok") and res.get("result"):
                    for update in res["result"]:
                        self._last_update_id = max(self._last_update_id, update["update_id"])
                        self._handle_update(update)

            except Exception:
                time.sleep(3)

    def _handle_update(self, update):
        # 1. Handle incoming text message e.g. /start or any text
        message = update.get("message")
        if message:
            chat_id = message.get("chat", {}).get("id")
            text = (message.get("text") or "").strip()
            # Send interactive account selection menu
            self._send_onboarding_menu(chat_id)
            return

        # 2. Handle button click (callback_query)
        cq = update.get("callback_query")
        if cq:
            cq_id = cq.get("id")
            data = cq.get("data", "")
            from_user = cq.get("from", {})
            user_id = str(from_user.get("id"))
            first_name = from_user.get("first_name", "Trader")
            chat_id = cq.get("message", {}).get("chat", {}).get("id") or user_id

            # Answer callback popup
            send_telegram_request(self.token, "answerCallbackQuery", {
                "callback_query_id": cq_id,
                "text": "Linking Account..."
            })

            if data.startswith("link_"):
                account_id = data.replace("link_", "").lower()
                self._register_user_account(account_id, user_id, chat_id, first_name)

    def _send_onboarding_menu(self, chat_id):
        accounts_data = load_accounts()
        accounts = accounts_data.get("accounts", {})

        buttons = [
            [
                {"text": "👤 Himanshu", "callback_data": "link_himanshu"},
                {"text": "👤 Prateek", "callback_data": "link_prateek"}
            ],
            [
                {"text": "👤 Gautam", "callback_data": "link_gautam"},
                {"text": "⚡ F&O Options Desk", "callback_data": "link_fno"}
            ]
        ]

        payload = {
            "chat_id": chat_id,
            "text": (
                "👋 <b>Welcome to Day Trader Pro!</b>\n\n"
                "Please select your name below to link your <b>Paper Trading Account</b> and receive your personal live trade alerts:"
            ),
            "parse_mode": "HTML",
            "reply_markup": {
                "inline_keyboard": buttons
            }
        }
        send_telegram_request(self.token, "sendMessage", payload)

    def _register_user_account(self, account_id, user_id, chat_id, first_name):
        accounts_data = load_accounts()
        accounts = accounts_data.setdefault("accounts", {})

        target_name = "F&O Options Desk" if account_id == "fno" else account_id.capitalize()
        default_cap = 200000.0 if account_id == "fno" else 1000000.0
        acc = accounts.setdefault(account_id, {
            "id": account_id,
            "name": target_name,
            "capital": default_cap,
            "ledger_file": f"ledger_{account_id}.json" if account_id != "gautam" else "ledger.json",
            "risk_state_file": f"daily_risk_{account_id}.json" if account_id != "gautam" else "daily_risk_state.json"
        })

        acc["telegram_chat_id"] = str(chat_id)
        acc["status"] = "ACTIVE"
        acc["linked_at"] = time.strftime("%Y-%m-%d %H:%M:%S")

        save_accounts(accounts_data)

        # Send confirmation message to the user
        confirm_msg = (
            f"🎉 <b>Account Linked Successfully!</b>\n\n"
            f"Welcome, <b>{target_name}</b>!\n"
            f"Your Telegram ID (<code>{chat_id}</code>) has been linked to your private portfolio.\n\n"
            f"💰 <b>Paper Capital:</b> ₹{acc.get('capital', 1000000.0):,.2f}\n"
            f"📊 <b>Dashboard:</b> Isolated multi-account ledger\n"
            f"⚡ All your personal entries, trailing stop-losses, and profit targets will be sent right here!"
        )
        send_telegram_request(self.token, "sendMessage", {
            "chat_id": chat_id,
            "text": confirm_msg,
            "parse_mode": "HTML"
        })
        print(f"[ONBOARDING] Successfully registered {target_name} (Chat ID: {chat_id})")

if __name__ == "__main__":
    listener = TelegramOnboardingListener()
    listener.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        listener.stop()

