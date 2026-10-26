file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# 1. Inject UTC Hour Filter for Counter-Trend Experts (BOT-2 Liquidity Sweep & BOT-3 Reversion)
hour_filter_logic = """
            # --- ROI MASTER OPTIMIZATION: HOUR & LOSS TRUNCATION FILTER ---
            now_utc = datetime.now(timezone.utc)
            current_hour = now_utc.hour
            
            # Block counter-trend proposals during high-drawdown choppy hours (UTC 06, 10, 13, 18)
            toxic_hours = [6, 10, 13, 18]
            expert_id = prop.get('expert_id', '')
            if current_hour in toxic_hours and expert_id in ['BOT-2', 'BOT-3']:
                logging.info(f"🚫 [HOUR FILTER] Suppressing {expert_id} counter-trend trade during high-drag UTC hour {current_hour}:00")
                continue
"""

if "toxic_hours" not in code:
    code = code.replace("for prop in proposals:", "for prop in proposals:\n" + hour_filter_logic)

# 2. Cap Stop-Loss Distance (12.0 pips min, 15.0 pips max to cap dollar loss to ~$5.00)
sl_cap_logic = """
                # --- ROI MASTER OPTIMIZATION: SL BUFFER & LOSS CAP ---
                pip_buffer = 0.120 if 'JPY' in instrument else 0.0012
                max_sl_dist = 0.150 if 'JPY' in instrument else 0.0015
                
                if final_units > 0:
                    dist = price - ai_sl
                    if dist < pip_buffer:
                        ai_sl = price - pip_buffer
                    elif dist > max_sl_dist:
                        ai_sl = price - max_sl_dist
                elif final_units < 0:
                    dist = ai_sl - price
                    if dist < pip_buffer:
                        ai_sl = price + pip_buffer
                    elif dist > max_sl_dist:
                        ai_sl = price + max_sl_dist
"""

if "max_sl_dist" not in code:
    code = code.replace("ai_sl = prop.get('sl', 0)", "ai_sl = prop.get('sl', 0)\n" + sl_cap_logic)

with open(file_path, "w") as f:
    f.write(code)

print("🚀 ROI Master Patch successfully installed in deploy_brain_fleet.py!")
