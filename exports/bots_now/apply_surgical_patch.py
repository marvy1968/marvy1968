import os
import hashlib

# Read baseline copy
with open("/home/moonrider1968/bots/run_config_b_d3_integration_BASELINE_COPY.py", "r") as f:
    code = f.read()

# 1. Expand ALL_SYMBOLS to include Donchian satellite universe
target_all_syms = 'ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + ["SPY"])))'
replacement_all_syms = """DONCHIAN_SATELLITE_UNIVERSE = [
    "MSFT", "NVDA", "AVGO", "AMD", "CRM", "TSLA", "AMZN", "GOOGL", 
    "META", "AAPL", "NFLX", "SNOW", "PLTR", "DDOG", "CRWD", "NET", "PANW"
]
ALL_SYMBOLS = sorted(list(set(XLK_LONG_UNIVERSE + STAGE4_SHORT_FLEET + DONCHIAN_SATELLITE_UNIVERSE + ["SPY"])))"""

if target_all_syms in code:
    code = code.replace(target_all_syms, replacement_all_syms)
else:
    raise RuntimeError("ALL_SYMBOLS target not found.")

# 2. Add rolling correlation helper function before simulation
corr_helper = """
def get_rolling_corr(sym1, sym2, curr_date, window=60):
    df1 = master_daily.get(sym1)
    df2 = master_daily.get(sym2)
    if df1 is None or df2 is None: return 0.0
    s1 = df1.loc[:curr_date, 'returns'].dropna().tail(window)
    s2 = df2.loc[:curr_date, 'returns'].dropna().tail(window)
    common = pd.concat([s1, s2], axis=1).dropna()
    if len(common) < 20: return 0.0
    return float(common.corr().iloc[0, 1])
"""
code = code.replace("def get_alpaca_bars", corr_helper + "\ndef get_alpaca_bars")

# 3. Update function signature: def run_adaptive_simulation(donchian_risk_pct=0.0):
code = code.replace("def run_adaptive_simulation():", "def run_adaptive_simulation(donchian_risk_pct=0.0):")

# 4. Initialize Donchian local variables inside the simulation function
init_target = "    realized_pnl = 0.0"
init_replacement = """    realized_pnl = 0.0
    open_donch_longs = {}
    donch_pnl_total = 0.0
    peak_concurrent_donch = 0"""
code = code.replace(init_target, init_replacement, 1)

# 5. Add Donchian position management right after active long positions evaluation (Step 2)
mgt_target = "        # 3. Daily Portfolio Accounting & Governor Transition Logic"
donchian_mgt = """        # 2.5 Manage Active Donchian Satellite Positions
        if donchian_risk_pct > 0.0:
            for sym in list(open_donch_longs.keys()):
                pos = open_donch_longs[sym]
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                row = df_d.loc[curr_date]
                atr_d = row['atr'] if pd.notna(row['atr']) and row['atr'] > 0 else pos['initial_atr']
                if row['low'] <= pos['virtual_sl']:
                    exit_px = min(row['open'], pos['virtual_sl']) - SLIP
                    raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
                    pnl = (exit_px - pos['entry_px']) * pos['qty']
                    realized_pnl += pnl
                    donch_pnl_total += pnl
                    closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": curr_date, "raw_r": raw_r, "dollar_pnl": pnl})
                    del open_donch_longs[sym]
                    continue
                mfe_r = (row['high'] - pos['entry_px']) / pos['risk_prs']
                if mfe_r >= 1.5 and pos['tier'] < 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], pos['entry_px'])
                    pos['tier'] = 1
                if pos['tier'] >= 1:
                    pos['virtual_sl'] = max(pos['virtual_sl'], row['sma50'] - (0.5 * atr_d))
            peak_concurrent_donch = max(peak_concurrent_donch, len(open_donch_longs))
"""
code = code.replace(mgt_target, donchian_mgt + "\n" + mgt_target)

# 6. Update current_eq calculation to include Donchian realized and unrealized PnL
eq_target = "        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)\n        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs"
eq_replacement = """        unrealized_longs = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_longs.items() if curr_date in master_daily[s].index)
        donch_unrealized = 0.0
        if donchian_risk_pct > 0.0:
            donch_unrealized = sum((master_daily[s].loc[curr_date]['close'] - p['entry_px']) * p['qty'] for s, p in open_donch_longs.items() if curr_date in master_daily[s].index)
        current_eq = INITIAL_NAV + realized_pnl + unrealized_longs + donch_pnl_total + donch_unrealized"""
code = code.replace(eq_target, eq_replacement)

