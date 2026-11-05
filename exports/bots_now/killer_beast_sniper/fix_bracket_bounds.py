import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

with open(EXECUTOR_PATH, "r") as f:
    content = f.read()

# Improved bracket calculation with price-floor safety
new_calc_func = """def calculate_bracket_params(ticker, strategy, price, side):
    rules = STOCK_OPTIMIZATION_MATRIX.get(ticker, {}).get(
        strategy, {"sl_pct": 0.0005, "tp_pct": 0.0006}
    )
    sl_pct = max(rules["sl_pct"], 0.0005) # Enforce 0.05% minimum to clear bid/ask spread
    tp_pct = max(rules["tp_pct"], 0.0005)

    sl_dist = max(round(price * sl_pct, 2), 0.05) # Minimum $0.05 stop offset
    tp_dist = max(round(price * tp_pct, 2), 0.05)

    if side.upper() == "BUY":
        stop_price = round(price - sl_dist, 2)
        limit_price = round(price + tp_dist, 2)
    else:
        stop_price = round(price + sl_dist, 2)
        limit_price = round(price - tp_dist, 2)

    return stop_price, limit_price, sl_pct, tp_pct"""

content = re.sub(r'def calculate_bracket_params\(.*?\):.*?(?=def submit_alpaca_bracket_order)', new_calc_func + "\n\n", content, flags=re.DOTALL)

with open(EXECUTOR_PATH, "w") as f:
    f.write(content)

print("✅ Patched calculate_bracket_params with spread floor & dynamic safety offsets.")
