#!/usr/bin/env python3

from pathlib import Path
import re
import numpy as np
import pandas as pd

ROOT = Path("/home/moonrider1968/bots")
RES = ROOT / "valeze2_oanda_h4_results"
TRADE_FILE = RES / "c1_trades.csv"

INITIAL_NAV = 100_000.0
RISK_PCT = 0.005
MAX_POSITIONS = 3

# ------------------------------------------------------------
# PRE-DECLARED SELECTION RULES
# ------------------------------------------------------------
# These are NOT optimized in this script.
#
# A sleeve = instrument + direction.
#
# For each holdout year:
#   * use prior years only
#   * >= 15 prior trades
#   * prior aggregate MeanR > 0
#   * prior aggregate PF > 1.05
#   * >= 50% of available prior calendar years positive
#
# Deliberately looser than final production gates because the
# training history is short. The OOS results determine survival.
# ------------------------------------------------------------

MIN_TRAIN_TRADES = 15
MIN_TRAIN_MEAN_R = 0.0
MIN_TRAIN_PF = 1.05
MIN_POSITIVE_YEAR_FRAC = 0.50

HOLDOUT_YEARS = [2025, 2026]


def pf(s):
    s = pd.to_numeric(s, errors="coerce").dropna()
    gp = s[s > 0].sum()
    gl = -s[s < 0].sum()

    if gl <= 0:
        return np.inf if gp > 0 else np.nan

    return float(gp / gl)


def metrics(g):
    if len(g) == 0:
        return {
            "Trades": 0,
            "WinPct": np.nan,
            "MeanR": np.nan,
            "PF": np.nan,
            "GrossMeanR": np.nan,
            "CostR": np.nan,
        }

    r = g["net_r"]

    return {
        "Trades": int(len(g)),
        "WinPct": 100.0 * float((r > 0).mean()),
        "MeanR": float(r.mean()),
        "PF": pf(r),
        "GrossMeanR": float(g["gross_r"].mean()),
        "CostR": float(g["cost_r"].mean()),
    }


def max_dd_pct(eq):
    if len(eq) == 0:
        return np.nan

    s = pd.Series(eq, dtype=float)
    peak = s.cummax()
    dd = s / peak - 1.0
    return float(-dd.min() * 100.0)


def sleeve_training_stats(train):
    rows = []

    for (inst, direction), g in train.groupby(
        ["instrument", "direction"]
    ):
        annual = (
            g.groupby("year")["net_r"]
            .agg(["count", "mean"])
            .reset_index()
        )

        tested_years = len(annual)
        positive_years = int((annual["mean"] > 0).sum())

        r = g["net_r"]

        rows.append({
            "instrument": inst,
            "direction": direction,
            "TrainTrades": len(g),
            "TrainMeanR": r.mean(),
            "TrainPF": pf(r),
            "TrainYears": tested_years,
            "TrainPositiveYears": positive_years,
            "TrainPositiveYearFrac": (
                positive_years / tested_years
                if tested_years else np.nan
            ),
        })

    return pd.DataFrame(rows)


def select_sleeves(train):
    st = sleeve_training_stats(train)

    if st.empty:
        return st, st

    selected = st[
        (st["TrainTrades"] >= MIN_TRAIN_TRADES)
        & (st["TrainMeanR"] > MIN_TRAIN_MEAN_R)
        & (st["TrainPF"] >= MIN_TRAIN_PF)
        & (
            st["TrainPositiveYearFrac"]
            >= MIN_POSITIVE_YEAR_FRAC
        )
    ].copy()

    return st, selected


