import subprocess

print("=== 1. Grep analysis of baseline simulation structure ===")
subprocess.run([
    "grep", "-nE",
    "^def run_adaptive_simulation|^eq_df,|run_adaptive_simulation\(|open_longs|open_shorts|daily_records|transition_log|return eq_df",
    "/home/moonrider1968/bots/run_config_b_d3_integration_BASELINE_COPY.py"
])

print("\n=== 2. Hashing verification ===")
subprocess.run([
    "sha256sum",
    "/home/moonrider1968/bots/run_config_b_d3_integration.py",
    "/home/moonrider1968/bots/run_config_b_d3_integration_BASELINE_COPY.py"
])
