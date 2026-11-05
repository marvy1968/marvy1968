import os, datetime
import numpy as np
import pandas as pd
import backtest_sleeve2_option_b_5min_momentum_burst as bot

# Pre-calculate point-in-time weekly Stage-2 status as a fast timestamp lookup
all_5m_timestamps = sorted(list(set.union(*[set(df.index) for df in bot.fleet_5m.values()])))

stage2_lookup = {}
for sym, df_w in bot.weekly_data.items():
    valid_mask = (df_w['close_pit'] > df_w['sma30w_pit']) & \
                 (df_w['sma30w_slope_pit'] > 0) & \
                 (df_w['close_pit'] >= (0.95 * df_w['prev_20w_high_pit']))
    w_idx = np.searchsorted(df_w['time'], all_5m_timestamps, side='right') - 1
    for t_idx, ts in enumerate(all_5m_timestamps):
        idx = w_idx[t_idx]
        if idx >= 32 and bool(valid_mask.iloc[idx]):
            stage2_lookup[(sym, ts)] = True
        else:
            stage2_lookup[(sym, ts)] = False

bot.get_pit_stage2 = lambda sym, ts: stage2_lookup.get((sym, ts), False)
bot.get_pit_breadth_score = lambda sec, ts: sum(1 for s in bot.TRI_SECTOR_UNIVERSE[sec] if stage2_lookup.get((s, ts), False))

print("=" * 135)
print(f"{'Variant / Thrust Window':<42} | {'Trades':<7} | {'Win Rate':<9} | {'Net R':<9} | {'PF':<6} | {'Exp/Tr':<10} | {'Eq MaxDD':<9} | {'Harvest/DD':<10} | {'Calmar':<7} | {'MAE/MFE'}")
print("-" * 135)

b3_b4_variants = [
    ("B3. 8x5m (40 min) / Req MFE +0.50R", 8, 0.50),
    ("B4. 6x5m (30 min) / Req MFE +0.75R", 6, 0.75),
]

for name, t_bars, req_mfe in b3_b4_variants:
    res = bot.run_option_b_simulation(thrust_bars=t_bars, required_mfe=req_mfe)
    print(f"{name:<42} | {res['n']:^7} | {res['wr']:^9.1f}% | {res['net_r']:^+9.2f}R | {res['pf']:^6.2f} | {res['exp']:^+10.2f}R/tr | {res['max_eq_dd_pct']:^8.2f}% | {res['harvest_dd']:^10.2f} | {res['calmar']:^7.2f} | {res['mae']:.2f}R / {res['mfe']:.2f}R")

print("=" * 135)

# Detailed Sector and Ticker Attribution for B3
b3_res = bot.run_option_b_simulation(thrust_bars=8, required_mfe=0.50)
b3_df = b3_res["df"]

print("\n📊 Sector Attribution Breakdown (Variant B3: 8x5m @ +0.50R):")
sec_grp = b3_df.groupby("sector").agg(
    Trades=("raw_r", "count"),
    WinRate=("raw_r", lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=("raw_r", "sum"),
    Dollar_PnL=("dollar_pnl", "sum"),
    PF=("raw_r", lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index()
print(sec_grp.to_string(index=False))

print("\n📈 Top Ticker Breakdown (Variant B3):")
tick_grp = b3_df.groupby("symbol").agg(
    Trades=("raw_r", "count"),
    WinRate=("raw_r", lambda x: f"{(x > 0).mean()*100:.1f}%"),
    Net_R=("raw_r", "sum"),
    Dollar_PnL=("dollar_pnl", "sum"),
    PF=("raw_r", lambda x: abs(x[x > 0].sum() / (x[x <= 0].sum() + 1e-9)))
).reset_index().sort_values("Net_R", ascending=False)
print(tick_grp.head(10).to_string(index=False))
print("=" * 135)
