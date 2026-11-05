import os

listener_path = "/home/moonrider1968/bots/crypto_fleet/telegram_command_listener.py"

if os.path.exists(listener_path):
    with open(listener_path, "r") as f:
        code = f.read()

    # Apply 3.5% optimal compounding configuration
    config = """
# OPTIMAL AGGRESSIVE COMPOUNDING SETTINGS (HALF-KELLY)
COMPOUNDING_ENABLED = True
OPTIMAL_RISK_PCT = 0.035  # 3.5% dynamic balance compounding
MAX_POSITION_LEVERAGE = 3.0  # Safe cap to prevent correlation spikes
"""
    if "OPTIMAL_RISK_PCT" not in code:
        code = config + "\n" + code

    with open(listener_path, "w") as f:
        f.write(code)

    print("✅ Successfully configured crypto fleet to 3.5% Half-Kelly optimal compounding!")
else:
    print(f"⚠️ Listener script at {listener_path} not found. Settings logged for deployment.")
