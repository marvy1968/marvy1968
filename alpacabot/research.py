"""Walk-forward protocol: pick parameters on the early years only, test once on the later years, then challenge the result.

  1. grid search on the TRAIN years (ranked by Sharpe, at least `min_trades` trades)
  2. the single winner is run on the untouched TEST years
  3. placebo: same exits, same number of trades per day, but random entry times (N seeds) - does the entry rule beat luck?
  4. cost stress: the same test with 1x / 2.5x / 5x the slippage
  5. benchmark: buy-and-hold SPY over the test years
Nothing here is advice and nothing trades: it returns numbers, including bad ones.
"""

import itertools
import math
from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd

from .engine import Costs, Portfolio, metrics, raw_intraday, simulate_day, simulate_swing, size_intraday, size_swing, split_days
from .brain import Brain
from .experts import ALL_EXPERTS, TEN_EXPERTS
from .strategies import Breakout, DualRegime, OpeningRangeBreak, RsiPullback, Signal, VwapReversion

GRIDS = {
    "scalp": (VwapReversion, {"z_entry": [1.5, 2.0, 2.5], "target": ["vwap", "r"], "max_bars": [15, 30]}),
    "day": (OpeningRangeBreak, {"range_minutes": [15, 30], "target_r": [1.5, 2.0, 3.0], "max_bars": [0, 90]}),
    "regime": (DualRegime, {"adx_split": [20.0, 25.0, 30.0], "trail": [1.0, 1.5, 2.0], "tp_scale": [0.75, 1.0, 1.25]}),
    "swing_rsi": (RsiPullback, {"rsi_entry": [5, 10, 15], "exit_sma": [5, 10], "stop_atr": [2.0, 3.0]}),
    "swing_breakout": (Breakout, {"lookback": [20, 55], "exit_lookback": [10, 20]}),
}


class Checkpoint:
    """Results of finished runs saved to disk after each run, so a stopped study resumes where it left off."""

    def __init__(self, path=None):
        import json as _j
        from pathlib import Path as _P
        self.path, self._j = (_P(path) if path else None), _j
        self.data = self._j.loads(self.path.read_text()) if self.path and self.path.exists() else {}

    def get(self, key, fn):
        if key in self.data:
            return self.data[key]
        val = fn()
        self.data[key] = val
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(self._j.dumps(self.data, default=str))
            tmp.replace(self.path)
        return val


def grid_points(grid: dict):
    keys = list(grid)
    for vals in itertools.product(*(grid[k] for k in keys)):
        yield dict(zip(keys, vals))


def run_intraday(strategy, days, costs, pf, start, end, placebo_seed=None) -> dict:
    trades = raw_intraday(strategy, days, costs, placebo_seed=placebo_seed, start=start, end=end)
    fills = size_intraday(trades, costs, pf) if trades else pd.DataFrame()
    return {"fills": fills, "metrics": metrics(fills, start_equity=pf.equity)}


def run_swing(strategy, daily: dict[str, pd.DataFrame], costs, pf, start, end) -> dict:
    trades = [t for sym, d in daily.items() for t in simulate_swing(sym, d, strategy, costs, all_signals=True)]
    trades = [t for t in trades if start <= t.entry_time.date() <= end]
    closes = {sym: d["close"] for sym, d in daily.items()}
    fills, curve = size_swing(sorted(trades, key=lambda t: (t.entry_time, -t.rank)), closes, costs, pf, start, end)
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


def exit_study(data, train, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), shorts: bool = False, ckpt=None) -> dict:
    """TRAIN years only (does not touch the test years): each expert's real entries with its OWN exits vs the STAIRCASE, after costs.
    Answers 'does the staircase help this expert?' without spending the held-out data."""
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe")
    table, cp = {}, Checkpoint(ckpt)
    for name in ALL_EXPERTS:
        for side in (["long", "short"] if shorts else ["long"]):
            row = {}
            for mode in ("own", "staircase"):
                kw = dict(experts=[name]) if side == "long" else dict(experts=[], short_experts=[name])
                row[mode] = cp.get(f"{name}-{side}-{mode}", lambda kw=kw, mode=mode: {k: run_intraday(Brain(route=False, mtf=False, exit_mode=mode, **kw), data, costs, pf, *train)["metrics"].get(k) for k in keys})
            e_own, e_st = row["own"].get("expectancy_pct_notional"), row["staircase"].get("expectancy_pct_notional")
            row["staircase_better"] = bool(e_own is not None and e_st is not None and e_st > e_own)
            table[f"{name} {side}"] = row
    better = [k for k, v in table.items() if v["staircase_better"]]
    return {"family": "exits", "train": [str(x) for x in train], "table": table, "staircase_better_for": better,
            "verdict": f"staircase better for {len(better)} of {len(table)} expert/side pairs (train years, after costs)"}


def symbol_check(days_by_symbol, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6),
                 brain_kwargs: dict | None = None, min_trades: int = 60) -> dict:
    """Should a stock join the paper universe? The CURRENT brain is run on that stock alone, on the train years and on the validation years,
    after costs. It qualifies only if both periods are profitable per trade with enough trades and the cost-stressed validation still is.
    How another bot did on the stock is not an input: only how THIS bot's rules trade it."""
    kw = brain_kwargs or {}
    out = {}
    for sym, frames in days_by_symbol.items():
        one = {sym: frames}
        tr = run_intraday(Brain(**kw), one, costs, pf, *train)["metrics"]
        va = run_intraday(Brain(**kw), one, costs, pf, *validate)["metrics"]
        st = run_intraday(Brain(**kw), one, replace(costs, slip_bps=costs.slip_bps * 2.5), pf, *validate)["metrics"]
        e = lambda m: m.get("expectancy_pct_notional") or 0.0   # noqa: E731
        ok = ((tr.get("trades") or 0) >= min_trades and (va.get("trades") or 0) >= min_trades // 2 and e(tr) > 0 and e(va) > 0 and e(st) > 0)
        out[sym] = {"qualifies": bool(ok), "train": {k: tr.get(k) for k in ("trades", "win_rate", "expectancy_pct_notional", "profit_factor")},
                    "validate": {k: va.get(k) for k in ("trades", "win_rate", "expectancy_pct_notional", "profit_factor")},
                    "validate_slip_x2.5": {k: st.get(k) for k in ("trades", "expectancy_pct_notional")}}
    return out


def limit_study(data, train, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), shorts: bool = True, ckpt=None) -> dict:
    """TRAIN years only: each expert with MARKET entries (next open + slippage) vs LIMIT entries (at the signal close, or 0.25 stop
    distances better, waiting 3 bars, no slippage, unfilled = no trade). Costs are what separates these signals from break-even."""
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor")
    modes = {"market": costs, "limit@close": replace(costs, entry="limit", limit_offset=0.0),
             "limit-0.25": replace(costs, entry="limit", limit_offset=0.25)}
    table, cp = {}, Checkpoint(ckpt)
    for name in ALL_EXPERTS:
        for side in (["long", "short"] if shorts else ["long"]):
            kw = dict(experts=[name]) if side == "long" else dict(experts=[], short_experts=[name])
            table[f"{name} {side}"] = {mode: cp.get(f"{name}-{side}-{mode}", lambda kw=kw, c=c: {k: run_intraday(Brain(route=False, mtf=False, **kw), data, c, pf, *train)["metrics"].get(k) for k in keys})
                                       for mode, c in modes.items()}
    best = sorted(((k, m, v[m].get("expectancy_pct_notional") or -9) for k, v in table.items() for m in v
                   if (v[m].get("trades") or 0) >= 100), key=lambda x: -x[2])[:5]
    positive = [f"{k} [{m}]" for k, m, e in best if e > 0]
    return {"family": "limit", "train": [str(x) for x in train], "table": table, "best5": best,
            "verdict": (f"positive after costs on the train years: {', '.join(positive)} -> candidates for validation" if positive
                        else "no expert is positive even with limit entries (train years)")}


def swing_expert_study(daily, train, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=10), spy_daily=None) -> dict:
    """TRAIN years only: each expert on daily bars, held for days (max 10), long, across the swing universe, vs buy-and-hold SPY."""
    from .experts import SWING_EXPERTS, ExpertSwing
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "cagr", "max_drawdown")
    table = {}
    for name in SWING_EXPERTS:
        for hold in (5, 10):
            m = run_swing(ExpertSwing(name, max_days=hold), daily, costs, pf, *train)["metrics"]
            table[f"{name} hold<={hold}d"] = {k: m.get(k) for k in keys}
    out = {"family": "swing_experts", "train": [str(x) for x in train], "table": table}
    if spy_daily is not None:
        out["benchmark_buy_hold_SPY"] = benchmark(spy_daily, *train)
    spy_cagr = (out.get("benchmark_buy_hold_SPY") or {}).get("cagr")
    good = [k for k, v in table.items() if (v.get("trades") or 0) >= 100 and (v.get("expectancy_pct_notional") or 0) > 0
            and (v.get("sharpe") or 0) > 0.5 and (spy_cagr is None or (v.get("cagr") or -1) > 0)]
    out["verdict"] = (f"candidates for validation: {', '.join(good)}" if good else "no daily-bar expert qualifies on the train years")
    return out


def filters_study(data, train, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """TRAIN years only: the whole expert set with every brain filter combination (regime router, 15-minute trend, how many experts must agree,
    sector heat-map gate, shorts on/off). Fewer, better-confirmed trades pay the same costs once, so this is the 'trade less' test."""
    keys = ("trades", "win_rate", "expectancy_pct_notional", "profit_factor", "daily_ret_pct_mean")
    cp = Checkpoint(ckpt)
    rows = []
    for route, mtf, cons, sect, shorts in itertools.product([False, True], [False, True], [1, 2, 3], [False, True], [False, True]):
        b = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS) if shorts else [], route=route, mtf=mtf, consensus=cons,
                  sector_filter=sect, short_gate="red")
        m = cp.get(f"{route}-{mtf}-{cons}-{sect}-{shorts}", lambda: {k: run_intraday(b, data, costs, pf, *train)["metrics"].get(k) for k in keys})
        rows.append({"route": route, "mtf": mtf, "consensus": cons, "sector_filter": sect, "shorts": shorts, **{k: m.get(k) for k in keys}})
    rows.sort(key=lambda r: -(r["expectancy_pct_notional"] if r["expectancy_pct_notional"] is not None else -9))
    ok = [r for r in rows if (r["trades"] or 0) >= 500 and (r["expectancy_pct_notional"] or 0) > 0 and (r["profit_factor"] or 0) > 1.05]
    return {"family": "filters", "train": [str(x) for x in train], "table": rows, "candidates": ok,
            "verdict": (f"{len(ok)} filter combinations are positive after costs on the train years -> validate the best one once" if ok
                        else "no filter combination is positive after costs on the train years")}


def _hold_stats(closes: pd.DataFrame, start: date, end: date) -> dict:
    """Equal-weight buy-and-hold of the same names (daily rebalanced) over the same dates."""
    c = closes[(closes.index.date >= start) & (closes.index.date <= end)]
    r = c.pct_change().mean(axis=1).dropna()
    if not len(r):
        return {}
    curve = (1 + r).cumprod()
    return {"total_return": float(curve.iloc[-1] - 1), "cagr": float(curve.iloc[-1] ** (252 / len(r)) - 1), "max_drawdown": float((curve / curve.cummax() - 1).min())}


def sector_td_study(universes: dict, spy: pd.DataFrame, periods: dict, costs: Costs, pf: Portfolio) -> dict:
    """The supplied Sector Top-Down rules, frozen exactly, on each universe and period, against equal-weight buy-and-hold of the same names."""
    from .strategies import SectorTopDown
    rows = []
    for uname, data in universes.items():
        closes = pd.DataFrame({s: d["close"] for s, d in data.items()})
        for pname, (a, b) in periods.items():
            res = run_swing(SectorTopDown(spy["close"]), data, costs, pf, a, b)
            m, f, cv = res["metrics"], res["fills"], res["curve"]
            tot = float(cv.iloc[-1] / pf.equity - 1) if len(cv) else None
            yearly = {}
            if len(cv):
                for y, g in cv.groupby([t.year for t in cv.index]):
                    yearly[int(y)] = round(float(g.iloc[-1] / g.iloc[0] - 1), 4)
            top = (f.groupby("symbol")["pnl"].sum().sort_values(ascending=False).head(3).round(0).to_dict() if len(f) else {})
            rows.append({"universe": uname, "period": pname, "dates": [str(a), str(b)], "trades": m.get("trades"), "win_rate": m.get("win_rate"),
                         "avg_r": m.get("avg_r"), "profit_factor": m.get("profit_factor"), "total_return": tot, "cagr": m.get("cagr"),
                         "max_drawdown": m.get("max_drawdown"), "sharpe": m.get("sharpe"), "by_year": yearly, "top_symbols_pnl": top,
                         "buy_hold_equal_weight": _hold_stats(closes, a, b), "spy": benchmark(spy, a, b), "symbols": len(data)})
    return {"family": "sector_td", "rows": rows}


# Pre-declared BEFORE the validation years were looked at: the three distinct experts with the best train-year (2016-2020) Sharpe in swing_expert_study.
SWING_CANDIDATES = [("brooks_pullback", 10), ("williams_r", 10), ("donchian_turtle", 10)]
# Declared 2026-10-14, before looking: connors_rsi2 passed the train years (PF 1.45, 601 trades) but was not in the first validation list.
# The owner's old bots (killer_beast_sniper "VCP + RSI2", "master sniper") all trade this same RSI(2) pullback idea.
RSI2_CANDIDATES = [("connors_rsi2", 10)]


def swing_validate(daily, test, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=10), spy_daily=None, placebo_runs: int = 20,
                   candidates=None) -> dict:
    """ONE look at the validation years for the pre-declared swing candidates: real entries vs random entries with the same exits (placebo),
    at normal and 2.5x slippage, against buy-and-hold SPY. Nothing is tuned here."""
    from .experts import ExpertSwing
    out = {"family": "swing_validate", "test": [str(x) for x in test], "rows": []}
    for name, hold in candidates or SWING_CANDIDATES:
        real = run_swing(ExpertSwing(name, max_days=hold), daily, costs, pf, *test)["metrics"]
        stress = run_swing(ExpertSwing(name, max_days=hold), daily, replace(costs, slip_bps=costs.slip_bps * 2.5), pf, *test)["metrics"]
        plc = [run_swing(ExpertSwing(name, max_days=hold, placebo_seed=sd), daily, costs, pf, *test)["metrics"] for sd in range(placebo_runs)]
        pe = [p.get("expectancy_pct_notional") for p in plc if p.get("trades")]
        e = real.get("expectancy_pct_notional")
        beat = float(np.mean([e > x for x in pe])) if pe and e is not None else None
        ok = ((real.get("trades") or 0) >= 100 and (e or 0) > 0 and (real.get("profit_factor") or 0) > 1.2 and (real.get("sharpe") or 0) > 0.5
              and (stress.get("expectancy_pct_notional") or -1) > 0 and (beat or 0) >= 0.9)
        out["rows"].append({"expert": name, "hold_days": hold, "real": {k: real.get(k) for k in ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "cagr", "max_drawdown")},
                            "slip_x2.5_expectancy": stress.get("expectancy_pct_notional"), "placebo_mean_expectancy": float(np.mean(pe)) if pe else None,
                            "real_beats_placebo_share": beat, "passes": bool(ok)})
    if spy_daily is not None:
        out["benchmark_buy_hold_SPY"] = benchmark(spy_daily, *test)
    passed = [r["expert"] for r in out["rows"] if r["passes"]]
    out["verdict"] = (f"PASSED validation: {', '.join(passed)} (one look at 2021-22; the final test is still unspent)" if passed else "none of the pre-declared candidates passed validation")
    return out


