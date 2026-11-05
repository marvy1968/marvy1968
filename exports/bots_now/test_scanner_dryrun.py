import os
import trade_velez_weinstein_swing as bot

print("[*] Running Stage 2 candidate evaluation...")
candidates = bot.fetch_signals()

print("=" * 90)
print(f"{'SYM':<6} | {'SCORE':>6} | {'CLOSE':>8} | {'STOP':>8} | {'TP1 (+0.75R)':>12} | {'TP2 (+2.0R)':>11}")
print("-" * 90)

for c in candidates[:10]:
    sym = c['symbol']
    close = c['close']
    stop = c['stop']
    r = close - stop
    tp1 = round(close + (0.75 * r), 2)
    tp2 = round(close + (2.00 * r), 2)
    print(f"{sym:<6} | {c['score']:>6.2f} | ${close:>7.2f} | ${stop:>7.2f} | ${tp1:>11.2f} | ${tp2:>10.2f}")
print("=" * 90)
