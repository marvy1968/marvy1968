import json

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

# Read file lines
with open(EXECUTOR_PATH, "r") as f:
    lines = f.readlines()

# 1. New dictionary definitions
STOCK_OPTIMIZATION_MATRIX = {
    "TSLA": {"RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "VELEZ_20EMA": {"sl_pct": 0.0003, "tp_pct": 0.0003}},
    "AMD":  {"RANGE_BREAKOUT": {"sl_pct": 0.0005, "tp_pct": 0.0006}, "VELEZ_20EMA": {"sl_pct": 0.0004, "tp_pct": 0.0004}},
    "NVDA": {"MICRO_MOMENTUM": {"sl_pct": 0.0006, "tp_pct": 0.0006}, "RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}},
    "AAPL": {"VELEZ_20EMA": {"sl_pct": 0.0002, "tp_pct": 0.0002}, "RANGE_BREAKOUT": {"sl_pct": 0.0002, "tp_pct": 0.0002}},
    "MSFT": {"MICRO_MOMENTUM": {"sl_pct": 0.0003, "tp_pct": 0.0004}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0004}},
    "AMZN": {"MICRO_MOMENTUM": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0003}},
    "SPY":  {"RANGE_BREAKOUT": {"sl_pct": 0.0001, "tp_pct": 0.0001}, "VELEZ_20EMA": {"sl_pct": 0.0001, "tp_pct": 0.0001}},
    "QQQ":  {"RANGE_BREAKOUT": {"sl_pct": 0.0001, "tp_pct": 0.0001}, "VOL_SQUEEZE":  {"sl_pct": 0.0002, "tp_pct": 0.0002}},
    "META": {"MICRO_MOMENTUM": {"sl_pct": 0.0004, "tp_pct": 0.0005}, "RANGE_BREAKOUT": {"sl_pct": 0.0003, "tp_pct": 0.0004}},
    "NFLX": {"MICRO_MOMENTUM": {"sl_pct": 0.0005, "tp_pct": 0.0006}, "RANGE_BREAKOUT": {"sl_pct": 0.0004, "tp_pct": 0.0005}}
}

KELLY_RISK_MATRIX = {
    ("AAPL", "MICRO_MOMENTUM", "BUY"): 0.020,
    ("AAPL", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "BUY"): 0.025,
    ("AAPL", "VOL_SQUEEZE", "SELL"): 0.025,
    ("AMD", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMD", "VELEZ_20EMA", "SELL"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("AMZN", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("AMZN", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("META", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("META", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("META", "RANGE_BREAKOUT", "BUY"): 0.020,
    ("MSFT", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("MSFT", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("MSFT", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("MSFT", "VOL_SQUEEZE", "SELL"): 0.025,
    ("NFLX", "MICRO_MOMENTUM", "BUY"): 0.015,
    ("NFLX", "RANGE_BREAKOUT", "BUY"): 0.015,
    ("NVDA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("NVDA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("NVDA", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("QQQ", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("QQQ", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "BUY"): 0.025,
    ("QQQ", "VOL_SQUEEZE", "SELL"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("SPY", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("SPY", "RANGE_BREAKOUT", "SELL"): 0.025,
    ("SPY", "VELEZ_20EMA", "BUY"): 0.025,
    ("SPY", "VELEZ_20EMA", "SELL"): 0.025,
    ("SPY", "VOL_SQUEEZE", "BUY"): 0.025,
    ("SPY", "VOL_SQUEEZE", "SELL"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "BUY"): 0.025,
    ("TSLA", "MICRO_MOMENTUM", "SELL"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "BUY"): 0.025,
    ("TSLA", "RANGE_BREAKOUT", "SELL"): 0.025,
}

# 2. Rebuild cleanly formatted string blocks
opt_block = "STOCK_OPTIMIZATION_MATRIX = " + json.dumps(STOCK_OPTIMIZATION_MATRIX, indent=4) + "\n\n"

kelly_lines = ["KELLY_RISK_MATRIX = {\n"]
for (ticker, strat, side), risk in sorted(KELLY_RISK_MATRIX.items()):
    kelly_lines.append(f'    ("{ticker}", "{strat}", "{side}"): {risk},\n')
kelly_lines.append("}\n\n")
kelly_block = "".join(kelly_lines)

# 3. Locate matrix bounds and replace content
full_text = "".join(lines)
opt_start = full_text.find("STOCK_OPTIMIZATION_MATRIX =")
kelly_start = full_text.find("KELLY_RISK_MATRIX =")
log_start = full_text.find("LOG_FILE =")

prefix = full_text[:opt_start]
suffix = full_text[log_start:]

new_content = prefix + opt_block + kelly_block + suffix

with open(EXECUTOR_PATH, "w") as f:
    f.write(new_content)

print("✅ Successfully repaired alpaca_paper_executor.py with clean matrix updates!")
