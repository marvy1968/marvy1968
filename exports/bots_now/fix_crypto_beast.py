import os
import re

bot_path = "/home/moonrider1968/bots/killer_beast_sniper/master_beast_v49.19_crypto_only.py"
API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

with open(bot_path, "r") as f:
    content = f.read()

# Inject keys globally at the top
header = f"\nAPI_KEY = "REDACTED"\nSECRET_KEY = "REDACTED"\n"
if "API_KEY =" not in content:
    # Insert after imports
    content = re.sub(r"(from .* import .*)", r"\1" + header, content, count=1)

with open(bot_path, "w") as f:
    f.write(content)
print(f"✅ Injected global keys into {bot_path}")
