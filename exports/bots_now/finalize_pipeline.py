import re

path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

# 1. Update candidates dictionary inside fetch_signals to compute stop loss (1.5x ATR)
old_dict = """            if stage2 and velez_setup:
                score = round(((close - sma50) / sma50) * 100, 2)
                candidates.append({
                    "symbol": sym,
                    "close": round(close, 2),
                    "sma200": round(sma200, 2),
                    "sma50": round(sma50, 2),
                    "score": score,
                    "sector": "Stage 2 Leader"
                })"""

new_dict = """            if stage2 and velez_setup:
                score = round(((close - sma50) / sma50) * 100, 2)
                # Compute 1.5x ATR stop buffer
                tr = (df['High'] - df['Low']).tail(14).mean()
                atr = float(tr.iloc[0] if hasattr(tr, 'iloc') else tr)
                stop_price = round(max(close - (1.5 * atr), sma20 * 0.985), 2)

                candidates.append({
                    "symbol": sym,
                    "close": round(close, 2),
                    "stop": stop_price,
                    "sma200": round(sma200, 2),
                    "sma50": round(sma50, 2),
                    "score": score,
                    "sector": "Stage 2 Leader"
                })"""

if old_dict in text:
    text = text.replace(old_dict, new_dict)
    print("[✓] Added stop-loss computation and 'stop' key to fetch_signals candidates.")
else:
    print("[!] Candidate dictionary pattern not found.")

# 2. Neutralize premature SMA5 / RSI2 liquidation via regex
pattern = r"(if\s+rsi2\s*>=\s*70\.0\s+or\s+c\s*>=\s*sma5:[\s\S]*?trading_client\.close_position\(sym\)[\s\S]*?continue)"
replacement = """# Bypassed premature SMA5/RSI2 liquidation; exits managed by brackets & ratchet_breakeven
            pass"""

if re.search(pattern, text):
    text = re.sub(pattern, replacement, text)
    print("[✓] Successfully neutralized premature SMA5 liquidation.")
else:
    print("[!] SMA5 liquidation pattern not found or already disabled.")

with open(path, "w") as f:
    f.write(text)

