#!/usr/bin/env python3

from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path("/home/moonrider1968/bots")
RESULTS = ROOT / "valeze2_oanda_h4_results"
C1_FILE = RESULTS / "c1_trades.csv"

PAIRS = [
    "EUR_USD","GBP_USD","USD_JPY",
    "AUD_USD","USD_CAD","NZD_USD",
    "USD_CHF","EUR_JPY","GBP_JPY",
]

print("=" * 150)
print("C1 OANDA H4 — PAIR × DIRECTION × YEAR ATTRIBUTION AUDIT")
print("=" * 150)

if not C1_FILE.exists():
    raise SystemExit(f"MISSING: {C1_FILE}")

df = pd.read_csv(C1_FILE)

for c in ["entry_time", "exit_time"]:
    df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")

for c in ["gross_r", "cost_r", "net_r"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

df = df.dropna(
    subset=[
        "instrument",
        "direction",
        "entry_time",
        "gross_r",
        "cost_r",
        "net_r",
    ]
).copy()

df["year"] = df["entry_time"].dt.year


def pf(series):
    s = pd.to_numeric(series, errors="coerce").dropna()
    gp = s[s > 0].sum()
    gl = -s[s < 0].sum()
    if gl == 0:
        return np.inf if gp > 0 else np.nan
    return gp / gl


def stats(g):
    r = g["net_r"]

    return pd.Series({
        "Trades": len(g),
        "WinPct": 100.0 * (r > 0).mean(),
        "GrossMeanR": g["gross_r"].mean(),
        "CostDragR": g["cost_r"].mean(),
        "NetMeanR": r.mean(),
        "PF": pf(r),
        "MedianR": r.median(),
        "AvgWinnerR": r[r > 0].mean() if (r > 0).any() else np.nan,
        "AvgLoserR": r[r < 0].mean() if (r < 0).any() else np.nan,
        "StopPct": 100.0 * g["exit_reason"].astype(str).str.contains("stop").mean(),
        "AvgBars": g["holding_bars"].mean(),
    })


# ============================================================
# 1. PAIR × DIRECTION
# ============================================================

pd_dir = (
    df.groupby(["instrument", "direction"], observed=True)
      .apply(stats, include_groups=False)
      .reset_index()
)

pd_dir = pd_dir.sort_values(
    ["NetMeanR", "PF"],
    ascending=[False, False]
)

print()
print("=" * 150)
print("PAIR × DIRECTION")
print("=" * 150)
print(pd_dir.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))


# ============================================================
# 2. PAIR COMBINED
# ============================================================

pair = (
    df.groupby("instrument", observed=True)
      .apply(stats, include_groups=False)
      .reset_index()
)

pair = pair.sort_values(
    ["NetMeanR", "PF"],
    ascending=[False, False]
)

print()
print("=" * 150)
print("PAIR COMBINED")
print("=" * 150)
print(pair.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))


# ============================================================
# 3. PAIR × DIRECTION × YEAR
# ============================================================

pdy = (
    df.groupby(
        ["instrument", "direction", "year"],
        observed=True
    )
    .apply(stats, include_groups=False)
    .reset_index()
)

print()
print("=" * 150)
print("PAIR × DIRECTION × YEAR")
print("=" * 150)
print(
    pdy.sort_values(
        ["instrument","direction","year"]
    ).to_string(
        index=False,
        float_format=lambda x: f"{x:,.4f}"
    )
)


# ============================================================
# 4. STABILITY SCORE
#
# This is intentionally NOT parameter optimization.
# It simply checks whether an already-existing sleeve has:
#   - enough trades
#   - positive total expectancy
#   - PF > 1
#   - multiple positive calendar years
# ============================================================

rows = []

