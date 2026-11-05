import os

bots = {
    "/home/moonrider1968/bots/killer_beast_sniper/stock_beast_v1.py": "STOCK",
    "/home/moonrider1968/bots/killer_beast_sniper/master_beast_v49.19_crypto_only.py": "CRYPTO"
}

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"
BASE_URL = "https://paper-api.alpaca.markets"

for bot_path, bot_type in bots.items():
    if os.path.exists(bot_path):
        with open(bot_path, "r") as f:
            lines = f.readlines()

        new_lines = []
        for line in lines:
            if "TradingClient(" in line:
                new_lines.append(f"api = TradingClient('{API_KEY}', '{SECRET_KEY}', paper=True, base_url='{BASE_URL}')\n")
            elif "API_KEY = "REDACTED"SECRET_KEY =" in line:
                continue # Skip old key definitions
            else:
                new_lines.append(line)
        
        # Ensure imports at top
        final_code = "".join(new_lines)
        if "from alpaca.trading.client import TradingClient" not in final_code:
            final_code = "from alpaca.trading.client import TradingClient\n" + final_code
            
        with open(bot_path, "w") as f:
            f.write(final_code)
        print(f"✅ Rewrote {bot_type} Bot connection logic.")
