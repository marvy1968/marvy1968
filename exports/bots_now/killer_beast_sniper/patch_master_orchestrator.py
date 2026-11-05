import re

SHADOW_PATH = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(SHADOW_PATH, "r") as f:
    code = f.read()

# 1. Inject Master Orchestrator Import
IMPORT_STMT = "from master_expert_orchestrator import evaluate_all_experts\n"

if "evaluate_all_experts" not in code:
    code = IMPORT_STMT + code

# 2. Inject Unified 10-Expert Handler Logic
ORCHESTRATOR_BRIDGE = '''
# ==============================================================================
# 🤖 UNIFIED 10-EXPERT ORCHESTRATOR ENGINE
# ==============================================================================
def process_tick_with_master_experts(symbol, candles, timeframe="1m", asset_class="STOCKS"):
    """
    Evaluates all 10 Scalper & Swing Experts simultaneously and dispatches
    qualified signals to execution bridges.
    """
    eval_result = evaluate_all_experts(symbol, candles, timeframe=timeframe)
    if not eval_result or not eval_result.get("signals"):
        return

    price = eval_result["price"]
    regime = eval_result["regime"]
    signals = eval_result["signals"]

    for strat_name, side, category in signals:
        # Check regime filter guardrail
        allowed, _ = is_signal_allowed_by_regime(symbol, strat_name, candles)
        if not allowed:
            continue

        print(f"🧪 [MASTER 10-EXPERT MATCH] {symbol:<8} | Class: {asset_class:<6} | Strat: {strat_name:<20} | Type: {category:<7} | Side: {side:<4} | Price: {price}")

        # Route Forex signals to OANDA Bridge
        if asset_class == "FOREX" or symbol in FOREX_SYMBOLS:
            route_forex_signal_to_oanda(symbol, side, price, strat_name)
        # Route Stock & Crypto signals to Alpaca Execution Engine
        else:
            execute_dynamic_kelly_order(symbol, side, price, strat_name)
# ==============================================================================
'''

if "process_tick_with_master_experts" not in code:
    code = ORCHESTRATOR_BRIDGE + "\n" + code
    with open(SHADOW_PATH, "w") as f:
        f.write(code)
    print("✅ Injected process_tick_with_master_experts into multi_scalper_shadow.py!")
else:
    print("ℹ️ Master orchestrator bridge already present.")

