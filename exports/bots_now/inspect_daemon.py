with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "r") as f:
    lines = f.readlines()

print(f"Total lines in production daemon: {len(lines)}")
for i, line in enumerate(lines[:40]):
    print(f"{i+1:2d}: {line.rstrip()}")
