daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# Replace static or coarse client_order_id logic with millisecond-granularity unique IDs
import re

# Match client_order_id assignments and append microsecond timestamps
if 'client_order_id' in code:
    code = re.sub(
        r'client_order_id\s*=\s*f?["\'].*?["\']',
        'client_order_id=f"v4_{symbol}_{int(time.time() * 1000)}"',
        code
    )

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ Unique client_order_id patch applied successfully.")