def _curve_stats(cv: pd.Series, equity: float) -> dict:
    if cv is None or not len(cv):
        return {}
    r = cv.pct_change().dropna()
    years = max(len(cv) / 252, 1e-9)
    return {"total_return": float(cv.iloc[-1] / equity - 1), "cagr": float((cv.iloc[-1] / equity) ** (1 / years) - 1),
            "max_drawdown": float((cv / cv.cummax() - 1).min()), "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else None}


def sector_td_robust(data: dict, spy: pd.DataFrame, start: date, end: date, costs: Costs, seed: int = 7) -> dict:
    """Robustness of the frozen Sector Top-Down rules on one universe over [start, end]:
    leave-one-symbol-out, nearby parameters, risk sizing, and a moving-block bootstrap of daily returns (keeps losing streaks together)."""
    from .strategies import SectorTopDown
    base_pf = Portfolio(risk_pct=0.0035, max_positions=3, max_pos_frac=0.333)

    def run(strat, d=data, pf=base_pf):
        res = run_swing(strat, d, costs, pf, start, end)
        m = res["metrics"]
        return {"trades": m.get("trades"), "profit_factor": m.get("profit_factor"), "avg_r": m.get("avg_r"), **_curve_stats(res["curve"], pf.equity)}, res

    base, base_res = run(SectorTopDown(spy["close"]))
    loo = {sym: run(SectorTopDown(spy["close"]), {k: v for k, v in data.items() if k != sym})[0] for sym in data}
    params = []
    for rs_m in (0.02, 0.03, 0.04):
        for e in (15, 20, 25):
            for am in (0.75, 1.0, 1.5):
                m, _ = run(SectorTopDown(spy["close"], rs_margin=rs_m, ema_n=e, atr_mult=am))
                params.append({"rs_margin": rs_m, "ema": e, "atr_mult": am, **m})
    sizing = []
    for risk in (0.0035, 0.005, 0.0075, 0.01):
        for slots in (3, 5):
            m, _ = run(SectorTopDown(spy["close"]), pf=Portfolio(risk_pct=risk, max_positions=slots, max_pos_frac=min(0.333, 1.0 / slots + 0.001)))
            sizing.append({"risk_pct": risk, "max_positions": slots, **m})
    r = base_res["curve"].pct_change().dropna().to_numpy()
    rng, block, paths = np.random.default_rng(seed), 20, 2000
    ann, dds = [], []
    if len(r) > block:
        for _ in range(paths):
            idx = np.concatenate([np.arange(k, k + block) for k in rng.integers(0, len(r) - block, size=len(r) // block + 1)])[:len(r)]
            c = np.cumprod(1 + r[idx])
            ann.append(c[-1] ** (252 / len(r)) - 1)
            dds.append(float((c / np.maximum.accumulate(c) - 1).min()))
    boot = ({"paths": paths, "block_days": block, "cagr_p5": float(np.percentile(ann, 5)), "cagr_p50": float(np.percentile(ann, 50)),
             "cagr_p95": float(np.percentile(ann, 95)), "share_profitable": float(np.mean(np.array(ann) > 0)),
             "maxdd_p50": float(np.percentile(dds, 50)), "maxdd_p95": float(np.percentile(dds, 5)), "maxdd_p99": float(np.percentile(dds, 1))} if ann else {})
    pf_vals = [p["profit_factor"] for p in params if p["profit_factor"] is not None]
    return {"family": "sector_td_robust", "period": [str(start), str(end)], "symbols": sorted(data), "base": base, "leave_one_out": loo,
            "params": params, "params_profitable_share": float(np.mean([v > 1 for v in pf_vals])) if pf_vals else None,
            "params_pf_range": [min(pf_vals), max(pf_vals)] if pf_vals else None, "sizing": sizing, "bootstrap": boot,
            "spy": benchmark(spy, start, end)}


# ------------------------------------------------------------------ sector breadth gate (idea from the owner's velez-bot "6/10 rule")
# PRE-DECLARED 2026-10-14, before any run. Breadth of a sector on a close = share of that sector's stocks in the tested universe whose
# close is above their 200-day SMA (stocks without 200 days of history are left out; fewer than 2 rated stocks = gate open). Gate ON when
# breadth >= 0.6. Sector Top-Down may only ENTER a stock while its sector's gate is ON at the signal close; exits/stops unchanged.
# Universes: the 2016 large-cap control list and the 55-stock top-5-per-sector list. Periods: train 2016-03-01..2019-12-31, validate
# 2020-01-01..2022-12-31 (the 2023-today final test is NOT run here). Placebo: each sector's gate shuffled in 20-day blocks (same
# on-share, same persistence), 20 seeds. PASS only if, on BOTH universes and in BOTH periods, the gated run has 30+ trades, a higher
# profit factor than ungated, a max drawdown no deeper than ungated, CAGR no more than 1 point below ungated, and (validation period)
# a profit factor above the placebo-gated one in >= 90% of seeds.
BREADTH_SPEC = {"threshold": 0.6, "sma": 200, "min_members": 2, "train": ("2016-03-01", "2019-12-31"), "validate": ("2020-01-01", "2022-12-31"),
                "placebo_runs": 20, "block_days": 20, "beat_share": 0.9}


def breadth_gates(data: dict, sector_of: dict, threshold: float = 0.6, sma_n: int = 200, min_members: int = 2) -> dict:
    """sector ETF -> bool Series (gate ON) on the union of the members' dates."""
    from .indicators import sma
    out = {}
    for etf in sorted(set(sector_of[s] for s in data if s in sector_of)):
        above = pd.DataFrame({s: (d["close"] > sma(d["close"], sma_n)).where(sma(d["close"], sma_n).notna())
                              for s, d in data.items() if sector_of.get(s) == etf})
        rated = above.notna().sum(axis=1)
        share = above.sum(axis=1) / rated.replace(0, np.nan)
        out[etf] = ((share >= threshold) | (rated < min_members)).astype(bool)
    return out


def _block_shuffle(g: pd.Series, seed: int, block: int = 20) -> pd.Series:
    rng = np.random.default_rng(seed)
    v = g.to_numpy()
    blocks = [v[i:i + block] for i in range(0, len(v), block)]
    order = rng.permutation(len(blocks))
    return pd.Series(np.concatenate([blocks[k] for k in order])[:len(v)], index=g.index)


def run_swing_gated(spy_close, data, sector_of, gates_by_etf, costs, pf, start, end) -> dict:
    from .strategies import GatedSectorTopDown, SectorTopDown
    trades = []
    for sym, d in data.items():
        g = gates_by_etf.get(sector_of.get(sym)) if gates_by_etf is not None else None
        trades += [t for t in simulate_swing(sym, d, GatedSectorTopDown(SectorTopDown(spy_close), g), costs, all_signals=True)]
    trades = [t for t in trades if start <= t.entry_time.date() <= end]
    fills, curve = size_swing(sorted(trades, key=lambda t: (t.entry_time, -t.rank)), {s: d["close"] for s, d in data.items()}, costs, pf, start, end)
    m = metrics(fills, curve, pf.equity)
    return {"trades": m.get("trades"), "profit_factor": m.get("profit_factor"), **_curve_stats(curve, pf.equity)}


def breadth_gate_study(universes: dict, spy: pd.DataFrame, costs: Costs, spec: dict = BREADTH_SPEC) -> dict:
    """universes: name -> (data dict, sector_of). One pre-declared run; nothing is tuned."""
    pf = Portfolio(risk_pct=0.0035, max_positions=3, max_pos_frac=0.333)
    periods = {k: (date.fromisoformat(spec[k][0]), date.fromisoformat(spec[k][1])) for k in ("train", "validate")}
    rows, ok_all = [], True
    for name, (data, sector_of) in universes.items():
        gates = breadth_gates(data, sector_of, spec["threshold"], spec["sma"], spec["min_members"])
        on_share = {e: round(float(g.mean()), 3) for e, g in gates.items()}
        for per, (a, b) in periods.items():
            base = run_swing_gated(spy["close"], data, sector_of, None, costs, pf, a, b)
            gated = run_swing_gated(spy["close"], data, sector_of, gates, costs, pf, a, b)
            row = {"universe": name, "period": per, "ungated": base, "gated": gated, "gate_on_share": on_share}
            ok = ((gated.get("trades") or 0) >= 30 and (gated.get("profit_factor") or 0) > (base.get("profit_factor") or 0)
                  and (gated.get("max_drawdown") or -1) >= (base.get("max_drawdown") or -1)
                  and (gated.get("cagr") or -1) >= (base.get("cagr") or 0) - 0.01)
            if per == "validate":
                plc = []
                for sd in range(spec["placebo_runs"]):
                    pg = {e: _block_shuffle(g, sd * 100 + i, spec["block_days"]) for i, (e, g) in enumerate(sorted(gates.items()))}
                    plc.append(run_swing_gated(spy["close"], data, sector_of, pg, costs, pf, a, b).get("profit_factor") or 0)
                beat = float(np.mean([(gated.get("profit_factor") or 0) > p for p in plc]))
                row.update(placebo_pf_mean=float(np.mean(plc)), beats_placebo_share=beat)
                ok = ok and beat >= spec["beat_share"]
            row["passes"] = bool(ok)
            ok_all = ok_all and ok
            rows.append(row)
    return {"family": "breadth_gate", "spec": spec, "rows": rows, "passes": ok_all,
            "verdict": ("PASSED (train + validation, both universes): the 2023-today final test may be run once" if ok_all
                        else "did not pass the pre-declared bar: the gate is not added")}


# ------------------------------------------------------------------ the owner's frozen systems: Book A (Donchian) and Elite-20 (mean reversion)
# PRE-DECLARED 2026-10-14, before any run, rules exactly as frozen (strategies.BookA / strategies.Elite20). Portfolio: Book A 0.5% risk,
# max 3 positions; Elite-20 0.5% risk, max 3 positions, one per sector. Costs 5 bps slippage + sell fees; stress at 2.5x slippage.
# Universes: the 2016 large-cap control list and the 55-stock top-5-per-sector list. Periods: train 2016-03-01..2019-12-31, validate
# 2020-01-01..2022-12-31 (2023-today final NOT run here). PASS a system only if, on BOTH universes and in BOTH periods: 30+ trades, profit
# factor >= 1.2, expectancy > 0 also at 2.5x slippage, and (validation) a profit factor above the random-entry placebo (same exits/stops,
# same entry count) in >= 90% of 20 seeds.
FROZEN_SPEC = {"train": ("2016-03-01", "2019-12-31"), "validate": ("2020-01-01", "2022-12-31"), "min_trades": 30, "min_pf": 1.2,
               "placebo_runs": 20, "beat_share": 0.9, "risk_pct": 0.005, "max_positions": 3}


def frozen_systems_study(universes: dict, costs: Costs, spec: dict = FROZEN_SPEC) -> dict:
    """universes: name -> (data dict, sector_of). One pre-declared run per system; nothing is tuned."""
    from .strategies import BookA, Elite20, RandomEntries
    pf = Portfolio(risk_pct=spec["risk_pct"], max_positions=spec["max_positions"], max_pos_frac=0.333)
    periods = {k: (date.fromisoformat(spec[k][0]), date.fromisoformat(spec[k][1])) for k in ("train", "validate")}
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor")

    def run(strat, data, groups, c, a, b):
        trades = [t for sym, d in data.items() for t in simulate_swing(sym, d, strat, c, all_signals=True)]
        trades = [t for t in trades if a <= t.entry_time.date() <= b]
        fills, curve = size_swing(sorted(trades, key=lambda t: (t.entry_time, -t.rank)), {s: d["close"] for s, d in data.items()}, c, pf, a, b, groups)
        m = metrics(fills, curve, pf.equity)
        return {**{k: m.get(k) for k in keys}, **_curve_stats(curve, pf.equity)}

    out = {"family": "frozen_systems", "spec": spec, "systems": {}}
    for sysname, make, per_sector in (("Book A (55/20 Donchian, 1.5 ATR stop)", BookA, False), ("Elite-20 (-2.5 sigma reclaim, MA50 target)", Elite20, True)):
        rows, ok_all = [], True
        for uname, (data, sector_of) in universes.items():
            groups = sector_of if per_sector else None
            for per, (a, b) in periods.items():
                real = run(make(), data, groups, costs, a, b)
                stress = run(make(), data, groups, replace(costs, slip_bps=costs.slip_bps * 2.5), a, b)
                row = {"universe": uname, "period": per, "real": real, "slip_x2.5_expectancy": stress.get("expectancy_pct_notional")}
                ok = ((real.get("trades") or 0) >= spec["min_trades"] and (real.get("profit_factor") or 0) >= spec["min_pf"]
                      and (real.get("expectancy_pct_notional") or -1) > 0 and (stress.get("expectancy_pct_notional") or -1) > 0)
                if per == "validate":
                    plc = [run(RandomEntries(make(), sd), data, groups, costs, a, b).get("profit_factor") or 0 for sd in range(spec["placebo_runs"])]
                    beat = float(np.mean([(real.get("profit_factor") or 0) > p for p in plc]))
                    row.update(placebo_pf_mean=float(np.mean(plc)), beats_placebo_share=beat)
                    ok = ok and beat >= spec["beat_share"]
                row["passes"] = bool(ok)
                ok_all = ok_all and ok
                rows.append(row)
        out["systems"][sysname] = {"rows": rows, "passes": ok_all}
    passed = [k for k, v in out["systems"].items() if v["passes"]]
    out["verdict"] = (f"PASSED: {', '.join(passed)} (the 2023-today final test may be run once)" if passed
                      else "neither frozen system passed the pre-declared bar")
    return out


# ------------------------------------------------------------------ the owner's trade zones (entry / invalidation / exhaustion)
# PRE-DECLARED 2026-10-14, before any run. Rules frozen in engine.ZONES. A bot adopts zones only if, with zones vs without, on BOTH the
# train and the validation years: expectancy per trade is higher, profit factor is higher, and max drawdown is no more than 1 point deeper.
ZONES_BAR = {"max_dd_worse": 0.01}


def _zone_verdict(rows: list) -> bool:
    ok = True
    for r in rows:
        a, b = r["without"], r["with"]
        ok = ok and ((b.get("expectancy_pct_notional") or -9) > (a.get("expectancy_pct_notional") or -9)
                     and (b.get("profit_factor") or 0) > (a.get("profit_factor") or 0)
                     and (b.get("max_drawdown") or -1) >= (a.get("max_drawdown") or -1) - ZONES_BAR["max_dd_worse"])
    return bool(ok)


def zones_intraday_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6),
                         brain_kwargs: dict | None = None, ckpt=None) -> dict:
    """The paper brain (scalps + day trades, longs and red-gated shorts) with and without trade zones, train and validation years."""
    from .engine import ZONES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean")
    kw = brain_kwargs or {"experts": list(ALL_EXPERTS), "short_experts": list(ALL_EXPERTS), "sector_filter": True, "short_gate": "red"}
    cp = Checkpoint(ckpt)

    from .engine import ZONE_PROFILES

    from .brain import DirectionFirst

    def run(z, a, b):
        br = Brain(**kw)
        if z == "flat":
            br.zones = dict(ZONES)
        elif z == "per_expert":
            br.zones = {"profiles": ZONE_PROFILES}
        elif z == "direction":
            br = DirectionFirst(br)
        elif z == "direction_zonemap":                      # owner's full design: direction first, per-expert zones, daily zone map
            br.zones = {"profiles": ZONE_PROFILES, "levels": True}
            br = DirectionFirst(br)
        r = run_intraday(br, days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    rows = []
    for per, (a, b) in (("train", train), ("validate", validate)):
        plain = cp.get(f"{per}-plain", lambda a=a, b=b: run(None, a, b))
        rows.append({"period": per, "variant": "same zones for every expert", "without": plain,
                     "with": cp.get(f"{per}-zones", lambda a=a, b=b: run("flat", a, b))})
        rows.append({"period": per, "variant": "per-expert zones (trend vs mean-reversion)", "without": plain,
                     "with": cp.get(f"{per}-zones-per-expert", lambda a=a, b=b: run("per_expert", a, b))})
        rows.append({"period": per, "variant": "direction first (daily bias)", "without": plain,
                     "with": cp.get(f"{per}-direction", lambda a=a, b=b: run("direction", a, b))})
        rows.append({"period": per, "variant": "direction first + per-expert zones + zone map", "without": plain,
                     "with": cp.get(f"{per}-direction-zonemap", lambda a=a, b=b: run("direction_zonemap", a, b))})
    names = ["same zones for every expert", "per-expert zones (trend vs mean-reversion)", "direction first (daily bias)",
             "direction first + per-expert zones + zone map"]
    passed = {v: _zone_verdict([r for r in rows if r["variant"] == v]) for v in names}
    winners = [v for v, ok in passed.items() if ok]
    return {"family": "zones_intraday", "rows": rows, "passes": bool(winners), "variant_passes": passed,
            "verdict": (f"PASS: {'; '.join(winners)} - adopt the best passing variant" if winners
                        else "no zone/direction variant passed for the intraday bot: keep them in shadow mode")}


def zones_swing_study(universes: dict, spy: pd.DataFrame, costs: Costs) -> dict:
    """Sector Top-Down (the live swing rules) with and without trade zones, per universe, train 2016-03..2019 and validate 2020-2022."""
    from .strategies import SectorTopDown, Zoned
    pf = Portfolio(risk_pct=0.0035, max_positions=3, max_pos_frac=0.333)
    periods = {"train": (date(2016, 3, 1), date(2019, 12, 31)), "validate": (date(2020, 1, 1), date(2022, 12, 31))}
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor")
    rows = []
    for name, data in universes.items():
        for per, (a, b) in periods.items():
            out = {}
            from .engine import ZONES
            res_ = {}
            for label, strat in (("without", SectorTopDown(spy["close"])), ("with", Zoned(SectorTopDown(spy["close"]))),
                                 ("with_map", Zoned(SectorTopDown(spy["close"]), dict(ZONES, levels=True)))):
                r = run_swing(strat, data, costs, pf, a, b)
                res_[label] = {**{k: r["metrics"].get(k) for k in keys}, **_curve_stats(r["curve"], pf.equity)}
            rows.append({"universe": name, "period": per, "variant": "zones", "without": res_["without"], "with": res_["with"]})
            rows.append({"universe": name, "period": per, "variant": "zones + zone map", "without": res_["without"], "with": res_["with_map"]})
    passed = {v: _zone_verdict([r for r in rows if r["variant"] == v]) for v in ("zones", "zones + zone map")}
    winners = [v for v, ok in passed.items() if ok]
    return {"family": "zones_swing", "rows": rows, "passes": bool(winners), "variant_passes": passed,
            "verdict": (f"PASS for the swing bot: {'; '.join(winners)}" if winners else "zones did not pass for the swing bot: keep them in shadow mode")}


# The owner's drawn exit plan (2026-10-14) and its pre-declared alternatives, for the live paper brain with direction first + zones + map.
# Chosen by ROI (mean daily return) on the train years; adopted only if the validation years agree (no lower expectancy and PF than
# the plain zones, max drawdown no more than 1 point worse). Nothing else is tuned.
_OWNER_LADDER = {"cut_fracs": [0.25, 0.5], "cut_depth_r": [0.5, 0.75], "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25],
                 "lock": ["breakeven", "tp"], "trail_atr": 1.0}
LADDER_VARIANTS = {
    "owner (cut 25/50, TP 25/50, trail 1 ATR)": _OWNER_LADDER,
    "no cut steps (one stop)": {**_OWNER_LADDER, "cut_fracs": [], "cut_depth_r": []},
    "trail 0.5 ATR": {**_OWNER_LADDER, "trail_atr": 0.5},
    "trail 2 ATR": {**_OWNER_LADDER, "trail_atr": 2.0},
    "TP 50/75 (bank more)": {**_OWNER_LADDER, "tp_fracs": [0.5, 0.75]},
    "TP 25/50 at zone/+0.5 ATR": {**_OWNER_LADDER, "tp_depth_atr": [0.0, 0.5]},
    # two ideas from the owner's Gemini review (2026-10-14): read the TP lines as additive (25% then 50% more = 75% banked) and trail
    # half the risk behind price from the first profit (with one stop, no cut steps, as Gemini wrote it)
    "TP 25% then +50% (75% banked)": {**_OWNER_LADDER, "tp_fracs": [0.25, 0.75]},
    "trail 0.5R from entry, one stop": {**_OWNER_LADDER, "cut_fracs": [], "cut_depth_r": [], "trail_from_entry_r": 0.5},
    # the owner's thought 2026-10-15: "make the sale zone a little tighter so we don't lose too much, and loosen the take-profit zones so
    # it can run" - each half separately and together (pre-declared)
    "tighter cuts (0.35R / 0.6R)": {**_OWNER_LADDER, "cut_depth_r": [0.35, 0.6]},
    "looser take-profit (0.5 / 1.0 ATR beyond the zone)": {**_OWNER_LADDER, "tp_depth_atr": [0.5, 1.0]},
    "tighter cuts + looser take-profit (owner's idea)": {**_OWNER_LADDER, "cut_depth_r": [0.35, 0.6], "tp_depth_atr": [0.5, 1.0]},
}
# owner (2026-10-14): let each master's own philosophy set its take-profit and sell zones (engine.EXPERT_LADDERS, one stop each)
from .engine import EXPERT_LADDERS  # noqa: E402
LADDER_VARIANTS["each expert's own exits"] = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25],
                                              "lock": ["breakeven", "tp"], "trail_atr": 1.0, "by_expert": EXPERT_LADDERS}


# owner 2026-10-26 ("make sure as Alpaca goes up it does not give back profit"): live paper trades 2026-10-12..26 - 21 of 82 trades that
# reached +0.5R ended as losers (-10.2R of the book's -15R). Profit locks on top of each expert's own exits: once the trade is `start` R up the stop
# trails `trail` R behind its best price (engine.simulate_day trail_from_entry_r / trail_start_r; the same keys paper.py already runs for trend_stairs).
def _profit_lock(start: float, trail: float) -> dict:
    base = LADDER_VARIANTS["each expert's own exits"]
    by = {k: ({**v, "trail_start_r": start, "trail_from_entry_r": trail} if not v.get("trail_from_entry_r") else v)
          for k, v in base["by_expert"].items()}
    return {**base, "trail_start_r": start, "trail_from_entry_r": trail, "by_expert": by}


for _s, _t in ((0.5, 0.5), (0.75, 0.75), (1.0, 1.0), (1.0, 0.5)):
    LADDER_VARIANTS[f"own exits + profit lock (+{_s}R, trail {_t}R)"] = _profit_lock(_s, _t)

QUICK_VARIANTS = ["owner (cut 25/50, TP 25/50, trail 1 ATR)", "no cut steps (one stop)", "TP 25% then +50% (75% banked)",
                  "each expert's own exits", "tighter cuts (0.35R / 0.6R)", "looser take-profit (0.5 / 1.0 ATR beyond the zone)",
                  "tighter cuts + looser take-profit (owner's idea)",
                  "own exits + profit lock (+0.5R, trail 0.5R)", "own exits + profit lock (+0.75R, trail 0.75R)",
                  "own exits + profit lock (+1.0R, trail 1.0R)", "own exits + profit lock (+1.0R, trail 0.5R)"]


def ladder_intraday_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6),
                          ckpt=None, quick: bool = False) -> dict:
    """Live configuration (direction first + per-expert zones + daily zone map) with the owner's scaled exits and the alternatives, plus
    the Weinstein stage filter and the repeat cap (10 trades per stock per day) on the chosen exits."""
    from .brain import DirectionFirst, StageFilter
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    kw = {"experts": list(ALL_EXPERTS), "short_experts": list(ALL_EXPERTS), "sector_filter": True, "short_gate": "red"}
    cp = Checkpoint(ckpt)

    def run(ladder, a, b, stage=False, repeat=None):
        br = Brain(**{**kw, **({"max_trades": repeat} if repeat else {})})
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, **({"ladder": ladder} if ladder else {})}
        br = DirectionFirst(br)
        if stage:
            br = StageFilter(br)
        r = run_intraday(br, days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}

    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        rows = {"zones, single exit (before today)": cp.get(f"{per}-base", lambda a=a, b=b: run(None, a, b))}
        for name, lad in LADDER_VARIANTS.items():
            if quick and name not in QUICK_VARIANTS:
                continue
            rows[name] = cp.get(f"{per}-{name}", lambda a=a, b=b, lad=lad: run(lad, a, b))
        out["periods"][per] = rows
    roi = lambda m: m.get("daily_ret_pct_mean") if m.get("daily_ret_pct_mean") is not None else -9   # noqa: E731
    best = max((v for v in LADDER_VARIANTS if v in out["periods"]["train"]), key=lambda v: roi(out["periods"]["train"][v]))
    for per, (a, b) in (("train", train), ("validate", validate)):
        lad = LADDER_VARIANTS[best]
        out["periods"][per][f"{best} + stage filter"] = cp.get(f"{per}-stage-{best}", lambda a=a, b=b: run(lad, a, b, stage=True))
        out["periods"][per][f"{best} + stage + repeat 10/day"] = cp.get(f"{per}-stage-repeat-{best}", lambda a=a, b=b: run(lad, a, b, True, 10))
    base_v, best_v = out["periods"]["validate"]["zones, single exit (before today)"], out["periods"]["validate"][best]
    ok = _zone_verdict([{"without": base_v, "with": best_v}])
    out.update({"family": "ladder_intraday", "chosen_on_train": best, "passes": ok, "quick": quick,
                "verdict": (f"ADOPT '{best}': best train ROI and it holds on the validation years" if ok else
                            f"'{best}' had the best train ROI but did not hold on the validation years vs the single exit")})
    return out


