#!/usr/bin/env python3
"""
Master runner script:
Launches both the Trading Bot (multi_live_trader.py) and Dashboard Web Server (dashboard/server.py).
Handles clean shutdown on Ctrl+C.
"""

import sys
import subprocess
import time
from pathlib import Path

# Fix Windows console unicode handling
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent

def run_all():
    python_exe = sys.executable
    processes = []

    print("=" * 65)
    print("[START] LAUNCHING DAY TRADING SYSTEM & WEB DASHBOARD")
    print("=" * 65)

    try:
        # 1. Start Dashboard Server
        dashboard_script = BASE_DIR / "dashboard" / "server.py"
        print(f"[*] Starting Web Dashboard on http://localhost:8080 ...")
        dash_proc = subprocess.Popen(
            [python_exe, str(dashboard_script), "8080"],
            cwd=str(BASE_DIR)
        )
        processes.append(("Dashboard Server", dash_proc))

        time.sleep(1)

        # 2. Start Live Trader Engine (Cash Equity)
        trader_script = BASE_DIR / "trader" / "multi_live_trader.py"
        print(f"[*] Starting Multi-Symbol Live Trader Engine ...")
        trader_proc = subprocess.Popen(
            [python_exe, str(trader_script)],
            cwd=str(BASE_DIR)
        )
        processes.append(("Live Trader", trader_proc))

        time.sleep(1)

        # 3. Start F&O Options Trader Engine (NIFTY & BANKNIFTY)
        fno_script = BASE_DIR / "trader" / "fno_trader.py"
        print(f"[*] Starting F&O Options Desk Engine ...")
        fno_proc = subprocess.Popen(
            [python_exe, str(fno_script)],
            cwd=str(BASE_DIR)
        )
        processes.append(("F&O Options Trader", fno_proc))

        time.sleep(1)

        # 4. Start Telegram Interactive Onboarding Listener
        onboard_script = BASE_DIR / "trader-alerts" / "telegram_onboarding.py"
        print(f"[*] Starting Telegram Multi-User Onboarding Listener ...")
        onboard_proc = subprocess.Popen(
            [python_exe, str(onboard_script)],
            cwd=str(BASE_DIR)
        )
        processes.append(("Telegram Onboarding", onboard_proc))

        print("\n" + "=" * 65)
        print("[READY] All services running. Press Ctrl+C to terminate.")
        print("=" * 65 + "\n")

        # Monitor processes
        while True:
            for item in list(processes):
                name, proc = item
                poll = proc.poll()
                if poll is not None:
                    if name in ("Live Trader", "F&O Options Trader") and poll == 0:
                        print(f"\n[INFO] {name} completed trading session for today.")
                        processes.remove(item)
                    else:
                        print(f"\n[ALERT] {name} exited with code {poll}.")
                        return
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[STOP] Shutdown signal received. Stopping all processes...")
    finally:
        for name, proc in processes:
            if proc.poll() is None:
                print(f"[*] Terminating {name} (PID: {proc.pid})...")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        print("[DONE] All processes stopped.")

if __name__ == "__main__":
    run_all()
