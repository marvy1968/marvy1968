from pathlib import Path
import pandas as pd
import numpy as np

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
SYMBOLS = [
    "AAPL", "AMZN", "AVGO", "CAT", "COP", "COST", "CVX", "GE",
    "GOOGL", "GS", "JPM", "META", "MSFT", "NFLX", "NVDA", "PG",
    "QQQ", "SPY", "WMT", "XOM"
]

def load_data():
    dfs = {}
    for s in SYMBOLS:
        p = DATA_DIR / f"{s}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        # 1. Donchian Levels
        df["high_55"] = df["high"].shift(1).rolling(55).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()

        # 2. ATR14
        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()

        # 3. Volatility Compression (Bollinger Band Width Percentile)
        sma20 = df["close"].rolling(20).mean()
        std20 = df["close"].rolling(20).std()
        bb_width = (4 * std20) / sma20
        df["bbwp"] = bb_width.rolling(126).rank(pct=True) * 100.0  # 6-month percentile

        # 4. Extension: Distance to 20 SMA in ATR units
        df["ext_sma20_atr"] = (df["close"] - sma20) / df["atr14"]

        # 5. Volume Mechanics: RVOL & Close Location Value (CLV)
        vol_sma20 = df["volume"].rolling(20).mean()
        df["rvol"] = df["volume"] / vol_sma20.replace(0, np.nan)
        hl_range = (df["high"] - df["low"]).replace(0, np.nan)
        df["clv"] = ((df["close"] - df["low"]) - (df["high"] - df["close"])) / hl_range

        dfs[s] = df
    return dfs

def analyze_breakout_events(dfs):
    events = []
    
    for sym, df in dfs.items():
        if len(df) < 150:
            continue

        for i in range(60, len(df) - 30):
            cur = df.iloc[i]
            prev = df.iloc[i - 1]

            # Did a 55D breakout occur?
            if cur["close"] > cur["high_55"] and prev["close"] <= prev["high_55"]:
                entry_date = df.index[i + 1]
                entry_px = df.iloc[i + 1]["open"]
                atr = cur["atr14"]
                r_dist = 1.5 * atr
                stop_px = entry_px - r_dist

                # Forward simulate trade outcome
                outcome_r = 0.0
                for j in range(i + 1, len(df)):
                    bar = df.iloc[j]
                    l = bar["low"]
                    c = bar["close"]
                    low_20 = bar["low_20"]

                    if l <= stop_px:
                        outcome_r = (stop_px - entry_px) / r_dist
                        break
                    if c < low_20:
                        outcome_r = (c - entry_px) / r_dist
                        break

                events.append({
                    "symbol": sym,
                    "date": str(df.index[i].date()),
                    "outcome_r": outcome_r,
                    "is_winner": outcome_r > 1.0,
                    "is_trap": outcome_r <= 0.0,
                    "clv": cur["clv"],
                    "bbwp": cur["bbwp"],
                    "ext_atr": cur["ext_sma20_atr"],
                    "rvol": cur["rvol"]
                })

    return pd.DataFrame(events)

if __name__ == "__main__":
    dfs = load_data()
    ev_df = analyze_breakout_events(dfs)

    print("=" * 80)
    print(f"BREAKOUT FORENSIC AUDIT: {len(ev_df)} TOTAL 55-DAY BREAKOUT EVENTS")
    print("=" * 80)

    winners = ev_df[ev_df["is_winner"]]
    traps = ev_df[ev_df["is_trap"]]

    print(f"Clean Winners (> +1.0R):  {len(winners)} ({len(winners)/len(ev_df)*100:.1f}%)")
    print(f"Bull Traps / Scratches:   {len(traps)} ({len(traps)/len(ev_df)*100:.1f}%)")
    print("-" * 80)
    print("FEATURE DISCRIMINATION (WINNERS vs. TRAPS):")
    print("-" * 80)
    
    summary = pd.DataFrame({
        "Metric": ["Close Location Value (CLV)", "Volatility Squeeze (BBWP %)", "Extension from 20SMA (ATR)", "Relative Volume (RVOL)"],
        "Winners Mean": [winners["clv"].mean(), winners["bbwp"].mean(), winners["ext_atr"].mean(), winners["rvol"].mean()],
        "Traps Mean": [traps["clv"].mean(), traps["bbwp"].mean(), traps["ext_atr"].mean(), traps["rvol"].mean()],
        "Delta / Edge Direction": [
            f"+{winners['clv'].mean() - traps['clv'].mean():.3f} (Winners close higher in bar)",
            f"{winners['bbwp'].mean() - traps['bbwp'].mean():.1f}% (Winners come from tighter coils)",
            f"{winners['ext_atr'].mean() - traps['ext_atr'].mean():.2f} (Traps are more over-extended)",
            f"{winners['rvol'].mean() - traps['rvol'].mean():.2f} (Winners have balanced volume)"
        ]
    })
    print(summary.to_string(index=False))
    print("=" * 80)
