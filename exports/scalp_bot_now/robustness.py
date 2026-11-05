"""Does the edge survive friction and luck?

  python robustness.py --start 2026-06-01 --end 2026-08-31
  python robustness.py --trades bt_results/trades.csv      # bootstrap an existing run only (no re-runs)

1. Slippage sweep: re-runs the backtest at 0 / 2 / 5 / 10 bps (same cached data, same signals).
   Reports P&L, profit factor, drawdown per tier and the break-even slippage, overall and per expert.
2. Trade-order shuffle: same trades, random order -> distribution of max drawdown (path luck).
3. Day-block bootstrap: resamples whole trading days with replacement (keeps same-day correlation
   between experts) -> distribution of final P&L, probability of losing, drawdown percentiles.
4. Per-expert bootstrap: probability each expert's P&L is really above zero.
5. Outlier dependency: P&L after removing the best 1% / 5% of trades.
6. Stability: first half vs second half of the period.
"""
import argparse
import os

import numpy as np
import pandas as pd

import config

TIERS = [0.0, 2.0, 5.0, 10.0]


# ---------------------------------------------------------------- stats helpers
def profit_factor(pnl):
    loss = -pnl[pnl < 0].sum()
    return float(pnl[pnl > 0].sum() / loss) if loss > 0 else float("inf")


def max_dd_pct(pnl, equity):
    eq = equity + np.concatenate([[0.0], np.cumsum(pnl)])
    return float((eq / np.maximum.accumulate(eq) - 1).min() * 100)


def max_dd_matrix(paths, equity):
    """paths: (n_sims, n_steps) P&L increments -> max drawdown % per simulation."""
    eq = equity + np.cumsum(paths, axis=1)
    eq = np.hstack([np.full((eq.shape[0], 1), equity), eq])
    return (eq / np.maximum.accumulate(eq, axis=1) - 1).min(axis=1) * 100


def breakeven_bps(tiers, pnls):
    """Linear interpolation of the slippage where net P&L crosses zero."""
    t, p = np.asarray(tiers, float), np.asarray(pnls, float)
    if p[0] <= 0:
        return 0.0
    for k in range(1, len(t)):
        if p[k] <= 0:
            return float(t[k - 1] + (t[k] - t[k - 1]) * p[k - 1] / (p[k - 1] - p[k]))
    return float("inf")   # still profitable at the highest tier


def run_summary(tr, equity):
    pnl = tr["pnl"].to_numpy()
    return dict(trades=len(tr), net_pnl=round(pnl.sum(), 2), ret_pct=round(pnl.sum() / equity * 100, 3),
                profit_factor=round(profit_factor(pnl), 3), avg_R=round(tr["r"].mean(), 4),
                win_rate=round((pnl > 0).mean() * 100, 1), max_dd_pct=round(max_dd_pct(pnl, equity), 3))


# ---------------------------------------------------------------- 1. slippage sweep
def slippage_sweep(arrays, frames, sym_sector, start, end, equity, tiers, fee_ps=0.0, overlay=True,
                   experts_fn=None):
    from backtest import run_backtest
    rows, per_exp, trades = [], {}, {}
    for bps in tiers:
        print(f"  running {bps:g} bps ...")
        sim, _ = run_backtest(arrays, sym_sector, frames, start, end, equity, bps, fee_ps,
                              overlay=overlay, verbose=False,
                              experts=experts_fn() if experts_fn else None)
        tr = pd.DataFrame(sim.trades)
        trades[bps] = tr
        rows.append(dict(slippage_bps=bps, **(run_summary(tr, equity) if len(tr) else dict(trades=0))))
        per_exp[bps] = tr.groupby("expert")["pnl"].sum() if len(tr) else pd.Series(dtype=float)
    summary = pd.DataFrame(rows).set_index("slippage_bps")
    by_exp = pd.DataFrame(per_exp).fillna(0.0).round(2)
    by_exp.columns = [f"{c:g}bps" for c in by_exp.columns]
    by_exp["breakeven_bps"] = [round(breakeven_bps(tiers, by_exp.loc[e].to_numpy()[:len(tiers)]), 2)
                               for e in by_exp.index]
    return summary, by_exp, trades


# ---------------------------------------------------------------- 2-6. bootstraps
def shuffle_drawdowns(pnl, equity, n, rng, chunk=250):
    out = []
    for k in range(0, n, chunk):
        m = min(chunk, n - k)
        idx = np.argsort(rng.random((m, len(pnl))), axis=1)
        out.append(max_dd_matrix(pnl[idx], equity))
    return np.concatenate(out)


def day_block_bootstrap(tr, equity, n, rng):
    tr = tr.sort_values("closed")
    days = tr["date"].astype(str).to_numpy()
    uniq = pd.unique(days)
    day_trades = [tr["pnl"].to_numpy()[days == d] for d in uniq]
    finals, dds = np.empty(n), np.empty(n)
    for k in range(n):
        pick = rng.integers(0, len(uniq), len(uniq))
        seq = np.concatenate([day_trades[j] for j in pick])
        finals[k] = seq.sum()
        dds[k] = max_dd_pct(seq, equity)
    return finals, dds


