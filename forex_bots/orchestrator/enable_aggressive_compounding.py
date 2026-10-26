import re

file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# Replace risk sizing logic with Aggressive Compounding parameters
old_risk_logic = """        abs_score = abs(confidence_score)
        if abs_score >= 5:
            risk_pct = 0.0100  # 1.0% Max Risk per High-Conviction Trade
        elif abs_score >= 3:
            risk_pct = 0.0075  # 0.75% Risk
        else:
            risk_pct = 0.0040  # 0.40% Risk"""

new_risk_logic = """        abs_score = abs(confidence_score)
        # AGGRESSIVE COMPOUNDING ENGINE: Reinvests NAV floating gains directly into unit sizing
        if abs_score >= 5:
            risk_pct = 0.0250  # 2.50% Equity Risk on High-Conviction Alignment
        elif abs_score >= 3:
            risk_pct = 0.0175  # 1.75% Equity Risk on Moderate Alignment
        else:
            risk_pct = 0.0100  # 1.00% Baseline Equity Risk"""

if "2.50% Equity Risk" not in code:
    code = code.replace(old_risk_logic, new_risk_logic)

# Increase position ceiling max units cap from 35,000 to 150,000 for aggressive compounding
code = code.replace("min(units, 35000)", "min(units, 150000)")

with open(file_path, "w") as f:
    f.write(code)

print("🚀 Aggressive Compounding Engine successfully installed in deploy_brain_fleet.py!")