def simulate_realized_portfolio(trades):
    """
    Realized-R chronological portfolio.
    Enforces 3 concurrent positions and 0.50% risk at entry.

    MaxDD here is realized-equity DD, not full H4 MTM DD.
    """

    if trades.empty:
        return pd.DataFrame(), {
            "Accepted": 0,
            "SkippedSlots": 0,
            "EndNAV": INITIAL_NAV,
            "ReturnPct": 0.0,
            "RealizedMaxDDPct": 0.0,
        }

    x = trades.sort_values(
        ["entry_time", "instrument", "direction"]
    ).copy()

    nav = INITIAL_NAV
    open_pos = []
    ledger = []
    skipped = 0

    def close_due(now):
        nonlocal nav, open_pos

        keep = []

        for p in sorted(
            open_pos,
            key=lambda z: z["exit_time"]
        ):
            if p["exit_time"] <= now:
                pnl = p["risk_dollars"] * p["net_r"]
                nav += pnl

                ledger.append({
                    "time": p["exit_time"],
                    "event": "exit",
                    "instrument": p["instrument"],
                    "direction": p["direction"],
                    "year": p["year"],
                    "risk_dollars": p["risk_dollars"],
                    "net_r": p["net_r"],
                    "pnl": pnl,
                    "nav": nav,
                })
            else:
                keep.append(p)

        open_pos = keep

    for _, row in x.iterrows():
        close_due(row["entry_time"])

        if len(open_pos) >= MAX_POSITIONS:
            skipped += 1
            continue

        risk_dollars = nav * RISK_PCT

        open_pos.append({
            "instrument": row["instrument"],
            "direction": row["direction"],
            "entry_time": row["entry_time"],
            "exit_time": row["exit_time"],
            "year": row["year"],
            "net_r": row["net_r"],
            "risk_dollars": risk_dollars,
        })

        ledger.append({
            "time": row["entry_time"],
            "event": "entry",
            "instrument": row["instrument"],
            "direction": row["direction"],
            "year": row["year"],
            "risk_dollars": risk_dollars,
            "net_r": np.nan,
            "pnl": 0.0,
            "nav": nav,
        })

    # Close remaining trades.
    for p in sorted(open_pos, key=lambda z: z["exit_time"]):
        pnl = p["risk_dollars"] * p["net_r"]
        nav += pnl

        ledger.append({
            "time": p["exit_time"],
            "event": "exit",
            "instrument": p["instrument"],
            "direction": p["direction"],
            "year": p["year"],
            "risk_dollars": p["risk_dollars"],
            "net_r": p["net_r"],
            "pnl": pnl,
            "nav": nav,
        })

    led = pd.DataFrame(ledger).sort_values(
        ["time", "event"]
    ).reset_index(drop=True)

    exits = led[led["event"] == "exit"]

    return led, {
        "Accepted": len(exits),
        "SkippedSlots": skipped,
        "EndNAV": nav,
        "ReturnPct": (nav / INITIAL_NAV - 1.0) * 100.0,
        "RealizedMaxDDPct": max_dd_pct(exits["nav"]),
    }


# ============================================================
# LOAD
# ============================================================

if not TRADE_FILE.exists():
    raise SystemExit(f"MISSING: {TRADE_FILE}")

df = pd.read_csv(TRADE_FILE)

for c in ["entry_time", "exit_time"]:
    df[c] = pd.to_datetime(
        df[c],
        utc=True,
        errors="coerce",
    )

for c in ["gross_r", "cost_r", "net_r"]:
    df[c] = pd.to_numeric(
        df[c],
        errors="coerce",
    )

df = df.dropna(
    subset=[
        "instrument",
        "direction",
        "entry_time",
        "exit_time",
        "gross_r",
        "cost_r",
        "net_r",
    ]
).copy()

df["year"] = df["entry_time"].dt.year

print("=" * 150)
print("C1 ANCHORED WALK-FORWARD OOS SLEEVE SELECTION")
print("=" * 150)
print(f"Trades loaded: {len(df)}")
print()
print("Selection rule:")
print(f"  Min training trades       : {MIN_TRAIN_TRADES}")
print(f"  Training MeanR            : > {MIN_TRAIN_MEAN_R:+.3f}R")
print(f"  Training PF               : >= {MIN_TRAIN_PF:.2f}")
print(
    f"  Positive training years   : >= "
    f"{MIN_POSITIVE_YEAR_FRAC:.0%}"
)
print("  Parameters                : UNCHANGED")
print("=" * 150)

all_oos = []
selection_rows = []

# ============================================================
# WALK FORWARD
# ============================================================

