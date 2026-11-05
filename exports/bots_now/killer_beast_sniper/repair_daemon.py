import re

daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# Add uuid import if missing
if "import uuid" not in code:
    code = "import uuid\n" + code

# Replace any client_order_id assignment with a unique UUID string
# Matches: client_order_id=... or client_order_id = ...
code = re.sub(
    r'client_order_id\s*=\s*[^,\n\)]+',
    'client_order_id=f"v4_{symbol}_{uuid.uuid4().hex[:8]}"',
    code
)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ Applied UUID order-id repair to velez_stage4_short_daemon.py")
