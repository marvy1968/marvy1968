#!/usr/bin/env python3

from pathlib import Path
import importlib.util
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
V3 = ROOT / "v3" / "stock_walkforward_v3.py"

if not V3.exists():
    raise SystemExit(f"Missing V3 base file: {V3}")

spec = importlib.util.spec_from_file_location("stock_v3_base", V3)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

print("===== STOCK VALIDATION REPAIR V3.1 =====")
print("Base engine: V3")
print("Repairs:")
print("  1. true +24 market-bar exits")
print("  2. continuous OOS equity reconstruction")
print("  3. diagnostic Monte Carlo below 100 trades")
print("Execution enabled: NO")


# ------------------------------------------------------------
# REPAIR 1:
# Store the actual timestamp of bar +24.
# ------------------------------------------------------------

def prep_symbol_v31(g):
    g = g.copy().sort_values("timestamp").reset_index(drop=True)

    close = g["close"].astype(float)
    high = g["high"].astype(float)
    low = g["low"].astype(float)
    volume = g["volume"].astype(float)

    g["mom24"] = close.pct_change(m.HOLDING_BARS)
    g["mom48"] = close.pct_change(m.HOLDING_BARS * 2)

    g["ma20"] = close.rolling(20).mean()
    g["ma50"] = close.rolling(50).mean()
    g["ma200"] = close.rolling(200).mean()

    g["vol20"] = volume.rolling(20).mean()

    g["vol_ratio"] = (
        volume /
        g["vol20"].replace(0, np.nan)
    )

    prev = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev).abs(),
            (low - prev).abs(),
        ],
        axis=1,
    ).max(axis=1)

    g["atr14"] = tr.rolling(14).mean()

    g["atr_pct"] = (
        g["atr14"] /
        close.replace(0, np.nan)
    )

    g["extension20"] = (
        close /
        g["ma20"].replace(0, np.nan)
        - 1.0
    )

    g["trend_quality"] = (
        g["ma20"] /
        g["ma50"].replace(0, np.nan)
        - 1.0
    )

    g["future_close"] = close.shift(
        -m.HOLDING_BARS
    )

    g["exit_time_true"] = g["timestamp"].shift(
        -m.HOLDING_BARS
    )

    g["raw_forward_return"] = (
        g["future_close"] /
        close.replace(0, np.nan)
        - 1.0
    )

    return g


# ------------------------------------------------------------
# REPAIR 2:
# Use true market-bar exit rather than entry + 24 clock hours.
# ------------------------------------------------------------

def portfolio_sim_v31(c):
    if c.empty:
        return pd.DataFrame()

    open_positions = []
    trades = []
    equity = m.INITIAL_EQUITY

    for ts, batch in c.groupby("timestamp", sort=True):

        open_positions = [
            p
            for p in open_positions
            if p["exit_time"] > ts
        ]

        available = (
            m.MAX_CONCURRENT -
            len(open_positions)
        )

        if available <= 0:
            continue

        used = {
            p["symbol"]
            for p in open_positions
        }

        for _, row in batch.head(
            max(available * 3, available)
        ).iterrows():

            symbol = str(row["symbol"])

            if symbol in used:
                continue

            atr_pct = float(row["atr_pct"])

            if (
                not np.isfinite(atr_pct)
                or atr_pct <= 0
            ):
                continue

            exit_time = row.get(
                "exit_time_true"
            )

            if pd.isna(exit_time):
                continue

            stop_distance = max(
                atr_pct * 1.5,
                0.01,
            )

            risk_dollars = (
                equity *
                m.RISK_PER_TRADE
            )

            position_dollars = min(
                risk_dollars / stop_distance,
                equity * m.MAX_POSITION_PCT,
            )

            if position_dollars <= 0:
                continue

            raw_return = float(
                row["raw_forward_return"]
            )

            net_return = (
                raw_return
                - m.ROUND_TRIP_COST_BPS
                / 10000.0
            )

            entry_equity = equity

            pnl = (
                position_dollars *
                net_return
            )

            equity += pnl

            exit_time = pd.Timestamp(
                exit_time
            )

            trades.append(
                {
                    "entry_time": ts,
                    "exit_time": exit_time,
                    "symbol": symbol,
                    "sector": row["sector"],
                    "entry_equity": entry_equity,
                    "position_dollars":
                        position_dollars,
                    "raw_return":
                        raw_return,
                    "net_return":
                        net_return,
                    "pnl":
                        pnl,
                    "equity":
                        equity,
                    "quality_score":
                        row["quality_score"],
                    "relative_strength":
                        row["relative_strength"],
                    "vol_ratio":
                        row["vol_ratio"],
                    "atr_pct":
                        row["atr_pct"],
                    "extension20":
                        row["extension20"],
                }
            )

            open_positions.append(
                {
                    "symbol": symbol,
                    "exit_time": exit_time,
                }
            )

            used.add(symbol)

            if (
                len(open_positions)
                >= m.MAX_CONCURRENT
            ):
                break

    return pd.DataFrame(trades)


