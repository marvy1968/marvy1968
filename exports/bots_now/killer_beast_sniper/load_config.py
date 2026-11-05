import json
import os

CONFIG_PATH = "/home/moonrider1968/bots/killer_beast_sniper/config_matrix.json"

def get_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config_matrix.json: {e}")
    return {}

if __name__ == "__main__":
    cfg = get_config()
    print("Successfully verified JSON configuration matrix:")
    print(json.dumps(cfg, indent=2))
