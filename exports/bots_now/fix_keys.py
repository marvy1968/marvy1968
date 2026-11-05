import os

bots = [
    "/home/moonrider1968/bots/killer_beast_sniper/stock_beast_v1.py",
    "/home/moonrider1968/bots/killer_beast_sniper/master_beast_v49.19_crypto_only.py"
]

API_KEY = "REDACTED"
API_SECRET = "REDACTED"

for bot in bots:
    with open(bot, "r") as f:
        content = f.read()
    
    # Replace the connection string pattern (common Alpaca pattern)
    # We look for where the API keys are usually defined
    new_content = content.replace("os.environ.get('APCA_API_KEY')", f"'{API_KEY}'")
    new_content = new_content.replace("os.environ.get('APCA_API_SECRET')", f"'{API_SECRET}'")
    
    with open(bot, "w") as f:
        f.write(new_content)
    print(f"✅ Credentials hard-coded into: {bot}")
