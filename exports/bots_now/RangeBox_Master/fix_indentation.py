file_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

with open(file_path, "r") as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    # Look for the lines we injected
    if "regime, bias = assess_regime(df)" in line:
        # Ensure it has 16 spaces of indentation
        new_lines.append("                " + line.strip() + "\n")
    elif "if decide_action(regime, bias, \"BUY\")" in line:
        # Ensure it has 16 spaces of indentation
        new_lines.append("                " + line.strip() + "\n")
    else:
        new_lines.append(line)

with open(file_path, "w") as f:
    f.writelines(new_lines)

print("✅ Indentation repaired.")