for holdout in HOLDOUT_YEARS:

    train = df[df["year"] < holdout].copy()
    test = df[df["year"] == holdout].copy()

    train_stats, selected = select_sleeves(train)

    print()
    print("=" * 150)
    print(f"HOLDOUT YEAR: {holdout}")
    print("=" * 150)

    print(
        f"Training period: "
        f"{train['year'].min()}-{train['year'].max()} | "
        f"{len(train)} trades"
    )
    print(
        f"Holdout: {holdout} | {len(test)} trades"
    )

    print()
    print("SELECTED USING PRIOR DATA ONLY:")

    if selected.empty:
        print("NONE")
        continue

    selected = selected.sort_values(
        ["TrainMeanR", "TrainPF"],
        ascending=[False, False]
    )

    print(
        selected.to_string(
            index=False,
            float_format=lambda x: f"{x:,.4f}"
        )
    )

    keys = set(
        zip(
            selected["instrument"],
            selected["direction"],
        )
    )

    oos = test[
        test.apply(
            lambda r: (
                r["instrument"],
                r["direction"],
            ) in keys,
            axis=1,
        )
    ].copy()

    oos["holdout_year"] = holdout
    all_oos.append(oos)

    print()
    print("HOLDOUT RESULTS — SELECTED SLEEVES:")

    hold_rows = []

    for (inst, direction), g in oos.groupby(
        ["instrument", "direction"]
    ):
        m = metrics(g)

        hold_rows.append({
            "instrument": inst,
            "direction": direction,
            **m,
        })

    hold_df = pd.DataFrame(hold_rows)

    if hold_df.empty:
        print("NO HOLDOUT TRADES")
    else:
        print(
            hold_df.sort_values(
                "MeanR",
                ascending=False
            ).to_string(
                index=False,
                float_format=lambda x: f"{x:,.4f}"
            )
        )

    overall = metrics(oos)

    print()
    print(
        f"OOS {holdout}: "
        f"Trades={overall['Trades']} | "
        f"Win={overall['WinPct']:.2f}% | "
        f"MeanR={overall['MeanR']:+.4f} | "
        f"PF={overall['PF']:.3f} | "
        f"GrossMeanR={overall['GrossMeanR']:+.4f} | "
        f"Cost={overall['CostR']:.4f}R"
    )

    for _, r in selected.iterrows():
        keytest = test[
            (test["instrument"] == r["instrument"])
            & (test["direction"] == r["direction"])
        ]

        km = metrics(keytest)

        selection_rows.append({
            "holdout_year": holdout,
            "instrument": r["instrument"],
            "direction": r["direction"],
            "TrainTrades": r["TrainTrades"],
            "TrainMeanR": r["TrainMeanR"],
            "TrainPF": r["TrainPF"],
            "TrainPositiveYearFrac":
                r["TrainPositiveYearFrac"],
            "OOSTrades": km["Trades"],
            "OOSMeanR": km["MeanR"],
            "OOSPF": km["PF"],
            "OOSWinPct": km["WinPct"],
        })


# ============================================================
# STITCHED OOS
# ============================================================

print()
print("=" * 150)
print("STITCHED TRUE WALK-FORWARD OOS")
print("=" * 150)

if all_oos:
    stitched = pd.concat(
        all_oos,
        ignore_index=True,
    ).sort_values(
        ["entry_time", "instrument", "direction"]
    )
else:
    stitched = pd.DataFrame()

if stitched.empty:
    print("NO OOS TRADES SELECTED")
else:
    sm = metrics(stitched)

    print(
        f"Trades        : {sm['Trades']}"
    )
    print(
        f"Win rate      : {sm['WinPct']:.2f}%"
    )
    print(
        f"Gross MeanR   : {sm['GrossMeanR']:+.4f}R"
    )
    print(
        f"Cost drag     : {sm['CostR']:.4f}R"
    )
    print(
        f"NET MeanR     : {sm['MeanR']:+.4f}R"
    )
    print(
        f"Profit factor : {sm['PF']:.3f}"
    )

    # Year attribution
    print()
    print("OOS BY YEAR:")

    yrrows = []

    for year, g in stitched.groupby("year"):
        m = metrics(g)
        yrrows.append({
            "year": year,
            **m,
        })

    yrdf = pd.DataFrame(yrrows)

    print(
        yrdf.to_string(
            index=False,
            float_format=lambda x: f"{x:,.4f}"
        )
    )

    # Sleeve attribution
    print()
    print("OOS BY SLEEVE:")

    srows = []

    for (inst, direction), g in stitched.groupby(
        ["instrument", "direction"]
    ):
        m = metrics(g)

        srows.append({
            "instrument": inst,
            "direction": direction,
            **m,
        })

    sdf = pd.DataFrame(srows).sort_values(
        "MeanR",
        ascending=False
    )

    print(
        sdf.to_string(
            index=False,
            float_format=lambda x: f"{x:,.4f}"
        )
    )

    # Portfolio
    ledger, pm = simulate_realized_portfolio(stitched)

    print()
    print("STITCHED OOS PORTFOLIO:")
    print(
        f"Accepted trades       : {pm['Accepted']}"
    )
    print(
        f"Skipped by 3-slot cap : {pm['SkippedSlots']}"
    )
    print(
        f"Ending NAV            : ${pm['EndNAV']:,.2f}"
    )
    print(
        f"Return                : {pm['ReturnPct']:+.2f}%"
    )
    print(
        f"Realized MaxDD        : {pm['RealizedMaxDDPct']:.2f}%"
    )

    stitched.to_csv(
        RES / "c1_walkforward_oos_trades.csv",
        index=False,
    )

    ledger.to_csv(
        RES / "c1_walkforward_oos_ledger.csv",
        index=False,
    )


