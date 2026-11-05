#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/hybrid_rangebox_factory.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # Target variations of the baseline pair structures inside the configurations
    target_1 = '["BTC/USD", "ETH/USD"]'
    target_2 = '["BTC/USD", "ETH/USD"]'
    replacement = '["BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"]'
    
    # Also watch for raw strings
    target_3 = '"BTC/USD", "ETH/USD"'
    replacement_3 = '"BTC/USD", "ETH/USD", "SOL/USD", "LINK/USD"'

    modified = False
    if target_1 in code:
        code = code.replace(target_1, replacement)
        modified = True
    elif target_2 in code:
        code = code.replace(target_2, replacement)
        modified = True
    elif target_3 in code:
        code = code.replace(target_3, replacement_3)
        modified = True

    if modified:
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Alpaca Crypto Watchlist successfully optimized to include SOL & LINK.")
    else:
        # Let's inspect the file configuration structure if explicit string misses happen
        print("⚠️ Direct pair layout missing. Forcing check on fallback asset block array...")
        lines = code.split('\n')
        for idx, line in enumerate(lines):
            if "CRYPTO" in line and ("BTC" in line or "ETH" in line) and "SOL" not in line:
                lines[idx] = line.replace(']', ', "SOL/USD", "LINK/USD"]')
                modified = True
        if modified:
            with open(file_path, "w") as f:
                f.write('\n'.join(lines))
            print("✅ Array structure successfully located and updated.")
        else:
            print("❌ Failure: Match target layout could not be verified.")
else:
    print("❌ Error: hybrid_rangebox_factory.py not found.")
