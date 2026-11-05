import os
import shutil

src = "/home/moonrider1968/bots/production_adaptive_governor_daemon.py"
dst = "/home/moonrider1968/bots/run_config_b_d3_integration.py"

if os.path.exists(src):
    shutil.copy(src, dst)
    print(f"Successfully copied validated Config B daemon to {dst}")
else:
    print(f"Source file {src} not found. Listing bots directory:")
    print(os.listdir("/home/moonrider1968/bots"))