def expert_bootstrap(tr, n, rng):
    rows = []
    for eid, g in tr.groupby("expert"):
        daily = g.groupby(g["date"].astype(str))["pnl"].sum().to_numpy()
        sims = daily[rng.integers(0, len(daily), (n, len(daily)))].sum(axis=1)
        rows.append(dict(expert=eid, trades=len(g), net_pnl=round(daily.sum(), 2),
                         p_profitable=round((sims > 0).mean() * 100, 1),
                         pnl_p5=round(np.percentile(sims, 5), 2), pnl_p95=round(np.percentile(sims, 95), 2)))
    return pd.DataFrame(rows).set_index("expert")


def outlier_dependency(tr):
    pnl = np.sort(tr["pnl"].to_numpy())[::-1]
    out = {"all trades": pnl.sum()}
    for pct in (1, 5):
        k = max(1, int(len(pnl) * pct / 100))
        out[f"without best {pct}% ({k} trades)"] = pnl[k:].sum()
    return pd.Series(out).round(2)


def halves(tr, equity):
    tr = tr.sort_values("closed")
    days = pd.unique(tr["date"].astype(str))
    cut = days[len(days) // 2] if len(days) > 1 else days[0]
    first, second = tr[tr["date"].astype(str) < cut], tr[tr["date"].astype(str) >= cut]
    rows = {}
    for name, part in (("first half", first), ("second half", second)):
        if len(part):
            rows[name] = run_summary(part, equity)
    exp = pd.DataFrame({"first half": first.groupby("expert")["pnl"].sum(),
                        "second half": second.groupby("expert")["pnl"].sum()}).fillna(0).round(2)
    return pd.DataFrame(rows).T, exp


def pct_table(x, name):
    q = np.percentile(x, [5, 25, 50, 75, 95])
    return pd.Series(q.round(3), index=["p5", "p25", "median", "p75", "p95"], name=name)


# ---------------------------------------------------------------- report
def analyse(tr, equity, n, seed, out):
    rng = np.random.default_rng(seed)
    pnl = tr.sort_values("closed")["pnl"].to_numpy()
    print(f"\n=== Bootstraps on {len(tr)} trades ({n:,} simulations) ===")

    dd_shuffle = shuffle_drawdowns(pnl, equity, n, rng)
    finals, dd_block = day_block_bootstrap(tr, equity, n, rng)
    dist = pd.DataFrame([pct_table(dd_shuffle, "max DD % (trade-order shuffle)"),
                         pct_table(dd_block, "max DD % (day-block bootstrap)"),
                         pct_table(finals, "final P&L $ (day-block bootstrap)")])
    print(f"\nActual max DD {max_dd_pct(pnl, equity):.3f}%   actual P&L ${pnl.sum():,.2f}")
    print(dist.to_string())
    p_loss = (finals <= 0).mean() * 100
    print(f"\nProbability the period loses money (day-block bootstrap): {p_loss:.1f}%")

    eb = expert_bootstrap(tr, n, rng)
    print("\n--- per-expert: how sure is the edge? ---")
    print(eb.to_string())

    od = outlier_dependency(tr)
    print("\n--- dependence on a few big winners ---")
    print(od.to_string())

    hv, hv_exp = halves(tr, equity)
    print("\n--- stability: first vs second half ---")
    print(hv.to_string())
    print(hv_exp.to_string())

    os.makedirs(out, exist_ok=True)
    dist.to_csv(os.path.join(out, "bootstrap_distributions.csv"))
    eb.to_csv(os.path.join(out, "expert_bootstrap.csv"))
    pd.DataFrame({"dd_shuffle": dd_shuffle, "dd_block": dd_block, "final_pnl_block": finals}).to_csv(
        os.path.join(out, "bootstrap_samples.csv"), index=False)
    _plots(dd_shuffle, dd_block, finals, out)
    return dict(p_loss=p_loss, dd_p5=float(np.percentile(dd_block, 5)), experts=eb, outliers=od)


def verdict(summary, by_exp, boot, base_bps):
    """Heuristic checklist - rules of thumb, not guarantees."""
    print("\n================ VERDICT (rules of thumb) ================")
    checks = []
    if summary is not None:
        be = breakeven_bps(summary.index.tolist(), summary["net_pnl"].tolist())
        checks.append((be >= 5, f"Overall break-even slippage {be:.1f} bps (want >= 5 bps cushion)"))
        pf = summary.loc[base_bps, "profit_factor"] if base_bps in summary.index else np.nan
        checks.append((pf >= 1.2, f"Profit factor at {base_bps:g} bps = {pf:.2f} (want >= 1.2)"))
        weak = [e for e, v in by_exp["breakeven_bps"].items() if v < base_bps]
        checks.append((not weak, f"Experts losing money at {base_bps:g} bps: {weak or 'none'}"))
    checks.append((boot["p_loss"] <= 10, f"P(losing period) = {boot['p_loss']:.1f}% (want <= 10%)"))
    od = boot["outliers"]
    checks.append((od.iloc[-1] > 0, f"Still profitable without best 5% of trades: ${od.iloc[-1]:,.2f}"))
    for ok, msg in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    print("  An expert failing these should be switched off or re-tuned (config.EXPERT_OVERRIDES)")


def _plots(dd_shuffle, dd_block, finals, out, summary=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(1, 2, figsize=(11, 4))
    ax[0].hist(dd_shuffle, 50, alpha=.6, label="trade shuffle")
    ax[0].hist(dd_block, 50, alpha=.6, label="day-block")
    ax[0].set_title("Max drawdown % distribution")
    ax[0].legend()
    ax[1].hist(finals, 50)
    ax[1].axvline(0, color="k")
    ax[1].set_title("Final P&L $ (day-block bootstrap)")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "bootstrap.png"), dpi=120)


