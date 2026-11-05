import numpy as np

np.random.seed(42)
n_trades = 2000

# Simulated trade maximum favorable excursion (MFE) distribution
mfe = np.random.exponential(scale=1.6, size=n_trades)

def simulate_combo(q1_pct, tp1_r, runner_type="fixed", runner_val=2.0):
    q2_pct = 1.0 - q1_pct
    pnl, wins = [], 0
    for peak in mfe:
        if peak < tp1_r:
            pnl.append(-1.0)
        else:
            wins += 1
            gain_leg1 = q1_pct * tp1_r
            if runner_type == "fixed":
                gain_leg2 = q2_pct * runner_val if peak >= runner_val else 0.0
            elif runner_type == "trailing":
                gain_leg2 = q2_pct * max(0.0, peak * runner_val)
            pnl.append(gain_leg1 + gain_leg2)

    tot = sum(pnl)
    wr = (wins / n_trades) * 100.0
    ev = tot / n_trades
    gains = sum(x for x in pnl if x > 0)
    losses = abs(sum(x for x in pnl if x < 0))
    pf = gains / losses if losses > 0 else 0.0
    return wr, tot, ev, pf

print("=" * 86)
print(f"{'MODEL DESCRIPTION':<38} | {'WIN RATE':>9} | {'TOTAL R':>9} | {'EV/TRADE':>9} | {'PF':>6}")
print("-" * 86)

print("--- 1. RUNNER TARGET EXTENSIONS (50/50 Split @ +0.75R) ---")
for r_tgt in [1.50, 2.00, 2.50, 3.00, 3.50]:
    w, t, e, p = simulate_combo(0.50, 0.75, "fixed", r_tgt)
    lbl = f"50/50 @ +0.75R + Runner @ +{r_tgt:.2f}R"
    print(f"{lbl:<38} | {w:>8.1f}% | {t:>8.2f}R | {e:>+8.3f}R | {p:>6.2f}")

print("\n--- 2. SIZE ALLOCATION SPLITS (Leg 1 @ +0.75R / Runner @ +2.0R) ---")
for q1, q2 in [(0.75, 0.25), (0.67, 0.33), (0.50, 0.50), (0.33, 0.67), (0.25, 0.75)]:
    w, t, e, p = simulate_combo(q1, 0.75, "fixed", 2.00)
    lbl = f"{int(q1*100)}/{int(q2*100)} Split (+0.75R / +2.0R)"
    print(f"{lbl:<38} | {w:>8.1f}% | {t:>8.2f}R | {e:>+8.3f}R | {p:>6.2f}")

print("\n--- 3. DYNAMIC TRAILING EXITS (Prior Bar Low Trail) ---")
for trail_eff in [0.55, 0.65, 0.75]:
    w, t, e, p = simulate_combo(0.50, 0.75, "trailing", trail_eff)
    lbl = f"50/50 @ +0.75R + Trail ({int(trail_eff*100)}% Peak Cap)"
    print(f"{lbl:<38} | {w:>8.1f}% | {t:>8.2f}R | {e:>+8.3f}R | {p:>6.2f}")
print("=" * 86)
