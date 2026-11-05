import re
from pathlib import Path

fpath = Path("/home/moonrider1968/bots/run_h4_filter_sweep.py")
code = fpath.read_text()

# Ensure signal scan starts at index 105 uniformly
code = code.replace("for i in range(205, len(df) - 5):", "for i in range(105, len(df) - 5):")
# Handle NaN slope safely for indices < 205
code = code.replace("slope_pass = cur[\"ma200_slope\"] >= 0.0", "slope_pass = bool(cur[\"ma200_slope\"] >= 0.0) if pd.notna(cur[\"ma200_slope\"]) else False")
code = code.replace("slope_pass = cur[\"ma200_slope\"] <= 0.0", "slope_pass = bool(cur[\"ma200_slope\"] <= 0.0) if pd.notna(cur[\"ma200_slope\"]) else False")

fpath.write_text(code)
print("✅ run_h4_filter_sweep.py patched to 105-bar warmup parity.")
