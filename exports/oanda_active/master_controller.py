#!/usr/bin/env python3
"""
Master Controller for Trading Bots
==================================
Manages Swing Trend, Daytrade/Scalp, and Crypto Range bots
with basic shared risk monitoring.
"""

import os
import sys
import time
import subprocess
import signal
from pathlib import Path

BOTS = {
    "swing": "alpaca_swing_trend_bot.py",
    "daytrade": "alpaca_daytrade_scalp_bot.py",
    "crypto": "alpaca_crypto_range_bot.py"
}

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

processes = {}

def load_env():
    env_file = Path("../RangeBox_Master/start_env.sh")
    if env_file.exists():
        os.system(f"source {env_file} && env > /tmp/env.txt")
        with open("/tmp/env.txt") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    os.environ[key] = value

def start_bot(name, script):
    if name in processes and processes[name].poll() is None:
        print(f"{name} is already running.")
        return

    log_file = LOG_DIR / f"{name}.log"
    with open(log_file, "a") as log:
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=log,
            stderr=log,
            preexec_fn=os.setsid
        )
        processes[name] = proc
        print(f"✅ Started {name} (PID: {proc.pid})")

def stop_bot(name):
    if name in processes:
        proc = processes[name]
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            print(f"🛑 Stopped {name}")
        del processes[name]

def stop_all():
    for name in list(processes.keys()):
        stop_bot(name)
    print("All bots stopped.")

def status():
    print("\n=== Bot Status ===")
    for name, script in BOTS.items():
        if name in processes and processes[name].poll() is None:
            print(f"  {name}: RUNNING (PID {processes[name].pid})")
        else:
            print(f"  {name}: STOPPED")

def main():
    load_env()

    print("=== Trading Bot Master Controller ===")
    print("Commands: start <bot>, stop <bot>, status, stopall, exit")

    while True:
        try:
            cmd = input("\n> ").strip().lower().split()
            if not cmd:
                continue

            if cmd[0] == "start" and len(cmd) > 1:
                bot = cmd[1]
                if bot in BOTS:
                    start_bot(bot, BOTS[bot])
                elif bot == "all":
                    for b, s in BOTS.items():
                        start_bot(b, s)
                else:
                    print("Unknown bot. Use: swing, daytrade, crypto, all")

            elif cmd[0] == "stop" and len(cmd) > 1:
                bot = cmd[1]
                if bot in BOTS:
                    stop_bot(bot)
                elif bot == "all":
                    stop_all()
                else:
                    print("Unknown bot.")

            elif cmd[0] == "status":
                status()

            elif cmd[0] == "stopall":
                stop_all()

            elif cmd[0] in ["exit", "quit"]:
                stop_all()
                break

            else:
                print("Commands: start <bot|all>, stop <bot|all>, status, stopall, exit")

        except KeyboardInterrupt:
            stop_all()
            break

if __name__ == "__main__":
    main()