# 7. Add Donchian entry scanner right alongside XLK long entries (Step 4)
entry_target = "        # 5. Queue Stage-4 Short Signals for Next-Day Limit Entry (Macro Bear Only)"
donchian_entries = """        # 4.5 Donchian Satellite Entries (Macro Bull Only)
        active_donch_risk = donchian_risk_pct if active_config == "B" else (donchian_risk_pct * 0.50)
        if donchian_risk_pct > 0.0 and active_donch_risk > 0 and len(open_donch_longs) < MAX_POSITIONS and macro_bull:
            for sym in DONCHIAN_SATELLITE_UNIVERSE:
                if sym in open_donch_longs: continue
                df_d = master_daily.get(sym)
                if df_d is None or curr_date not in df_d.index: continue
                idx = df_d.index.get_loc(curr_date)
                if idx < 200: continue
                row = df_d.iloc[idx]
                atr = row['atr']
                if pd.isna(atr) or atr <= 0: continue
                if row['close'] > row['donchian_55']:
                    max_corr = 0.0
                    for held_sym in open_donch_longs:
                        c = get_rolling_corr(sym, held_sym, curr_date, window=60)
                        max_corr = max(max_corr, c)
                    if max_corr >= 0.80: continue
                    entry_px = row['close'] + SLIP + SPREAD
                    initial_sl = entry_px - (2.00 * atr)
                    rps = max(entry_px - initial_sl, 0.01)
                    qty = int((current_eq * active_donch_risk) / rps)
                    if qty > 0:
                        open_donch_longs[sym] = {
                            "entry_date": curr_date, "entry_px": entry_px, "qty": qty,
                            "risk_prs": rps, "virtual_sl": initial_sl, "tier": 0, "initial_atr": atr,
                            "days_held": 0, "peak_price": entry_px
                        }
                        break
"""
code = code.replace(entry_target, donchian_entries + "\n" + entry_target)

# 8. Final liquidation of surviving Donchian positions before eq_df creation
liq_target = "    eq_df = pd.DataFrame(daily_records)"
donchian_liq = """    # Final Liquidation of open Donchian positions
    if donchian_risk_pct > 0.0:
        for sym, pos in list(open_donch_longs.items()):
            df_d = master_daily[sym]
            window_df = df_d.loc[(df_d.index >= start_dt) & (df_d.index <= end_dt)]
            if window_df.empty: continue
            last_row = window_df.iloc[-1]
            exit_px = last_row['close'] - SLIP
            raw_r = (exit_px - pos['entry_px']) / pos['risk_prs']
            pnl = (exit_px - pos['entry_px']) * pos['qty']
            realized_pnl += pnl
            donch_pnl_total += pnl
            closed_trades.append({"sleeve": "DONCHIAN_SATELLITE", "symbol": sym, "entry_date": end_dt, "raw_r": raw_r, "dollar_pnl": pnl})
            del open_donch_longs[sym]
"""
code = code.replace(liq_target, donchian_liq + "\n" + liq_target)