def eod_squeeze_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6)) -> dict:
    """Gemini idea 2 (2026-10-26): end-of-day short squeeze (strategies.EodSqueeze), three pre-declared settings, each also at 3x the slippage
    (the wide last-half-hour spread). PASS only if the validate half is profitable (PF > 1, avg R > 0, 30+ trades) at the normal costs."""
    from .strategies import EodSqueeze
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    heavy = replace(costs, slip_bps=costs.slip_bps * 3)
    variants = {"top 10% of range & >2% over VWAP": EodSqueeze(0.9, 0.02), "top 20% & >1% over VWAP": EodSqueeze(0.8, 0.01),
                "top 10% & >2%, 2 ATR stop": EodSqueeze(0.9, 0.02, stop_atr=2.0)}
    control = {"CONTROL: every stock long at 15:10, no filter": EodSqueeze(0.0, -1.0)}        # does the filter beat a naive time-of-day long?
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        rows = {}
        for name, strat in {**variants, **control}.items():
            for label, c in (("", costs), (" | 3x slippage", heavy)):
                r = run_intraday(strat, days, c, pf, a, b)
                rows[name + label] = {k: r["metrics"].get(k) for k in keys}
        out["periods"][per] = rows
    ok = [n for n in variants if (out["periods"]["validate"][n].get("profit_factor") or 0) > 1 and (out["periods"]["validate"][n].get("avg_r") or 0) > 0
          and (out["periods"]["validate"][n].get("trades") or 0) >= 30]
    out.update({"family": "eod_squeeze", "passes": bool(ok),
                "verdict": f"PASS: {', '.join(ok)}" if ok else "no setting was profitable on the validate half: the end-of-day squeeze is not an edge here"})
    return out


def sector_momentum_study(days: dict, sector_of: dict, train, validate, costs: Costs = Costs(),
                          pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), at: str = "10:10", seed: int = 7) -> dict:
    """Gemini idea 1 (2026-10-26): cross-sectional sector momentum. At 10:15 (the 10:10 5-minute bar's close) rank the stocks by % change since the
    open and buy the leaders whose SECTOR ETF is also up by at least a threshold; hold to ~15:45 (66 five-minute bars) with a wide 3 ATR stop.
    Three pre-declared settings (top 1 / top 3 with the ETF > +0.5%, top 3 with the ETF > +0.25%) against two controls: the same ranking with NO
    sector filter, and 3 random stocks at 10:15. PASS needs the validate half: PF > 1, avg R > 0, 30+ trades, AND beating both controls on avg R.
    days: symbol -> list of 5-minute session frames (stocks and their sector ETFs); sector_of: stock -> ETF."""
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    by_day, frames = {}, {}
    for sym, fl in days.items():
        for d in fl:
            dd = d.index[0].date()
            frames[(sym, dd)] = d
            t = d.index.strftime("%H:%M")
            hit = np.flatnonzero(t == at)
            if len(hit):
                by_day.setdefault(dd, {})[sym] = float(d["close"].iloc[int(hit[0])] / d["open"].iloc[0] - 1.0)
    rng = np.random.default_rng(seed)

    def picks(rule):
        out = []
        for dd, pc in by_day.items():
            cands = [(pc[s], s) for s in pc if s in sector_of and sector_of[s] in pc]
            if not cands:
                continue
            if rule["kind"] == "random":
                sel = [cands[i][1] for i in rng.choice(len(cands), size=min(rule["n"], len(cands)), replace=False)]
            else:
                ranked = sorted(cands, reverse=True)
                if rule["thr"] is not None:
                    ranked = [c for c in ranked if pc[sector_of[c[1]]] >= rule["thr"]]
                sel = [s for _, s in ranked[: rule["n"]]]
            out += [(s, dd) for s in sel]
        return out

    rules = {"top 1, sector ETF > +0.5%": {"kind": "rank", "n": 1, "thr": 0.005}, "top 3, sector ETF > +0.5%": {"kind": "rank", "n": 3, "thr": 0.005},
             "top 3, sector ETF > +0.25%": {"kind": "rank", "n": 3, "thr": 0.0025},
             "CONTROL: top 3, no sector filter": {"kind": "rank", "n": 3, "thr": None}, "CONTROL: 3 random stocks": {"kind": "random", "n": 3}}
    chosen = {name: picks(rule) for name, rule in rules.items()}
    heavy = replace(costs, slip_bps=costs.slip_bps * 2)

    def run_rule(sel, c, a, b):
        trades = []
        for sym, dd in sel:
            if not (a <= dd <= b):
                continue
            d = frames[(sym, dd)]
            t = d.index.strftime("%H:%M")
            i = int(np.flatnonzero(t == at)[0])
            av = float((d["high"] - d["low"]).iloc[: i + 1].mean())          # mean bar range so far today (a 14-bar ATR does not exist at 10:15)
            if not np.isfinite(av) or av <= 0:
                continue
            trades += simulate_day(sym, d, [Signal(i, 1, 3.0 * av, 0.0, 66, "sector_mom")], c, max_trades=1)
        fills = size_intraday(sorted(trades, key=lambda x: x.entry_time), c, pf) if trades else pd.DataFrame()
        return {k: metrics(fills, start_equity=pf.equity).get(k) for k in keys}

    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        rows = {}
        for name, sel in chosen.items():
            rows[name] = run_rule(sel, costs, a, b)
            rows[name + " | 2x slippage"] = run_rule(sel, heavy, a, b)
        out["periods"][per] = rows
    v = out["periods"]["validate"]
    ctl = max((v[n].get("avg_r") or -9) for n in rules if n.startswith("CONTROL"))
    ok = [n for n in rules if not n.startswith("CONTROL") and (v[n].get("profit_factor") or 0) > 1 and (v[n].get("avg_r") or 0) > 0
          and (v[n].get("trades") or 0) >= 30 and (v[n].get("avg_r") or 0) > ctl]
    out.update({"family": "sector_mom", "passes": bool(ok),
                "verdict": f"PASS: {', '.join(ok)}" if ok else "no setting was profitable on the validate half AND better than both controls: not an edge here"})
    return out


def _trade_stats(rets: pd.Series) -> str:
    """n, win rate, mean %, PF of per-trade returns (a Series indexed by entry date)."""
    if len(rets) == 0:
        return "0 trades"
    w, l = rets[rets > 0].sum(), -rets[rets < 0].sum()
    return f"{len(rets)}t win {(rets > 0).mean():.0%} avg {rets.mean() * 100:+.2f}% PF {w / l if l > 0 else float('inf'):.2f}"


def _ret_curve_stats(ret: pd.Series) -> str:
    if len(ret) < 20:
        return "too short"
    eq = (1 + ret).cumprod()
    yrs = max(len(ret) / 252, 1e-9)
    sd = ret.std()
    return (f"CAGR {eq.iloc[-1] ** (1 / yrs) - 1:+.1%} Sharpe {ret.mean() / sd * np.sqrt(252) if sd > 0 else 0:.2f} "
            f"maxDD {(eq / eq.cummax() - 1).min():.1%}")


