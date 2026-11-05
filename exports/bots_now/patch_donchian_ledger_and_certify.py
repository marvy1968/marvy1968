import re

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "r") as f:
    code = f.read()

# 1. Update regular Donchian exit trade record (around line 254)
target_reg_exit = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})'
replacement_reg_exit = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", curr_date), "exit_date": curr_date, "days_held": (curr_date - pos.get("entry_date", curr_date)).days, "raw_r": raw_r, "dollar_pnl": pnl})'

if target_reg_exit in code:
    code = code.replace(target_reg_exit, replacement_reg_exit)
else:
    # try matching a broader pattern if needed
    print("Warning: exact regular Donchian exit pattern not matched directly, checking regex...")

# 2. Update final Donchian liquidation trade record (around line 398)
target_liq_exit = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": end_dt, "raw_r": raw_r, "dollar_pnl": pnl})'
replacement_liq_exit = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", end_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get("entry_date", end_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl})'

if target_liq_exit in code:
    code = code.replace(target_liq_exit, replacement_liq_exit)

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "w") as f:
    f.write(code)

print("Donchian ledger metadata patched successfully.")
