import os
from datetime import datetime, timezone, timedelta

file_name = "all_engines_live.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# Logic to skip games too far in the future
filter_logic = """
        # --- HORIZON FILTER ---
        commence_str = event.get("commence_time")
        if commence_str:
            commence_dt = datetime.fromisoformat(commence_str.replace("Z", "+00:00"))
            hours_until = (commence_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            if hours_until > 12:
                continue # Skip games more than 12 hours away
        # ----------------------
"""

# We insert this filter right after the loop starts for events
if "for event in events:" in content:
    content = content.replace("for event in events:", "for event in events:" + filter_logic)
    
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: The Horizon Filter is live. Future clutter removed!")
else:
    print("⚠️ FAILED: Could not find the event loop.")