# ------------------------------------------------------------
# REPAIR 3:
# Reconstruct ONE equity curve from trade returns.
# This prevents each walk-forward window from resetting the
# aggregate ROI calculation to $100,000.
# ------------------------------------------------------------

def summarize_v31(trades):
    if trades.empty:
        return {
            "trades": 0,
            "roi_pct": 0.0,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "expectancy_bps": 0.0,
            "max_drawdown_pct": 0.0,
        }

    t = trades.sort_values(
        ["entry_time", "exit_time", "symbol"]
    ).copy()

    returns = (
        t["net_return"]
        .astype(float)
        .to_numpy()
    )

    equity = m.INITIAL_EQUITY
    curve = [equity]

    for r in returns:
        equity *= (
            1.0
            + m.MAX_POSITION_PCT * r
        )

        curve.append(equity)

    roi = (
        equity /
        m.INITIAL_EQUITY
        - 1.0
    ) * 100.0

    return {
        "trades": int(len(t)),
        "roi_pct": float(roi),
        "win_rate_pct":
            float((returns > 0).mean() * 100.0),
        "profit_factor":
            m.safe_pf(returns),
        "expectancy_bps":
            float(returns.mean() * 10000.0),
        "max_drawdown_pct":
            m.max_dd(curve),
    }


# ------------------------------------------------------------
# REPAIR 4:
# Diagnostic Monte Carlo regardless of sample <100.
# formal_gate_eligible tells us whether it counts toward the
# strict validation gate.
# ------------------------------------------------------------

def mc_v31(trades, iterations):
    if trades.empty:
        return {
            "iterations": iterations,
            "trades": 0,
            "warning": "no trades",
            "formal_gate_eligible": False,
        }

    returns = (
        trades["net_return"]
        .dropna()
        .astype(float)
        .to_numpy()
    )

    rng = np.random.default_rng(42)

    finals = []
    dds = []

    for _ in range(iterations):

        sample = rng.choice(
            returns,
            size=len(returns),
            replace=True,
        )

        equity = 1.0
        curve = [equity]

        for r in sample:
            equity *= (
                1.0
                + m.MAX_POSITION_PCT * r
            )

            curve.append(equity)

        finals.append(
            (equity - 1.0) * 100.0
        )

        dds.append(
            m.max_dd(curve)
        )

    finals = np.asarray(finals)

    return {
        "iterations":
            int(iterations),

        "trades":
            int(len(returns)),

        "probability_profitable_pct":
            float(
                (finals > 0).mean()
                * 100.0
            ),

        "median_roi_pct":
            float(
                np.median(finals)
            ),

        "roi_5th_pct":
            float(
                np.percentile(
                    finals,
                    5,
                )
            ),

        "roi_95th_pct":
            float(
                np.percentile(
                    finals,
                    95,
                )
            ),

        "dd_95th_pct":
            float(
                np.percentile(
                    dds,
                    95,
                )
            ),

        "formal_gate_eligible":
            bool(
                len(returns)
                >= m.MIN_OOS_TRADES
            ),
    }


# Monkey-patch only the validation mechanics.
m.prep_symbol = prep_symbol_v31
m.portfolio_sim = portfolio_sim_v31
m.summarize = summarize_v31
m.mc = mc_v31

# Redirect reports so V3 results remain untouched.
m.REPORTS = (
    ROOT /
    "reports" /
    "v3_1"
)

m.REPORTS.mkdir(
    parents=True,
    exist_ok=True,
)

# Run V3 methodology with corrected engine.
m.main()
