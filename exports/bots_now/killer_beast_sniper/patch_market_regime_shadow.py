import re

SHADOW_PATH = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(SHADOW_PATH, "r") as f:
    code = f.read()

# 1. Inject import statement for market_regime module
IMPORT_STMT = "from market_regime import detect_market_regime\n"

if "detect_market_regime" not in code:
    code = IMPORT_STMT + code

# 2. Add Regime Check Guardrail Logic
REGIME_CHECK_FUNCTION = '''
# ==============================================================================
# 🛡️ MARKET REGIME FILTER GUARDRAIL
# ==============================================================================
BREAKOUT_STRATEGIES = {"RANGE_BREAKOUT", "MICRO_MOMENTUM", "VELEZ_20EMA"}

def is_signal_allowed_by_regime(symbol, strategy_name, candles):
    """
    Evaluates market regime using ADX & Choppiness Index.
    Blocks BREAKOUT/TREND signals if regime is RANGING.
    """
    if len(candles) < 28: # Minimum lookback needed for ADX/CHOP
        return True, "NEUTRAL"

    regime, adx, chop = detect_market_regime(candles)

    # Block trend & breakout setups during choppy/ranging periods
    if strategy_name in BREAKOUT_STRATEGIES and regime == "RANGING":
        print(f"⛔ [REGIME BLOCKED] {symbol:<8} | Strategy: {strategy_name:<15} | Reason: Market is RANGING (ADX: {adx:.1f}, CHOP: {chop:.1f})")
        return False, regime

    return True, regime
# ==============================================================================
'''

if "is_signal_allowed_by_regime" not in code:
    code = REGIME_CHECK_FUNCTION + "\n" + code
    with open(SHADOW_PATH, "w") as f:
        f.write(code)
    print("✅ Injected is_signal_allowed_by_regime guardrail into multi_scalper_shadow.py!")
else:
    print("ℹ️ Regime check guardrail function already present.")

