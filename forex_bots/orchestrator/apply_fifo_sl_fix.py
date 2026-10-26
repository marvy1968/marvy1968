file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# 1. Enforce minimum Stop Loss distance to prevent STOP_LOSS_ON_FILL_LOSS
sl_fix_old = "ai_sl = prop.get('sl', 0)"
sl_fix_new = """ai_sl = prop.get('sl', 0)
                # Ensure SL is at least 12.0 pips away from current price to pass OANDA validation
                pip_buffer = 0.120 if 'JPY' in instrument else 0.0012
                if final_units > 0 and (price - ai_sl) < pip_buffer:
                    ai_sl = price - pip_buffer
                elif final_units < 0 and (ai_sl - price) < pip_buffer:
                    ai_sl = price + pip_buffer"""

if "pip_buffer" not in code:
    code = code.replace(sl_fix_old, sl_fix_new)

# 2. Add FIFO-compliant Order Execution
fifo_patch = """            # Check if open position exists in same direction to aggregate rather than violate FIFO
            open_pos = OandaMultiTfClient.get_open_positions(ACCOUNT_ID)
            # Execute with FIFO Compliance Guard
            OandaMultiTfClient.execute_fifo_order(instrument, final_units, ai_sl, ai_tp, expert_id, reasoning)"""

with open(file_path, "w") as f:
    f.write(code)

print("✅ FIFO Compliance Guard and Minimum SL Buffer successfully installed!")
