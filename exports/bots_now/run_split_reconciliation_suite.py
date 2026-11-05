import sys
import datetime
import numpy as np
import pandas as pd

# Let's write the execution harness for SPLIT data that runs:
# 1. NVDA split-adjusted price check around July 20, 2021
# 2. RAW vs SPLIT B0 and D25 comparison
# 3. Outlier check for extreme R multiples
# 4. Paired D10 - D35 frontier sweep with 5,000 Monte Carlo paths

# We will modify run_config_b_d3_integration_SPLIT.py to execute our SPLIT suite.
with open("/home/moonrider1968/bots/run_config_b_d3_integration_SPLIT.py", "r") as f:
    split_code = f.read()

# Strip any old execution blocks at the bottom and replace with our SPLIT reconciliation runner
parts = split_code.split('print("\\n--- GATE 0:')
base_split_engine = parts[0]

split_runner = """
print("\\n" + "=" * 135)
print(" 🔬 SPLIT-ADJUSTED RECONCILIATION & FRONTIER SWEEP")
print("=" * 135)

# 1. NVDA Sanity Check Around July 20, 2021 Split
print("--- NVDA SPLIT SANITY CHECK (July 16 - 21, 2021) ---")
if "NVDA" in master_daily:
    nvda_df = master_daily["NVDA"]
    sub_nvda = nvda_df.loc[datetime.date(2021, 7, 16):datetime.date(2021, 7, 21), ['open', 'high', 'low', 'close', 'volume']]
    print(sub_nvda)
print("=" * 135)

# 2. Run SPLIT B0 and D25
print("\\n--- RUNNING SPLIT B0 & D25 ---")
eq_b0_s, tdf_b0_s, _ = run_adaptive_simulation(0.00)
eq_d25_s, tdf_d25_s, _ = run_adaptive_simulation(0.0025)

years = (end_dt - start_dt).days / 365.25

b0_end = eq_b0_s['equity'].iloc[-1]
b0_cagr = (((b0_end / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
b0_dd = ((eq_b0_s['equity'].cummax() - eq_b0_s['equity']) / eq_b0_s['equity'].cummax()).max() * 100.0

d25_end = eq_d25_s['equity'].iloc[-1]
d25_cagr = (((d25_end / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
d25_dd = ((eq_d25_s['equity'].cummax() - eq_d25_s['equity']) / eq_d25_s['equity'].cummax()).max() * 100.0

print(f"SPLIT B0  -> Ending Equity: ${b0_end:,.2f} | CAGR: {b0_cagr:.2f}% | Max DD: {b0_dd:.2f}%")
print(f"SPLIT D25 -> Ending Equity: ${d25_end:,.2f} | CAGR: {d25_cagr:.2f}% | Max DD: {d25_dd:.2f}%")

# 3. Check Extreme R Outliers for D25
print("\\n--- EXTREME R AUDIT (Top 10 Smallest R Multiples in SPLIT D25) ---")
donch_s = tdf_d25_s[tdf_d25_s['sleeve'] == 'DONCHIAN_SATELLITE'].copy()
if not donch_s.empty:
    print(donch_s.nsmallest(10, 'raw_r')[['symbol', 'entry_date', 'exit_date', 'raw_r', 'dollar_pnl']])
print("=" * 135)

# 4. Paired-Path Efficient Frontier Sweep (D10 - D35, 5,000 sims)
print("\\n" + "=" * 135)
print(" 🚀 SPLIT-ADJUSTED PAIRED-PATH EFFICIENT FRONTIER SWEEP (D10 - D35, 5,000 SIMS)")
print("=" * 135)

RISK_LEVELS = [
    (0.0010, "SPLIT + D10 (0.10%)"),
    (0.0015, "SPLIT + D15 (0.15%)"),
    (0.0020, "SPLIT + D20 (0.20%)"),
    (0.0025, "SPLIT + D25 (0.25%)"),
    (0.0030, "SPLIT + D30 (0.30%)"),
    (0.0035, "SPLIT + D35 (0.35%)"),
]

n_sims = 5000
block_size = 20

print(f"{'Config':<20} | {'CAGR':<6} | {'HistDD':<6} | {'Calmar':<6} | {'PF':<4} | {'MedCAGR':<7} | {'5thCAGR':<7} | {'MedDD':<6} | {'95thDD':<6} | {'99thDD':<6} | {'P(DD>10%)':<9}")
print("-" * 145)

base_ret = eq_d25_s['equity'].pct_change().dropna().values
n_days = len(base_ret)
n_blocks = int(np.ceil(n_days / block_size))

np.random.seed(42)
shared_starts = [np.random.randint(0, n_days - block_size + 1, size=n_blocks) for _ in range(n_sims)]

for risk_val, label in RISK_LEVELS:
    eq_df_c, tdf_c, _ = run_adaptive_simulation(risk_val)
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

    print(f"{label:<20} | {cagr_hist:>5.2f}% | {max_dd_hist:>5.2f}% | {calmar_hist:>6.2f} | {pf_c:>4.2f} | {med_cagr:>6.2f}% | {p5_cagr:>6.2f}% | {med_dd:>5.2f}% | {p95_dd:>5.2f}% | {p99_dd:>5.2f}% | {prob_dd10:>8.1f}%")

print("=" * 145)
"""

with open("/home/moonrider1968/bots/run_config_b_d3_integration_SPLIT.py", "w") as f:
    f.write(base_split_engine + split_runner)

print("SPLIT candidate updated with reconciliation suite.")
