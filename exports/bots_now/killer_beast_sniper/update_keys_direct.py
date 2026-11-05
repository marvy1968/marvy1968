import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# Paste your EXACT Alpaca Paper credentials here
FULL_API_KEY = "REDACTED"
FULL_SECRET_KEY = "REDACTED"

with open(EXECUTOR_PATH, "r") as f:
    content = f.read()

# Overwrite header block directly
old_headers = """HEADERS = {
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
}"""

new_headers = f"""ALPACA_API_KEY = "REDACTED"
ALPACA_SECRET_KEY = "REDACTED"

HEADERS = {{
    "APCA-API-KEY-ID": ALPACA_API_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    "Content-Type": "application/json"
}}"""

content = re.sub(r'HEADERS = \{.*?\}', new_headers, content, flags=re.DOTALL)

with open(EXECUTOR_PATH, "w") as f:
    f.write(content)

print("✅ Directly injected verified API keys into alpaca_paper_executor.py")
