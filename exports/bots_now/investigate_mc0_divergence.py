from pathlib import Path
import pandas as pd
import numpy as np

SOURCE = Path("/home/moonrider1968/bots/run_phase10_fixed_ndays.py")

print("=" * 135)
print(" 🔍 MC-0 MAX-DRAWDOWN DIVERGENCE AUDIT")
print("=" * 135)

src = SOURCE.read_text()
marker = 'print("\\n--- GATEWAY CHECK: Chronological Tape Reconstruction (MC-0) ---")'
if marker not in src:
    raise RuntimeError("Could not locate Phase-10 gateway marker.")

prefix = src.split(marker, 1)[0]
ns = {
    "__name__": "phase10_audit_loader",
    "__file__": str(SOURCE),
}

exec(compile(prefix, str(SOURCE), "exec"), ns)

tape = ns["tape"]
simulate_single_path = ns["simulate_single_path"]
INITIAL_NAV = ns["INITIAL_NAV"]
years = ns["years"]

print()
print(f"Loaded tape records: {len(tape)}")

eq = simulate_single_path(tape, mode="G2")

if len(eq) != len(tape):
    raise RuntimeError(
        f"Equity/tape length mismatch: equity={len(eq)}, tape={len(tape)}"
    )

dates = [x["date"] for x in tape]
df = pd.DataFrame({
    "date": dates,
    "equity": eq,
})
df["peak"] = df["equity"].cummax()
df["dd_pct"] = (
    (df["peak"] - df["equity"]) / df["peak"]
) * 100.0

trough_i = int(df["dd_pct"].idxmax())
trough = df.loc[trough_i]

peak_value = trough["peak"]
peak_candidates = df.loc[
    :trough_i
].index[
    np.isclose(
        df.loc[:trough_i, "equity"].values,
        peak_value,
        rtol=0,
        atol=0.01,
    )
]

if len(peak_candidates):
    peak_i = int(peak_candidates[-1])
else:
    peak_i = int(
        df.loc[:trough_i, "equity"].idxmax()
    )

peak_row = df.loc[peak_i]

print()
print("--- SYNTHETIC CHRONOLOGICAL G2 MAX-DD EPISODE ---")
print(f"Peak index    : {peak_i}")
print(f"Peak date     : {peak_row['date']}")
print(f"Peak equity   : ${peak_row['equity']:,.2f}")
print()
print(f"Trough index  : {trough_i}")
print(f"Trough date   : {trough['date']}")
print(f"Trough equity : ${trough['equity']:,.2f}")
print(f"Maximum DD    : {trough['dd_pct']:.4f}%")
print(f"DD duration   : {trough_i - peak_i} trading records")

window_start = max(0, trough_i - 15)
window_end = min(len(df) - 1, trough_i + 15)

print()
print("--- ±15 RECORD WINDOW AROUND MAX-DD TROUGH ---")
print(
    df.loc[
        window_start:window_end,
        ["date", "equity", "peak", "dd_pct"]
    ].to_string(
        index=True,
        formatters={
            "equity": lambda x: f"${x:,.2f}",
            "peak": lambda x: f"${x:,.2f}",
            "dd_pct": lambda x: f"{x:.4f}%"
        }
    )
)

print()
print("--- TAPE STATE AROUND MAX-DD TROUGH ---")
for i in range(max(0, trough_i - 5), min(len(tape), trough_i + 6)):
    day = tape[i]
    print(
        f"{i:4d} | "
        f"{day['date']} | "
        f"EQ=${eq[i]:,.2f} | "
        f"DD={df.loc[i, 'dd_pct']:.3f}% | "
        f"macro_bull={day['macro_bull']} | "
        f"SPYvol={day['spy_vol']:.3f} | "
        f"XLKsig={len(day['long_signals'])} | "
        f"DONCHsig={len(day['donch_signals'])} | "
        f"SHORTsig={len(day['short_signals'])}"
    )

terminal = eq[-1]
cagr = (
    ((terminal / INITIAL_NAV) ** (1.0 / years)) - 1.0
) * 100.0
synthetic_dd = df["dd_pct"].max()

TARGET_NAV = 219100.34
TARGET_CAGR = 8.48
TARGET_DD = 9.08

print()
print("=" * 135)
print(" MC-0 RECONCILIATION SUMMARY")
print("=" * 135)
print(f"Synthetic NAV : ${terminal:,.2f}")
print(f"Canonical NAV : ${TARGET_NAV:,.2f}")
print(
    f"NAV difference : "
    f"${terminal - TARGET_NAV:+,.2f}"
)
print()
print(f"Synthetic CAGR : {cagr:.4f}%")
print(f"Canonical CAGR : {TARGET_CAGR:.4f}%")
print(
    f"CAGR difference : "
    f"{cagr - TARGET_CAGR:+.4f} pp"
)
print()
print(f"Synthetic Max DD : {synthetic_dd:.4f}%")
print(f"Canonical Max DD : {TARGET_DD:.4f}%")
print(
    f"DD difference : "
    f"{synthetic_dd - TARGET_DD:+.4f} pp"
)
print("=" * 135)
print("✅ Audit complete — no MC certification was run.")
print("=" * 135)