def daily_anomalies_study(daily: dict, etfs: list, split: date, stocks_cost: float = 0.0005, etf_cost: float = 0.0002) -> list[str]:
    """Gemini round 2 (2026-10-26): five multi-day ideas on daily bars, each with ONE pre-declared setting and its own control, judged on the
    halves before / after `split`. Costs are per side (stocks 5 bps, ETFs 2 bps). Entries after a close signal fill at the NEXT open.
    A turn-of-month on SPY; B sector-ETF dual momentum (top 3 of 11 by 120-day return, 20-day hold) vs SPY buy-and-hold; C 52-week-high
    breakout with volume > 2x its 50-day average (exit: close under the prior 10-day low) vs the same without the volume filter; D 12-1 momentum
    (top third monthly) vs the equal-weight universe; E NR7 breakout, 3-day hold, vs every day."""
    out = []
    stocks = {s: d for s, d in daily.items() if s not in etfs and s != "SPY"}
    half = lambda s: (s[s.index.date <= split], s[s.index.date > split])        # noqa: E731

    # ---- A turn of the month on SPY
    spy = daily["SPY"]
    c = spy["close"]
    key = pd.Series(list(zip(c.index.year, c.index.month)), index=c.index)
    pos_in = c.groupby(key).cumcount()
    n_in = c.groupby(key).transform("size")
    ent = np.flatnonzero((n_in - pos_in == 4).to_numpy())
    rets = {}
    for i in ent:
        nxt = np.flatnonzero(((pos_in == 2) & (c.index > c.index[i])).to_numpy())
        if len(nxt):
            rets[c.index[i]] = c.iloc[nxt[0]] / c.iloc[i] - 1 - 2 * etf_cost
    tom = pd.Series(rets)
    hold = int(np.median([int(np.flatnonzero(((pos_in == 2) & (c.index > c.index[i])).to_numpy())[0] - i) for i in ent[:40] if len(np.flatnonzero(((pos_in == 2) & (c.index > c.index[i])).to_numpy()))]) or 7)
    win7 = (c.shift(-hold) / c - 1 - 2 * etf_cost).dropna()
    out.append(f"### A turn-of-the-month SPY (buy 4th-to-last day's close, sell the 3rd trading day of the next month; ~{hold} days)")
    for nm, (a, b) in (("train", half(tom)), ("validate", half(tom))):
        wa, wb = half(win7)
        ctl = (wa if nm == "train" else wb)
        out.append(f"- {nm}: {_trade_stats(a if nm == 'train' else b)} | CONTROL every {hold}-day window: avg {ctl.mean() * 100:+.2f}%, win {(ctl > 0).mean():.0%}")

    # ---- B sector ETF dual momentum
    e = pd.DataFrame({s: daily[s]["close"] for s in etfs if s in daily}).dropna(how="any")
    sp = daily["SPY"]["close"].reindex(e.index)
    mom, spm = e.pct_change(120), sp.pct_change(120)
    daily_ret = e.pct_change()
    port, w_prev = pd.Series(0.0, index=e.index), pd.Series(0.0, index=e.columns)
    for t in range(121, len(e) - 1, 20):
        w = pd.Series(0.0, index=e.columns)
        if spm.iloc[t] > 0:
            top = mom.iloc[t].nlargest(3)
            w[top[top > 0].index] = 1.0 / 3
        seg = slice(t + 1, min(t + 21, len(e)))
        port.iloc[seg] = (daily_ret.iloc[seg] * w).sum(axis=1).to_numpy()
        port.iloc[t + 1] -= 2 * etf_cost * float((w - w_prev).abs().sum()) / 2
        w_prev = w
    port = port.iloc[121:]
    bh = sp.pct_change().reindex(port.index)
    out.append("### B sector-ETF dual momentum (top 3 of the sector ETFs by 120-day return if > 0 and SPY 120-day > 0, rebalanced every 20 days) vs SPY buy-and-hold")
    for nm, i in (("train", 0), ("validate", 1)):
        out.append(f"- {nm}: rotation {_ret_curve_stats(half(port)[i])} | SPY buy-and-hold {_ret_curve_stats(half(bh)[i])}")

    # ---- C 52-week-high breakout (with and without the volume filter)
    def breakouts(volume_filter: bool) -> pd.Series:
        res = {}
        for s, d in stocks.items():
            if len(d) < 300:
                continue
            cl, op, lo = d["close"], d["open"], d["low"]
            sig = cl >= cl.shift(1).rolling(252).max()
            if volume_filter:
                sig &= d["volume"] > 2 * d["volume"].rolling(50).mean()
            exit_ = cl < lo.shift(1).rolling(10).min()
            i, n = 0, len(d)
            sg, ex = sig.fillna(False).to_numpy(), exit_.fillna(False).to_numpy()
            while i < n - 2:
                if sg[i]:
                    e0 = i + 1
                    j = e0 + 1
                    while j < min(n - 1, e0 + 60) and not ex[j]:
                        j += 1
                    res[(d.index[e0], s)] = float(op.iloc[min(j + 1, n - 1)] / op.iloc[e0] - 1 - 2 * stocks_cost)
                    i = j + 1
                else:
                    i += 1
        return pd.Series({k: v for k, v in res.items()}).sort_index(level=0) if res else pd.Series(dtype=float)
    out.append("### C 52-week-high breakout (next open; exit next open after a close under the prior 10-day low; max 60 days)")
    for label, vf in (("with volume > 2x 50-day avg", True), ("CONTROL no volume filter", False)):
        br = breakouts(vf)
        idx = pd.Series(br.values, index=pd.to_datetime([k[0] for k in br.index])) if len(br) else br
        for nm, i in (("train", 0), ("validate", 1)):
            out.append(f"- {nm} | {label}: {_trade_stats(half(idx)[i] if len(idx) else idx)}")

    # ---- D 12-1 momentum, top third monthly vs the equal-weight universe
    px = pd.DataFrame({s: d["close"] for s, d in stocks.items()}).sort_index()        # NOT dropna(any): a young stock must not cut the history
    fwd = px.shift(-21) / px - 1 - 2 * stocks_cost
    sc = px.shift(21) / px.shift(252) - 1
    rows = []
    for t in range(252, len(px) - 21, 21):
        r_ = sc.iloc[t].dropna()
        r_ = r_[fwd.iloc[t].reindex(r_.index).notna()]
        if len(r_) < 6:
            continue
        top = r_.nlargest(max(len(r_) // 3, 1)).index
        rows.append((px.index[t], fwd.iloc[t][top].mean(), fwd.iloc[t][r_.index].mean()))
    mo = pd.DataFrame(rows, columns=["d", "top", "all"]).set_index("d")
    mo.index = pd.to_datetime(mo.index)
    out.append("### D 12-1 momentum (top third of the universe by return from t-252 to t-21, held 21 days) vs the equal-weight universe")
    for nm, i in (("train", 0), ("validate", 1)):
        if mo.empty:
            out.append(f"- {nm}: not enough stocks with history")
            continue
        m_ = half(mo["top"])[i]
        a_ = half(mo["all"])[i]
        out.append(f"- {nm}: {len(m_)} months, top third avg {m_.mean() * 100:+.2f}%/month, universe avg {a_.mean() * 100:+.2f}%/month, "
                   f"top beat the universe in {(m_ > a_).mean():.0%} of months")

    # ---- E NR7 breakout, 3-day hold, vs every day
    ne, base = {}, {}
    for s, d in stocks.items():
        rng_ = d["high"] - d["low"]
        nr7 = rng_.shift(1) <= rng_.shift(1).rolling(7).min()
        sig = (nr7 & (d["close"] > d["high"].shift(1))).fillna(False).to_numpy()
        fr = (d["close"].shift(-3) / d["open"].shift(-1) - 1 - 2 * stocks_cost)
        for i in np.flatnonzero(sig):
            if i < len(d) - 4:
                ne[(d.index[i], s)] = fr.iloc[i]
        for t_, v in fr.dropna().items():
            base[(t_, s)] = v
    en = pd.Series(list(ne.values()), index=pd.to_datetime([k[0] for k in ne])) if ne else pd.Series(dtype=float)
    bs = pd.Series(list(base.values()), index=pd.to_datetime([k[0] for k in base]))
    out.append("### E NR7 breakout (yesterday the narrowest range of 7, today closes above yesterday's high; next open to the close 3 days later) vs every day")
    for nm, i in (("train", 0), ("validate", 1)):
        bb = half(bs)[i]
        out.append(f"- {nm}: {_trade_stats(half(en)[i] if len(en) else en)} | CONTROL every stock-day: avg {bb.mean() * 100:+.2f}%, win {(bb > 0).mean():.0%}")
    return out


def trend_benchmark_lines(daily: dict, split: date, cost: float = 0.0002) -> list[str]:
    """Gemini round 3 (2026-10-26): the benchmark every swing idea must beat - hold the index while its close is above its 200-day average, cash otherwise
    (signal on the close, position from the next day, 2 bps per side when it switches) vs buy-and-hold, per half and in full."""
    out = ["### Benchmark: hold the index above its 200-day average, cash below"]
    for sym in ("SPY", "QQQ"):
        if sym not in daily:
            continue
        c = daily[sym]["close"]
        ret = c.pct_change()
        on = (c > c.rolling(200).mean()).shift(1).fillna(False).astype(float)
        strat = ret * on - cost * on.diff().abs().fillna(0.0)
        for label, sel in (("train", lambda s: s[s.index.date <= split]), ("validate", lambda s: s[s.index.date > split]), ("all", lambda s: s)):
            s_, b_ = sel(strat.iloc[200:]), sel(ret.iloc[200:])
            out.append(f"- {sym} {label}: 200-SMA rule {_ret_curve_stats(s_)}, in the market {sel(on.iloc[200:]).mean():.0%} | buy-and-hold {_ret_curve_stats(b_)}")
    return out


def vol_target_lines(daily: dict, split: date, target: float = 0.15, cost: float = 0.0002) -> list[str]:
    """Gemini round 6 (2026-10-27, Moreira & Muir 2017): the 200-day rule with each index scaled to a 15% volatility target. Every Friday close the weight
    becomes min(1, 15% / 20-day realized vol) while the close is above the 200-day average (0 below; the 200-day exit still acts daily); 2 bps per side on
    every weight change. CONTROL = the plain 200-day rule (100% / 0%). Gemini's kill rule: keep only if Sharpe improves by >= +0.10 AND the max drawdown
    improves by >= 15% relative."""
    out = [f"### Vol-targeted 200-day rule (target {target:.0%}, weekly) vs the plain 200-day rule"]
    for sym in ("SPY", "QQQ"):
        if sym not in daily:
            continue
        c = daily[sym]["close"]
        ret = c.pct_change()
        above = c > c.rolling(200).mean()
        vol = np.log(c).diff().rolling(20).std() * np.sqrt(252)
        raw = (target / vol).clip(upper=1.0)
        fri = pd.Series(c.index.weekday == 4, index=c.index)
        w_vt = raw.where(fri).ffill().where(above, 0.0).fillna(0.0)
        w_pl = above.astype(float)
        res = {}
        for label, w in (("vol-target", w_vt), ("plain 200-day", w_pl)):
            wp = w.shift(1).fillna(0.0)
            s = (ret * wp - cost * w.diff().abs().shift(1).fillna(0.0)).iloc[220:]
            res[label] = s
        for nm, sel in (("train", lambda s: s[s.index.date <= split]), ("validate", lambda s: s[s.index.date > split]), ("all", lambda s: s)):
            out.append(f"- {sym} {nm}: vol-target {_ret_curve_stats(sel(res['vol-target']))} | plain {_ret_curve_stats(sel(res['plain 200-day']))}")
        def sh_dd(s):
            eq = (1 + s).cumprod()
            return s.mean() / s.std() * np.sqrt(252), (eq / eq.cummax() - 1).min()
        a_sh, a_dd = sh_dd(res["vol-target"])
        b_sh, b_dd = sh_dd(res["plain 200-day"])
        keep = (a_sh - b_sh) >= 0.10 and a_dd >= b_dd * 0.85
        out.append(f"- {sym} verdict (all years): Sharpe {a_sh:.2f} vs {b_sh:.2f}, maxDD {a_dd:.1%} vs {b_dd:.1%} -> {'KEEP' if keep else 'REJECT'} (needs +0.10 Sharpe and 15% smaller drawdown)")
    return out


def power_gap_lines(daily: dict, split: date, label: str, cost: float = 0.0005, hold: int = 10) -> list[str]:
    """Gemini round 3 'power earnings gap' (a price/volume proxy for a big earnings surprise): the open gaps up > 4% on volume > 3x the 50-day average and
    the close is in the top 25% of the day's range. Buy the NEXT open, sell the close `hold` days later, or exit at the next open if a close falls under
    the gap day's low. CONTROL: every stock-day with the same entry and 10-day exit. Gemini's kill rule: the edge must beat the control by >= 0.20%."""
    rows, base = [], []
    for s, d in daily.items():
        if s in ("SPY", "QQQ") or len(d) < 300:
            continue
        o, h, l, c, v = (d[k] for k in ("open", "high", "low", "close", "volume"))
        sig = ((o / c.shift(1) - 1) > 0.04) & (v > 3 * v.rolling(50).mean()) & (c >= l + 0.75 * (h - l))
        sg, n = sig.fillna(False).to_numpy(), len(d)
        for i in range(n - hold - 2):
            e = i + 1
            ctl = c.iloc[e + hold - 1] / o.iloc[e] - 1 - 2 * cost
            base.append((d.index[e], ctl))
            if sg[i]:
                gap_low, j = float(l.iloc[i]), e
                while j < e + hold - 1 and c.iloc[j] >= gap_low:
                    j += 1
                px_out = c.iloc[j] if j == e + hold - 1 and c.iloc[j] >= gap_low else o.iloc[min(j + 1, n - 1)]
                rows.append((d.index[e], px_out / o.iloc[e] - 1 - 2 * cost))
    tr = pd.Series({k: v for k, v in rows}) if rows else pd.Series(dtype=float)
    bs = pd.Series([v for _, v in base], index=pd.to_datetime([k for k, _ in base]))
    out = [f"### Power earnings gap proxy on {label} (buy next open after a >4% gap up on >3x volume closing in the top 25% of its range; {hold}-day hold; stop = close under the gap-day low)"]
    for nm, sel in (("train", lambda s: s[s.index.date <= split]), ("validate", lambda s: s[s.index.date > split])):
        t_, b_ = (sel(tr) if len(tr) else tr), sel(bs)
        diff = (t_.mean() - b_.mean()) * 100 if len(t_) else float("nan")
        out.append(f"- {nm}: {_trade_stats(t_)} | CONTROL every stock-day {hold}-day hold: avg {b_.mean() * 100:+.2f}%, win {(b_ > 0).mean():.0%} | edge vs control {diff:+.2f}% (kill if < +0.20%)")
    return out


def orb_candidate_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None,
                        ladder: dict | None = None) -> dict:
    """Owner's idea (2026-10-14, "trading the open"): the opening-range-breakout expert as an 11th expert on the live configuration
    (direction first + per-expert zones + zone map + the exit plan in use). It joins the live bot only if the brain WITH it beats the
    brain without it in both the train and the validation years (_zone_verdict). ORB alone is reported for information."""
    from .brain import DirectionFirst
    from .engine import EXPERT_LADDERS, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    lad = ladder or {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [0.25, 0.5], "tp_depth_atr": [0.0, 0.25], "lock": ["breakeven", "tp"],
                     "trail_atr": 1.0, "by_expert": EXPERT_LADDERS}

    def run(experts, a, b):
        br = Brain(experts=list(experts), short_experts=list(experts), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": lad}
        r = run_intraday(DirectionFirst(br), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {
            "ten experts (live)": cp.get(f"orb-{per}-base", lambda a=a, b=b: run(TEN_EXPERTS, a, b)),
            "ten + opening-range breakout": cp.get(f"orb-{per}-with", lambda a=a, b=b: run(list(TEN_EXPERTS) + ["opening_range_breakout"], a, b)),
            "opening-range breakout alone": cp.get(f"orb-{per}-alone", lambda a=a, b=b: run(["opening_range_breakout"], a, b))}
    rows = [{"without": out["periods"][p]["ten experts (live)"], "with": out["periods"][p]["ten + opening-range breakout"]} for p in out["periods"]]
    ok = _zone_verdict(rows)
    out.update({"family": "orb_candidate", "passes": ok,
                "verdict": ("ADD opening_range_breakout as an 11th expert: better than the live ten in the train and validation years" if ok else
                            "do not add it: the ten experts without it did as well or better in the train or validation years")})
    return out


def swing_short_study(universes: dict, spy: pd.DataFrame, costs: Costs) -> dict:
    """Owner's 'follow the trend down' for the SWING bot (2026-10-15): does adding a short side to Sector Top-Down help the live long-only
    book? Short rules = the live rules mirrored (strategies.SectorTopDownShort), with the pre-declared gates Weinstein stage 4 and a
    weak market (SPY below its 50-day average). Per universe, train 2016-03..2019 and validate 2020-2022. A variant PASSES when the
    combined long+short book beats the long-only book in every row (research._zone_verdict). Shorts alone are reported for information.
    Note the top-5-per-sector list is a list of 2026's winners, which is hard on shorts; the 2016 control list is the fairer row."""
    from .engine import simulate_swing_short
    from .strategies import SectorTopDown, SectorTopDownShort
    pf = Portfolio(risk_pct=0.0035, max_positions=3, max_pos_frac=0.333)
    periods = {"train": (date(2016, 3, 1), date(2019, 12, 31)), "validate": (date(2020, 1, 1), date(2022, 12, 31))}
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor")
    shorts = {"short mirror": dict(), "short + stage 4": dict(stage4=True), "short + weak market": dict(market_weak=True),
              "short + stage 4 + weak market": dict(stage4=True, market_weak=True)}

    def book(data, a, b, with_long=True, short_kw=None):
        trades = []
        for sym, d in data.items():
            if with_long:
                trades += simulate_swing(sym, d, SectorTopDown(spy["close"]), costs, all_signals=True)
            if short_kw is not None:
                trades += simulate_swing_short(sym, d, SectorTopDownShort(spy["close"], **short_kw), costs)
        trades = [t for t in trades if a <= t.entry_time.date() <= b]
        closes = {sym: d["close"] for sym, d in data.items()}
        fills, curve = size_swing(sorted(trades, key=lambda t: (t.entry_time, -t.rank)), closes, costs, pf, a, b)
        return {**{k: metrics(fills, curve, pf.equity).get(k) for k in keys}, **_curve_stats(curve, pf.equity)}
    rows = []
    for name, data in universes.items():
        for per, (a, b) in periods.items():
            base = book(data, a, b)
            for vname, kw in shorts.items():
                rows.append({"universe": name, "period": per, "variant": f"long + {vname}", "without": base, "with": book(data, a, b, True, kw)})
                rows.append({"universe": name, "period": per, "variant": f"(info) {vname} alone", "without": base, "with": book(data, a, b, False, kw)})
    names = [f"long + {v}" for v in shorts]
    passed = {v: _zone_verdict([r for r in rows if r["variant"] == v]) for v in names}
    winners = [v for v, ok in passed.items() if ok]
    return {"family": "swing_short", "rows": rows, "passes": bool(winners), "variant_passes": passed,
            "verdict": (f"PASS: {'; '.join(winners)} beat long-only in every row - propose adding the short side to the swing bot" if winners
                        else "no short variant beat the long-only swing book in every row: the swing bot stays long-only")}


def mtf_confirm_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner's multi-timeframe trend confirmation (2026-10-15) on the live configuration (direction first + Weinstein stage + zones + zone map +
    the drawn exit plan): (a) entries confirmed on weekly / 1-hour / 15-minute, (b) plus the in-trade re-check (1-hour trend turns against a
    trend-expert trade -> exit). Each variant must beat the unconfirmed brain in the train AND validation years (_zone_verdict)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(confirm, trend_exit, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": trend_exit}
        br = StageFilter(DirectionFirst(br))
        if confirm:
            br = TrendConfirm(br)
        r = run_intraday(br, days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {
            "live brain without confirmation": cp.get(f"mtf-{per}-base", lambda a=a, b=b: run(False, False, a, b)),
            "+ entry confirmed on weekly/1h/15m": cp.get(f"mtf-{per}-entry", lambda a=a, b=b: run(True, False, a, b)),
            "+ entry and in-trade re-check": cp.get(f"mtf-{per}-both", lambda a=a, b=b: run(True, True, a, b))}
    verdicts = {}
    for v in ("+ entry confirmed on weekly/1h/15m", "+ entry and in-trade re-check"):
        verdicts[v] = _zone_verdict([{"without": out["periods"][p]["live brain without confirmation"], "with": out["periods"][p][v]} for p in out["periods"]])
    winners = [v for v, ok in verdicts.items() if ok]
    out.update({"family": "mtf_confirm", "passes": bool(winners), "variant_passes": verdicts,
                "verdict": (f"PASS: {'; '.join(winners)} beat the unconfirmed brain in train and validation" if winners else
                            "multi-timeframe confirmation did not beat the live brain in both periods: switch it off in zones.json (mtf.entry / mtf.trend_exit)")})
    return out


def profile_assign_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Which zone profile suits the two experts that sit on the trend profile only by default (owner's question, 2026-10-15): dual_regime and
    opening_range_breakout. The live configuration (direction first + stage + multi-timeframe confirmation + the drawn exit plan + zone map),
    with the expert(s) moved to the range profile (20-bar mean target) by adding them to engine.RANGE_STYLE for the run. Each variant must beat
    the live assignment in the train AND validation years (_zone_verdict)."""
    from . import engine
    from .brain import DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(moved, a, b):
        before = set(engine.RANGE_STYLE)
        engine.RANGE_STYLE.update(moved)
        try:
            br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
            br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
            r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(br))), days, costs, pf, a, b)
        finally:
            engine.RANGE_STYLE.clear()
            engine.RANGE_STYLE.update(before)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (both on the trend profile)": [], "dual_regime on the range profile": ["dual_regime"],
                "opening_range_breakout on the range profile": ["opening_range_breakout"],
                "both on the range profile": ["dual_regime", "opening_range_breakout"]}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"pa-{per}-{k}", lambda m=moved, a=a, b=b: run(m, a, b)) for k, (name, moved) in enumerate(variants.items())}
    base = "live (both on the trend profile)"
    verdicts = {v: _zone_verdict([{"without": out["periods"][p][base], "with": out["periods"][p][v]} for p in out["periods"]]) for v in variants if v != base}
    winners = [v for v, ok in verdicts.items() if ok]
    out.update({"family": "profile_assign", "passes": bool(winners), "variant_passes": verdicts,
                "verdict": (f"MOVE: {'; '.join(winners)} beat the live assignment in train and validation" if winners else
                            "no move beat the live assignment in both periods: leave dual_regime and opening_range_breakout on the trend profile")})
    return out


def box_range_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's OANDA range box applied to the stock range experts (2026-10-16): the five mean-reversion experts keep the Weinstein stage filter
    (owner's paper plan: Weinstein, trend indicators, then the box) and in addition need the stock's daily range box (valid box; "half" = buy the lower
    half / sell the upper half; "edge" = outer third). Judged against
    the live configuration (direction first + stage + multi-timeframe confirmation + the drawn exits + zone map); a variant must beat it in the
    train AND validation years (_zone_verdict). Needs days attached with with_box=True."""
    from .brain import BoxRangeBrain, DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(position, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        br = StageFilter(DirectionFirst(br)) if position is None else BoxRangeBrain(DirectionFirst(br), position)
        r = run_intraday(TrendConfirm(br), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (no box)": None, "range experts on the box: valid": "valid",
                "range experts on the box: lower/upper half": "half", "range experts on the box: outer third": "edge"}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"br-{per}-{k}", lambda p=pos, a=a, b=b: run(p, a, b)) for k, (name, pos) in enumerate(variants.items())}
    base = "live (no box)"
    verdicts = {v: _zone_verdict([{"without": out["periods"][p][base], "with": out["periods"][p][v]} for p in out["periods"]]) for v in variants if v != base}
    winners = [v for v, ok in verdicts.items() if ok]
    out.update({"family": "box_range", "passes": bool(winners), "variant_passes": verdicts,
                "verdict": (f"ADOPT: {'; '.join(winners)} beat the live configuration in train and validation" if winners else
                            "the range box did not beat the live configuration for the range experts in both periods: leave the stock range experts as they are")})
    return out


def box_strategy_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's range box as a standalone stock strategy (BoxSignals), on the live configuration (direction first + Weinstein stage + multi-
    timeframe confirmation + the drawn exit plan + zone map). Variants: live; live + the box strategy as an extra expert; the box strategy alone.
    "+ box strategy" must beat live in the train AND validation years (_zone_verdict). Needs days attached with with_box=True."""
    from . import engine
    from . import experts as ex
    from .brain import BoxSignals, DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(mode, a, b):
        before_s, before_e = set(engine.RANGE_STYLE), set(ex.RANGE_EXPERTS)
        engine.RANGE_STYLE.add("range_box")                      # a mean-reversion strategy: range profile, no trend-style checks
        ex.RANGE_EXPERTS.add("range_box")
        try:
            br = Brain(experts=[] if mode == "alone" else list(ALL_EXPERTS), short_experts=[] if mode == "alone" else list(ALL_EXPERTS),
                       sector_filter=True, short_gate="red")
            br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
            inner = BoxSignals(br) if mode in ("with", "alone") else br
            r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        finally:
            engine.RANGE_STYLE.clear(); engine.RANGE_STYLE.update(before_s)
            ex.RANGE_EXPERTS.clear(); ex.RANGE_EXPERTS.update(before_e)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (no box strategy)": "live", "live + range box strategy": "with", "range box strategy alone": "alone"}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"bs-{per}-{mode}", lambda m=mode, a=a, b=b: run(m, a, b)) for name, mode in variants.items()}
    ok = _zone_verdict([{"without": out["periods"][p]["live (no box strategy)"], "with": out["periods"][p]["live + range box strategy"]} for p in out["periods"]])
    out.update({"family": "box_strategy", "passes": bool(ok),
                "verdict": ("ADD: the range box as a stock strategy beat the live configuration in train and validation" if ok else
                            "the range box strategy did not beat the live configuration in both periods: not added")})
    return out


def trend_stairs_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's trend_stairs design (brain.TrendStairsSignals) on the live configuration (direction first + Weinstein stage + multi-timeframe +
    zone map), with its own zones and stair-step exits. Variants: live; live + trend_stairs (3 of 4); trend_stairs alone 3 of 4 and 2 of 4.
    Reported for the owner's live-paper rule (it goes live at 0.5% unless clearly harmful)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(mode, need, a, b, sr_rsi=False, sides=(1, -1), confirm=0.0):
        br = Brain(experts=[] if mode == "alone" else list(ALL_EXPERTS), short_experts=[] if mode == "alone" else list(ALL_EXPERTS),
                   sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {"trend_stairs": EXPERT_LADDERS["trend_stairs"]}}}
        inner = TrendStairsSignals(br, need=need, sr_rsi=sr_rsi, need6=need, sides=sides, confirm=confirm) if mode in ("with", "alone") else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (no trend_stairs)": ("live", 3), "live + trend_stairs (3 of 4)": ("with", 3),
                "trend_stairs alone, 3 of 4 agree": ("alone", 3), "trend_stairs alone, 2 of 4 agree": ("alone", 2),
                "trend_stairs alone + S/R + RSI, 4 of 6": ("alone", 4, True), "trend_stairs alone + S/R + RSI, 5 of 6": ("alone", 5, True),
                # owner 2026-10-17: the short side = the opposite of the long trend, stair-step stop, trend confirmed by candles / volume /
                # volatility (3 of 5) - longs and shorts reported apart
                "LONGS only, 3 of 4": ("alone", 3, False, (1,)), "SHORTS only, 3 of 4": ("alone", 3, False, (-1,)),
                "LONGS only, 3 of 4 + confirmed (3 of 5)": ("alone", 3, False, (1,), 0.6),
                "SHORTS only, 3 of 4 + confirmed (3 of 5)": ("alone", 3, False, (-1,), 0.6),
                "SHORTS only, + S/R + RSI 4 of 6 + confirmed": ("alone", 4, True, (-1,), 0.6)}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"ts-{per}-{v[0]}-{v[1]}" + ("-srrsi" if len(v) > 2 and v[2] else "")
                                            + (f"-s{''.join(str(x) for x in v[3])}" if len(v) > 3 else "") + (f"-c{v[4]}" if len(v) > 4 else ""),
                                            lambda v=v, a=a, b=b: run(v[0], v[1], a, b, *v[2:])) for name, v in variants.items()}
    ok = _zone_verdict([{"without": out["periods"][p]["live (no trend_stairs)"], "with": out["periods"][p]["live + trend_stairs (3 of 4)"]}
                        for p in out["periods"]])
    out.update({"family": "trend_stairs", "passes": bool(ok),
                "verdict": "live + trend_stairs beat live in both halves" if ok else "trend_stairs did not beat live in both halves (live-paper rule decides)"})
    return out


def trend_stairs58_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's Trend Stock / Short Trend drawings on all 58 live stocks (2026-10-17): trend_stairs alone, with and without the daily
    funnel lists (day.attrs["ts_ok"] from alpacabot.selection), the TP box exit and the candle / volume / volatility confirmation, longs and
    shorts apart. Live configuration filters (direction first, Weinstein stage, multi-timeframe)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(kw, a, b):
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {"trend_stairs": EXPERT_LADDERS["trend_stairs"]}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(TrendStairsSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {
        "1. both sides, no lists (as live)": {},
        "2. both sides + daily lists": {"select": True},
        "3. both sides + lists + TP box": {"select": True, "tp_zone": True},
        "4. LONGS + lists + TP box": {"select": True, "tp_zone": True, "sides": (1,)},
        "5. SHORTS + lists + TP box": {"select": True, "tp_zone": True, "sides": (-1,)},
        "6. LONGS + lists + TP box + confirmed (3 of 5)": {"select": True, "tp_zone": True, "sides": (1,), "confirm": 0.6},
        "7. SHORTS + lists + TP box + confirmed (3 of 5)": {"select": True, "tp_zone": True, "sides": (-1,), "confirm": 0.6},
        "8. SHORTS, no lists (plain mirror)": {"sides": (-1,)},
    }
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"ts58-{per}-{name[:2]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "trend_stairs58", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def scalper_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-19 scalper redesign (brain.ScalperSignals) alone on the live filters (direction first, Weinstein stage, multi-timeframe),
    longs only, 0.5% risk: the stop at 0.5 / 0.75 / 1.0 R with the 1.25 R target and the 30-minute time stop, 3 of 5 oversold, and 2 of 5 /
    no time stop for context. It goes live on paper at the first variant (live-paper rule); this reports how the others compare."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, ScalperSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(kw, a, b):
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, "scalper": EXPERT_ZONES["scalper"]}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {"scalper": EXPERT_LADDERS["scalper"]}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(ScalperSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"1. stop 0.5 R / target 1.25 R / 30 min (live)": {}, "2. stop 0.75 R": {"stop_r": 0.75}, "3. stop 1.0 R": {"stop_r": 1.0},
                "4. 2 of 5 oversold": {"need": 2}, "5. hold 12 bars (1 hour)": {"hold": 12},
                "6. 15-minute trend only": {"trend": "m15"}, "7. 15-minute trend only + 2 of 5": {"trend": "m15", "need": 2},
                "8. no intraday trend condition + 2 of 5": {"trend": "none", "need": 2}, "9. no intraday trend, 3 of 5, stop 1.0 R": {"trend": "none", "stop_r": 1.0}}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"sc-{per}-{name[:2]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "scalper", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def velez_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-20 Velez rules (brain.VelezSignals) on the live filters (direction first, Weinstein stage, multi-timeframe), longs, 0.5% risk:
    all three setups, each alone, and the target at 1R / 2R. Goes live on paper at the first variant (live-paper rule)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals, VELEZ_TAGS
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(kw, a, b):
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, **{t: EXPERT_ZONES[t] for t in VELEZ_TAGS}}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {t: EXPERT_LADDERS[t] for t in VELEZ_TAGS}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"1. all three setups, 1.5R (live)": {}, "2. 20 MA halt only": {"setups": ("velez_halt",)},
                "3. VBS (3-5 bar dip) only": {"setups": ("velez_vbs",)}, "4. red bar ignored only": {"setups": ("velez_rbi",)},
                "5. all three, target 1R": {"tp_r": 1.0}, "6. all three, target 2R": {"tp_r": 2.0}}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"vz-{per}-{name[:2]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "velez", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def velez2_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-20, Velez's second list (brain.VelezSignals research setups): GIFT, NRB, 9:35 opening-range break, bear trap, late-day
    breakout, each alone and together; plus the "look left" obstruction filter and his reversal-time windows on the first three setups. Live filters
    (direction first, stage, multi-timeframe), longs, 0.5% risk. Meant for the one-month test (--month1): small samples, read with care."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals, VELEZ_RESEARCH, VELEZ_TAGS, VELEZ_TOD
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    tags = VELEZ_TAGS + VELEZ_RESEARCH
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 1.5, "exhaust_r": 99.0, "target": "extreme"}
    lad = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}

    def run(kw, a, b):
        from .engine import ZONE_PROFILES
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, **{t: prof for t in tags}}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {t: lad for t in tags}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"01. original three (live)": {}, "02. GIFT (elephant, then the retracement)": {"setups": ("velez_gift",)},
                "03. NRB (narrow range bar break)": {"setups": ("velez_nrb",)}, "04. 9:35 opening-range break": {"setups": ("velez_orb",)},
                "05. bear trap": {"setups": ("velez_trap",)}, "06. late-day breakout": {"setups": ("velez_ldb",)},
                "07. all five new setups": {"setups": VELEZ_RESEARCH}, "08. original three + look-left filter": {"look_left": True},
                "09. original three, reversal-time windows only": {"tod": VELEZ_TOD}, "10. all eight + look-left": {"setups": tags, "look_left": True}}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"vz2-{per}-{name[:2]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "velez2", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def velez3_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-20: the Velez setups held like DAY trades (the live data shows scalps <= 30 min lose, longer day trades win): a wider stop,
    a 2-3R target, hours instead of 90 minutes, with and without the "20 SMA broke" exit. One-month test (--month1)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals, VELEZ_RESEARCH, VELEZ_TAGS
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    tags = VELEZ_TAGS + VELEZ_RESEARCH
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 2.0, "exhaust_r": 99.0, "target": "extreme"}
    lad = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}

    def run(kw, a, b):
        from .engine import ZONE_PROFILES
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, **{t: prof for t in tags}}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {t: lad for t in tags}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"1. live: 1.5R, 90 min, 20 SMA exit": {},
                "2. day hold: 2R, 6 hours, 20 SMA exit": {"tp_r": 2.0, "hold": 72},
                "3. day hold: 2R, 6 hours, no signal exit, stop x1.5": {"tp_r": 2.0, "hold": 72, "sig_exit": False, "stop_mult": 1.5},
                "4. day hold: 3R, 6 hours, no signal exit, stop x1.5": {"tp_r": 3.0, "hold": 72, "sig_exit": False, "stop_mult": 1.5},
                "5. 2R, 3 hours, no signal exit, stop x1.5": {"tp_r": 2.0, "hold": 36, "sig_exit": False, "stop_mult": 1.5},
                "6. 2R, 6 hours, no signal exit, normal stop": {"tp_r": 2.0, "hold": 72, "sig_exit": False},
                "7. NRB only, 2R, 6 hours, no signal exit": {"setups": ("velez_nrb",), "tp_r": 2.0, "hold": 72, "sig_exit": False},
                "8. all eight, 2R, 6 hours, no signal exit, stop x1.5": {"setups": tags, "tp_r": 2.0, "hold": 72, "sig_exit": False, "stop_mult": 1.5}}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"vz3-{per}-{name[:1]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "velez3", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def velez4_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-21 ("all those rules"): Velez's remaining 5-minute setups (bottoming tail, clearing elephant, Bull 180 on simple averages,
    20/20 + Mortgage, flat-20 range scalp, flat-200 reversal) each alone, and the best-looking exit style from velez3 (2R, 3 hours, no signal exit,
    stop x1.5) beside the default. One-month test (--month1). Gap plays, TICK/TRIN breadth and the 2-minute chart are not in our data."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals, VELEZ_RESEARCH, VELEZ_RESEARCH2, VELEZ_TAGS
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    tags = VELEZ_TAGS + VELEZ_RESEARCH + VELEZ_RESEARCH2
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 2.0, "exhaust_r": 99.0, "target": "extreme"}
    lad = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}
    day_style = {"tp_r": 2.0, "hold": 36, "sig_exit": False, "stop_mult": 1.5}

    def run(kw, a, b):
        from .engine import ZONE_PROFILES
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, **{t: prof for t in tags}}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {t: lad for t in tags}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, **kw)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {}
    for i, tag in enumerate(VELEZ_RESEARCH2, 1):
        variants[f"{i}a. {tag}, default exit"] = {"setups": (tag,)}
        variants[f"{i}b. {tag}, day style (2R, 3h, wide stop)"] = {"setups": (tag,), **day_style}
    variants["8. all seven new, day style"] = {"setups": VELEZ_RESEARCH2, **day_style}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"vz4-{per}-{name[:2]}", lambda kw=kw, a=a, b=b: run(kw, a, b)) for name, kw in variants.items()}
    out.update({"family": "velez4", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def velez_repair_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-22 rescue plan for the two Velez setups with the most live trades (velez_rbi, velez_halt; live audit: tight stops, repeat
    entries, 15 of 24 never green). Each change ALONE against the live baseline (one-bar stop, 1.5R, 20-SMA exit, 18 bars, 3 trades / stock / day,
    1-bar cooldown): ATR stops, a structural stop with a wider buffer, cooldowns, one-per-day (fresh setup), max attempts, next-bar confirmation,
    exits (1.5R no signal exit, 2R, 8-SMA trail) and time exits. Live filters (direction first, stage, trend confirm), longs, 0.5% risk, the usual
    slippage. Read train AND validate: a change counts only when it helps on the unseen half. 'stopped' = stop exits, 'never green' = trades whose
    exit was a stop at or below the entry without ever closing a bar above it (approximated by exit reason stop and R <= -0.9)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 1.5, "exhaust_r": 99.0, "target": "extreme"}
    lad = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}
    tags = ("velez_rbi", "velez_halt")

    def run(tag, kw, bkw, a, b):
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red", **bkw)
        br.zones = {"profiles": {**ZONE_PROFILES, **{t: prof for t in tags}}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {t: lad for t in tags}}}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, setups=(tag,), **kw)))), days, costs, pf, a, b)
        m = {k: r["metrics"].get(k) for k in keys}
        f = r["fills"]
        if len(f):
            st = f["reason"].astype(str).str.contains("stop")
            m["stopped"], m["never_green"] = int(st.sum()), int((st & (f["r"] <= -0.9)).sum())
        return m
    variants = {"00 baseline (live rules)": ({}, {}),
                "01 stop 1.0 ATR": ({"stop_atr": 1.0}, {}), "02 stop 1.5 ATR": ({"stop_atr": 1.5}, {}), "03 stop 2.0 ATR": ({"stop_atr": 2.0}, {}),
                "04 structural stop + 0.5 ATR buffer": ({"buffer_atr": 0.5}, {}),
                "05 cooldown 30 min": ({}, {"cooldown": 6}), "06 cooldown 60 min": ({}, {"cooldown": 12}),
                "07 fresh setup only (first per day)": ({"first_only": True}, {}),
                "08 max 1 attempt / stock / day": ({}, {"max_trades": 1}), "09 max 2 attempts": ({}, {"max_trades": 2}),
                "10 next-bar confirmation": ({"confirm": True}, {}),
                "11 exit 1.5R, no 20-SMA exit": ({"sig_exit": False}, {}), "12 exit 2R": ({"tp_r": 2.0}, {}),
                "13 exit 8-SMA trail": ({"tp_r": 10.0, "exit_col": "x_sma8", "hold": 78}, {}),
                "14 time exit 6 bars": ({"hold": 6}, {}), "15 time exit 12 bars": ({"hold": 12}, {})}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {f"{tag} | {name}": cp.get(f"vrep-{per}-{tag}-{name[:2]}", lambda tag=tag, kw=kw, bkw=bkw, a=a, b=b: run(tag, kw, bkw, a, b))
                               for tag in tags for name, (kw, bkw) in variants.items()}
    out.update({"family": "velez_repair", "passes": False, "verdict": "see the table: a change counts only if it helps on validate too"})
    return out


def velez_redesign_study(days, train, validate, groups: dict, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6),
                         ckpt=None, neg_bars: int = 0) -> dict:
    """Owner 2026-10-22 redesign: the Velez-style scalper (brain.VZ_SCALP) and intraday trader (brain.VZ_DAY) through the live filters
    (direction first, stage, trend confirm), next to the experts kept after cancelling the live losers, on large / mid / small caps separately
    (`groups` = {name: set of symbols}). 2-bar (10-minute) cooldown per stock, 3 trades per stock per day. One-month test (--month1)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezSignals, VZ_DAY, VZ_SCALP
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 1.5, "exhaust_r": 99.0, "target": "extreme"}
    lad = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}
    keep_long = ["connors_rsi2", "vwap_dev", "bollinger_bounce", "williams_r", "brooks_pullback", "opening_range_breakout"]
    keep_short = ["dual_regime", "connors_rsi2", "brooks_pullback", "bollinger_bounce", "vwap_dev", "raschke_grail"]

    def run(system, syms, a, b):
        sub = {s_: fr for s_, fr in days.items() if s_ in syms or s_ in ("SPY", "QQQ", "IWM")}
        if system == "kept experts":
            br = Brain(experts=keep_long, short_experts=keep_short, sector_filter=True, short_gate="red", cooldown=2)
            strat = TrendConfirm(StageFilter(DirectionFirst(br)))
        else:
            br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red", cooldown=2)
            kw = VZ_SCALP if system == "vz_scalp" else VZ_DAY
            br.zones = {"profiles": {**ZONE_PROFILES, kw["tag_as"]: prof}, "levels": True, "trend_exit": True,
                        "ladder": {**_OWNER_LADDER, "by_expert": {kw["tag_as"]: lad}}}
            strat = TrendConfirm(StageFilter(DirectionFirst(VelezSignals(br, **kw))))
        sub = {s_: fr for s_, fr in sub.items() if s_ in syms}
        if not sub:
            return {"trades": 0}
        strat.neg_bars = neg_bars                                 # 2 = the live 10-minute under-water exit on 5-minute bars
        r = run_intraday(strat, sub, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    for per, (a, b) in (("first half", train), ("second half", validate)):
        out["periods"][per] = {f"{g} | {sysn}": cp.get(f"vzr-{per[:3]}-{g}-{sysn}-n{neg_bars}", lambda sysn=sysn, syms=syms, a=a, b=b: run(sysn, syms, a, b))
                               for g, syms in groups.items() for sysn in ("vz_scalp", "vz_day", "kept experts")}
    out.update({"family": "velez_redesign", "passes": False, "verdict": "see the table (one month: provisional)"})
    return out


TS58_GRID = {   # owner 2026-10-17 "optimize refine test": one knob at a time around the drawn design, chosen on train, checked on validate
    "base: lists + TP box": {},
    "entry 2 of 4": {"need": 2},
    "1-hour stochastic <= 60": {"h1_stoch_max": 60.0},
    "TP1 +1.0 ATR": {"tp1_atr": 1.0},
    "TP1 +1.8 ATR": {"tp1_atr": 1.8},
    "stop 1.0 ATR under the 20 MA": {"stop_atr": 1.0},
    "stop 1.6 ATR under the 20 MA": {"stop_atr": 1.6},
    "no TP box": {"tp_zone": False},
    "lists: trend strength 2 of 3": {"list_key": "ts_ok2"},
    "lists: 5 per sector": {"list_key": "ts_ok5"},
    "confirmed (3 of 5)": {"confirm": 0.6},
}


def trend_stairs58_opt(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Refine the owner's trend_stairs + daily lists on all 58 stocks: each TS58_GRID change alone (both sides, lists on, TP box on unless
    the change says otherwise), longs and shorts apart. Pre-declared rule: on TRAIN pick the change with the best profit factor among those
    with 15+ trades; it is ADOPTED only if it also beats the base on VALIDATE (profit factor and daily return). Then the adopted set is
    reported per side so a losing side can be switched off."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(kw, a, b):
        br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {"trend_stairs": EXPERT_LADDERS["trend_stairs"]}}}
        full = {"select": True, "tp_zone": True, **kw}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(TrendStairsSignals(br, **full)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {"train": {}, "validate": {}}}
    for name, kw in TS58_GRID.items():
        for per, (a, b) in (("train", train), ("validate", validate)):
            out["periods"][per][name] = cp.get(f"opt-{per}-{name}", lambda kw=kw, a=a, b=b: run(kw, a, b))
    tr, va = out["periods"]["train"], out["periods"]["validate"]
    ok = [n for n in TS58_GRID if n != "base: lists + TP box" and (tr[n].get("trades") or 0) >= 15]
    best = max(ok, key=lambda n: tr[n].get("profit_factor") or 0, default=None)
    base_v = va["base: lists + TP box"]
    adopt = bool(best) and (va[best].get("profit_factor") or 0) > (base_v.get("profit_factor") or 0) and \
        (va[best].get("daily_ret_pct_mean") or 0) > (base_v.get("daily_ret_pct_mean") or 0)
    chosen = dict(TS58_GRID[best]) if adopt else {}
    for side, label in (((1,), "LONGS"), ((-1,), "SHORTS")):
        for per, (a, b) in (("train", train), ("validate", validate)):
            out["periods"][per][f"chosen set, {label} only"] = cp.get(f"opt-{per}-chosen-{label}-{sorted(chosen.items())}",
                                                                     lambda a=a, b=b, side=side: run({**chosen, "sides": side}, a, b))
    out.update({"family": "trend_stairs58_opt", "best_on_train": best, "adopted": adopt, "chosen_changes": chosen, "passes": adopt,
                "verdict": (f"ADOPT '{best}': best on train and beat the base on validate" if adopt else
                            f"keep the base: best on train was '{best}', it did not beat the base on validate")})
    return out


