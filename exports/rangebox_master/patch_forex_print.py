#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/hybrid_rangebox_factory.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # Ensure all loops handle the print uniform mapping across the framework
    old_target = "        is_short_zone = current_price >= (box_high - (box_width * 0.15))"
    new_target = """        is_short_zone = current_price >= (box_high - (box_width * 0.15))
        if self.mode == "FOREX":
            log.info(f"🔍 [SCAN] {symbol} | Price: {current_price:.4f} | ADX: {last['adx']:.1f} | BB_Inside: {df['inside_bb'].iloc[-50:].sum()}/50")"""

    if old_target in code and "self.mode == \"FOREX\"" not in code:
        code = code.replace(old_target, new_target)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Forex reporting verbosity unified successfully.")
    else:
        print("⚠️ Matrix lines already matching.")
