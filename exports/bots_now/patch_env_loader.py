import re
from pathlib import Path

env_file = Path("/home/moonrider1968/bots/.env")
bot_file = Path("/home/moonrider1968/bots/oanda_practice_bot_c1.py")

code = bot_file.read_text()

# Replace the plain os.environ lookups with a robust local .env reader
env_loader_block = """# Load environment variables from .env file if present
def load_env_file(filepath="/home/moonrider1968/bots/.env"):
    p = Path(filepath)
    if not p.exists():
        return
    with open(p) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k not in os.environ:
                os.environ[k] = v

load_env_file()

OANDA_API_KEY = os.environ.get("OANDA_API_TOKEN") or os.environ.get("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.environ.get("OANDA_ACCOUNT_ID", "")
OANDA_URL = os.environ.get("OANDA_URL", "https://api-fxpractice.oanda.com")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
"""

target = 'OANDA_API_KEY = os.environ.get("OANDA_PRACTICE_API_KEY", "")\nOANDA_ACCOUNT_ID = os.environ.get("OANDA_PRACTICE_ACCOUNT_ID", "")\nOANDA_URL = "https://api-fxpractice.oanda.com"'

if target in code:
    code = code.replace(target, env_loader_block)
    bot_file.write_text(code)
    print("Successfully patched oanda_practice_bot_c1.py to source /home/moonrider1968/bots/.env")
else:
    print("Could not find target block; let's inspect the top of the file.")