def no_stop_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-17 "remove all stops, retest" (RESEARCH ONLY - the live bots keep their stops): the same trades with and without stops.
    trend_stairs as live (both sides, confirmed 3 of 5) and the 11 live experts (owner's ladder), on the given days. no_stop "initial" keeps
    the stair-step / trailing stops, "all" drops every stop (exits: sell vote, take-profit, 3:50 close)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(kind, no_stop, a, b):
        ts = kind == "trend_stairs"
        br = Brain(experts=[] if ts else list(ALL_EXPERTS), short_experts=[] if ts else list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        lad = {**_OWNER_LADDER, "by_expert": {"trend_stairs": EXPERT_LADDERS["trend_stairs"]}}
        if no_stop:
            lad["no_stop"] = no_stop
            lad["by_expert"] = {k: {**v, "no_stop": no_stop} for k, v in lad["by_expert"].items()}
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True, "ladder": lad}
        inner = TrendStairsSignals(br, confirm=0.6) if ts else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"trend_stairs WITH stops (as live)": ("trend_stairs", None), "trend_stairs, no starting stop (stair-step kept)": ("trend_stairs", "initial"),
                "trend_stairs, NO stops at all": ("trend_stairs", "all"), "11 experts WITH stops (as live)": ("experts", None),
                "11 experts, NO stops at all": ("experts", "all")}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {n: cp.get(f"ns-{per}-{k}-{ns}", lambda k=k, ns=ns, a=a, b=b: run(k, ns, a, b)) for n, (k, ns) in variants.items()}
    out.update({"family": "no_stops", "passes": False, "verdict": "research only: the live bots keep their stops"})
    return out


