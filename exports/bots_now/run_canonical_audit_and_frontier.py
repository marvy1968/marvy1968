import sys
import datetime
import numpy as np
import pandas as pd

# Import the canonical engine directly as a module or execute its logic
# Since run_config_b_d3_integration.py is already fully set up, let's write a clean script 
# that imports or executes the necessary parts while patching the Donchian ledger to store entry_px, risk_prs, etc.

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "r") as f:
    code = f.read()

# Make sure Donchian trade records store entry_px, risk_prs, etc. for the outlier audit
old_rec_1 = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", curr_date), "exit_date": curr_date, "days_held": (curr_date - pos.get("entry_date", curr_date)).days, "raw_r": raw_r, "dollar_pnl": pnl})'
new_rec_1 = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", curr_date), "exit_date": curr_date, "days_held": (curr_date - pos.get("entry_date", curr_date)).days, "raw_r": raw_r, "dollar_pnl": pnl, "entry_px": pos["entry_px"], "risk_prs": pos["risk_prs"], "exit_px": exit_px})'

old_rec_2 = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", end_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get("entry_date", end_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl})'
new_rec_2 = 'closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": pos.get("entry_date", end_dt), "exit_date": end_dt, "days_held": (end_dt - pos.get("entry_date", end_dt)).days, "raw_r": raw_r, "dollar_pnl": pnl, "entry_px": pos["entry_px"], "risk_prs": pos["risk_prs"], "exit_px": exit_px})'

if old_rec_1 in code:
    code = code.replace(old_rec_1, new_rec_1)
if old_rec_2 in code:
    code = code.replace(old_rec_2, new_rec_2)

# Now append our Audit + Paired Frontier Sweep script replacing the bottom attribution block
# Let's truncate everything after "--- GATE 0" and append our exact paired runner
parts = code.split('print("\\n--- GATE 0:')
base_engine_code = parts[0]