# ============================================================
# WALK-FORWARD PASS / FAIL
# ============================================================

print()
print("=" * 150)
print("WALK-FORWARD DECISION")
print("=" * 150)

if stitched.empty:
    print("FAIL — no OOS trades")
else:
    sm = metrics(stitched)

    year_means = (
        stitched.groupby("year")["net_r"].mean()
    )

    positive_years = int((year_means > 0).sum())
    tested_years = int(len(year_means))

    gates = {
        "Trades >= 75": sm["Trades"] >= 75,
        "Net MeanR > +0.025": sm["MeanR"] > 0.025,
        "PF >= 1.15": sm["PF"] >= 1.15,
        "Both OOS years positive":
            tested_years >= 2
            and positive_years == tested_years,
    }

    for k, v in gates.items():
        print(
            f"{k:28s}: "
            f"{'PASS' if v else 'FAIL'}"
        )

    passed = all(gates.values())

    print()
    print(
        "FINAL WALK-FORWARD RESULT: "
        + ("PASS" if passed else "FAIL")
    )

    if passed:
        print(
            "Next stage: fixed-sleeve MTM portfolio replay, "
            "historical bid/ask friction and perturbation."
        )
    else:
        print(
            "Do not optimize yet. Diagnose which training-selected "
            "sleeves failed OOS first."
        )


# ============================================================
# SAVE SELECTION AUDIT
# ============================================================

sel = pd.DataFrame(selection_rows)

sel.to_csv(
    RES / "c1_walkforward_selection_audit.csv",
    index=False,
)


# ============================================================
# DEEPER F0 FORENSICS
# ============================================================

print()
print("=" * 150)
print("F0 DEEP FORENSIC SEARCH")
print("=" * 150)

# Search text-like files for references to F0 / 628 events.
suffixes = {
    ".py", ".txt", ".log", ".json",
    ".md", ".sh", ".csv"
}

hits = []

skip_parts = {
    ".git",
    "__pycache__",
    "venv",
    ".venv",
}

for p in ROOT.rglob("*"):

    if not p.is_file():
        continue

    if p.suffix.lower() not in suffixes:
        continue

    if any(part in skip_parts for part in p.parts):
        continue

    try:
        if p.stat().st_size > 20_000_000:
            continue

        text = p.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        low = text.lower()

        score = 0
        reasons = []

        if re.search(r"\bf0\b", low):
            score += 10
            reasons.append("F0")

        if "628" in text:
            score += 10
            reasons.append("628")

        if "net mean" in low or "meanr" in low:
            score += 2

        if "profit factor" in low or "pf" in low:
            score += 1

        if "eur_usd" in low and "gbp_usd" in low:
            score += 2

        if "h4" in low:
            score += 2

        if score >= 10:
            hits.append(
                (
                    score,
                    str(p),
                    ",".join(reasons),
                )
            )

    except Exception:
        pass

hits.sort(
    key=lambda z: (-z[0], z[1])
)

if not hits:
    print("No F0/628 textual references found.")
else:
    for score, path, reason in hits[:60]:
        print(
            f"SCORE={score:3d}  {path}"
        )
        print(
            f"           {reason}"
        )

print()
print("=" * 150)
print("COMPLETE")
print("=" * 150)
