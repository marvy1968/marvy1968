#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/hybrid_rangebox_factory.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    old_line = "        last = df.iloc[-1]"
    new_line = """        last = df.iloc[-1]
        log.info(f"🔍 [SCAN] {symbol} | Price: {last['close']:.2f} | ADX: {last['adx']:.1f} | BB_Inside: {df['inside_bb'].iloc[-50:].sum()}/50")"""

    if old_line in code and "[SCAN]" not in code:
        code = code.replace(old_line, new_line)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Added diagnostic telemetry to the Hybrid Range Factory.")
    else:
        print("⚠️ Patch target line already modified or not found.")
