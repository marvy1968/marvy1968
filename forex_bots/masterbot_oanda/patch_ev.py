import os

file_name = "bet_intelligence.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

old_code = """def _ev(probability: float, decimal_odds: float) -> float:
    b = max(0.0, decimal_odds - 1.0)
    p = _clamp(probability, 0.0, 1.0)
    q = 1.0 - p
    return (p * b) - q"""

new_code = """def _ev(probability: float, decimal_odds: float) -> float:
    if decimal_odds <= 1.01:
        decimal_odds = 1.9091
    b = max(0.0, decimal_odds - 1.0)
    p = _clamp(probability, 0.0, 1.0)
    q = 1.0 - p
    return (p * b) - q"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: EV Math patched! Missing odds will now default to -110.")
else:
    print("⚠️ FAILED: Could not find the exact code block. Let me know if you see this!")