def plot_sweep(summary, by_exp, tiers, out):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot(tiers, summary["net_pnl"], "k-o", lw=2, label="ALL")
    for e in by_exp.index:
        ax.plot(tiers, by_exp.loc[e].to_numpy()[:len(tiers)], "-o", label=e)
    ax.axhline(0, color="grey")
    ax.set_xlabel("slippage per side (bps)")
    ax.set_ylabel("net P&L $")
    ax.set_title("Edge vs execution friction")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out, "slippage_sweep.png"), dpi=120)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--trades", help="existing trades.csv: skip the sweep, bootstrap only")
    ap.add_argument("--equity", type=float, default=100_000)
    ap.add_argument("--tiers", default="0,2,5,10")
    ap.add_argument("--base-bps", type=float, default=2.0, help="tier used for the bootstraps")
    ap.add_argument("--fee-per-share", type=float, default=0.0)
    ap.add_argument("--feed", default=config.HIST_FEED)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="robustness_results")
    ap.add_argument("--velez", action="store_true", help="Velez 2-min study (separate study)")
    ap.add_argument("--runner", action="store_true",
                    help="-1R stop, no target, trailing stop that tightens above +1R")
    ap.add_argument("--volfilter", action="store_true",
                    help="only enter while volatility contracts and volume rises")
    ap.add_argument("--confirm", action="store_true",
                    help="require 60m trend + 15m momentum + 5m flow to agree on direction")
    ap.add_argument("--stop-frac", type=float, default=config.STOP_FRACTION,
                    help="stop at this fraction of each expert's stop distance (v1 = 1.0)")
    ap.add_argument("--target-r", type=float, default=config.TARGET_R,
                    help="target at this multiple of the ORIGINAL stop distance (v1 = expert default)")
    args = ap.parse_args()
    config.STOP_FRACTION, config.TARGET_R = args.stop_frac, args.target_r
    config.DIRECTION_CONFIRM = args.confirm
    config.VOL_FILTER = args.volfilter
    config.RUNNER_MODE = args.runner
    if args.runner:
        print("VARIANT: runner exits ON (-1R stop, no target, trail tightens above +1R)")
    if args.volfilter:
        print(f"VARIANT: vol filter ON (ATR5 < {config.VX_ATR_RATIO} x ATR30, "
              f"volume5 > {config.VX_VOL_RATIO} x prior volume30)")
    if args.confirm:
        print("VARIANT: direction confirmation ON (60m trend + 15m MACD + 5m flow)")
    if args.stop_frac != 1.0 or args.target_r is not None:
        print(f"VARIANT: stop at {args.stop_frac} x stop distance, target at {args.target_r} x stop distance")
    os.makedirs(args.out, exist_ok=True)
    pd.set_option("display.width", 160)

    summary = by_exp = None
    if args.trades:
        tr = pd.read_csv(args.trades, parse_dates=["opened", "closed"])
    else:
        if not (args.start and args.end):
            raise SystemExit("give --start/--end, or --trades")
        from backtest import load_data
        tiers = [float(x) for x in args.tiers.split(",")]
        if args.base_bps not in tiers:
            tiers = sorted(tiers + [args.base_bps])
        start, end = pd.Timestamp(args.start).date(), pd.Timestamp(args.end).date()
        experts_fn, transform = None, None
        if args.velez:
            import velez
            velez.activate()
            experts_fn, transform = (lambda: [velez.VelezExpert()]), velez.prepare_2min
        arrays, frames, sym_sector = load_data(start, end, args.feed, transform=transform)
        print("\n=== Slippage sweep ===")
        summary, by_exp, trades = slippage_sweep(arrays, frames, sym_sector, start, end, args.equity,
                                                 tiers, args.fee_per_share, experts_fn=experts_fn)
        print(summary.to_string())
        print("\n--- net P&L by expert per tier ---")
        print(by_exp.to_string())
        summary.to_csv(os.path.join(args.out, "slippage_sweep.csv"))
        by_exp.to_csv(os.path.join(args.out, "slippage_by_expert.csv"))
        plot_sweep(summary, by_exp, tiers, args.out)
        tr = trades[args.base_bps]
    if tr.empty:
        raise SystemExit("no trades to analyse")
    boot = analyse(tr, args.equity, args.n, args.seed, args.out)
    verdict(summary, by_exp, boot, args.base_bps)
    print(f"\nFiles written to {args.out}/")


if __name__ == "__main__":
    main()
