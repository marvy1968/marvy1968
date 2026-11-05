path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

old_block = """    last_equity = float(account.last_equity)
    day_pct = (equity - last_equity) / last_equity

    # 1. Hard Floor: Down 1.0% on the day
    if day_pct <= -CIRCUIT_BREAKER_PCT:
        print(f"[CRITICAL] Portfolio hit -{CIRCUIT_BREAKER_PCT*100:.1f}% floor (Current: {day_pct*100:.2f}%). Flattening book.")
        trading_client.close_all_positions(cancel_orders=True)
        return True

    # 2. Profit Lock: If up >= +1.0%, never let it fall back below +0.50%
    if day_pct >= 0.010:
        PROFIT_LOCK_FLOOR = 0.005  # Lock in minimum +0.50%
        if day_pct < PROFIT_LOCK_FLOOR:
            print(f"[PROTECT GAINS] Equity slipped below locked +{PROFIT_LOCK_FLOOR*100:.1f}% floor after reaching +1%. Flattening.")
            trading_client.close_all_positions(cancel_orders=True)
            return True

    return False"""

new_block = """    import json
    from datetime import date
    last_equity = float(account.last_equity)
    day_pct = (equity - last_equity) / last_equity

    # State tracking for Intraday High-Water Mark
    state_file = Path("/home/moonrider1968/bots/.daily_hwm.json")
    today_str = str(date.today())
    hwm = day_pct

    if state_file.exists():
        try:
            with open(state_file, "r") as sf:
                data = json.load(sf)
                if data.get("date") == today_str:
                    hwm = max(data.get("max_day_pct", day_pct), day_pct)
        except Exception:
            pass

    # Save today's highest equity percentage reached
    try:
        with open(state_file, "w") as sf:
            json.dump({"date": today_str, "max_day_pct": hwm}, sf)
    except Exception:
        pass

    # 1. Hard Floor: Down 1.0% on the day from prior close
    if day_pct <= -CIRCUIT_BREAKER_PCT:
        print(f"[CRITICAL] Portfolio hit -{CIRCUIT_BREAKER_PCT*100:.1f}% floor (Current: {day_pct*100:.2f}%). Flattening book.")
        trading_client.close_all_positions(cancel_orders=True)
        return True

    # 2. Dynamic Profit Lock: If peaked >= +1.0%, never let it fall below +0.50%
    if hwm >= 0.010:
        PROFIT_LOCK_FLOOR = 0.005  # Lock in minimum +0.50%
        if day_pct < PROFIT_LOCK_FLOOR:
            print(f"[PROTECT GAINS] Daily high hit +{hwm*100:.2f}%, but equity fell to {day_pct*100:.2f}% (Below +{PROFIT_LOCK_FLOOR*100:.1f}% Floor). Flattening.")
            trading_client.close_all_positions(cancel_orders=True)
            return True

    return False"""

if old_block in text:
    text = text.replace(old_block, new_block)
    with open(path, "w") as f:
        f.write(text)
    print("[✓] Deployed persistent High-Water Mark profit lock.")
else:
    print("[!] Target block not found. Verify manually.")