# 9. Replace bottom execution to run Gate 0 assertion and risk allocation matrix sweep
bottom_exec_target = "eq_df, tdf, transitions = run_adaptive_simulation()"
allocation_sweep_code = """
print("\\n--- GATE 0: BASELINE RECONCILIATION CHECK (Satellite OFF) ---")
eq_df, tdf, transitions = run_adaptive_simulation(0.00)

total_ret = ((eq_df['equity'].iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
eq_peak = eq_df['equity'].cummax()
max_dd = ((eq_peak - eq_df['equity']) / eq_peak).max() * 100.0
days = (end_dt - start_dt).days
years = days / 365.25
cagr = (((eq_df['equity'].iloc[-1] / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
calmar = (cagr / max_dd) if max_dd > 0 else 0.0

r_arr = tdf['raw_r'].values if not tdf.empty else np.array([0])
wins = r_arr[r_arr > 0]
losses = r_arr[r_arr <= 0]
pf = float(np.sum(wins) / np.abs(np.sum(losses))) if len(losses) > 0 else float("inf")
exp_r = float(np.mean(r_arr)) if len(r_arr) > 0 else 0.0

ending_equity = eq_df['equity'].iloc[-1]
trans_count = len(transitions)

print(f"• Ending Equity  : ${ending_equity:,.2f} (Target: $175,444.31)")
print(f"• CAGR           : {cagr:.2f}% (Target: 6.01%)")
print(f"• Max DD         : {max_dd:.2f}% (Target: 7.75%)")
print(f"• Calmar         : {calmar:.2f} (Target: 0.78)")
print(f"• PF             : {pf:.2f} (Target: 1.34)")
print(f"• Expectancy     : {exp_r:+.2f}R/tr (Target: +0.09R/tr)")
print(f"• Transitions    : {trans_count} (Target: 17)")

assert abs(ending_equity - 175444.31) <= 1.00, f"Gate 0 Failed: Equity ${ending_equity} != $175,444.31"
assert abs(cagr - 6.01) <= 0.01, f"Gate 0 Failed: CAGR {cagr}% != 6.01%"
assert abs(max_dd - 7.75) <= 0.01, f"Gate 0 Failed: Max DD {max_dd}% != 7.75%"
assert abs(calmar - 0.78) <= 0.01, f"Gate 0 Failed: Calmar {calmar} != 0.78"
assert abs(pf - 1.34) <= 0.02, f"Gate 0 Failed: PF {pf} != 1.34"
assert abs(exp_r - 0.09) <= 0.02, f"Gate 0 Failed: Expectancy {exp_r} != +0.09R"
assert trans_count == 17, f"Gate 0 Failed: Transitions {trans_count} != 17"
print("✅ GATE 0 PASSED: Baseline identity verified. Initiating Satellite Risk Sweep...\\n")

CONFIGS = [
    (0.00, "B0 Control (Off)"),
    (0.0025, "B + D25 (0.25%)"),
    (0.0050, "B + D50 (0.50%)"),
    (0.0075, "B + D75 (0.75%)"),
    (0.0100, "B + D100 (1.00%)"),
]

print(f"{'Config':<18} | {'CAGR':<6} | {'TotRet':<7} | {'MaxDD':<6} | {'Calmar':<6} | {'PF':<4} | {'ExpR':<5} | {'2020 Ret/DD':<11} | {'2022 Ret/DD':<11} | {'Trans':<5} | {'Donch PnL':<10} | {'MaxPos'}")
print("-" * 135)

for risk_val, label in CONFIGS:
    eq_df_c, tdf_c, trans_c = run_adaptive_simulation(risk_val)
    tot_ret_c = ((eq_df_c['equity'].iloc[-1] - INITIAL_NAV) / INITIAL_NAV) * 100.0
    eq_peak_c = eq_df_c['equity'].cummax()
    max_dd_c = ((eq_peak_c - eq_df_c['equity']) / eq_peak_c).max() * 100.0
    cagr_c = (((eq_df_c['equity'].iloc[-1] / INITIAL_NAV) ** (1.0 / years)) - 1.0) * 100.0
    calmar_c = (cagr_c / max_dd_c) if max_dd_c > 0 else 0.0

    r_arr_c = tdf_c['raw_r'].values if not tdf_c.empty else np.array([0])
    wins_c = r_arr_c[r_arr_c > 0]
    losses_c = r_arr_c[r_arr_c <= 0]
    pf_c = float(np.sum(wins_c) / np.abs(np.sum(losses_c))) if len(losses_c) > 0 else float("inf")
    exp_r_c = float(np.mean(r_arr_c)) if len(r_arr_c) > 0 else 0.0

    # 2020 / 2022 Windows
    eq_2020 = eq_df_c.loc[datetime.date(2020, 1, 1):datetime.date(2020, 12, 31)]
    ret_2020 = ((eq_2020['equity'].iloc[-1] / eq_2020['equity'].iloc[0]) - 1.0) * 100.0 if not eq_2020.empty else 0.0
    peak_2020 = eq_2020['equity'].cummax()
    dd_2020 = ((peak_2020 - eq_2020['equity']) / peak_2020).max() * 100.0 if not eq_2020.empty else 0.0
    r20 = f"{ret_2020:+.1f}%/{dd_2020:.1f}%"

    eq_2022 = eq_df_c.loc[datetime.date(2022, 1, 1):datetime.date(2022, 12, 31)]
    ret_2022 = ((eq_2022['equity'].iloc[-1] / eq_2022['equity'].iloc[0]) - 1.0) * 100.0 if not eq_2022.empty else 0.0
    peak_2022 = eq_2022['equity'].cummax()
    dd_2022 = ((peak_2022 - eq_2022['equity']) / peak_2022).max() * 100.0 if not eq_2022.empty else 0.0
    r22 = f"{ret_2022:+.1f}%/{dd_2022:.1f}%"

    # Donchian PnL sum
    donch_pnl_c = tdf_c.loc[tdf_c['sleeve'] == 'DONCHIAN_SATELLITE', 'dollar_pnl'].sum() if not tdf_c.empty and 'sleeve' in tdf_c.columns else 0.0
    
    # We can infer peak concurrent donch from running simulation or tracking inside
    print(f"{label:<18} | {cagr_c:>5.2f}% | {tot_ret_c:>6.1f}% | {max_dd_c:>5.2f}% | {calmar_c:>6.2f} | {pf_c:>4.2f} | {exp_r_c:>+.2f}R | {r20:>11} | {r22:>11} | {len(trans_c):>5} | ${donch_pnl_c:>9,.0f} | {'N/A'}")

print("=" * 135)
"""

parts = code.split(bottom_exec_target)
if len(parts) > 1:
    code = parts[0] + allocation_sweep_code
else:
    raise RuntimeError("Bottom execution target not found.")

with open("/home/moonrider1968/bots/run_config_b_d3_integration.py", "w") as f:
    f.write(code)

print("Surgical patch applied successfully.")
