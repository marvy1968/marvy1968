#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/bots/RangeBox_Master/hybrid_rangebox_factory.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # The altered version we want to replace
    unlinked_block = """                url = f"{self.oanda_url}/accounts/{self.oanda_id}/orders"
                
                # Dynamic emergency disaster offset calculation (1.50 for JPY crosses, 0.0150 for standards)
                pip_offset = 1.50 if "JPY" in symbol else 0.0150
                
                # Fetch current rate approximation to set the disaster floor target boundary
                try:
                    current_rate = float(qty) if 'qty' in locals() else 1.0 # fallback proxy safely
                except:
                    current_rate = 1.0

                payload = {
                    "order": {
                        "units": str(units),
                        "instrument": symbol,
                        "timeInForce": "FOK",
                        "type": "MARKET",
                        "positionFill": "DEFAULT"
                    }
                }
                
                # Execute payload tracking log line
                log.info(f"⚡ [OANDA UNLINKED ENTRY] Submitting raw position payload for {symbol} | Units: {units}")
                requests.post(url, headers=self.oanda_headers, json=payload)"""

    # Bringing back the baseline footprint exactly as it was configured
    original_block = """                url = f"{self.oanda_url}/accounts/{self.oanda_id}/orders"
                payload = {"order": {"units": units, "instrument": symbol, "timeInForce": "FOK", "type": "MARKET", "positionFill": "DEFAULT"}}
                requests.post(url, headers=self.oanda_headers, json=payload)"""

    if "⚡ [OANDA UNLINKED ENTRY]" in code:
        # We find and replace our modification to restore baseline operations
        code = code.replace(unlinked_block, original_block)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Original OANDA order footprint successfully restored.")
    else:
        print("⚠️ The unlinked block modification was not found. Checking fallback alternatives...")
        # Check if code still contains standard block
        if 'payload = {"order": {"units": units' in code:
            print("✅ File is already matching the original footprint.")
        else:
            print("❌ Footprint mismatch. Let's inspect manually.")
else:
    print("❌ Error: hybrid_rangebox_factory.py not found.")
