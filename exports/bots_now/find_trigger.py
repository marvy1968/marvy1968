import subprocess, os

print("--- 1. CURRENT USER CRONTAB ---")
try:
    print(subprocess.check_output(["crontab", "-l"]).decode())
except Exception as e:
    print(e)

print("\n--- 2. SYSTEMD TIMERS ---")
try:
    print(subprocess.check_output(["systemctl", "list-timers", "--no-pager"]).decode())
except Exception as e:
    print(e)

print("\n--- 3. SCRIPTS CALLING CLOSE_ALL ---")
try:
    res = subprocess.check_output(["grep", "-rn", "close_all_positions", "/home/moonrider1968/bots/"]).decode()
    print(res if res else "None found.")
except Exception as e:
    print(e)
