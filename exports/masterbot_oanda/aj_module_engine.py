#!/usr/bin/env python3
import subprocess, time, sys, os
BOT_DIR = "/home/moonrider1968/bots/oanda_active/MarchEdge"
VENV_PYTHON = f"{BOT_DIR}/venv/bin/python3"
CONFIDENCE_FLOOR = 85.0
MODULES = ['basketball_team_props_master.py','nba_team_props.py','wnba_team_props.py','mlb_team_props.py','nfl_team_props.py','ncaaf_team_props.py','middles_engine.py','line_shopper.py','daily_pl_summary.py']
def launch_aj_system():
    processes = {}
    print(f"🚀 [AJ ELITE] FULL MarchEdge launch — STRICT 85%+ Floor (NBA+WNBA+MLB+NFL+NCAAF)")
    os.chdir(BOT_DIR)
    for i, script in enumerate(MODULES):
        if i > 0: time.sleep(5)
        try:
            p = subprocess.Popen([VENV_PYTHON, script, str(CONFIDENCE_FLOOR)])
            processes[script] = p
            print(f"✅ Launched: {script} (PID: {p.pid})")
        except Exception as e:
            print(f"❌ Error launching {script}: {e}")
    return processes
def monitor_aj_health(processes):
    print("🛡️ [AJ MONITOR] All soldiers under watch. Recovery mode active (85%+ only).")
    last_heartbeat = 0
    while True:
        if time.time() - last_heartbeat > 3600:
            print("💓 [AJ HEARTBEAT] All systems nominal.")
            last_heartbeat = time.time()
        for script, p in list(processes.items()):
            if p.poll() is not None:
                print(f"⚠️ [RESTART] {script} crashed → rebooting")
                processes[script] = subprocess.Popen([VENV_PYTHON, script, str(CONFIDENCE_FLOOR)])
        time.sleep(300)
if __name__ == "__main__":
    active = launch_aj_system()
    monitor_aj_health(active)