def fixed_r_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-19 ("test the .5 r to 1.5 tp", the OANDA setting, on the stock bots): the live exits against a fixed stop at `cut` x the strategy's
    risk with the WHOLE position out at `tp` x that risk (engine ladder "fixed_r"), for the 11 experts and for trend_stairs, both periods. Each trade is
    sized on the stop it actually uses, so risk per trade is equal across variants. The 11 experts run through the live filters (direction first,
    Weinstein stage, trend confirmation)."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    pairs = {"live exits (ladder)": None, "OWNER: stop 0.5R, out at 1.5R": (0.5, 1.5), "stop 0.25R, out at 0.75R": (0.25, 0.75),
             "stop 1.0R, out at 2.0R": (1.0, 2.0), "stop 1.0R, out at 3.0R": (1.0, 3.0), "stop 0.5R, out at 1.0R": (0.5, 1.0),
             "stop 0.5R, out at 1.25R": (0.5, 1.25)}          # (owner 2026-10-19: "test over 3 months .5 r and 1.25 r")

    def run(kind, pair, a, b):
        ts = kind == "trend_stairs"
        br = Brain(experts=[] if ts else list(ALL_EXPERTS), short_experts=[] if ts else list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        lad = {**_OWNER_LADDER, "by_expert": ({"trend_stairs": EXPERT_LADDERS["trend_stairs"]} if ts else EXPERT_LADDERS)} if pair is None else {"fixed_r": pair}
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True, "ladder": lad}
        inner = TrendStairsSignals(br, confirm=0.6) if ts else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {"train": {}, "validate": {}}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        for kind, label in (("experts", "11 experts"), ("trend_stairs", "trend_stairs")):
            for name, pair in pairs.items():
                out["periods"][per][f"{label}: {name}"] = cp.get(f"fr-{per}-{kind}-{pair}", lambda kind=kind, pair=pair, a=a, b=b: run(kind, pair, a, b))
    out.update({"family": "fixed_r", "passes": False, "verdict": "research: see the table"})
    return out


def oversold_gate_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-19 ("only buy when the majority of indicators say oversold"): the live stock set (the 11 experts + trend_stairs, live filters and
    exits) with no gate vs OversoldGate needing 2, 3 or 4 of the five oversold readings on every long signal. Shorts are untouched."""
    from .brain import DirectionFirst, OversoldGate, StageFilter, TrendConfirm, TrendStairsSignals
    from .engine import EXPERT_LADDERS, EXPERT_ZONES, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(need, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": {**ZONE_PROFILES, "trend_stairs": EXPERT_ZONES["trend_stairs"]}, "levels": True, "trend_exit": True,
                    "ladder": {**_OWNER_LADDER, "by_expert": {**EXPERT_LADDERS, "trend_stairs": EXPERT_LADDERS["trend_stairs"]}}}
        inner = TrendConfirm(StageFilter(DirectionFirst(TrendStairsSignals(br, confirm=0.6))))
        r = run_intraday(OversoldGate(inner, need) if need else inner, days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    names = {0: "no gate (before)", 2: "buy only when 2 of 5 say oversold", 3: "OWNER: majority, 3 of 5 say oversold", 4: "4 of 5 say oversold"}
    out = {"periods": {"train": {}, "validate": {}}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        for need, label in names.items():
            out["periods"][per][label] = cp.get(f"og-{per}-{need}", lambda need=need, a=a, b=b: run(need, a, b))
    out.update({"family": "oversold_gate", "passes": False, "verdict": "research: see the table"})
    return out


def velez_hybrid_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-21 ("hybrid all Alpaca experts with those rules"): Velez's context rules (brain.VelezHybrid) on top of every expert, with the
    live filters and exits. Two expert sets: all 11 both ways, and the lists live since 2026-10-20 (alpacabot/brain_config.json). One-month test."""
    import json as _json
    from pathlib import Path as _P
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezHybrid
    from .engine import EXPERT_LADDERS, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    live = _json.loads((_P(__file__).with_name("brain_config.json")).read_text())["brain"]
    sets = {"all 11": (list(ALL_EXPERTS), list(ALL_EXPERTS)), "live lists": (live["experts"], live["short_experts"])}

    def run(set_name, kw, a, b):
        ex, sx = sets[set_name]
        br = Brain(experts=list(ex), short_experts=list(sx), sector_filter=True, short_gate="red")
        br.zones = {"profiles": dict(ZONE_PROFILES), "levels": True, "trend_exit": True, "ladder": {**_OWNER_LADDER, "by_expert": dict(EXPERT_LADDERS)}}
        inner = VelezHybrid(br, **kw) if kw is not None else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    hy = {"a. as is (no Velez rules)": None, "b. + Velez trend (20 SMA rising over the 200)": {"env": True},
          "c. + trend + look left": {"env": True, "look_left": True}, "d. + trend + look left + one-bar stop": {"env": True, "look_left": True, "one_bar": True},
          "e. + trend + look left + no wide-state chase": {"env": True, "look_left": True, "no_chase": True}}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {f"{sn}: {name}": cp.get(f"vh-{per}-{sn[:3]}-{name[:1]}", lambda sn=sn, kw=kw, a=a, b=b: run(sn, kw, a, b))
                               for sn in sets for name, kw in hy.items()}
    out.update({"family": "velez_hybrid", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def winrate_tune_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-21 ("fine tune until all are 62% win rate"): the live expert lists + Velez trend + look-left (the least-losing hybrid) with
    exits that trade reward for hit rate: a fixed stop at `cut` x the expert's risk and everything out at `tp` x that risk. Win rate alone can be
    bought with a near target, so profit factor and daily return are reported beside it. One-month test."""
    import json as _json
    from pathlib import Path as _P
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezHybrid
    from .engine import EXPERT_LADDERS, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    live = _json.loads((_P(__file__).with_name("brain_config.json")).read_text())["brain"]
    pairs = {"live exits (ladder)": None, "stop 1.0R / target 0.5R": (1.0, 0.5), "stop 1.0R / target 0.75R": (1.0, 0.75),
             "stop 1.5R / target 0.75R": (1.5, 0.75), "stop 1.0R / target 1.0R": (1.0, 1.0), "stop 2.0R / target 1.0R": (2.0, 1.0),
             "stop 1.5R / target 0.5R": (1.5, 0.5)}

    def run(pair, a, b):
        br = Brain(experts=list(live["experts"]), short_experts=list(live["short_experts"]), sector_filter=True, short_gate="red")
        lad = {**_OWNER_LADDER, "by_expert": dict(EXPERT_LADDERS)} if pair is None else {"fixed_r": pair}
        br.zones = {"profiles": dict(ZONE_PROFILES), "levels": True, "trend_exit": True, "ladder": lad}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(VelezHybrid(br, env=True, look_left=True)))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"wr-{per}-{pair}", lambda pair=pair, a=a, b=b: run(pair, a, b)) for name, pair in pairs.items()}
    out.update({"family": "winrate_tune", "passes": False, "verdict": "see the table (win rate AND profit factor)"})
    return out


def winrate_live_check(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The win-rate tune as the LIVE bot will run it (signal-level stop 1.5R / target 0.5R through the live zones, one take-profit, no cuts),
    beside the research fixed_r version, so the live change is known to behave like the tested one. One month."""
    import json as _json
    from pathlib import Path as _P
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezHybrid
    from .engine import EXPERT_LADDERS, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    live = _json.loads((_P(__file__).with_name("brain_config.json")).read_text())["brain"]
    one = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}

    def run(mode, a, b):
        br = Brain(experts=list(live["experts"]), short_experts=list(live["short_experts"]), sector_filter=True, short_gate="red")
        if mode == "research":
            br.zones = {"profiles": dict(ZONE_PROFILES), "levels": True, "trend_exit": True, "ladder": {"fixed_r": (1.5, 0.5)}}
            inner = VelezHybrid(br, env=True, look_left=True)
        else:
            names = set(live["experts"]) | set(live["short_experts"])
            br.zones = {"profiles": dict(ZONE_PROFILES), "levels": True, "trend_exit": True,
                        "ladder": {**_OWNER_LADDER, "by_expert": {**EXPERT_LADDERS, **{n: one for n in names}}}}
            inner = VelezHybrid(br, env=True, look_left=True, stop_r=1.5, target_r=0.5)
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"wl-{per}-{name[:3]}", lambda name=name, a=a, b=b: run(name, a, b)) for name in ("research", "live")}
    out.update({"family": "winrate_live", "passes": False, "verdict": "see the table"})
    return out


def velez_all_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.005, max_positions=6), ckpt=None) -> dict:
    """Owner 2026-10-21 ("hybrid these rules into our experts, build new Velez intraday experts, backtest all 3 months, incorporate all"): every
    Velez setup alone on each side (longs on the chart, shorts on the mirrored chart), all of them together with the default and the win-rate exits,
    with a volume filter, and the experts with the Velez hybrid rules. Live filters (direction first, stage, multi-timeframe). Three months."""
    import json as _json
    from pathlib import Path as _P
    from .brain import DirectionFirst, StageFilter, TrendConfirm, VelezHybrid, VelezSignals, VELEZ_ALL
    from .engine import EXPERT_LADDERS, ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)
    prof = {"chase_atr": 0.1, "lookback": 20, "min_r": 0.5, "default_r": 1.5, "exhaust_r": 99.0, "target": "extreme"}
    one = {"cut_fracs": [], "cut_depth_r": [], "tp_fracs": [1.0], "tp_depth_atr": [0.0], "lock": []}
    live = _json.loads((_P(__file__).with_name("brain_config.json")).read_text())["brain"]

    def run(kind, kw, a, b):
        if kind == "velez":
            br = Brain(experts=[], short_experts=[], sector_filter=True, short_gate="red")
            br.zones = {"profiles": {**ZONE_PROFILES, **{t: prof for t in VELEZ_ALL}}, "levels": True, "trend_exit": True,
                        "ladder": {**_OWNER_LADDER, "by_expert": {t: one for t in VELEZ_ALL}}}
            inner = VelezSignals(br, **kw)
        else:
            ex, sx = (live["experts"], live["short_experts"]) if kind == "live" else (list(ALL_EXPERTS), list(ALL_EXPERTS))
            br = Brain(experts=list(ex), short_experts=list(sx), sector_filter=True, short_gate="red")
            names = set(ex) | set(sx)
            tuned = kw and kw.get("stop_r")
            br.zones = {"profiles": dict(ZONE_PROFILES), "levels": True, "trend_exit": True,
                        "ladder": {**_OWNER_LADDER, "by_expert": {**EXPERT_LADDERS, **({n: one for n in names} if tuned else {})}}}
            inner = VelezHybrid(br, **kw) if kw is not None else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    tune = {"stop_mult": 1.5, "tp_r": 0.5 / 1.5, "sig_exit": False}         # stop 1.5R, everything out at 0.5R (the installed win-rate tune)
    v = {}
    for tag in VELEZ_ALL:
        v[f"L {tag}"] = ("velez", {"setups": (tag,), "sides": (1,)})
        v[f"S {tag}"] = ("velez", {"setups": (tag,), "sides": (-1,)})
    v["ALL velez, both sides"] = ("velez", {"setups": VELEZ_ALL, "sides": (1, -1)})
    v["ALL velez, both sides, win-rate exit"] = ("velez", {"setups": VELEZ_ALL, "sides": (1, -1), **tune})
    v["ALL velez, both sides, volume 1.2x + look left"] = ("velez", {"setups": VELEZ_ALL, "sides": (1, -1), "vol_mult": 1.2, "look_left": True})
    v["EXPERTS live lists, as is"] = ("live", None)
    v["EXPERTS live lists + Velez trend + look left"] = ("live", {"env": True, "look_left": True})
    v["EXPERTS live lists + Velez + win-rate exit (LIVE NOW)"] = ("live", {"env": True, "look_left": True, "stop_r": 1.5, "target_r": 0.5})
    v["EXPERTS all 11 + Velez + win-rate exit"] = ("all", {"env": True, "look_left": True, "stop_r": 1.5, "target_r": 0.5})
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"va-{per}-{name}", lambda kind=kind, kw=kw, a=a, b=b: run(kind, kw, a, b)) for name, (kind, kw) in v.items()}
    out.update({"family": "velez_all", "passes": False, "verdict": "see the table (live-paper rule decides)"})
    return out


def box_diag(days, a, b) -> dict:
    """Why does the range box strategy trade or not? Counts, over sessions a..b: sessions with a valid daily box, raw box / Fibonacci-box
    signals, and how many survive direction first, the Weinstein stage and the multi-timeframe check (the live filter order)."""
    from .brain import BoxSignals, DirectionFirst, FibBoxSignals, StageFilter, TrendConfirm
    import datetime as _d
    a, b = _d.date.fromisoformat(str(a)), _d.date.fromisoformat(str(b))
    out = {"sessions": 0, "box_attr": 0, "valid_box": 0, "atr_bad": 0}
    for gen in ("box", "fib"):
        for k in ("raw", "direction", "stage", "mtf"):
            out[f"{gen}_{k}"] = 0
    class Empty:
        def signals(self, d):
            return []
    for sym, frames in days.items():
        for f in frames:
            dd = f.index[0].date()
            if not (a <= dd <= b):
                continue
            out["sessions"] += 1
            bx = f.attrs.get("box")
            if bx is None:
                continue
            out["box_attr"] += 1
            if not bx[0]:
                continue
            out["valid_box"] += 1
            at = f.attrs.get("box_atr", 0.0)
            if not (at == at) or at <= 0:
                out["atr_bad"] += 1
            for gen, cls in (("box", BoxSignals), ("fib", FibBoxSignals)):
                raw = cls(Empty())
                out[f"{gen}_raw"] += len(raw.signals(f))
                out[f"{gen}_direction"] += len(DirectionFirst(raw).signals(f))
                out[f"{gen}_stage"] += len(StageFilter(DirectionFirst(raw)).signals(f))
                out[f"{gen}_mtf"] += len(TrendConfirm(StageFilter(DirectionFirst(raw))).signals(f))
    return out


