with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "r") as f:
    code = f.read()

# 1. Update regular Donchian exit
target_reg = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})'
replacement_reg = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", curr_date), "exit_date": curr_date, "days_held": (curr_date - pos.get("entry_date", curr_date)).days, "raw_r": raw_r, "dollar_pnl": pnl})'

if target_reg in code:
    code = code.replace(target_reg, replacement_reg)
else:
    # Fallback search if exact string spacing differs
    target_reg_alt = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE"'
    if target_reg_alt not in code:
        raise RuntimeError("Donchian regular exit target not found.")

# 2. Update final liquidation exit
target_liq = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": end_dt, "raw_r": raw_r, "dollar_pnl": pnl})'
replacement_liq = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", end_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get("entry_date", end_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl})'

if target_liq in code:
    code = code.replace(target_liq, replacement_liq)

# 3. Append the comprehensive attribution and block-bootstrap Monte Carlo suite at the very end
attribution_suite = """
print("\\n" + "=" * 135)
print(" 📊 B + D25 COMPREHENSIVE TRADE-LEVEL ATTRIBUTION")
print("=" * 135)

eq_df_d25, tdf_d25, _ = run_adaptive_simulation(0.0025)
donch_trades = tdf_d25[tdf_d25['sleeve'] == 'DONCHIAN_SATELLITE'] if not tdf_d25.empty and 'sleeve' in tdf_d25.columns else pd.DataFrame()

if not donch_trades.empty:
    wins = donch_trades[donch_trades['raw_r'] > 0]
    losses = donch_trades[donch_trades['raw_r'] <= 0]
    
    total_donch_pnl = donch_trades['dollar_pnl'].sum()
    gross_wins = wins['dollar_pnl'].sum()
    gross_losses = abs(losses['dollar_pnl'].sum())
    donch_pf = gross_wins / gross_losses if gross_losses > 0 else float('inf')

    print(f"• Total Donchian Trades          : {len(donch_trades)}")
    print(f"• Win Rate                       : {(len(wins) / len(donch_trades)) * 100:.1f}%")
    print(f"• Profit Factor                  : {donch_pf:.2f}")
    print(f"• Average R Multiple             : {donch_trades['raw_r'].mean():+.2f}R (Wins: {wins['raw_r'].mean():+.2f}R, Losses: {losses['raw_r'].mean():+.2f}R)")
    print(f"• Median R Multiple              : {donch_trades['raw_r'].median():+.2f}R")
    print(f"• Largest Winner / Loser         : {donch_trades['raw_r'].max():+.2f}R / {donch_trades['raw_r'].min():+.2f}R")
    print(f"• Average Holding Period         : {donch_trades['days_held'].mean():.1f} days")
    print(f"• Total Donchian Realized P&L    : ${total_donch_pnl:,.2f}")

    print("\\n🏆 Top Profits by Symbol & Concentration Share:")
    sym_pnl = donch_trades.groupby('symbol')['dollar_pnl'].sum().sort_values(ascending=False)
    sym_cnt = donch_trades.groupby('symbol')['dollar_pnl'].count()
    
    positive_sym_pnl = sym_pnl[sym_pnl > 0]
    gross_pos = positive_sym_pnl.sum() if len(positive_sym_pnl) > 0 else 1.0
    top1_share = (positive_sym_pnl.head(1).sum() / gross_pos) * 100.0
    top3_share = (positive_sym_pnl.head(3).sum() / gross_pos) * 100.0
    top5_share = (positive_sym_pnl.head(5).sum() / gross_pos) * 100.0
    
    print(f"  - Top-1 Gross Profit Share     : {top1_share:.1f}%")
    print(f"  - Top-3 Gross Profit Share     : {top3_share:.1f}%")
    print(f"  - Top-5 Gross Profit Share     : {top5_share:.1f}%")
    for sym, pnl in sym_pnl.head(5).items():
        print(f"    * {sym}: ${pnl:,.2f} ({sym_cnt[sym]} trades)")

    print("\\n📅 Donchian P&L & Trade Count by Year:")
    donch_trades['year'] = pd.to_datetime(donch_trades['exit_date']).dt.year
    yr_pnl = donch_trades.groupby('year')['dollar_pnl'].sum()
    yr_cnt = donch_trades.groupby('year')['dollar_pnl'].count()
    for yr, pnl in yr_pnl.items():
        print(f"  - {yr}: ${pnl:,.2f} ({yr_cnt[yr]} trades)")

    print("\\n📦 Sleeve-Level Realized Contribution:")
    sleeve_pnl = tdf_d25.groupby('sleeve')['dollar_pnl'].sum()
    for sleeve, pnl in sleeve_pnl.items():
        print(f"  - {sleeve}: ${pnl:,.2f}")
else:
    print("No Donchian trades recorded.")

print("=" * 135)

print("\\n" + "=" * 135)
print(" 🎲 DAILY BLOCK-BOOTSTRAP MONTE CARLO CERTIFICATION (5,000 SIMS)")
print("=" * 135)

daily_ret = eq_df_d25['equity'].pct_change().dropna().values
n_days = len(daily_ret)
block_size = 20
n_blocks = int(np.ceil(n_days / block_size))
n_sims = 5000

sim_cagrs = []
sim_max_dds = []
sim_calmars = []
terminal_pos = 0
dd_exceed_10 = 0
dd_exceed_125 = 0
dd_exceed_15 = 0
dd_exceed_20 = 0

np.random.seed(42)
for _ in range(n_sims):
    starts = np.random.randint(0, n_days - block_size + 1, size=n_blocks)
    boot_ret = np.concatenate([daily_ret[s:s + block_size] for s in starts])[:n_days]
    equity = INITIAL_NAV * np.cumprod(1.0 + boot_ret)
    term_val = equity[-1]
    
    peak = np.maximum.accumulate(equity)
    dd = np.max((peak - equity) / peak)

    if term_val > INITIAL_NAV: terminal_pos += 1
    if dd > 0.10: dd_exceed_10 += 1
    if dd > 0.125: dd_exceed_125 += 1
    if dd > 0.15: dd_exceed_15 += 1
    if dd > 0.20: dd_exceed_20 += 1

    cagr_sim = (((term_val / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    max_dd_pct = dd * 100.0
    calmar_sim = (cagr_sim / max_dd_pct) if max_dd_pct > 0 else 0.0

    sim_cagrs.append(cagr_sim)
    sim_max_dds.append(max_dd_pct)
    sim_calmars.append(calmar_sim)

print(f"• Positive Terminal Probability    : {(terminal_pos / n_sims) * 100:.1f}%")
print(f"• Median CAGR                    : {np.median(sim_cagrs):.2f}% (5th Pct: {np.percentile(sim_cagrs, 5):.2f}%, 1st Pct: {np.percentile(sim_cagrs, 1):.2f}%)")
print(f"• Median Max Drawdown            : {np.median(sim_max_dds):.2f}% (90th Pct: {np.percentile(sim_max_dds, 90):.2f}%, 95th Pct: {np.percentile(sim_max_dds, 95):.2f}%, 99th Pct: {np.percentile(sim_max_dds, 99):.2f}%)")
print(f"• Median Calmar Ratio            : {np.median(sim_calmars):.2f} (5th Pct: {np.percentile(sim_calmars, 5):.2f}%)")
print(f"• Prob(Max DD > 10.0%)           : {(dd_exceed_10 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 12.5%)           : {(dd_exceed_125 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 15.0%)           : {(dd_exceed_15 / n_sims) * 100:.1f}%")
print(f"• Prob(Max DD > 20.0%)           : {(dd_exceed_20 / n_sims) * 100:.1f}%")
print("=" * 135)
"""

if "COMPREHENSIVE TRADE-LEVEL ATTRIBUTION" not in code:
    code += attribution_suite

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "w") as f:
    f.write(code)

print("Donchian ledger patched and attribution suite appended successfully.")
