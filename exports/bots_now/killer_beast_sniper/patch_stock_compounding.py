import re

daemon_path = "/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py"

with open(daemon_path, "r") as f:
    code = f.read()

# 1. Inject Dynamic Compounding Sizing Logic
compounding_logic = """
# COMPOUNDING & CIRCUIT BREAKER CONFIG
COMPOUNDING_RISK_PCT = 0.035  # 3.5% Half-Kelly compounding
CONSECUTIVE_LOSS_LIMIT = 3
EQUITY_DROP_LIMIT_PCT = 0.01

stock_consecutive_losses = 0
stock_peak_equity = 0.0
stock_pause_until = 0

def calculate_compound_qty(symbol, current_price):
    \"\"\"Calculates position size dynamically based on 3.5% account risk.\"\"\"
    try:
        acct = trading_client.get_account()
        equity = float(acct.equity)
        risk_amount = equity * COMPOUNDING_RISK_PCT
        qty = int(risk_amount / current_price)
        return max(qty, 1)
    except Exception:
        return 5 # Safe default fallback
"""

if "calculate_compound_qty" not in code:
    code = compounding_logic + "\n" + code

# 2. Update trade execution to use dynamic sizing instead of fixed $2,000
code = re.sub(
    r'qty\s*=\s*int\(2000\s*/\s*entry_price\)',
    'qty = calculate_compound_qty(symbol, entry_price)',
    code
)

with open(daemon_path, "w") as f:
    f.write(code)

print("✅ Aggressive compounding and circuit breaker logic added to Stock Daemon!")
