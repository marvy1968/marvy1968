import os

bots = [
    "/home/moonrider1968/bots/killer_beast_sniper/stock_beast_v1.py",
    "/home/moonrider1968/bots/killer_beast_sniper/master_beast_v49.19_crypto_only.py"
]

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

for bot_path in bots:
    if os.path.exists(bot_path):
        with open(bot_path, "r") as f:
            content = f.read()

        # Remove the broken 'base_url' line and restore simple initialization
        new_content = content.replace("api = TradingClient", "# api removed")
        # Add back the clean initialization
        new_content += f"\napi = TradingClient('{API_KEY}', '{SECRET_KEY}', paper=True)\n"
        
        with open(bot_path, "w") as f:
            f.write(new_content)
        print(f"✅ Fixed {bot_path}")
