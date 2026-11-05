#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/oanda_live_beast.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # Locate the start of loop variables
    old_target = """                symbol_pos_count = len(self.local_positions[symbol])
                
                # --- REGIME INTERLOCK PROCESSING ---"""

    # Inject real-time indicator logging visibility
    new_target = """                symbol_pos_count = len(self.local_positions[symbol])
                regime_str = "RANGE 🔄" if adx < Config.RANGE_ADX_MAX else "TREND 🚀"
                log.info(f"📊 [{symbol}] Price: {price:.5f} | ADX: {adx:.1f} [{regime_str}] | Active Units: {sum(p['units'] for p in self.local_positions[symbol])}")
                
                # --- REGIME INTERLOCK PROCESSING ---"""

    if "symbol_pos_count = len(self.local_positions[symbol])" in code and "regime_str" not in code:
        code = code.replace(old_target, new_target)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Success: Indicator tracking telemetry injected into streaming loop.")
    else:
        print("⚠️ Footprint mismatch or telemetry already injected.")