paired_runner = """
print("\\n--- GATE 0: BASELINE CHECK ---")
eq_b0, tdf_b0, _ = run_adaptive_simulation(0.00)
ending_eq_b0 = eq_b0['equity'].iloc[-1]
peak_b0 = eq_b0['equity'].cummax()
max_dd_b0 = ((peak_b0 - eq_b0['equity']) / peak_b0).max() * 100.0
years = (end_dt - start_dt).days / 365.25
cagr_b0 = (((ending_eq_b0 / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0

assert abs(ending_eq_b0 - 175444.31) <= 1.00
assert abs(cagr_b0 - 6.01) <= 0.01
assert abs(max_dd_b0 - 7.75) <= 0.01
print("✅ GATE 0 PASSED.\\n")

# D25 Regression Identity Check
print("--- D25 REGRESSION IDENTITY CHECK ---")
eq_d25, tdf_d25, _ = run_adaptive_simulation(0.0025)
ending_eq_d25 = eq_d25['equity'].iloc[-1]
peak_d25 = eq_d25['equity'].cummax()
max_dd_d25 = ((peak_d25 - eq_d25['equity']) / peak_d25).max() * 100.0
cagr_d25 = (((ending_eq_d25 / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0

print(f"D25 REPRODUCED: CAGR={cagr_d25:.2f}% | DD={max_dd_d25:.2f}%")
assert abs(cagr_d25 - 8.60) <= 0.02, f"D25 CAGR {cagr_d25}% != 8.60%"
assert abs(max_dd_d25 - 8.27) <= 0.02, f"D25 DD {max_dd_d25}% != 8.27%"
print("✅ D25 REGRESSION GATE PASSED.\\n")

print("=" * 135)
print(" 🔍 AUDITING WORST DONCHIAN OUTLIER (-10.92R)")
print("=" * 135)
donch_trades_d25 = tdf_d25[tdf_d25['sleeve'] == 'DONCHIAN_SATELLITE']
if not donch_trades_d25.empty:
    worst = donch_trades_d25.sort_values("raw_r").iloc[0]
    sym = worst["symbol"]
    exit_date = worst["exit_date"]
    df = master_daily[sym]
    if exit_date in df.index:
        row = df.loc[exit_date]
        idx = df.index.get_loc(exit_date)
        prev_close = df.iloc[idx - 1]["close"] if idx > 0 else np.nan
        print("Symbol       :", sym)
        print("Entry date   :", worst["entry_date"])
        print("Exit date    :", exit_date)
        print("Entry price  :", worst.get("entry_px", "N/A"))
        print("Risk/share   :", worst.get("risk_prs", "N/A"))
        print("Exit price   :", worst.get("exit_px", "N/A"))
        print("Raw R        :", worst["raw_r"])
        print("Exit-day open:", row["open"])
        print("Exit-day low :", row["low"])
        print("Prior close  :", prev_close)
        gap_pct = ((row["open"] / prev_close) - 1) * 100 if pd.notna(prev_close) else 0.0
        print(f"Gap %        : {gap_pct:+.2f}%")
print("=" * 135)

print("\\n" + "=" * 135)
print(" 🚀 PAIRED-PATH EFFICIENT FRONTIER SWEEP (D10 - D35, 5,000 SIMS)")
print("=" * 135)

RISK_LEVELS = [
    (0.0010, "B + D10 (0.10%)"),
    (0.0015, "B + D15 (0.15%)"),
    (0.0020, "B + D20 (0.20%)"),
    (0.0025, "B + D25 (0.25%)"),
    (0.0030, "B + D30 (0.30%)"),
    (0.0035, "B + D35 (0.35%)"),
]

n_sims = 5000
block_size = 20

print(f"{'Config':<16} | {'CAGR':<6} | {'HistDD':<6} | {'Calmar':<6} | {'PF':<4} | {'MedCAGR':<7} | {'5thCAGR':<7} | {'MedDD':<6} | {'95thDD':<6} | {'99thDD':<6} | {'P(DD>10%)':<9}")
print("-" * 135)

# Generate shared paired bootstrap start indices once for all tiers
# We'll use D25 equity returns as the base length reference
base_ret = eq_d25['equity'].pct_change().dropna().values
n_days = len(base_ret)
n_blocks = int(np.ceil(n_days / block_size))

np.random.seed(42)
shared_starts = [np.random.randint(0, n_days - block_size + 1, size=n_blocks) for _ in range(n_sims)]

for risk_val, label in RISK_LEVELS:
    eq_df_c, tdf_c, trans_c = run_adaptive_simulation(risk_val)
    ending_equity = eq_df_c['equity'].iloc[-1]
    eq_peak = eq_df_c['equity'].cummax()
    max_dd_hist = ((eq_peak - eq_df_c['equity']) / eq_peak).max() * 100.0
    cagr_hist = (((ending_equity / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    calmar_hist = (cagr_hist / max_dd_hist) if max_dd_hist > 0 else 0.0

    r_arr_c = tdf_c['raw_r'].values if not tdf_c.empty else np.array([0])
    wins_c = r_arr_c[r_arr_c > 0]
    losses_c = r_arr_c[r_arr_c <= 0]
    pf_c = float(np.sum(wins_c) / np.abs(np.sum(losses_c))) if len(losses_c) > 0 else float("inf")

    daily_ret = eq_df_c['equity'].pct_change().dropna().values
    
    sim_cagrs = []
    sim_max_dds = []
    dd_exceed_10 = 0

    for starts in shared_starts:
        boot_ret = np.concatenate([daily_ret[s:s + block_size] for s in starts])[:n_days]
        equity = INITIAL_NAV * np.cumprod(1.0 + boot_ret)
        term_val = equity[-1]
        
        peak = np.maximum.accumulate(equity)
        dd = np.max((peak - equity) / peak)
        max_dd_pct = dd * 100.0

        if max_dd_pct > 10.0: dd_exceed_10 += 1
        cagr_sim = (((term_val / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0

        sim_cagrs.append(cagr_sim)
        sim_max_dds.append(max_dd_pct)

    med_cagr = np.median(sim_cagrs)
    p5_cagr = np.percentile(sim_cagrs, 5)
    med_dd = np.median(sim_max_dds)
    p95_dd = np.percentile(sim_max_dds, 95)
    p99_dd = np.percentile(sim_max_dds, 99)
    prob_dd10 = (dd_exceed_10 / n_sims) * 100.0

    print(f"{label:<16} | {cagr_hist:>5.2f}% | {max_dd_hist:>5.2f}% | {calmar_hist:>6.2f} | {pf_c:>4.2f} | {med_cagr:>6.2f}% | {p5_cagr:>6.2f}% | {med_dd:>5.2f}% | {p95_dd:>5.2f}% | {p99_dd:>5.2f}% | {prob_dd10:>8.1f}%")

print("=" * 135)
"""

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "w") as f:
    f.write(base_engine_code + paired_runner)

print("Canonical engine updated with audited ledger and paired-path frontier sweep.")
