"""Walk-forward protocol: pick parameters on the early years only, test once on the later years, then challenge the result.

  1. grid search on the TRAIN years (ranked by Sharpe, at least `min_trades` trades)
  2. the single winner is run on the untouched TEST years
  3. placebo: same exits, same number of trades per day, but random entry times (N seeds) - does the entry rule beat luck?
  4. cost stress: the same test with 1x / 2.5x / 5x the slippage
  5. benchmark: buy-and-hold SPY over the test years
Nothing here is advice and nothing trades: it returns numbers, including bad ones.
"""

import itertools
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from .engine import Costs, Portfolio, metrics, raw_intraday, simulate_swing, size_intraday, size_swing, split_days
from .brain import Brain
from .experts import ALL_EXPERTS
from .strategies import Breakout, DualRegime, OpeningRangeBreak, RsiPullback, VwapReversion

GRIDS = {
    "scalp": (VwapReversion, {"z_entry": [1.5, 2.0, 2.5], "target": ["vwap", "r"], "max_bars": [15, 30]}),
    "day": (OpeningRangeBreak, {"range_minutes": [15, 30], "target_r": [1.5, 2.0, 3.0], "max_bars": [0, 90]}),
    "regime": (DualRegime, {"adx_split": [20.0, 25.0, 30.0], "trail": [1.0, 1.5, 2.0], "tp_scale": [0.75, 1.0, 1.25]}),
    "swing_rsi": (RsiPullback, {"rsi_entry": [5, 10, 15], "exit_sma": [5, 10], "stop_atr": [2.0, 3.0]}),
    "swing_breakout": (Breakout, {"lookback": [20, 55], "exit_lookback": [10, 20]}),
}


def grid_points(grid: dict):
    keys = list(grid)
    for vals in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, vals))


def run_intraday(strategy, days, costs, pf, start, end, placebo_seed=None) -> dict:
    trades = raw_intraday(strategy, days, costs, placebo_seed=placebo_seed, start=start, end=end)
    fills = size_intraday(trades, costs, pf) if trades else pd.DataFrame()
    return {"fills": fills, "metrics": metrics(fills, start_equity=pf.equity)}


def run_swing(strategy, daily: dict[str, pd.DataFrame], costs, pf, start, end) -> dict:
    trades = [t for sym, d in daily.items() for t in simulate_swing(sym, d, strategy, costs)]
    trades = [t for t in trades if start <= t.entry_time.date() <= end]
    closes = {sym: d["close"] for sym, d in daily.items()}
    fills, curve = size_swing(sorted(trades, key=lambda t: t.entry_time), closes, costs, pf, start, end)
    return {"fills": fills, "curve": curve, "metrics": metrics(fills, curve, pf.equity)}


def benchmark(spy: pd.DataFrame, start: date, end: date) -> dict:
    c = spy["close"][(spy.index.date >= start) & (spy.index.date <= end)]
    ret = c.pct_change().dropna()
    years = max(len(c) / 252, 1e-9)
    return {"cagr": float((c.iloc[-1] / c.iloc[0]) ** (1 / years) - 1), "sharpe": float(ret.mean() / ret.std() * np.sqrt(252)),
            "max_drawdown": float((c / c.cummax() - 1).min())}


def walk_forward(family: str, data, train: tuple[date, date], test: tuple[date, date], costs: Costs = Costs(), pf: Portfolio = Portfolio(),
                 min_trades: int = 100, placebo_runs: int = 20, spy_daily: pd.DataFrame | None = None) -> dict:
    """data: for intraday families the dict from split_days(); for swing families symbol -> daily frame."""
    cls, grid = GRIDS[family]
    intraday = family in ("scalp", "day", "regime")
    run = (lambda s, c, a, b, **k: run_intraday(s, data, c, pf, a, b, **k)) if intraday else (lambda s, c, a, b, **k: run_swing(s, data, c, pf, a, b))
    table = []
    for params in grid_points(grid):
        m = run(cls(**params), costs, *train)["metrics"]
        table.append({"params": params, **{k: m.get(k) for k in ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "cagr", "max_drawdown")}})
    ranked = sorted([t for t in table if (t["trades"] or 0) >= min_trades and t["sharpe"] is not None], key=lambda t: -t["sharpe"])
    out = {"family": family, "train": [str(x) for x in train], "test": [str(x) for x in test], "grid": table, "chosen": None}
    if not ranked:
        out["note"] = f"no parameter set produced {min_trades}+ trades on the train years"
        return out
    best = cls(**ranked[0]["params"])
    out["chosen"] = ranked[0]["params"]
    res = run(best, costs, *test)
    out["train_metrics"] = {k: v for k, v in ranked[0].items() if k != "params"}
    out["test_metrics"] = res["metrics"]
    out["stress"] = {f"slip x{x}": run(best, replace(costs, slip_bps=costs.slip_bps * x), *test)["metrics"] for x in (2.5, 5.0)}
    if intraday:
        plc = [run(best, costs, *test, placebo_seed=s)["metrics"] for s in range(placebo_runs)]
        ex = [p.get("expectancy_pct_notional") for p in plc if p.get("trades")]
        real = res["metrics"].get("expectancy_pct_notional")
        out["placebo"] = {"runs": len(ex), "mean_expectancy_pct": float(np.mean(ex)) if ex else None,
                          "real_beats_share": float(np.mean([real > e for e in ex])) if ex and real is not None else None}
    if spy_daily is not None:
        out["benchmark_buy_hold_SPY"] = benchmark(spy_daily, *test)
    return out


