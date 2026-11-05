import pandas as pd
import numpy as np
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

API_KEY = "REDACTED"
SECRET_KEY = "REDACTED"

SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "AVAX/USD", "DOGE/USD", "XRP/USD"]
START_DATE = "2025-01-01"
END_DATE = "2026-06-06"

client = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)

def run_full_backtest():
    equity = 53757.0
    equity_curve = [equity]
    trades = []
    risk_pct = 0.0030
    stop_pct = 0.0045
    target_pct = 0.0135

    for sym in SYMBOLS:
        print(f"\n📈 Processing {sym}...")
        req = CryptoBarsRequest(
            symbol_or_symbols=sym,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=START_DATE,
            end=END_DATE,
            limit=10000
        )
        bars = client.get_crypto_bars(req)
        
        if isinstance(bars.df, pd.DataFrame) and bars.df.index.nlevels > 1:
            df = bars.df.xs(sym, level=1)
        else:
            df = bars.df
        
        if df is None or len(df) < 100:
            continue
        
        df = df.copy()
        c, h, l = df["close"], df["high"], df["low"]
        df["box_high"] = h.rolling(30).max()
        df["box_low"] = l.rolling(30).min()
        df["box_pos"] = (c - df["box_low"]) / (df["box_high"] - df["box_low"] + 1e-9)

        for i in range(50, len(df)):
            if df["box_pos"].iloc[i] > 0.15:
                continue

            entry = float(c.iloc[i])
            stop = entry * (1 - stop_pct)
            tp = entry * (1 + target_pct)
            risk = entry - stop

            size = (equity * risk_pct) / risk

            future = df.iloc[i+1:i+30]
            if future.empty:
                continue

            hit_tp = (future["high"] >= tp).any()
            hit_stop = (future["low"] <= stop).any()

            if hit_tp and not hit_stop:
                r = 3.0
            elif hit_stop:
                r = -1.0
            else:
                exit_price = float(future["close"].iloc[-1])
                pnl_pct = (exit_price - entry) / entry
                r = pnl_pct / stop_pct

            pnl = equity * risk_pct * r
            equity += pnl
            equity_curve.append(equity)
            trades.append({"symbol": sym, "r": r, "pnl": pnl})

    total_return = (equity / 53757 - 1) * 100
    win_rate = len([t for t in trades if t["r"] > 0]) / len(trades) * 100 if trades else 0

    print(f"\n✅ FULL BACKTEST RESULTS (Real Historical Paths)")
    print(f"Final Equity     : ${equity:,.2f} ({total_return:+.1f}%)")
    print(f"Total Trades     : {len(trades)}")
    print(f"Win Rate         : {win_rate:.1f}%")
    print(f"Approx. Trades/Day : {len(trades) / ((len(equity_curve)-1)/ (6*24*30)) :.1f}" if len(equity_curve) > 1 else "")

    # Simple text equity summary
    print(f"Peak Equity      : ${max(equity_curve):,.2f}")
    print(f"Final vs Start   : {equity / equity_curve[0]:.2f}x")

    return trades, equity_curve

if __name__ == "__main__":
    trades, curve = run_full_backtest()
