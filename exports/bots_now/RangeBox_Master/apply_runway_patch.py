#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"

if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # 1. Define the trailing function code block
    runway_function_code = """
    def calculate_dynamic_trailing_stop(self, side: str, entry_price: float, current_price: float, atr: float) -> float:
        \"\"\"Dynamically tightens trailing stop distance as profit expands to minimize give-back.\"\"\"
        profit_dist = (current_price - entry_price) if side.upper() == "BUY" else (entry_price - current_price)
        atr_multiple = profit_dist / atr if atr > 0 else 0
        trail_distance = 1.5 * atr 

        if atr_multiple >= 2.5:
            trail_distance = 0.25 * atr
        elif atr_multiple >= 1.5:
            trail_distance = 0.50 * atr
        elif atr_multiple >= 0.75:
            trail_distance = 1.00 * atr

        return (current_price - trail_distance) if side.upper() == "BUY" else (current_price + trail_distance)
"""

    # Inject the calculation function into the class
    if "def calculate_dynamic_trailing_stop" not in code:
        code = code.replace("class OandaLiveBeast:", "class OandaLiveBeast:" + runway_function_code)

    # 2. Define the new target check loop block
    old_target_loop = """                # --- ASYMMETRIC VIRTUAL TARGET SCANNER ---
                retaining_positions = []
                for pos in self.local_positions[symbol]:
                    entry = pos['entry']
                    closed = False
                    
                    if pos['dir'] == 'BUY':
                        if price >= entry + 0.0018 or (pos['type'] == 'RANGE' and price >= row['bb_mid']):
                            log.info(f"💰 [LIVE TP CLEAR] {symbol} long target cleared at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "SELL")
                            closed = True
                    elif pos['dir'] == 'SELL':
                        if price <= entry - 0.0018 or (pos['type'] == 'RANGE' and price <= row['bb_mid']):
                            log.info(f"💰 [LIVE TP CLEAR] {symbol} short target cleared at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "BUY")
                            closed = True"""

    new_runway_loop = """                # --- ASYMMETRIC VIRTUAL TARGET SCANNER & RUNWAY MATRIX ---
                retaining_positions = []
                for pos in self.local_positions[symbol]:
                    entry = pos['entry']
                    closed = False
                    
                    # Compute the aggressive volatility runway trailing stop boundary
                    dynamic_stop = self.calculate_dynamic_trailing_stop(pos['dir'], entry, price, row['atr'])
                    
                    if pos['dir'] == 'BUY':
                        if price >= entry + 0.0018 or (pos['type'] == 'RANGE' and price >= row['bb_mid']):
                            log.info(f"💰 [LIVE TP CLEAR] {symbol} long target cleared at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "SELL")
                            closed = True
                        elif price <= dynamic_stop:
                            log.info(f"🚨 [RUNWAY TRIGGER] Squeezed trailing floor hit for {symbol} at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "SELL")
                            closed = True
                    elif pos['dir'] == 'SELL':
                        if price <= entry - 0.0018 or (pos['type'] == 'RANGE' and price <= row['bb_mid']):
                            log.info(f"💰 [LIVE TP CLEAR] {symbol} short target cleared at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "BUY")
                            closed = True
                        elif price >= dynamic_stop:
                            log.info(f"🚨 [RUNWAY TRIGGER] Squeezed trailing ceiling hit for {symbol} at {price:.5f}. Liquidating position.")
                            self.submit_live_order(symbol, pos['units'], "BUY")
                            closed = True"""

    if "# --- ASYMMETRIC VIRTUAL TARGET SCANNER ---" in code:
        code = code.replace(old_target_loop, new_runway_loop)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Success: Aggressive Profit Runway logic baked directly into production code.")
    else:
        print("⚠️ Script structure modified or patch already applied.")
else:
    print("❌ Critical Error: Master live script file could not be localized.")
