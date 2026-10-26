import time
import os

def run_desk():
    print("🛡️ [MASTER ORCHESTRATOR ONLINE]")
    # Define absolute paths clearly
    RANGE_ENGINE = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"
    VELEZ_ENGINE = "/home/moonrider1968/bots/RangeBox_Master/oanda_velez_beast.py"
    PYTHON = "/home/moonrider1968/botenv/bin/python3"
    
    while True:
        adx_value = 22
        if adx_value < 20:
            print("🔄 [MODE] Running Range Engine")
            os.system(f"{PYTHON} {RANGE_ENGINE} > /home/moonrider1968/bots/RangeBox_Master/logs/range.log 2>&1 &")
        else:
            print("📈 [MODE] Running Velez Engine")
            os.system(f"{PYTHON} {VELEZ_ENGINE} > /home/moonrider1968/bots/RangeBox_Master/logs/velez.log 2>&1 &")
            
        time.sleep(300)

if __name__ == "__main__":
    run_desk()
