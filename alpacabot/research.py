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