def verdict(r: dict) -> str:
    m = r.get("test_metrics") or {}
    if not r.get("chosen") or not m.get("trades"):
        return "NO RESULT"
    ok = (m.get("expectancy_pct_notional", 0) > 0 and (m.get("sharpe") or 0) > 0.5 and m["trades"] >= 100
          and r.get("stress", {}).get("slip x2.5", {}).get("expectancy_pct_notional", -1) > 0)
    plc = (r.get("placebo") or {}).get("real_beats_share")
    if ok and (plc is None or plc >= 0.9):
        return "PROMISING (paper trade next; not proof)"
    return "NO EDGE FOUND"


TARGET_DAILY_PCT = (0.75, 1.0)


def brain_walk_forward(data, train, test, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), min_trades: int = 50,
                       placebo_runs: int = 20, spy_daily=None, shorts: bool = False) -> dict:
    """data: split_days(..., minutes=5, feature_fn=features_all) - or features_both + gates.add_gates when shorts=True.
    Select experts (each side separately) and tune the brain on TRAIN only; test once."""
    base = dict(route=False, mtf=False, consensus=1)
    pick_keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe")
    good = lambda m: (m["trades"] or 0) >= min_trades and (m["expectancy_pct_notional"] or 0) > 0 and (m["profit_factor"] or 0) > 1.05  # noqa: E731
    solo, solo_short = {}, {}
    for name in ALL_EXPERTS:
        m = run_intraday(Brain(experts=[name], **base), data, costs, pf, *train)["metrics"]
        solo[name] = {k: m.get(k) for k in pick_keys}
        if shorts:
            m = run_intraday(Brain(experts=[], short_experts=[name], **base), data, costs, pf, *train)["metrics"]
            solo_short[name] = {k: m.get(k) for k in pick_keys}
    keep = sorted([n for n, m in solo.items() if good(m)], key=lambda n: -(solo[n]["expectancy_pct_notional"] or 0))
    keep_s = sorted([n for n, m in solo_short.items() if good(m)], key=lambda n: -(solo_short[n]["expectancy_pct_notional"] or 0))
    out = {"family": "brain_ls" if shorts else "brain", "train": [str(x) for x in train], "test": [str(x) for x in test], "solo_train": solo,
           "selected_experts": keep}
    if shorts:
        out["solo_train_short"], out["selected_short_experts"] = solo_short, keep_s
    if not keep and not keep_s:
        out["note"] = "no expert had positive expectancy, profit factor > 1.05 and enough trades on the train years: the brain stays flat"
        out["verdict"] = "NO EDGE FOUND"
        return out
    table = []
    sector_opts = [False, True] if shorts else [False]
    gate_opts = ["bearish", "red"] if keep_s else ["bearish"]
    for route, mtf, cons, sect, gate in itertools.product([True, False], [True, False], [1, 2] if max(len(keep), len(keep_s)) > 1 else [1],
                                                          sector_opts, gate_opts):
        b = Brain(experts=keep, priority=keep + keep_s, route=route, mtf=mtf, consensus=cons, short_experts=keep_s, sector_filter=sect, short_gate=gate)
        m = run_intraday(b, data, costs, pf, *train)["metrics"]
        table.append({"route": route, "mtf": mtf, "consensus": cons, "sector_filter": sect, "short_gate": gate,
                      **{k: m.get(k) for k in ("trades", "expectancy_pct_notional", "profit_factor", "sharpe", "daily_ret_pct_mean")}})
    ranked = sorted([t for t in table if (t["trades"] or 0) >= min_trades and t["sharpe"] is not None], key=lambda t: -t["sharpe"])
    out["brain_grid_train"] = table
    if not ranked:
        out["note"] = "the selected experts together produced too few trades on the train years"
        out["verdict"] = "NO RESULT"
        return out
    pick = {k: ranked[0][k] for k in ("route", "mtf", "consensus", "sector_filter", "short_gate")}
    best = Brain(experts=keep, priority=keep + keep_s, short_experts=keep_s, **pick)
    out["chosen"] = {"experts": keep, "short_experts": keep_s, **pick}
    res = run_intraday(best, data, costs, pf, *test)
    out["test_metrics"] = res["metrics"]
    fills = res["fills"]
    if len(fills):
        g = fills.groupby("tag").agg(trades=("pnl", "size"), win_rate=("pnl", lambda x: float((x > 0).mean())), avg_r=("r", "mean"), pnl=("pnl", "sum"))
        out["test_by_expert"] = g.round(4).to_dict("index")
        if "side" in fills:
            sd = fills.groupby("side").agg(trades=("pnl", "size"), win_rate=("pnl", lambda x: float((x > 0).mean())), avg_r=("r", "mean"), pnl=("pnl", "sum"))
            out["test_by_side"] = {("long" if k == 1 else "short"): v for k, v in sd.round(4).to_dict("index").items()}
    out["stress"] = {f"slip x{x}": run_intraday(best, data, replace(costs, slip_bps=costs.slip_bps * x), pf, *test)["metrics"] for x in (2.5, 5.0)}
    plc = [run_intraday(best, data, costs, pf, *test, placebo_seed=s)["metrics"] for s in range(placebo_runs)]
    ex = [p.get("expectancy_pct_notional") for p in plc if p.get("trades")]
    real = res["metrics"].get("expectancy_pct_notional")
    out["placebo"] = {"runs": len(ex), "mean_expectancy_pct": float(np.mean(ex)) if ex else None,
                      "real_beats_share": float(np.mean([real > e for e in ex])) if ex and real is not None else None}
    if spy_daily is not None:
        out["benchmark_buy_hold_SPY"] = benchmark(spy_daily, *test)
    dm = res["metrics"].get("daily_ret_pct_mean")
    out["target_0.75_to_1pct_per_day"] = {"average_daily_return_pct": dm, "met": bool(dm is not None and dm >= TARGET_DAILY_PCT[0])}
    out["verdict"] = verdict({**out, "chosen": out["chosen"]})
    return out
