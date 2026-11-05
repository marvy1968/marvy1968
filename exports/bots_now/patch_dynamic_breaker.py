path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

old_block = """    last_equity = float(account.last_equity)
    daily_drawdown = (equity - last_equity) / last_equity

    if daily_drawdown <= -CIRCUIT_BREAKER_PCT:
        print(f"[CRITICAL] Portfolio Drawdown hit {daily_drawdown*100:.2f}% (Threshold: -{CIRCUIT_BREAKER_PCT*100:.1f}%).")
        print("[!] Tripping Circuit Breaker: Liquidating all open positions and canceling orders...")
        trading_client.close_all_positions(cancel_orders=True)
        return True
    return False"""

new_block = """    last_equity = float(account.last_equity)
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

if old_block in text:
    text = text.replace(old_block, new_block)
    with open(path, "w") as f:
        f.write(text)
    print("[✓] Dynamic Profit Lock + -1% Circuit Breaker installed.")
else:
    print("[!] Target code block not found; check file manually.")
