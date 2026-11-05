#!/usr/bin/env python3
"""
PHASE 2 — DESTRUCTION TESTING & TOURNAMENT CERTIFICATION
Paired Head-to-Head: Config E (Sector Top-Down) vs Config G (Sector + Momentum Burst)
5,000-Path Monte Carlo | 2x/3x Slippage Stress | +/-10% Parameter Perturbations | Sleeve Attribution
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import itertools
import numpy as np
import pandas as pd

from tournament_engine import (
    load_and_prep_data,
    Trade,
    Position,
    close_trade,
    check_open_gap,
    check_intraday_ohlc,
    calculate_mtm_nav,
    INITIAL_NAV,
    RISK_PER_TRADE_PCT,
    sell_regulatory_fees,
    UNIVERSE,
    BENCHMARK,
)

OUTPUT_DIR = Path("/home/moonrider1968/bots/phase2_output")

MAX_PORTFOLIO_POSITIONS = 3
MAX_SINGLE_POS_NOTIONAL_PCT = 0.333
MAX_PORTFOLIO_GROSS_PCT = 1.000

# =============================================================================
# PARAMETRIC STRATEGY ENGINES
# =============================================================================

def signal_sector_topdown_p(
    df: pd.DataFrame,
    idx: int,
    pos: Optional[Position],
    spy_df: pd.DataFrame,
    ema_col: str = "ema20",
    sma_col: str = "sma50",
    perf_thresh: float = 0.03,
) -> dict:
    row = df.iloc[idx]
    date = df.index[idx]
    if date not in spy_df.index or idx < 20 or pd.isna(row[ema_col]) or pd.isna(row[sma_col]):
        return {"signal": "HOLD"}

    stock_base = float(df["close"].iloc[idx - 20])
    spy_hist = spy_df.loc[:date]
    if len(spy_hist) < 21:
        return {"signal": "HOLD"}

    spy_now = float(spy_hist["close"].iloc[-1])
    spy_base = float(spy_hist["close"].iloc[-21])
    if stock_base <= 0 or spy_base <= 0:
        return {"signal": "HOLD"}

    stock_perf = (float(row["close"]) / stock_base) - 1.0
    spy_perf = (spy_now / spy_base) - 1.0

    if pos is None:
        if stock_perf > (spy_perf + perf_thresh) and row["close"] > row[ema_col] > row[sma_col]:
            return {"signal": "BUY", "stop": float(row[ema_col]) - float(row["atr14"]), "target_r": None}
    else:
        if row["close"] < row[ema_col]:
            return {"signal": "EXIT", "reason": "TOPDOWN_EMA_BREAK"}
    return {"signal": "HOLD"}


def signal_momentum_burst_p(
    df: pd.DataFrame,
    idx: int,
    pos: Optional[Position],
    ema_col: str = "ema20",
    roc_thresh: float = 0.04,
    vol_thresh: float = 1.80,
) -> dict:
    row = df.iloc[idx]
    if pd.isna(row["roc5"]) or pd.isna(row["vol_surge"]) or pd.isna(row[ema_col]):
        return {"signal": "HOLD"}

    if pos is None:
        if row["roc5"] > roc_thresh and row["vol_surge"] > vol_thresh and row["close"] > row[ema_col] and row["atr_expansion"] > 1.10:
            return {"signal": "BUY", "stop": float(row["close"]) - 1.5 * float(row["atr14"]), "target_r": 2.0}
    return {"signal": "HOLD"}


# =============================================================================
# PARAMETRIC SIMULATOR
# =============================================================================

def run_simulation(
    enabled_experts: List[str],
    dfs: Dict[str, pd.DataFrame],
    slippage_pct: float = 0.0005,
    ema_window: int = 20,
    sma_window: int = 50,
    sector_perf_thresh: float = 0.03,
    mom_roc_thresh: float = 0.04,
    mom_vol_thresh: float = 1.80,
) -> Tuple[dict, List[Trade], pd.DataFrame, int]:

    spy_df = dfs[BENCHMARK]
    common_dates = sorted(set.intersection(*[set(dfs[s].index) for s in UNIVERSE + [BENCHMARK]]))

    # Dynamic MA columns for perturbation
    ema_col = f"ema_{ema_window}"
    sma_col = f"sma_{sma_window}"

    for sym, df in dfs.items():
        if ema_col not in df.columns:
            df[ema_col] = df["close"].ewm(span=ema_window, adjust=False).mean()
        if sma_col not in df.columns:
            df[sma_col] = df["close"].rolling(sma_window, min_periods=sma_window).mean()

    realized_nav = INITIAL_NAV
    open_positions: Dict[str, Tuple[str, Position]] = {}
    trades: List[Trade] = []
    equity_rows: List[dict] = []
    slot_conflicts = 0

    for t_idx in range(199, len(common_dates) - 1):
        curr_date = common_dates[t_idx]
        next_date = common_dates[t_idx + 1]

        # 1. Bar t Exits
        exit_intents: Dict[str, str] = {}
        for sym, (exp_name, pos) in list(open_positions.items()):
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)
            res = {"signal": "HOLD"}
            if exp_name == "Sector Top-Down":
                res = signal_sector_topdown_p(df, idx, pos, spy_df, ema_col, sma_col, sector_perf_thresh)
            if res.get("signal") == "EXIT":
                exit_intents[sym] = res.get("reason", "SIGNAL_EXIT")

        # 2. Bar t Entries
        candidates: List[Tuple[str, str, dict]] = []
        for sym in UNIVERSE:
            if sym in open_positions:
                continue
            df = dfs[sym]
            idx = df.index.get_loc(curr_date)

            if "Sector Top-Down" in enabled_experts:
                res = signal_sector_topdown_p(df, idx, None, spy_df, ema_col, sma_col, sector_perf_thresh)
                if res.get("signal") == "BUY":
                    candidates.append((sym, "Sector Top-Down", res))

            if "Momentum Burst" in enabled_experts:
                res = signal_momentum_burst_p(df, idx, None, ema_col, mom_roc_thresh, mom_vol_thresh)
                if res.get("signal") == "BUY":
                    candidates.append((sym, "Momentum Burst", res))

        # First valid signal per symbol
        ordered_entries = []
        seen = set()
        for sym, exp, res in candidates:
            if sym not in seen:
                ordered_entries.append((sym, exp, res))
                seen.add(sym)

        # 3. Session t+1 Open: Gap Exits & Signal Exits
        for sym in list(open_positions.keys()):
            exp_name, pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            gap_event = check_open_gap(pos, row)
            if gap_event:
                px, reason = gap_event
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        for sym, reason in exit_intents.items():
            if sym in open_positions:
                exp_name, pos = open_positions[sym]
                row = dfs[sym].loc[next_date]
                raw_open = float(row["open"])
                px = raw_open * (1.0 - slippage_pct)
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 4. Session t+1 Open: Entries
        for sym, exp_name, res in ordered_entries:
            if len(open_positions) >= MAX_PORTFOLIO_POSITIONS:
                slot_conflicts += 1
                continue
            if sym in open_positions:
                continue

            row = dfs[sym].loc[next_date]
            side = res["signal"]
            raw_open = float(row["open"])
            entry_fill = raw_open * (1.0 + slippage_pct)
            initial_stop = float(res["stop"])

            if initial_stop >= entry_fill:
                continue

            risk_per_share = entry_fill - initial_stop
            if not np.isfinite(risk_per_share) or risk_per_share <= 0:
                continue

            pos_dict_only = {s: p for s, (_, p) in open_positions.items()}
            nav_open = calculate_mtm_nav(realized_nav, pos_dict_only, dfs, next_date, price_col="open")

            risk_dollars = nav_open * RISK_PER_TRADE_PCT
            qty_risk = int(risk_dollars / risk_per_share)
            qty_single_cap = int((nav_open * MAX_SINGLE_POS_NOTIONAL_PCT) / entry_fill)
            current_gross = sum(p.entry_price * p.qty for p in pos_dict_only.values())
            remaining_gross = max(0.0, (nav_open * MAX_PORTFOLIO_GROSS_PCT) - current_gross)
            qty_gross_cap = int(remaining_gross / entry_fill)

            qty = min(qty_risk, qty_single_cap, qty_gross_cap)
            if qty <= 0:
                continue

            target_r = res.get("target_r")
            tp = entry_fill + (float(target_r) * risk_per_share) if target_r is not None else None

            open_positions[sym] = (exp_name, Position(
                symbol=sym, side=side, entry_date=next_date, entry_price=entry_fill,
                initial_stop=initial_stop, current_stop=initial_stop,
                initial_risk_per_share=risk_per_share, qty=qty, tp=tp, entry_fees=0.0
            ))

        # 5. Session t+1 Intraday: Stop-First
        for sym in list(open_positions.keys()):
            exp_name, pos = open_positions[sym]
            row = dfs[sym].loc[next_date]
            event = check_intraday_ohlc(pos, row)
            if event:
                px, reason = event
                t, nav_chg = close_trade(exp_name, pos, next_date, px, reason)
                realized_nav += nav_chg
                trades.append(t)
                del open_positions[sym]

        # 6. MTM Close Tracking
        pos_dict_only = {s: p for s, (_, p) in open_positions.items()}
        mtm = calculate_mtm_nav(realized_nav, pos_dict_only, dfs, next_date, price_col="close")
        equity_rows.append({"date": next_date, "realized_nav": realized_nav, "mtm_nav": mtm, "positions": len(open_positions)})

    # Final Close Liquidation & Reconciliation
    final_date = common_dates[-1]
    for sym in list(open_positions.keys()):
        exp_name, pos = open_positions[sym]
        raw_close = float(dfs[sym].loc[final_date, "close"])
        px = raw_close * (1.0 - slippage_pct)
        t, nav_chg = close_trade(exp_name, pos, final_date, px, "END_OF_TEST")
        realized_nav += nav_chg
        trades.append(t)

    open_positions.clear()
    equity_rows.append({"date": final_date, "realized_nav": realized_nav, "mtm_nav": realized_nav, "positions": 0})
    equity_df = pd.DataFrame(equity_rows).drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)

    # Metrics
    pnls = np.array([t.pnl for t in trades], dtype=float)
    rvals = np.array([t.r_mult for t in trades], dtype=float)
    wins = pnls[pnls > 0].sum()
    losses = -pnls[pnls < 0].sum()
    pf = (wins / losses) if losses > 0 else (99.0 if wins > 0 else 0.0)
    win_rate = float((pnls > 0).mean() * 100.0) if len(pnls) else 0.0
    exp_r = float(np.mean(rvals)) if len(rvals) else 0.0

    eq = equity_df["mtm_nav"].to_numpy()
    peaks = np.maximum.accumulate(eq)
    drawdowns = (peaks - eq) / np.where(peaks != 0, peaks, np.nan)
    max_dd = float(np.nanmax(drawdowns) * 100.0) if len(eq) else 0.0

    total_net = realized_nav - INITIAL_NAV
    total_ret = (realized_nav / INITIAL_NAV - 1.0) * 100.0

    metrics = {
        "trades": len(trades),
        "win_rate": win_rate,
        "pf": pf,
        "exp_r": exp_r,
        "net_pnl": total_net,
        "return_pct": total_ret,
        "max_dd": max_dd,
        "final_nav": realized_nav,
    }

    return metrics, trades, equity_df, slot_conflicts


# =============================================================================
# MONTE CARLO SIMULATOR (5,000 PATHS)
# =============================================================================

def run_monte_carlo(trades: List[Trade], n_paths: int = 5000, seed: int = 42) -> dict:
    if not trades:
        return {"profitable_pct": 0, "dd_95": 0, "dd_99": 0, "ruin_count": 0}

    np.random.seed(seed)
    trade_pnls = np.array([t.pnl for t in trades], dtype=float)
    n_trades = len(trade_pnls)

    final_returns = []
    max_drawdowns = []
    ruin_count = 0

    for _ in range(n_paths):
        sampled_pnls = np.random.choice(trade_pnls, size=n_trades, replace=True)
        equity_curve = INITIAL_NAV + np.cumsum(sampled_pnls)
        equity_curve = np.insert(equity_curve, 0, INITIAL_NAV)

        peaks = np.maximum.accumulate(equity_curve)
        dds = (peaks - equity_curve) / np.where(peaks > 0, peaks, np.nan)
        m_dd = np.nanmax(dds) * 100.0
        max_drawdowns.append(m_dd)

        final_ret = (equity_curve[-1] / INITIAL_NAV - 1.0) * 100.0
        final_returns.append(final_ret)

        if np.any(equity_curve < (INITIAL_NAV * 0.50)):
            ruin_count += 1

    final_returns = np.array(final_returns)
    max_drawdowns = np.array(max_drawdowns)

    return {
        "profitable_pct": float((final_returns > 0).mean() * 100.0),
        "dd_95": float(np.percentile(max_drawdowns, 95)),
        "dd_99": float(np.percentile(max_drawdowns, 99)),
        "ruin_count": ruin_count,
    }


# =============================================================================
# MAIN DESTRUCTION RUNNER
# =============================================================================

def main():
    print("=" * 115)
    print("PHASE 2 — DESTRUCTION TESTING & HEAD-TO-HEAD CERTIFICATION")
    print("Defending Champion (Config E: Sector Alone) vs Challenger (Config G: Sector + Momentum)")
    print("=" * 115)

    dfs = load_and_prep_data()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Baseline Runs
    e_metrics, e_trades, e_eq, e_conf = run_simulation(["Sector Top-Down"], dfs, slippage_pct=0.0005)
    g_metrics, g_trades, g_eq, g_conf = run_simulation(["Sector Top-Down", "Momentum Burst"], dfs, slippage_pct=0.0005)

    # 2. Monte Carlo 5,000 Paths
    print("\n[1/4] Running 5,000-Path Monte Carlo Resampling (Seed=42)...")
    e_mc = run_monte_carlo(e_trades, n_paths=5000, seed=42)
    g_mc = run_monte_carlo(g_trades, n_paths=5000, seed=42)

    # 3. Execution Slippage Stress Tests
    print("[2/4] Running 2x (0.10%) and 3x (0.15%) Slippage Stress Tests...")
    e_s10, _, _, _ = run_simulation(["Sector Top-Down"], dfs, slippage_pct=0.0010)
    e_s15, _, _, _ = run_simulation(["Sector Top-Down"], dfs, slippage_pct=0.0015)

    g_s10, _, _, _ = run_simulation(["Sector Top-Down", "Momentum Burst"], dfs, slippage_pct=0.0010)
    g_s15, _, _, _ = run_simulation(["Sector Top-Down", "Momentum Burst"], dfs, slippage_pct=0.0015)

    # 4. Parameter Neighborhood Perturbations (+/-10%)
    print("[3/4] Running +/-10% Parameter Neighborhood Matrix (16 Combinations)...")
    p_emas = [18, 22]
    p_smas = [45, 55]
    p_perfs = [0.027, 0.033]
    p_mom_rocs = [0.036, 0.044]
    p_mom_vols = [1.62, 1.98]

    e_pert_pfs = []
    g_pert_pfs = []

    for ema, sma, perf in itertools.product(p_emas, p_smas, p_perfs):
        m, _, _, _ = run_simulation(["Sector Top-Down"], dfs, ema_window=ema, sma_window=sma, sector_perf_thresh=perf)
        e_pert_pfs.append(m["pf"])

    for ema, sma, perf, roc, vol in itertools.product(p_emas, p_smas, p_perfs, p_mom_rocs, p_mom_vols):
        m, _, _, _ = run_simulation(["Sector Top-Down", "Momentum Burst"], dfs, ema_window=ema, sma_window=sma, sector_perf_thresh=perf, mom_roc_thresh=roc, mom_vol_thresh=vol)
        g_pert_pfs.append(m["pf"])

    e_pert_pfs = np.array(e_pert_pfs)
    g_pert_pfs = np.array(g_pert_pfs)

    # 5. G-Specific Diversification Attribution
    print("[4/4] Computing Sleeve Attribution & Daily Correlation for Config G...")
    g_mom_trades = [t for t in g_trades if t.expert == "Momentum Burst"]
    g_sec_trades = [t for t in g_trades if t.expert == "Sector Top-Down"]

    mom_pnl = sum(t.pnl for t in g_mom_trades)
    sec_pnl = sum(t.pnl for t in g_sec_trades)

    # Reconstruct standalone sleeve equity curves for daily correlation
    _, _, sec_only_eq, _ = run_simulation(["Sector Top-Down"], dfs)
    _, _, mom_only_eq, _ = run_simulation(["Momentum Burst"], dfs)

    sec_ret = sec_only_eq.set_index("date")["mtm_nav"].pct_change().dropna()
    mom_ret = mom_only_eq.set_index("date")["mtm_nav"].pct_change().dropna()
    common_idx = sec_ret.index.intersection(mom_ret.index)
    daily_corr = float(sec_ret.loc[common_idx].corr(mom_ret.loc[common_idx]))

    # =========================================================================
    # CONSOLIDATED SCORECARD & CERTIFICATION GATES
    # =========================================================================
    results = [
        {
            "Gate / Stress Test": "Baseline Profit Factor",
            "Target": ">= 1.60",
            "Config E (Sector Alone)": f"{e_metrics['pf']:.2f}",
            "Config G (Sector + Momentum)": f"{g_metrics['pf']:.2f}",
            "E Status": "PASS" if e_metrics['pf'] >= 1.60 else "FAIL",
            "G Status": "PASS" if g_metrics['pf'] >= 1.60 else "FAIL",
        },
        {
            "Gate / Stress Test": "Baseline Expectancy",
            "Target": ">= +0.30R",
            "Config E (Sector Alone)": f"{e_metrics['exp_r']:+.3f}R",
            "Config G (Sector + Momentum)": f"{g_metrics['exp_r']:+.3f}R",
            "E Status": "PASS" if e_metrics['exp_r'] >= 0.30 else "FAIL",
            "G Status": "PASS" if g_metrics['exp_r'] >= 0.30 else "FAIL",
        },
        {
            "Gate / Stress Test": "Baseline Max MTM DD",
            "Target": "<= 5.00%",
            "Config E (Sector Alone)": f"{e_metrics['max_dd']:.2f}%",
            "Config G (Sector + Momentum)": f"{g_metrics['max_dd']:.2f}%",
            "E Status": "PASS" if e_metrics['max_dd'] <= 5.0 else "FAIL",
            "G Status": "PASS" if g_metrics['max_dd'] <= 5.0 else "FAIL",
        },
        {
            "Gate / Stress Test": "0.10% Slippage PF",
            "Target": ">= 1.40",
            "Config E (Sector Alone)": f"{e_s10['pf']:.2f}",
            "Config G (Sector + Momentum)": f"{g_s10['pf']:.2f}",
            "E Status": "PASS" if e_s10['pf'] >= 1.40 else "FAIL",
            "G Status": "PASS" if g_s10['pf'] >= 1.40 else "FAIL",
        },
        {
            "Gate / Stress Test": "0.10% Slippage Net Return",
            "Target": "> 0.00%",
            "Config E (Sector Alone)": f"{e_s10['return_pct']:+.2f}%",
            "Config G (Sector + Momentum)": f"{g_s10['return_pct']:+.2f}%",
            "E Status": "PASS" if e_s10['return_pct'] > 0 else "FAIL",
            "G Status": "PASS" if g_s10['return_pct'] > 0 else "FAIL",
        },
        {
            "Gate / Stress Test": "0.15% Slippage PF",
            "Target": ">= 1.20",
            "Config E (Sector Alone)": f"{e_s15['pf']:.2f}",
            "Config G (Sector + Momentum)": f"{g_s15['pf']:.2f}",
            "E Status": "PASS" if e_s15['pf'] >= 1.20 else "FAIL",
            "G Status": "PASS" if g_s15['pf'] >= 1.20 else "FAIL",
        },
        {
            "Gate / Stress Test": "0.15% Slippage Net Return",
            "Target": "> 0.00%",
            "Config E (Sector Alone)": f"{e_s15['return_pct']:+.2f}%",
            "Config G (Sector + Momentum)": f"{g_s15['return_pct']:+.2f}%",
            "E Status": "PASS" if e_s15['return_pct'] > 0 else "FAIL",
            "G Status": "PASS" if g_s15['return_pct'] > 0 else "FAIL",
        },
        {
            "Gate / Stress Test": "Monte Carlo Profitable Paths",
            "Target": ">= 85.0%",
            "Config E (Sector Alone)": f"{e_mc['profitable_pct']:.1f}%",
            "Config G (Sector + Momentum)": f"{g_mc['profitable_pct']:.1f}%",
            "E Status": "PASS" if e_mc['profitable_pct'] >= 85.0 else "FAIL",
            "G Status": "PASS" if g_mc['profitable_pct'] >= 85.0 else "FAIL",
        },
        {
            "Gate / Stress Test": "MC 95th Percentile Max DD",
            "Target": "<= 10.0%",
            "Config E (Sector Alone)": f"{e_mc['dd_95']:.2f}%",
            "Config G (Sector + Momentum)": f"{g_mc['dd_95']:.2f}%",
            "E Status": "PASS" if e_mc['dd_95'] <= 10.0 else "FAIL",
            "G Status": "PASS" if g_mc['dd_95'] <= 10.0 else "FAIL",
        },
        {
            "Gate / Stress Test": "MC 99th Percentile Max DD",
            "Target": "<= 15.0%",
            "Config E (Sector Alone)": f"{e_mc['dd_99']:.2f}%",
            "Config G (Sector + Momentum)": f"{g_mc['dd_99']:.2f}%",
            "E Status": "PASS" if e_mc['dd_99'] <= 15.0 else "FAIL",
            "G Status": "PASS" if g_mc['dd_99'] <= 15.0 else "FAIL",
        },
        {
            "Gate / Stress Test": "MC Ruin Paths (<50% NAV)",
            "Target": "0 Paths",
            "Config E (Sector Alone)": f"{e_mc['ruin_count']}",
            "Config G (Sector + Momentum)": f"{g_mc['ruin_count']}",
            "E Status": "PASS" if e_mc['ruin_count'] == 0 else "FAIL",
            "G Status": "PASS" if g_mc['ruin_count'] == 0 else "FAIL",
        },
        {
            "Gate / Stress Test": "+/-10% Perturbation Win Rate",
            "Target": ">= 70.0%",
            "Config E (Sector Alone)": f"{(e_pert_pfs >= 1.0).mean()*100:.1f}%",
            "Config G (Sector + Momentum)": f"{(g_pert_pfs >= 1.0).mean()*100:.1f}%",
            "E Status": "PASS" if (e_pert_pfs >= 1.0).mean() >= 0.70 else "FAIL",
            "G Status": "PASS" if (g_pert_pfs >= 1.0).mean() >= 0.70 else "FAIL",
        },
        {
            "Gate / Stress Test": "Median Perturbation PF",
            "Target": ">= 1.30",
            "Config E (Sector Alone)": f"{np.median(e_pert_pfs):.2f}",
            "Config G (Sector + Momentum)": f"{np.median(g_pert_pfs):.2f}",
            "E Status": "PASS" if np.median(e_pert_pfs) >= 1.30 else "FAIL",
            "G Status": "PASS" if np.median(g_pert_pfs) >= 1.30 else "FAIL",
        },
    ]

    df_scorecard = pd.DataFrame(results)
    print("\n" + "=" * 115)
    print("PHASE 2 DESTRUCTION SCORECARD")
    print("=" * 115)
    print(df_scorecard.to_string(index=False))
    print("=" * 115)

    print("\nCONFIG G DIVERSIFICATION ATTRIBUTION:")
    print(f"Daily MTM Return Correlation:   {daily_corr:+.3f}")
    print(f"Slot Conflict Count:            {g_conf}")
    print(f"Sector Top-Down PnL in G:       ${sec_pnl:+,.2f} ({len(g_sec_trades)} trades)")
    print(f"Momentum Burst PnL in G:        ${mom_pnl:+,.2f} ({len(g_mom_trades)} trades)")
    print("=" * 115)

    df_scorecard.to_csv(OUTPUT_DIR / "phase2_destruction_scorecard.csv", index=False)


if __name__ == "__main__":
    main()
