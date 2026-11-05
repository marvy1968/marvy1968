EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# --- YOUR ACTUAL ALPACA PAPER KEYS ---
REAL_API_KEY = "REDACTED"
REAL_SECRET_KEY = "REDACTED"

with open(EXECUTOR_PATH, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if line.startswith('ALPACA_API_KEY ='):
        new_lines.append(f'ALPACA_API_KEY = "REDACTED"\n')
    elif line.startswith('ALPACA_SECRET_KEY ='):
        new_lines.append(f'ALPACA_SECRET_KEY = "REDACTED"\n')
    else:
        new_lines.append(line)

with open(EXECUTOR_PATH, "w") as f:
    f.writelines(new_lines)

print("✅ Hardcoded active Alpaca keys into alpaca_paper_executor.py")
