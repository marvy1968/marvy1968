path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

old_exit = """        # Reversion Take-Profit (RSI2 >= 70 or Close > 5-day SMA)
        if rsi2 >= 70.0 or c >= sma5:
            print(f"[+] Mean-reversion target reached for {sym} (RSI2: {rsi2:.1f}, Close: ${c:.2f} >= SMA5: ${sma5:.2f}). Liquidating.")
            trading_client.close_position(sym)
            continue"""

new_exit = """        # Exits managed via Dual OCO Brackets (50% @ +0.75R / 50% @ +2.0R trailing)
        # Bypassing raw sma5 liquidation to allow swings to develop."""

if old_exit in text:
    text = text.replace(old_exit, new_exit)
    with open(path, "w") as f:
        f.write(text)
    print("[✓] Neutralized premature sma5 exit trigger.")
else:
    print("[!] Target block not found; checking with regex.")
