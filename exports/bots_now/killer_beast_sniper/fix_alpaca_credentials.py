import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"  # Replace with full secret string if truncated

with open(EXECUTOR_PATH, "r") as f:
    content = f.read()

# Update key fallbacks and check both APCA_ and ALPACA_ environment variable names
content = re.sub(
    r'ALPACA_API_KEY = os\.getenv\("ALPACA_API_KEY", ".*?"\)',
    f'ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", os.getenv("APCA_API_KEY_ID", "{API_KEY}"))',
    content
)
content = re.sub(
    r'ALPACA_SECRET_KEY = os\.getenv\("ALPACA_SECRET_KEY", ".*?"\)',
    f'ALPACA_SECRET_KEY = os.getenv("ALPACA_SECRET_KEY", os.getenv("APCA_API_SECRET_KEY", "{SECRET_KEY}"))',
    content
)

with open(EXECUTOR_PATH, "w") as f:
    f.write(content)

print("✅ Updated Alpaca API credentials in alpaca_paper_executor.py")
