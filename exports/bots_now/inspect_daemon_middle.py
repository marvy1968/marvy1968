with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "r") as f:
    lines = f.readlines()

print("--- Lines 90 to 240 ---")
for i, line in enumerate(lines[89:240]):
    print(f"{90+i:3d}: {line.rstrip()}")

print("\n--- Lines 240 to 342 ---")
for i, line in enumerate(lines[239:]):
    print(f"{240+i:3d}: {line.rstrip()}")
