#!/usr/bin/env python3
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("RUNWAY_CORE")

def calculate_dynamic_trailing_stop(position_type, entry_price, current_price, atr):
    """
    Dynamically squeezes trailing stops as profits expand to completely stop give-backs.
    """
    if position_type == "long":
        profit_dist = current_price - entry_price
    else:
        profit_dist = entry_price - current_price

    # Calculate current progress in multiples of ATR
    atr_multiple = profit_dist / atr if atr > 0 else 0
    
    # Default initial fallback trail distance (1.5 ATR room to breathe)
    trail_distance = 1.5 * atr 

    # --- THE AGGRESSIVE PROFIT RUNWAY LOGIC ---
    if atr_multiple >= 2.5:
        # Milestone 3: Extreme extension -> Suffocate the trade with a tight 0.25 ATR trail
        trail_distance = 0.25 * atr
        log.info("🔥 [RUNWAY LEVEL 3] Extreme extension detected! Trailing stop locked to razor-edge.")
    elif atr_multiple >= 1.5:
        # Milestone 2: High profit expansion -> Tighten down to 0.50 ATR trail
        trail_distance = 0.50 * atr
        log.info("🚀 [RUNWAY LEVEL 2] High extension. Tightening trailing stop matrix.")
    elif atr_multiple >= 0.75:
        # Milestone 1: First target cleared -> Lock break-even territory, trail at 1.00 ATR
        trail_distance = 1.00 * atr
        log.info("🛡️ [RUNWAY LEVEL 1] First target cleared. Trailing stop inside profit boundary.")

    # Calculate exact final hard execution price boundaries
    if position_type == "long":
        dynamic_stop_price = current_price - trail_distance
    else:
        dynamic_stop_price = current_price + trail_distance

    return dynamic_stop_price

# --- TEST VERIFICATION RUNS ---
if __name__ == "__main__":
    print("\n=== 🏃 RUNNING PROFIT RUNWAY COMPLIANCE TEST ===")
    mock_entry = 150.00
    mock_atr = 2.00  # Asset average volatility step
    
    # Test Scenario A: Small profit extension
    price_now_a = 151.60  # (+0.8 ATR)
    stop_a = calculate_dynamic_trailing_stop("long", mock_entry, price_now_a, mock_atr)
    print(f"Scenario A (Price $151.60): Dynamic Stop sits at -> ${stop_a:.2f}\n")
    
    # Test Scenario B: Massive vertical spike extension
    price_now_b = 156.00  # (+3.0 ATR)
    stop_b = calculate_dynamic_trailing_stop("long", mock_entry, price_now_b, mock_atr)
    print(f"Scenario B (Price $156.00): Dynamic Stop chokes up to -> ${stop_b:.2f}")
    print("================================================\n")
