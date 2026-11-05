import os
from pathlib import Path
from dotenv import load_dotenv
import yfinance as yf
from alpaca.trading.client import TradingClient

load_dotenv("/home/moonrider1968/bots/.env")
tc = TradingClient(os.getenv("ALPACA_API_KEY"), os.getenv("ALPACA_SECRET_KEY"), paper=True)

positions = tc.get_all_positions()

print("=" * 85)
print(f"{'SYM':<6} | {'QTY':>4} | {'ENTRY':>8} | {'CURRENT':>8} | {'1.5x ATR':>8} | {'STOP (SL)':>9} | {'TP1 (+0.75R)':>12}")
print("-" * 85)

for p in positions:
    sym = p.symbol
    qty = int(float(p.qty))
    entry = float(p.avg_entry_price)
    cur = float(p.current_price)
    
    # 14-day ATR for swing cushion
    df = yf.download(sym, period="1mo", interval="1d", progress=False)
    high_low = df['High'] - df['Low']
    atr = float(high_low.tail(14).mean().iloc[0] if hasattr(high_low.tail(14).mean(), 'iloc') else high_low.tail(14).mean())
    risk = 1.5 * atr
    
    stop_price = round(entry - risk, 2)
    # Ensure stop is below current mark if trade has slipped (e.g. APD)
    if stop_price >= cur:
        stop_price = round(cur * 0.985, 2)
        
    tp1 = round(entry + (0.75 * risk), 2)
    
    print(f"{sym:<6} | {qty:>4} | ${entry:>7.2f} | ${cur:>7.2f} | ${risk:>7.2f} | ${stop_price:>8.2f} | ${tp1:>11.2f}")
print("=" * 85)