def box_confirm_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's box with candlestick + volume + volatility confirmation on stocks (BoxConfirmSignals) on the live configuration. Pre-declared
    variants: live; live + box_confirm in each mode (continuation / failure / both, vol 1.5x, ATR 1.1x); box_confirm alone (both). The best
    "+" variant by TRAIN daily return must also beat live in validation (_zone_verdict) to count. Needs days attached with with_box=True."""
    from .brain import BoxConfirmSignals, DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(mode, alone, a, b):
        br = Brain(experts=[] if alone else list(ALL_EXPERTS), short_experts=[] if alone else list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        inner = BoxConfirmSignals(br, mode) if mode else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live": (None, False), "+ confirm continuation": ("continuation", False), "+ confirm failure": ("failure", False),
                "+ confirm both": ("both", False), "confirm both alone": ("both", True)}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"bc-{per}-{k}", lambda md=md, al=al, a=a, b=b: run(md, al, a, b)) for k, (name, (md, al)) in enumerate(variants.items())}
    roi = lambda m: m.get("daily_ret_pct_mean") if m.get("daily_ret_pct_mean") is not None else -9   # noqa: E731
    best = max(("+ confirm continuation", "+ confirm failure", "+ confirm both"), key=lambda v: roi(out["periods"]["train"][v]))
    ok = _zone_verdict([{"without": out["periods"][p]["live"], "with": out["periods"][p][best]} for p in out["periods"]])
    out.update({"family": "box_confirm", "chosen": best, "passes": bool(ok),
                "verdict": (f"ADD: {best} beat the live configuration in train and validation" if ok else
                            f"best on train was {best}; it did not beat the live configuration in both periods")})
    return out


def trend_continuation_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Owner's trend-continuation check on the trend experts (brain.TrendContinuation) on the live configuration: live vs vote 60% vs 80%.
    A vote must beat live in the train AND validation years (_zone_verdict) to count as an improvement."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm, TrendContinuation
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(vote, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        inner = TrendContinuation(br, vote) if vote else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (no continuation check)": None, "trend experts need 60% (3 of 5)": 0.6, "trend experts need 80% (4 of 5)": 0.8}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"tc-{per}-{k}", lambda vt=vt, a=a, b=b: run(vt, a, b)) for k, (name, vt) in enumerate(variants.items())}
    base = "live (no continuation check)"
    verdicts = {v: _zone_verdict([{"without": out["periods"][p][base], "with": out["periods"][p][v]} for p in out["periods"]]) for v in variants if v != base}
    winners = [v for v, ok in verdicts.items() if ok]
    out.update({"family": "trend_continuation", "passes": bool(winners), "variant_passes": verdicts,
                "verdict": (f"IMPROVES: {'; '.join(winners)} beat live in train and validation" if winners else
                            "the continuation check did not beat live in both periods")})
    return out


def expert_breakdown(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """Why the stock bot loses (owner 2026-10-16 "analyze why and fix"): each expert ALONE (long and short) through the live configuration
    (direction first + Weinstein stage + multi-timeframe + the drawn exits + zone map), in both periods. Pre-declared rule: DROP an expert
    whose profit factor is below 0.9 in BOTH periods (with 5+ trades in each); keep the rest. Then the kept set must beat the live set in
    both periods (_zone_verdict) for the change to count."""
    from .brain import DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(experts, a, b):
        br = Brain(experts=list(experts), short_experts=list(experts), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(br))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    out = {"periods": {}}
    per_exp = {}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {"live (all experts)": cp.get(f"eb-{per}-all", lambda a=a, b=b: run(ALL_EXPERTS, a, b))}
        for name in ALL_EXPERTS:
            m = cp.get(f"eb-{per}-{name}", lambda name=name, a=a, b=b: run([name], a, b))
            out["periods"][per][f"{name} alone"] = m
            per_exp.setdefault(name, {})[per] = m
    drop = [n for n, d in per_exp.items() if all((d[p].get("trades") or 0) >= 5 and (d[p].get("profit_factor") or 0) < 0.9 for p in d)]
    keep = [n for n in ALL_EXPERTS if n not in drop]
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per]["kept experts"] = cp.get(f"eb-{per}-keep-{'-'.join(sorted(drop))}", lambda a=a, b=b: run(keep, a, b)) if keep else {}
    ok = bool(drop) and bool(keep) and _zone_verdict([{"without": out["periods"][p]["live (all experts)"], "with": out["periods"][p]["kept experts"]} for p in out["periods"]])
    out.update({"family": "expert_breakdown", "drop": drop, "keep": keep, "passes": bool(ok),
                "verdict": (f"FIX: drop {', '.join(drop)} (PF < 0.9 in both periods); the kept set beat live in both periods" if ok else
                            (f"drop candidates {', '.join(drop)}, but the kept set did not beat live in both periods" if drop else
                             "no expert lost clearly (PF < 0.9) in both periods"))})
    return out


def reversal_confirm_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The owner's mean-reversion fix (brain.ReversalConfirm, 4 checks incl. Velez) on the live configuration: live vs need 2 vs 3 of 4."""
    from .brain import DirectionFirst, ReversalConfirm, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(need, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": ZONE_PROFILES, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        inner = ReversalConfirm(br, need) if need else br
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(inner))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}
    variants = {"live (no reversal check)": None, "mean reversion needs 2 of 4 (incl. Velez tail / 180 / rubber band)": 2,
                "mean reversion needs 3 of 4": 3}
    out = {"periods": {}}
    for per, (a, b) in (("train", train), ("validate", validate)):
        out["periods"][per] = {name: cp.get(f"rc-{per}-{k}", lambda nd=nd, a=a, b=b: run(nd, a, b)) for k, (name, nd) in enumerate(variants.items())}
    base = "live (no reversal check)"
    verdicts = {v: _zone_verdict([{"without": out["periods"][p][base], "with": out["periods"][p][v]} for p in out["periods"]]) for v in variants if v != base}
    winners = [v for v, ok in verdicts.items() if ok]
    out.update({"family": "reversal_confirm", "passes": bool(winners), "variant_passes": verdicts,
                "verdict": (f"IMPROVES: {'; '.join(winners)} beat live in both halves" if winners else "the reversal check did not beat live in both halves")})
    return out


ZONE_GRID = {"lookback": [10, 20, 40], "min_r": [0.75, 1.0, 1.5], "default_r": [1.5, 2.0, 3.0], "exhaust_r": [1.0, 1.5, 2.0],
             "chase_atr": [0.25, 0.5, 1.0]}                                  # the frozen grid (owner's zones, 2026-10-14)


def zone_param_variants() -> list:
    """(label, profile, parameter, value) for the one-at-a-time sweep: every grid value of every parameter, separately for the trend and the
    range profile, the other settings left at the live defaults. The live default of each parameter is the baseline and is not repeated."""
    from .engine import ZONE_PROFILES
    out = []
    for prof in ("trend", "range"):
        for par, vals in ZONE_GRID.items():
            for v in vals:
                if v != ZONE_PROFILES[prof][par]:
                    out.append((f"{prof}.{par}={v}", prof, par, v))
    return out


def zones_params_study(days, train, validate, costs: Costs = Costs(), pf: Portfolio = Portfolio(risk_pct=0.01, max_positions=6), ckpt=None) -> dict:
    """The take-profit / buy zones' own settings, optimized on the live configuration (direction first + Weinstein stage + multi-timeframe
    confirmation + zone map + the owner's drawn exit plan). Sweep one setting at a time per profile (ZONE_GRID); for each setting keep the
    value with the best TRAIN return (mean daily %), then test the combined winners. ADOPT only if the combination beats the live zones in
    BOTH the train and the validation period (_zone_verdict); otherwise the live zones stay."""
    import copy

    from .brain import DirectionFirst, StageFilter, TrendConfirm
    from .engine import ZONE_PROFILES
    keys = ("trades", "win_rate", "avg_r", "expectancy_pct_notional", "profit_factor", "sharpe", "max_drawdown", "daily_ret_pct_mean", "cagr")
    cp = Checkpoint(ckpt)

    def run(profiles, a, b):
        br = Brain(experts=list(ALL_EXPERTS), short_experts=list(ALL_EXPERTS), sector_filter=True, short_gate="red")
        br.zones = {"profiles": profiles, "levels": True, "ladder": dict(_OWNER_LADDER), "trend_exit": True}
        r = run_intraday(TrendConfirm(StageFilter(DirectionFirst(br))), days, costs, pf, a, b)
        return {k: r["metrics"].get(k) for k in keys}

    def profiles_with(changes: dict):
        pr = copy.deepcopy(ZONE_PROFILES)
        for (prof, par), v in changes.items():
            pr[prof][par] = v
        return pr
    roi = lambda m: m.get("daily_ret_pct_mean") if m.get("daily_ret_pct_mean") is not None else -9   # noqa: E731
    periods = {"train": train, "validate": validate}
    res = {per: {"live zones": cp.get(f"zp-{per}-base", lambda a=a, b=b: run(profiles_with({}), a, b))} for per, (a, b) in periods.items()}
    sweep = {}
    for label, prof, par, v in zone_param_variants():
        a, b = train
        sweep[label] = (prof, par, v, cp.get(f"zp-train-{label}", lambda a=a, b=b, prof=prof, par=par, v=v: run(profiles_with({(prof, par): v}), a, b)))
    chosen = {}
    for prof in ("trend", "range"):
        for par in ZONE_GRID:
            cands = [(roi(res["train"]["live zones"]), ZONE_PROFILES[prof][par])] + [(roi(m), v) for lab, (pp, pa, v, m) in sweep.items() if pp == prof and pa == par]
            chosen[(prof, par)] = max(cands, key=lambda x: x[0])[1]
    changed = {k: v for k, v in chosen.items() if v != ZONE_PROFILES[k[0]][k[1]]}
    for per, (a, b) in periods.items():
        res[per]["combined winners"] = cp.get(f"zp-{per}-combo", lambda a=a, b=b: run(profiles_with(changed), a, b))
    ok = bool(changed) and _zone_verdict([{"without": res[p]["live zones"], "with": res[p]["combined winners"]} for p in periods])
    return {"family": "zones_params", "periods": res, "sweep_train": {k: v[3] for k, v in sweep.items()},
            "chosen_changes": {f"{k[0]}.{k[1]}": v for k, v in changed.items()}, "passes": ok,
            "verdict": ("ADOPT the zone settings " + ", ".join(f"{k[0]}.{k[1]}={v}" for k, v in changed.items()) + ": better in train and validation" if ok else
                        ("no setting beat the live zones on the train years" if not changed else
                         "the best settings (" + ", ".join(f"{k[0]}.{k[1]}={v}" for k, v in changed.items()) + ") did not beat the live zones in both periods: the live zones stay"))}


# ------------------------------------------------------------------ credit-stress entry gate (Gemini round 7, 2026-10-27)
# PRE-DECLARED before any run: credit = close(HYG) / close(IEF); gate ON while its 20-day SMA >= its 50-day SMA. CONTROL = Sector Top-Down with the
# live SPY-above-its-200-day entry gate; TEST = control AND the credit gate (exits and stops unchanged). Live portfolio rules (0.35% risk, 3 slots,
# a third of equity per position), 10 bps a side (2x), 2016-03-01..today, today's list and the hindsight-free 2016 list. KEEP only if, on BOTH lists:
# Sharpe +0.10 or more over the control, the 2018 and 2022 max drawdowns each at least 25% smaller, net profit on trades closed in 2017/2019/2021/2023
# no more than 15% lower (Gemini's kill rules) AND the test Sharpe beats the control with the credit gate block-shuffled (20-day blocks) in >= 90%
# of 20 seeds (is it the credit signal, or just being out of the market some of the time?).
CREDIT_SPEC = {"fast": 20, "slow": 50, "start": "2016-03-01", "slip_bps": 10.0, "risk_pct": 0.0035, "max_positions": 3, "max_pos_frac": 0.333,
               "placebo_runs": 20, "block_days": 20}


def credit_gate(hyg: pd.DataFrame, ief: pd.DataFrame, fast: int = 20, slow: int = 50) -> pd.Series:
    r = (hyg["close"] / ief["close"]).dropna()
    return (r.rolling(fast).mean() >= r.rolling(slow).mean()).where(r.rolling(slow).mean().notna(), True).astype(bool)


def _year_dd(curve: pd.Series, y: int) -> float:
    c = curve[curve.index.year == y] if hasattr(curve.index, "year") else curve[[pd.Timestamp(d).year == y for d in curve.index]]
    return float((c / c.cummax() - 1).min()) if len(c) else 0.0


def credit_gate_lines(universes: dict, spy: pd.DataFrame, hyg: pd.DataFrame, ief: pd.DataFrame, end: date) -> list[str]:
    from .strategies import GatedSectorTopDown, SectorTopDown
    sp = CREDIT_SPEC
    start = date.fromisoformat(sp["start"])
    costs, pf = Costs(slip_bps=sp["slip_bps"]), Portfolio(risk_pct=sp["risk_pct"], max_positions=sp["max_positions"], max_pos_frac=sp["max_pos_frac"])
    c = spy["close"]
    spy_gate = (c > c.rolling(200).mean()).where(c.rolling(200).mean().notna(), True)
    cg = credit_gate(hyg, ief, sp["fast"], sp["slow"]).reindex(c.index, method="ffill").fillna(True).astype(bool)
    base = SectorTopDown(c)
    out = [f"### Credit gate (HYG/IEF 20 > 50-day) on Sector Top-Down with the SPY 200-day gate, {start}..{end}, 10 bps a side, live sizing; "
           f"gate ON {cg[cg.index.date >= start].mean():.0%} of days"]
    verdicts = []
    for label, data in universes.items():
        res = {}
        for nm, g in (("control (SPY gate)", spy_gate), ("test (SPY + credit)", spy_gate & cg)):
            r = run_swing(GatedSectorTopDown(base, g), data, costs, pf, start, end)
            res[nm] = r
        ctl, tst = res["control (SPY gate)"], res["test (SPY + credit)"]
        plc = []
        for s in range(sp["placebo_runs"]):
            sh = _block_shuffle(cg, s, sp["block_days"])
            plc.append(run_swing(GatedSectorTopDown(base, spy_gate & sh), data, costs, pf, start, end)["metrics"].get("sharpe") or 0.0)
        bull = lambda r: float(r["fills"][pd.to_datetime(r["fills"]["exit"]).dt.year.isin([2017, 2019, 2021, 2023])]["pnl"].sum()) if len(r["fills"]) else 0.0  # noqa: E731
        m0, m1 = ctl["metrics"], tst["metrics"]
        dd = {y: (_year_dd(ctl["curve"], y), _year_dd(tst["curve"], y)) for y in (2018, 2022)}
        ok_sh = (m1.get("sharpe") or 0) - (m0.get("sharpe") or 0) >= 0.10
        ok_dd = all(t >= 0.75 * c_ for c_, t in dd.values())
        b0, b1 = bull(ctl), bull(tst)
        ok_bull = b1 >= 0.85 * b0
        beat = float(np.mean([(m1.get("sharpe") or 0) > p for p in plc]))
        ok = ok_sh and ok_dd and ok_bull and beat >= 0.9
        verdicts.append(ok)
        for nm, r in res.items():
            m = r["metrics"]
            out.append(f"- {label} | {nm}: {m.get('trades', 0)} trades, PF {m.get('profit_factor', 0):.2f}, CAGR {m.get('cagr', 0):+.1%}, Sharpe {m.get('sharpe', 0):.2f}, "
                       f"maxDD {m.get('max_drawdown', 0):.1%}")
        out.append(f"  {label}: Sharpe {m1.get('sharpe', 0) - m0.get('sharpe', 0):+.2f} ({'ok' if ok_sh else 'FAIL'}) | 2018 DD {dd[2018][0]:.1%} -> {dd[2018][1]:.1%}, "
                   f"2022 DD {dd[2022][0]:.1%} -> {dd[2022][1]:.1%} ({'ok' if ok_dd else 'FAIL'}) | bull-year profit ${b0:,.0f} -> ${b1:,.0f} ({'ok' if ok_bull else 'FAIL'}) | "
                   f"beats shuffled credit gate {beat:.0%} ({'ok' if beat >= 0.9 else 'FAIL'}) -> {'KEEP' if ok else 'REJECT'}")
    out.append(f"- verdict: {'KEEP on both lists (paper candidate)' if verdicts and all(verdicts) else 'REJECT (must pass every rule on both lists)'}")
    return out


# ------------------------------------------------------------------ Top-Down position sizing (owner 2026-10-27: "increase risk per trade on the profitable set ups")
# PRE-DECLARED: Sector Top-Down + the live SPY 200-day entry gate, 10 bps a side, risk per trade {0.35 (live), 0.5, 0.75, 1.0, 1.5}% x slots {3, 5},
# a third of equity max per position, cash-limited. SELECT on 2016-03-01..2022-12-31: the highest CAGR whose max drawdown is no deeper than -15% and whose
# Sharpe is within 0.05 of the live setting, on BOTH lists. CONFIRM on 2023-01-01..today (unseen by the choice): the same two limits on both lists.
SIZING_SPEC = {"risks": (0.0035, 0.005, 0.0075, 0.01, 0.015), "slots": (3, 5), "live": (0.0035, 3), "max_dd": -0.15, "sharpe_slack": 0.05,
               "select": ("2016-03-01", "2022-12-31"), "confirm_from": "2023-01-01", "slip_bps": 10.0}


def topdown_sizing_lines(universes: dict, spy: pd.DataFrame, end: date) -> list[str]:
    from .strategies import GatedSectorTopDown, SectorTopDown
    sp = SIZING_SPEC
    c = spy["close"]
    gate = (c > c.rolling(200).mean()).where(c.rolling(200).mean().notna(), True)
    strat = GatedSectorTopDown(SectorTopDown(c), gate)
    costs = Costs(slip_bps=sp["slip_bps"])
    periods = {"select": tuple(date.fromisoformat(x) for x in sp["select"]), "confirm": (date.fromisoformat(sp["confirm_from"]), end)}
    res = {}
    for per, (a, b) in periods.items():
        for label, data in universes.items():
            for r_ in sp["risks"]:
                for n in sp["slots"]:
                    m = run_swing(strat, data, costs, Portfolio(risk_pct=r_, max_positions=n, max_pos_frac=0.333), a, b)["metrics"]
                    res[(per, label, r_, n)] = m
    out = [f"### Sector Top-Down sizing (SPY 200-day gate, 10 bps a side): select {periods['select'][0]}..{periods['select'][1]}, confirm {periods['confirm'][0]}..{end}"]
    for (per, label, r_, n), m in res.items():
        out.append(f"- {per} | {label} | risk {r_:.2%} x {n} slots: {m.get('trades', 0)} trades, CAGR {m.get('cagr', 0):+.1%}, Sharpe {m.get('sharpe', 0):.2f}, "
                   f"maxDD {m.get('max_drawdown', 0):.1%}{'  (LIVE)' if (r_, n) == sp['live'] else ''}")

    def ok(per, r_, n):
        return all((res[(per, lb, r_, n)].get("max_drawdown") or -1) >= sp["max_dd"]
                   and (res[(per, lb, r_, n)].get("sharpe") or -9) >= (res[(per, lb, *sp["live"])].get("sharpe") or 0) - sp["sharpe_slack"] for lb in universes)
    cands = [(r_, n) for r_ in sp["risks"] for n in sp["slots"] if ok("select", r_, n)]
    if not cands:
        return out + ["- verdict: no setting passes the select limits: keep the live sizing"]
    best = max(cands, key=lambda k: min(res[("select", lb, *k)].get("cagr") or -1 for lb in universes))
    conf = ok("confirm", *best)
    out.append(f"- chosen on the select years: risk {best[0]:.2%} x {best[1]} slots (worst-list CAGR "
               f"{min(res[('select', lb, *best)].get('cagr') or 0 for lb in universes):+.1%}) -> confirm years: {'PASS' if conf else 'FAIL'}")
    out.append(f"- verdict: {'ADOPT risk %.2f%% x %d slots' % (100 * best[0], best[1]) if conf and best != sp['live'] else 'keep the live sizing'}")
    return out


