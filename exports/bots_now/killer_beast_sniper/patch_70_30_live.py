import re

path = "/home/moonrider1968/bots/killer_beast_sniper/multi_scalper_shadow.py"

with open(path, "r") as f:
    code = f.read()

# Replace any direct close upon target hit with scale_out_runner
pattern = r'(\[INFO\] 🟢 \[.*\] \[.*\] TARGET HIT @ [\d\.]+ \(\+0\.85R Profit\)\s*\n\s*)(.*trade_client\.close_position[^\n]+)'

replacement = r'''\1try:
    import scale_out_runner
    scale_out_runner.execute_70_30_scale_out(trade_client, sym, {'qty': pos.qty, 'side': pos.side, 'entry_price': pos.avg_entry_price, 'risk_per_share': risk_per_share}, current_price, current_atr)
except Exception as e:
    trade_client.close_position(symbol_or_asset_id=sym)'''

new_code = re.sub(pattern, replacement, code)

with open(path, "w") as f:
    f.write(new_code)

print("✅ Live 70/30 scale-out interceptor applied.")