for (instrument, direction), g in df.groupby(
    ["instrument","direction"]
):
    annual = (
        g.groupby("year")["net_r"]
        .agg(["count","mean"])
        .reset_index()
    )

    tested_years = int(len(annual))
    positive_years = int((annual["mean"] > 0).sum())

    m = stats(g)

    trades = int(m["Trades"])
    meanr = float(m["NetMeanR"])
    pfx = float(m["PF"])

    sample_pass = trades >= 40
    edge_pass = meanr > 0.025
    pf_pass = pfx >= 1.15
    stability_pass = (
        tested_years >= 3
        and positive_years >= max(2, tested_years - 1)
    )

    score = sum([
        sample_pass,
        edge_pass,
        pf_pass,
        stability_pass,
    ])

    rows.append({
        "instrument": instrument,
        "direction": direction,
        "Trades": trades,
        "NetMeanR": meanr,
        "PF": pfx,
        "PositiveYears": positive_years,
        "TestedYears": tested_years,
        "SampleGate": sample_pass,
        "EdgeGate": edge_pass,
        "PFGate": pf_pass,
        "StabilityGate": stability_pass,
        "Score_4": score,
    })

stable = pd.DataFrame(rows).sort_values(
    ["Score_4","NetMeanR","PF"],
    ascending=[False,False,False]
)

print()
print("=" * 150)
print("C1 SLEEVE STABILITY RANKING")
print("=" * 150)
print(
    stable.to_string(
        index=False,
        float_format=lambda x: f"{x:,.4f}"
    )
)


# ============================================================
# 5. STRICT SURVIVORS
# ============================================================

survivors = stable[
    (stable["SampleGate"])
    & (stable["EdgeGate"])
    & (stable["PFGate"])
    & (stable["StabilityGate"])
].copy()

print()
print("=" * 150)
print("STRICT C1 SURVIVORS")
print("=" * 150)

if survivors.empty:
    print("NONE")
else:
    print(
        survivors.to_string(
            index=False,
            float_format=lambda x: f"{x:,.4f}"
        )
    )


# ============================================================
# 6. SAVE
# ============================================================

pd_dir.to_csv(
    RESULTS / "c1_pair_direction.csv",
    index=False
)

pair.to_csv(
    RESULTS / "c1_pair_combined.csv",
    index=False
)

pdy.to_csv(
    RESULTS / "c1_pair_direction_year.csv",
    index=False
)

stable.to_csv(
    RESULTS / "c1_sleeve_stability.csv",
    index=False
)

survivors.to_csv(
    RESULTS / "c1_strict_survivors.csv",
    index=False
)


# ============================================================
# 7. F0 FORENSIC DISCOVERY
# ============================================================

print()
print("=" * 150)
print("F0 628-EVENT FORENSIC SEARCH")
print("=" * 150)

candidates = []

for p in ROOT.rglob("*.csv"):

    if "valeze2_oanda_h4_results" in str(p):
        continue

    try:
        x = pd.read_csv(p)

        score = 0
        reasons = []

        if len(x) == 628:
            score += 20
            reasons.append("628 rows")

        cols = {str(c).lower() for c in x.columns}

        for expected in [
            "instrument",
            "entry_time",
            "direction",
            "r_multiple",
            "outcome",
            "target_price",
            "stop_price",
        ]:
            if expected in cols:
                score += 2
                reasons.append(expected)

        if "f0" in p.name.lower():
            score += 10
            reasons.append("filename F0")

        if "event" in p.name.lower():
            score += 3

        if "h4" in p.name.lower():
            score += 3

        if score >= 8:
            candidates.append(
                (
                    score,
                    len(x),
                    str(p),
                    ",".join(reasons),
                )
            )

    except Exception:
        pass

candidates.sort(
    key=lambda z: (-z[0], abs(z[1] - 628), z[2])
)

if not candidates:
    print("No strong CSV candidates found.")
else:
    for score, n, path, why in candidates[:40]:
        print(
            f"SCORE={score:3d} ROWS={n:5d} "
            f"FILE={path}"
        )
        print(f"      {why}")

print()
print("=" * 150)
print("AUDIT COMPLETE")
print("=" * 150)