# ------------------------------------------------------------------ Top-Down candidate ranking (Gemini round 8, 2026-10-27)
# PRE-DECLARED before any run. Only the ORDER in which competing signals take the 5 slots changes; entries, stops, exits, the SPY 200-day gate, live
# sizing (0.5% risk x 5 slots), costs (10 bps a side) are the control's. Control: 20-day raw RS vs SPY (the live rank). Test: 90-day volatility-adjusted
# sector RS = (stock 90d return - its sector ETF's 90d return) / stdev of the stock's daily returns over 90 days. Also reported: a RANDOM rank (20 seeds)
# - does the order matter at all? 2016-03-01..today on today's list and the hindsight-free 2016 list. KEEP only if the hindsight-free Sharpe rises
# by >= 0.10 over the control AND today's list does not fall, AND the test beats the random-rank Sharpe in >= 90% of seeds.
RANK_SPEC = {"start": "2016-03-01", "window": 90, "slip_bps": 10.0, "risk_pct": 0.005, "max_positions": 5, "max_pos_frac": 0.333, "seeds": 20, "min_gain": 0.10}


def _swing_run_ranked(per_symbol_strat: dict, daily: dict, costs, pf, start, end) -> dict:
    trades = [t for sym, d in daily.items() for t in simulate_swing(sym, d, per_symbol_strat[sym], costs, all_signals=True)]
    trades = [t for t in trades if start <= t.entry_time.date() <= end]
    closes = {sym: d["close"] for sym, d in daily.items()}
    fills, curve = size_swing(sorted(trades, key=lambda t: (t.entry_time, -t.rank)), closes, costs, pf, start, end)
    return {"fills": fills, "curve": curve, "metrics": metrics(fills, curve, pf.equity)}


class _RankedTopDown:
    """GatedSectorTopDown whose rank() reads a precomputed per-bar series (same entries, stops and exits)."""

    def __init__(self, gated, rank_series: pd.Series):
        self.g, self.rk = gated, rank_series
        self.max_days, self.name = gated.max_days, gated.name + "+rank"

    def frame(self, d):
        f = self.g.frame(d)
        f["rank_alt"] = self.rk.reindex(d.index).to_numpy(float)
        return f

    def stop_dist(self, f, i):
        return self.g.stop_dist(f, i)

    def stop_price(self, f, i):
        return self.g.stop_price(f, i)

    def rank(self, f, i):
        v = float(f["rank_alt"].iloc[i])
        return v if np.isfinite(v) else -1e9


def topdown_rank_lines(universes: dict, sector_of_by_uni: dict, etfs: dict, spy: pd.DataFrame, end: date) -> list[str]:
    from .strategies import GatedSectorTopDown, SectorTopDown
    sp = RANK_SPEC
    start = date.fromisoformat(sp["start"])
    costs, pf = Costs(slip_bps=sp["slip_bps"]), Portfolio(risk_pct=sp["risk_pct"], max_positions=sp["max_positions"], max_pos_frac=sp["max_pos_frac"])
    c = spy["close"]
    gate = (c > c.rolling(200).mean()).where(c.rolling(200).mean().notna(), True)
    gated = GatedSectorTopDown(SectorTopDown(c), gate)
    w = sp["window"]
    out = [f"### Top-Down ranking: 20-day raw RS (control) vs {w}-day volatility-adjusted sector RS, SPY gate, 0.5% x 5 slots, 10 bps a side, {start}..{end}"]
    verdict = {}
    for label, data in universes.items():
        smap = sector_of_by_uni[label]
        syms = {s: d for s, d in data.items() if smap.get(s) in etfs}
        ctl = {s: gated for s in syms}
        tst = {}
        rng_series = {}
        for s, d in syms.items():
            px, ex = d["close"], etfs[smap[s]]["close"].reindex(d.index, method="ffill")
            vol = px.pct_change().rolling(w).std()
            tst[s] = _RankedTopDown(gated, ((px.pct_change(w) - ex.pct_change(w)) / vol).replace([np.inf, -np.inf], np.nan))
            rng_series[s] = px
        m_c = _swing_run_ranked({s: _RankedTopDown(gated, gated.base.frame(d)["rs"]) for s, d in syms.items()}, syms, costs, pf, start, end)["metrics"]
        m_t = _swing_run_ranked(tst, syms, costs, pf, start, end)["metrics"]
        plc = []
        for k in range(sp["seeds"]):
            rg = np.random.default_rng(k)
            rs_ = {s: _RankedTopDown(gated, pd.Series(rg.random(len(d)), index=d.index)) for s, d in syms.items()}
            plc.append(_swing_run_ranked(rs_, syms, costs, pf, start, end)["metrics"].get("sharpe") or 0.0)
        beat = float(np.mean([(m_t.get("sharpe") or 0) > p for p in plc]))
        for nm, m in (("control (20d raw RS)", m_c), (f"test ({w}d vol-adj sector RS)", m_t)):
            out.append(f"- {label} | {nm}: {m.get('trades', 0)} trades, PF {m.get('profit_factor', 0):.2f}, CAGR {m.get('cagr', 0):+.1%}, "
                       f"Sharpe {m.get('sharpe', 0):.2f}, maxDD {m.get('max_drawdown', 0):.1%}")
        out.append(f"  {label}: random rank Sharpe mean {np.mean(plc):.2f} (min {min(plc):.2f}, max {max(plc):.2f}); test beats random in {beat:.0%}; "
                   f"Sharpe change vs control {(m_t.get('sharpe') or 0) - (m_c.get('sharpe') or 0):+.2f}")
        verdict[label] = ((m_t.get("sharpe") or 0) - (m_c.get("sharpe") or 0), beat)
    hf = verdict.get("hindsight-free 2016 list", (-9, 0))
    td = verdict.get("today's list", (-9, 0))
    keep = hf[0] >= sp["min_gain"] and td[0] >= 0 and hf[1] >= 0.9
    out.append(f"- verdict: {'KEEP (paper candidate)' if keep else 'REJECT'} (needs hindsight-free Sharpe +{sp['min_gain']:.2f}, today's list not lower, beats random rank in 90%)")
    return out


# ------------------------------------------------------------------ direction check (owner 2026-10-28: "it's got to be a buy and sell issue")
# Is any strategy's buy / sell DIRECTION inverted or broken? For every entry signal: the signed forward return (+ = price moved the way the signal said)
# after 1 / several / many bars, as a hit rate and as the mean in ATR units, with a z-score against 50%. A strategy whose signals point the WRONG way
# shows a hit rate clearly under 50% (z <= -2) - that is a sign error; one at about 50% has no direction skill (no sign error, just no edge); one clearly over
# 50% (z >= 2) predicts direction. Entries only, before any stop, target, cost or sizing, so it isolates the buy / sell decision itself.
def direction_pooled(label: str, parts: list, horizons: tuple, atr_n: int = 14) -> list[str]:
    """parts: [(bars, long_signal_array, short_signal_array)] (one per symbol); signals pooled across symbols."""
    out = {}
    for bars, long_sig, short_sig in parts:
        c = bars["close"].to_numpy(float)
        pc = bars["close"].shift(1)
        atr = pd.concat([bars["high"] - bars["low"], (bars["high"] - pc).abs(), (bars["low"] - pc).abs()], axis=1).max(axis=1).rolling(atr_n).mean().to_numpy(float)
        for side, sig in ((1, long_sig), (-1, short_sig)):
            idx = np.flatnonzero(np.asarray(sig, bool))
            for h in horizons:
                ii = idx[(idx + h < len(c))]
                ii = ii[np.isfinite(atr[ii])] if len(ii) else ii
                if len(ii):
                    out.setdefault(h, []).append(side * (c[ii + h] - c[ii]) / atr[ii])
    rows = []
    for h in horizons:
        if h not in out:
            continue
        x = np.concatenate(out[h])
        n = len(x)
        hit = float((x > 0).mean())
        z = (hit - 0.5) / math.sqrt(0.25 / n)
        rows.append(f"{h} bars: {n} signals, hit {hit:.1%}, mean {x.mean():+.3f} ATR, z {z:+.1f}")
    return [f"- {label}: " + " | ".join(rows)] if rows else [f"- {label}: no signals"]


# ------------------------------------------------------------------ cross-asset trend sleeve (owner 2026-10-28: "build the cross-asset trend sleeve")
# PRE-DECLARED before any run. Universe (Alpaca ETFs, dividend-adjusted): SPY US stocks, EFA developed ex-US, EEM emerging, TLT long Treasuries, IEF 7-10y
# Treasuries, GLD gold, DBC commodities, UUP US dollar. Each month-end close: an asset is HELD if its 12-month total return is > 0 (time-series momentum;
# Moskowitz, Ooi & Pedersen 2012), else its slot is cash (0%). Size: inverse volatility - weight = (0.12 / its 60-day annualised vol) / 8, at most 35%
# per asset, gross at most 100% (no leverage); rebalanced at the month-end close, traded at the next open; 5 bps a side on turnover (stress: 10 bps).
# Variant reported alongside (not used for the verdict): the same with "close above its 200-day average" instead of the 12-month return.
# Controls: (1) the same inverse-vol portfolio ALWAYS held (does the trend timing add anything?), (2) 60/40 SPY/IEF monthly, (3) SPY buy-and-hold.
# Windows: in-sample to 2018-12-31, OUT-OF-SAMPLE 2019-01-01..today (nothing was fitted; the split only checks stability). PASS only if: OOS Sharpe >= 0.5
# and above control (1), OOS max drawdown shallower than both control (1) and the 60/40, both windows profitable, and OOS Sharpe >= 0.4 at 10 bps.
# Added before the first real run (a smoke test on synthetic random prices passed the rules above): control (4) RANDOM timing - each asset on / off at
# random each month with the sleeve's own share of months on FOR THAT ASSET, 20 seeds; the sleeve's FULL-PERIOD Sharpe must beat at least 18 of the 20.
TREND_SLEEVE = {"assets": ("SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "UUP"), "lookback_days": 252, "vol_days": 60, "target": 0.12,
                "cap": 0.35, "cost_bps": 5.0, "split": "2019-01-01"}


def trend_sleeve_returns(closes: pd.DataFrame, opens: pd.DataFrame, signal: str = "tsmom12", always: bool = False, cost_bps: float = 5.0,
                         seed: int | None = None, on_share=0.5) -> pd.Series:
    """Daily portfolio returns. closes / opens: date x asset. Weights set at each month-end close, effective from the next open."""
    sp = TREND_SLEEVE
    rets = closes.pct_change()
    vol = rets.rolling(sp["vol_days"]).std() * np.sqrt(252)
    if signal == "tsmom12":
        on = closes / closes.shift(sp["lookback_days"]) - 1 > 0
    elif signal == "random":                                                   # placebo: each asset on / off at random each month, same average exposure
        rng = np.random.default_rng(seed)
        months = closes.index.tz_localize(None).to_period("M")
        share = np.asarray(on_share if np.ndim(on_share) else [on_share] * len(closes.columns), float)
        draw = {m: rng.random(len(closes.columns)) < share for m in months.unique()}
        on = pd.DataFrame([draw[m] for m in months], index=closes.index, columns=closes.columns)
    else:
        on = closes > closes.rolling(200).mean()
    month_end = closes.index.to_series().groupby([closes.index.year, closes.index.month]).transform("max") == closes.index.to_series()
    w_target = ((sp["target"] / vol) / len(closes.columns)).clip(upper=sp["cap"])
    if not always:
        w_target = w_target.where(on, 0.0)
    w_target = w_target.where(vol.notna() & closes.shift(sp["lookback_days"]).notna(), np.nan)
    gross = w_target.sum(axis=1)
    w_target = w_target.div(gross.clip(lower=1.0), axis=0)                    # never more than 100% invested
    w = w_target.where(month_end).ffill().fillna(0.0)                         # held from the month-end decision...
    w_eff = w.shift(1).fillna(0.0)                                            # ...executed at the next open
    # day d return of a position decided at d-1's close and bought at d's open: open->close on the first day, close->close after
    first_day = (w_eff.diff().abs().sum(axis=1) > 0)
    oc = closes / opens - 1
    port = (w_eff * rets).sum(axis=1)
    port[first_day] = (w_eff * oc).sum(axis=1)[first_day] + (w_eff.shift(1).fillna(0.0) * (opens / closes.shift(1) - 1)).sum(axis=1)[first_day]
    turnover = w_eff.diff().abs().sum(axis=1).fillna(0.0)
    return port - turnover * cost_bps / 1e4


def _curve_stats_sleeve(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 30 or r.std() == 0:
        return {"cagr": 0.0, "sharpe": 0.0, "max_dd": 0.0, "years": 0.0}
    eq = (1 + r).cumprod()
    years = len(r) / 252
    return {"cagr": float(eq.iloc[-1] ** (1 / years) - 1), "sharpe": float(r.mean() / r.std() * np.sqrt(252)),
            "max_dd": float((eq / eq.cummax() - 1).min()), "years": years}


def trend_sleeve_lines(daily: dict) -> list[str]:
    sp = TREND_SLEEVE
    assets = [a for a in sp["assets"] if a in daily]
    closes = pd.DataFrame({a: daily[a]["close"] for a in assets}).dropna()
    opens = pd.DataFrame({a: daily[a]["open"] for a in assets}).reindex(closes.index)
    split = pd.Timestamp(sp["split"], tz=closes.index.tz)
    out = [f"### Cross-asset trend sleeve: {', '.join(assets)} | data {closes.index[0].date()}..{closes.index[-1].date()} | in-sample to {split.date() - pd.Timedelta(days=1)}, "
           f"OOS from {split.date()}"]
    if len(assets) < len(sp["assets"]):
        out.append(f"- missing assets: {sorted(set(sp['assets']) - set(assets))}")
    series = {"TREND SLEEVE (12-month TSMOM)": trend_sleeve_returns(closes, opens, "tsmom12"),
              "variant: 200-day average": trend_sleeve_returns(closes, opens, "sma200"),
              "control 1: same weights, always held": trend_sleeve_returns(closes, opens, "tsmom12", always=True),
              "TREND SLEEVE at 10 bps": trend_sleeve_returns(closes, opens, "tsmom12", cost_bps=2 * sp["cost_bps"])}
    sig_on = (closes / closes.shift(sp["lookback_days"]) - 1 > 0)[closes.shift(sp["lookback_days"]).notna()]
    on_share = sig_on.mean().reindex(closes.columns).fillna(0.5).to_numpy()   # EACH asset keeps its own share of months on (timing, not asset choice)
    placebo = [_curve_stats_sleeve(trend_sleeve_returns(closes, opens, "random", seed=k, on_share=on_share).iloc[sp["lookback_days"] + sp["vol_days"]:])["sharpe"]
               for k in range(20)]
    spy, ief = closes.get("SPY"), closes.get("IEF")
    if spy is not None and ief is not None:
        r6040 = 0.6 * spy.pct_change() + 0.4 * ief.pct_change()               # daily-rebalanced proxy of a monthly 60/40 (close enough for a control)
        series["control 2: 60/40 SPY/IEF"] = r6040
        series["control 3: SPY buy-and-hold"] = spy.pct_change()
    start = closes.index[sp["lookback_days"] + sp["vol_days"]]
    stats = {}
    for name, r in series.items():
        r = r[r.index >= start]
        ins, oos = _curve_stats_sleeve(r[r.index < split]), _curve_stats_sleeve(r[r.index >= split])
        stats[name] = (ins, oos)
        out.append(f"- {name}: in-sample CAGR {ins['cagr']:+.1%} Sharpe {ins['sharpe']:.2f} maxDD {ins['max_dd']:.1%} | "
                   f"OOS CAGR {oos['cagr']:+.1%} Sharpe {oos['sharpe']:.2f} maxDD {oos['max_dd']:.1%} ({oos['years']:.1f}y)")
    t, c1 = stats["TREND SLEEVE (12-month TSMOM)"], stats["control 1: same weights, always held"]
    c2 = stats.get("control 2: 60/40 SPY/IEF", (None, {"max_dd": -1.0}))
    st = stats["TREND SLEEVE at 10 bps"]
    full = _curve_stats_sleeve(series["TREND SLEEVE (12-month TSMOM)"][series["TREND SLEEVE (12-month TSMOM)"].index >= start])["sharpe"]
    beat = sum(full > p for p in placebo)
    out.append(f"- control 4: random monthly timing, each asset on as often as the sleeve holds it ({float(np.mean(on_share)):.0%} on average), 20 seeds: full-period Sharpe {np.mean(placebo):.2f} mean "
               f"(min {min(placebo):.2f}, max {max(placebo):.2f}); the sleeve {full:.2f} beats {beat}/20")
    ok = (t[1]["sharpe"] >= 0.5 and t[1]["sharpe"] > c1[1]["sharpe"] and t[1]["max_dd"] > c1[1]["max_dd"] and t[1]["max_dd"] > c2[1]["max_dd"]
          and t[0]["cagr"] > 0 and t[1]["cagr"] > 0 and st[1]["sharpe"] >= 0.4 and beat >= 18)
    out.append(f"- verdict: {'PASS (paper candidate)' if ok else 'REJECT'} (OOS Sharpe >= 0.5 and above control 1, OOS maxDD shallower than control 1 and the 60/40, "
               f"both windows profitable, OOS Sharpe >= 0.4 at 10 bps, beats 18/20 random timings)")
    last = closes.index[-1]
    rets = closes.pct_change()
    vol = rets.rolling(sp["vol_days"]).std().iloc[-1] * np.sqrt(252)
    on = (closes.iloc[-1] / closes.iloc[-1 - sp["lookback_days"]] - 1) > 0
    w = ((sp["target"] / vol) / len(assets)).clip(upper=sp["cap"]).where(on, 0.0)
    w = w / max(1.0, float(w.sum()))
    out.append(f"- today's signal ({last.date()}): " + ", ".join(f"{a} {'ON' if on[a] else 'off'} {w[a]:.0%}" for a in assets) + f" | cash {1 - float(w.sum()):.0%}")
    return out
