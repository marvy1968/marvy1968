"""Walk-forward for the FX families, same protocol as alpacabot.

PRE-DECLARED (2026-10-14, before any run): train 2016-01-01..2020-12-31 picks ONE parameter set per family (highest profit factor among
sets with 100+ trades and a positive total); validate 2021-01-01..2022-12-31 is looked at once. A family PASSES only if on validation:
50+ trades, profit factor >= 1.2, total return > 0, still > 0 with the spread doubled, and it beats random entries (same count, same
exits/stops) in >= 90% of 20 seeds by profit factor. The 2023-today final test is not run here. Portfolio: 0.5% risk per trade,
max 4 positions, one per pair, at most 2 same-side trades per currency, leverage cap 20:1, financing 0.01%/night on notional.
"""

import itertools
from datetime import date

import numpy as np

from .data import MAJORS
from .engine import Book, metrics, portfolio, simulate
from .strategies import InBox, TightStop, BoxConfirm, RangeBoxFailed, BoxGated, DirectionZoneMap, Laddered, Locked, Zoned, AsianBreakout, ConsensusMR, F0Reversion, LienFader, MaCrossBigBuild, Donchian, EmaPullback, NR7Breakout, RangeBoxFade, RangeBox200, RangeMR, RegimeSwitch, TsMom

TRAIN = (date(2016, 1, 1), date(2020, 12, 31))
VALIDATE = (date(2021, 1, 1), date(2022, 12, 31))

FAMILIES = {
    # name: (granularity, class, grid)
    "fx_donchian_d": ("D", Donchian, {"entry_n": [20, 55], "exit_n": [10, 20], "stop_atr": [2.0, 3.0]}),
    "fx_donchian_h4": ("H4", Donchian, {"entry_n": [20, 55], "exit_n": [10, 20], "stop_atr": [2.0, 3.0]}),
    "fx_tsmom_d": ("D", TsMom, {"lookback": [63, 126, 252], "hold": [5, 21], "stop_atr": [3.0]}),
    "fx_range_h1": ("H1", RangeMR, {"bb_k": [2.0, 2.5], "stop_atr": [1.5, 2.5], "asian_only": [False, True]}),
    "fx_regime_h4": ("H4", RegimeSwitch, {"entry_n": [20, 55], "bb_k": [2.0, 2.5], "stop_atr": [2.0, 3.0]}),
    "fx_consensus_h1": ("H1", ConsensusMR, {"votes": [3]}),          # the owner's daemon exactly as found: one setting, nothing tuned
    # added 2026-10-14 from the owner's other forex bots (pre-declared, before any run)
    "fx_asian_breakout_h1": ("H1", AsianBreakout, {"buffer_atr": [0.0, 0.1], "exit_hour": [12, 16]}),
    "fx_rangebox_h1": ("H1", RangeBoxFade, {"stop_atr": [1.2]}),       # RangeBox_Master frozen: one setting
    "fx_pullback_h4": ("H4", EmaPullback, {"trend_n": [100, 200]}),
    "fx_pullback_d": ("D", EmaPullback, {"trend_n": [100, 200]}),
    "fx_nr7_d": ("D", NR7Breakout, {"max_bars": [5]}),
    # the one system the owner's fx-experts backtest found positive after costs (PF 1.18, 2016-26): book rules, one setting
    "fx_lien_fader_h1": ("H1", LienFader, {"break_pips": [15]}),
    # the owner's "Big Build" as recalled (MA20/60, 80/160-pip SL/TP, RSI filter): one frozen setting per timeframe
    "fx_bigbuild_h4": ("H4", MaCrossBigBuild, {"fast": [20]}),
    "fx_bigbuild_d": ("D", MaCrossBigBuild, {"fast": [20]}),
    # the owner's F0 (H4 SMA100 +-2.5 sigma reclaim, 3.5 sigma stop, mean target): the best recorded FX result, frozen
    "fx_f0_h4": ("H4", F0Reversion, {"n": [100]}),
    # owner 2026-10-15 "optimize oanda": Lien Fader's own knobs on a small PRE-DECLARED grid (27 points); chosen on train 2016-20 only,
    # then judged on validation 2021-22 with the usual 2x-spread and placebo checks. Nothing is adopted unless it passes.
    "fx_lien_fader_grid_h1": ("H1", LienFader, {"break_pips": [10, 15, 20], "stop_pips": [20, 30, 40], "target_pips": [40, 60, 90]}),
    # the owner's range box (20/200 MA flat = ranging; buy the bottom, sell the top), drawn 2026-10-14; live on practice from day one
    "fx_box200_h1": ("H1", RangeBox200, {"lookback": [100]}),
    # same rules on other timeframes (owner asked "is hourly the best?"): 100 bars each, nothing else changed; judged like every family
    # Gemini's "statistical range box" (owner's paste, 2026-10-15): SMA200 +- 2 sigma envelope, buy when price reclaims the lower band
    # (stop 3 sigma, exit at the mean) = F0's mechanics with 200 bars / 2.0 sigma, one frozen setting per timeframe
    "fx_zbox200_h1": ("H1", F0Reversion, {"n": [200], "entry_z": [2.0], "stop_z": [3.0]}),
    "fx_zbox200_h4": ("H4", F0Reversion, {"n": [200], "entry_z": [2.0], "stop_z": [3.0]}),
    # the owner's range box with its levels from the statistical envelope (SMA200 +- 2 sigma) instead of the 100-bar high/low
    "fx_box200z_h1": ("H1", RangeBox200, {"lookback": [100], "levels": ["zscore"], "z": [2.0]}),
    # owner 2026-10-16: virtual breakout orders outside the box when the indicators agree: breakouts alone, and the box fade + breakouts together
    "fx_boxbreak_only_h1": ("H1", RangeBox200, {"lookback": [100], "breakout": ["only"]}),
    "fx_boxbreak_also_h1": ("H1", RangeBox200, {"lookback": [100], "breakout": ["also"]}),
    # REPAIR (2026-10-16): fade the FAILED breakout instead of the first touch of the edge (the edge broke on 68% of the box's trades)
    "fx_boxfail_h1": ("H1", RangeBoxFailed, {"lookback": [100], "look": [6], "target": ["edge", "mid"]}),
    # Gemini's fix (owner's paste 2026-10-16): only fade the edge on a volume expansion (>= 1.5x the 20-bar average)
    "fx_boxvol_h1": ("H1", RangeBox200, {"lookback": [100], "vol_mult": [1.5], "target": ["edge", "mid"]}),
    "fx_boxvolz_h1": ("H1", RangeBox200, {"lookback": [100], "levels": ["zscore"], "vol_mult": [1.5], "target": ["mid"]}),
    "fx_boxfailvol_h1": ("H1", RangeBoxFailed, {"lookback": [100], "look": [6], "vol_mult": [1.5], "target": ["edge", "mid"]}),
    # FIX (owner 2026-10-16): candlestick + volume + volatility decide whether a break of the box is continuing (trade it) or failing (fade)
    "fx_boxconfirm_h1": ("H1", BoxConfirm, {"mode": ["continuation", "failure", "both"], "vol_mult": [1.2, 1.5], "atr_ratio": [1.0, 1.2]}),
    "fx_box200_m15": ("M15", RangeBox200, {"lookback": [100]}),
    "fx_box200_h4": ("H4", RangeBox200, {"lookback": [100]}),
}

# The owner's range box as a FILTER on the range experts (owner, 2026-10-16: "test the range experts with the range box I designed for OANDA"):
# each range expert plain vs gated by the box (valid = pair is ranging per the box; half = fade only from the correct half; edge = outer third).
# One frozen setting per expert (Lien Fader at the provisional 20-pip break), same pass bar, compared with its plain twin.
BOX_GATE_EXPERTS = {"lienf20": ("H1", LienFader, {"break_pips": [20]}), "rangemr": ("H1", RangeMR, {"bb_k": [2.0], "stop_atr": [1.5], "asian_only": [False]}),
                    "consensus": ("H1", ConsensusMR, {"votes": [3]}), "f0": ("H4", F0Reversion, {"n": [100]})}
for _n, (_g, _c, _p) in BOX_GATE_EXPERTS.items():
    FAMILIES[f"fx_boxgate_{_n}_plain"] = (_g, _c, _p)
    for _pos in ("valid", "half", "edge"):
        FAMILIES[f"fx_boxgate_{_n}_{_pos}"] = (_g, (lambda c, pos: (lambda **p: BoxGated(c(**p), position=pos)))(_c, _pos), _p)

# 24/5 scalpers on M15 (added 2026-10-14, owner asked for a forex scalper): the range fade restricted to the quiet Asian session, and the
# Asian-range breakout at the London open, both on 15-minute bars with real bid/ask. Small pre-declared grids.
FAMILIES["fx_scalp_asia_range_m15"] = ("M15", RangeMR, {"bb_k": [2.0, 2.5], "stop_atr": [1.5, 2.5], "asian_only": [True], "max_bars": [8]})
FAMILIES["fx_scalp_london_break_m15"] = ("M15", AsianBreakout, {"buffer_atr": [0.0, 0.5], "exit_hour": [9, 12], "max_bars": [16]})

# Profit-lock variants (added 2026-10-14, owner: "secure profits"): the same frozen strategies with stop -> break-even at +1R and a
# 1R trail after +2R. Same pass bar; compare each with its unlocked twin. Nothing here is tuned.
LOCK = {"be_at": 1.0, "trail_after": 2.0, "trail_r": 1.0}
for _name in ("fx_donchian_d", "fx_bigbuild_h4", "fx_f0_h4", "fx_lien_fader_h1", "fx_pullback_h4"):
    _gran, _cls, _params = FAMILIES[_name]
    FAMILIES[_name + "_lock"] = (_gran, (lambda c: (lambda **p: Locked(c(**p), **LOCK)))(_cls), _params)


def _grid(g):
    keys = list(g)
    return [dict(zip(keys, v)) for v in itertools.product(*(g[k] for k in keys))]


def run(strat, data: dict, a: date, b: date, book: Book = None, spread_mult: float = 1.0, placebo_seed=None) -> dict:
    book = book or Book()
    trades = [t for inst, df in data.items() for t in simulate(inst, df, strat, spread_mult, placebo_seed)
              if a <= t.entry_time.date() <= b]
    fills, curve = portfolio(trades, book)
    return metrics(fills, curve, book.equity, (b - a).days)


def walk_forward(family: str, data: dict, placebo_runs: int = 20) -> dict:
    gran, cls, grid = FAMILIES[family]
    table = []
    for p in _grid(grid):
        m = run(cls(**p), data, *TRAIN)
        table.append({"params": p, **m})
    ok = [t for t in table if (t.get("trades") or 0) >= 100 and (t.get("total_return") or -1) > 0]
    out = {"family": family, "granularity": gran, "train": [str(x) for x in TRAIN], "validate": [str(x) for x in VALIDATE], "grid": table}
    if not ok:
        out.update(chosen=None, passes=False, verdict="no parameter set made money with 100+ trades on the train years")
        return out
    best = max(ok, key=lambda t: t["profit_factor"])
    strat = cls(**best["params"])
    val = run(strat, data, *VALIDATE)
    stress = run(strat, data, *VALIDATE, spread_mult=2.0)
    plc = [run(strat, data, *VALIDATE, placebo_seed=s).get("profit_factor") or 0 for s in range(placebo_runs)]
    beat = float(np.mean([(val.get("profit_factor") or 0) > p for p in plc]))
    passes = ((val.get("trades") or 0) >= 50 and (val.get("profit_factor") or 0) >= 1.2 and (val.get("total_return") or -1) > 0
              and (stress.get("total_return") or -1) > 0 and beat >= 0.9)
    out.update(chosen=best["params"], train_metrics={k: v for k, v in best.items() if k != "params"}, validate_metrics=val,
               spread_x2=stress, placebo_pf_mean=float(np.mean(plc)), beats_placebo_share=beat, passes=bool(passes),
               verdict=("PASSED validation (the 2023-today final test may be run once)" if passes else "did not pass validation"))
    return out


def instruments():
    return list(MAJORS)


# Trade-zone variants (owner's idea, 2026-10-14, priority one): entry zone (no chase), invalidation (own stop), exhaustion take-profit.
# Frozen rules in strategies.ZONES; same pass bar; compare each with its plain twin.
# Each strategy gets the zone profile of its style (owner: "each expert needs its own zones"); the levels are each pair's own.
ZONE_STYLE = {"fx_donchian_d": "trend", "fx_bigbuild_h4": "trend", "fx_pullback_h4": "trend", "fx_scalp_london_break_m15": "trend",
              "fx_f0_h4": "range", "fx_lien_fader_h1": "range", "fx_scalp_asia_range_m15": "range", "fx_consensus_h1": "range"}
for _name, _style in ZONE_STYLE.items():
    _gran, _cls, _params = FAMILIES[_name]
    FAMILIES[_name + "_zones"] = (_gran, (lambda c, st: (lambda **p: Zoned(c(**p), profile=st)))(_cls, _style), _params)


# Direction first + per-style zones + the pair's daily zone map (owner, 2026-10-14): every family that has a zone style, frozen rules.
for _name, _style in ZONE_STYLE.items():
    _gran, _cls, _params = FAMILIES[_name]
    FAMILIES[_name + "_dirmap"] = (_gran, (lambda c, st: (lambda **p: DirectionZoneMap(Zoned(c(**p), profile=st), style=st)))(_cls, _style),
                                   _params)


# The owner's drawn exits on the three LIVE practice strategies (2026-10-14), with the same pre-declared alternatives as the stock bot:
# grid parameter "variant" picks the exit plan; walk_forward chooses on train and checks validation as for every family.
from alpacabot.research import LADDER_VARIANTS  # noqa: E402

for _name in ("fx_lien_fader_h1", "fx_f0_h4", "fx_box200_h1"):
    _gran, _cls, _params = FAMILIES[_name]
    FAMILIES[_name + "_ladder"] = (_gran, (lambda c, p0: (lambda variant, **p: Laddered(c(**{**p0, **p}), LADDER_VARIANTS[variant])))(
        _cls, {k: v[0] for k, v in _params.items()}), {"variant": list(LADDER_VARIANTS)})



def box_diagnosis(data: dict, a=None, b=None) -> list[str]:
    """Why the owner's range box (RangeBox200, H1) loses: per-trade anatomy on 2016-2022 bid/ask data, plus stop / target what-ifs that are
    diagnostics only (nothing here is adopted)."""
    import datetime as _d
    from alpacabot.indicators import atr as _atr
    from .data import mid as _mid
    a = a or _d.date(2016, 1, 1)
    b = b or _d.date(2022, 12, 31)
    lines = []

    def anatomy(label, strat):
        rows = []
        for inst, df in data.items():
            m = _mid(df)
            at = _atr(m, 14).to_numpy(float)
            f = strat.frame(m)
            hi_, lo_ = m["high"].to_numpy(float), m["low"].to_numpy(float)
            top = m["high"].shift(1).rolling(100).max().to_numpy(float)
            bot = m["low"].shift(1).rolling(100).min().to_numpy(float)
            spread = (df["ac"] - df["bc"]).to_numpy(float)
            tg = f["target"].to_numpy(float)
            idx = {t: k for k, t in enumerate(df.index)}
            for t in simulate(inst, df, strat):
                if not (a <= t.entry_time.date() <= b):
                    continue
                j0, j1 = idx[t.entry_time], idx[t.exit_time]
                i = j0 - 1
                r = t.side * (t.exit_px - t.entry_px) / t.stop_dist
                seg_hi, seg_lo = hi_[j0:j1 + 1].max(), lo_[j0:j1 + 1].min()
                mfe = (seg_hi - t.entry_px if t.side == 1 else t.entry_px - seg_lo) / t.stop_dist
                # broke out of the box against the trade within 24 bars after entry (close beyond the edge it was faded from)
                w = m["close"].to_numpy(float)[j0:j0 + 24]
                broke = bool((w < bot[i]).any()) if t.side == 1 else bool((w > top[i]).any())
                rows.append({"r": r, "why": t.reason, "mfe": mfe, "bars": t.bars, "stop_atr": t.stop_dist / at[i] if at[i] > 0 else np.nan,
                             "spread_frac": spread[i] / t.stop_dist, "tgt_r": tg[i] / t.stop_dist if np.isfinite(tg[i]) else np.nan,
                             "broke": broke, "box_atr": (top[i] - bot[i]) / at[i] if at[i] > 0 else np.nan})
        if not rows:
            lines.append(f"### {label}: no trades")
            return
        R = np.array([x["r"] for x in rows])
        wins, losses = R[R > 0], R[R <= 0]
        pf = wins.sum() / -losses.sum() if losses.sum() < 0 else float("inf")
        lines.append(f"### {label}: {len(R)} trades, win {len(wins) / len(R):.0%}, avg R {R.mean():+.2f}, avg win {wins.mean() if len(wins) else 0:+.2f}R, "
                     f"avg loss {losses.mean() if len(losses) else 0:+.2f}R, PF {pf:.2f} (before spread costs counted in R)")
        whys = {}
        for x in rows:
            whys.setdefault(x["why"], []).append(x["r"])
        lines.append("- exits: " + ", ".join(f"{k} {len(v)} ({len(v) / len(rows):.0%}, avg {np.mean(v):+.2f}R)" for k, v in sorted(whys.items(), key=lambda z: -len(z[1]))))
        med = lambda k: float(np.nanmedian([x[k] for x in rows]))   # noqa: E731
        lines.append(f"- median stop {med('stop_atr'):.2f} ATR; spread = {med('spread_frac'):.0%} of the stop; target {med('tgt_r'):.1f}R away; "
                     f"box {med('box_atr'):.1f} ATR tall; held {med('bars'):.0f} bars")
        mfe = np.array([x["mfe"] for x in rows])
        lines.append(f"- best move in the trade's favour before exit (MFE): median {np.median(mfe):.2f}R; reached +1R {np.mean(mfe >= 1):.0%}, "
                     f"+2R {np.mean(mfe >= 2):.0%}, half way to target {np.mean(mfe >= 0.5 * np.array([x['tgt_r'] for x in rows])):.0%}")
        br = np.array([x["broke"] for x in rows])
        lines.append(f"- price CLOSED beyond the faded edge within 24 h after entry (a breakout against the trade): {br.mean():.0%} of trades; "
                     f"their avg {R[br].mean() if br.any() else 0:+.2f}R vs {R[~br].mean() if (~br).any() else 0:+.2f}R for the rest")
        lines.append("")

    anatomy("live box (stop 0.5 ATR past the edge, target the far edge)", RangeBox200())
    anatomy("what-if: stop 1.0 ATR past the edge", RangeBox200(stop_atr=1.0))
    anatomy("what-if: stop 2.0 ATR past the edge", RangeBox200(stop_atr=2.0))
    anatomy("what-if: target the box middle", RangeBox200(target="mid"))
    anatomy("what-if: stop 1.0 ATR + target the middle", RangeBox200(stop_atr=1.0, target="mid"))
    anatomy("REPAIR: failed breakout, target the far edge", RangeBoxFailed(target="edge"))
    anatomy("REPAIR: failed breakout, target the middle", RangeBoxFailed(target="mid"))
    for mode in ("continuation", "failure", "both"):
        anatomy(f"CONFIRM ({mode}): candles + volume 1.5x + volatility 1.1x", BoxConfirm(mode=mode))
    anatomy("VOLUME (Gemini's fix): live box + volume >= 1.5x 20-bar average", RangeBox200(vol_mult=1.5))
    anatomy("VOLUME: live box, target the middle + volume >= 1.5x", RangeBox200(vol_mult=1.5, target="mid"))
    anatomy("VOLUME: z-score band (SMA200 +- 2 sigma) + volume >= 1.5x, target the middle", RangeBox200(levels="zscore", vol_mult=1.5, target="mid"))
    anatomy("VOLUME: failed breakout + volume >= 1.5x, target the middle", RangeBoxFailed(target="mid", vol_mult=1.5))
    return lines



def confirm_check(data: dict) -> list[str]:
    """Confirmed-breakout (continuation) settings from the fx_boxconfirm_h1 grid, train 2016-20 vs validate 2021-22, real bid/ask costs,
    portfolio as in walk_forward (Book defaults), plus 2x spread and 20 placebos on validation."""
    import datetime as _d
    out = []
    for vm, ar in ((1.5, 1.2), (1.5, 1.1), (1.5, 1.0), (1.2, 1.2)):
        strat = BoxConfirm(mode="continuation", vol_mult=vm, atr_ratio=ar)
        tr = run(strat, data, *TRAIN)
        va = run(strat, data, *VALIDATE)
        x2 = run(strat, data, *VALIDATE, spread_mult=2.0)
        plc = [run(strat, data, *VALIDATE, placebo_seed=s_).get("profit_factor") or 0 for s_ in range(20)]
        beat = float(np.mean([(va.get("profit_factor") or 0) > p for p in plc]))
        out.append(f"- continuation vol {vm}x ATR {ar}x | train {tr.get('trades')}t PF {tr.get('profit_factor') or 0:.2f} total {tr.get('total_return') or 0:+.1%} "
                   f"maxDD {tr.get('max_drawdown') or 0:.1%} | validate {va.get('trades')}t win {va.get('win_rate') or 0:.0%} PF {va.get('profit_factor') or 0:.2f} "
                   f"total {va.get('total_return') or 0:+.1%} maxDD {va.get('max_drawdown') or 0:.1%} | 2x spread {x2.get('total_return') or 0:+.1%} | "
                   f"placebo PF {np.mean(plc):.2f}, real beats {beat:.0%}")
    return out



def vote_check(data: dict) -> list[str]:
    """Owner's question 2026-10-16: does a 60% vote of the continuation indicators (3 of 5) beat requiring all of them?"""
    out = []
    for vote in (0.6, 0.8, 1.0):
        for ar in (1.1, 1.2):
            strat = BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=ar, vote=vote)
            tr, va = run(strat, data, *TRAIN), run(strat, data, *VALIDATE)
            x2 = run(strat, data, *VALIDATE, spread_mult=2.0)
            plc = [run(strat, data, *VALIDATE, placebo_seed=s_).get("profit_factor") or 0 for s_ in range(20)]
            beat = float(np.mean([(va.get("profit_factor") or 0) > p for p in plc]))
            out.append(f"- vote {vote:.0%} (ATR {ar}x) | train {tr.get('trades')}t win {tr.get('win_rate') or 0:.0%} PF {tr.get('profit_factor') or 0:.2f} "
                       f"total {tr.get('total_return') or 0:+.1%} maxDD {tr.get('max_drawdown') or 0:.1%} | validate {va.get('trades')}t win {va.get('win_rate') or 0:.0%} "
                       f"PF {va.get('profit_factor') or 0:.2f} total {va.get('total_return') or 0:+.1%} maxDD {va.get('max_drawdown') or 0:.1%} | "
                       f"2x spread {x2.get('total_return') or 0:+.1%} | placebo PF {np.mean(plc):.2f}, real beats {beat:.0%}")
    return out



def recent_vote_check(data: dict, a, b) -> tuple[list[str], float]:
    """Owner 2026-10-16: a SHORT test on the last 3 months (a..b) of the confirmed-breakout votes, then install. Pre-declared pick: the vote
    with the best profit factor among those with >= 10 trades and PF > 1; if none, the all-indicators rule (1.0, best on 2016-20)."""
    out, best = [], (None, 1.0)
    for vote in (0.6, 0.8, 1.0):
        strat = BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=vote)
        m = run(strat, data, a, b)
        pf, n = m.get("profit_factor") or 0, m.get("trades") or 0
        out.append(f"- vote {vote:.0%}: {n} trades, win {m.get('win_rate') or 0:.0%}, PF {pf:.2f}, total {m.get('total_return') or 0:+.2%}, "
                   f"maxDD {m.get('max_drawdown') or 0:.1%}")
        if n >= 10 and pf > 1 and (best[0] is None or pf > best[0]):
            best = (pf, vote)
    out.append(f"- PICK (pre-declared rule): vote {best[1]:.0%}" + ("" if best[0] else " (no vote was profitable with 10+ trades: all-indicators rule)"))
    return out, best[1]


IN_BOX_EXPERTS = {"lien": ("H1", lambda: LienFader(break_pips=20)), "rangemr": ("H1", lambda: RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False)),
                  "consensus": ("H1", lambda: ConsensusMR(votes=3)), "f0": ("H4", lambda: F0Reversion(n=100))}


def inbox_check(data_by_gran: dict, a, b) -> list[str]:
    """Owner 2026-10-19 ("modify my paper range idea and place my experts inside it", OANDA): each range expert plain, gated by the box (the
    earlier BoxGated, its own stop / target) and placed INSIDE the box (InBox: the box's stop and take-profit lines), over a..b."""
    out = []
    for key, (gran, make) in IN_BOX_EXPERTS.items():
        data = data_by_gran[gran]
        rows = {"plain (expert alone)": make(), "gated by the box (own stop/target)": BoxGated(make(), position="half"),
                "INSIDE the box, TP1 at the middle": InBox(make(), tp_frac=0.5), "INSIDE the box, TP1 at the far edge": InBox(make(), tp_frac=1.0)}
        out.append(f"### {key} ({gran})")
        for label, strat in rows.items():
            m = run(strat, data, a, b)
            out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def losscut_check(data: dict, a, b) -> list[str]:
    """Owner 2026-10-19: the live strategies with the normal stop vs a stop at 10% of it (a trade is closed once it is 0.10 x its risk against us).
    Hourly bars with bid/ask fills: the intrabar order is unknown and the spread counts against the tight stop here (live measures the mid), so
    this is the PESSIMISTIC view of the rule."""
    from .strategies import HybridBox, RangeMR
    rows = {"lien_h1": lambda: LienFader(break_pips=20), "hybrid_h1": lambda: HybridBox(),
            "confirm_h1": lambda: BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=1.0),
            "boxmr_h1": lambda: InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5)}
    out = []
    for name, make in rows.items():
        out.append(f"### {name}")
        for label, strat in (("normal stop", make()), ("loss cut at 0.10R", TightStop(make(), 0.10)), ("loss cut at 0.25R", TightStop(make(), 0.25)),
                             ("loss cut at 0.50R", TightStop(make(), 0.50))):
            m = run(strat, data, a, b)
            out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def scalp_check(data: dict, a, b) -> list[str]:
    """Owner 2026-10-19 ("leave the loss cut at 10, tp and closeout at 30%"): the live strategies with a 0.10R loss cut and the whole position taken
    off at +0.30R, against a few neighbours and the normal ladder. Hourly bars, bid/ask fills; PESSIMISTIC (spread counts against the tight levels and the
    intrabar order of stop and target is unknown, so a bar touching both counts as the stop)."""
    from .strategies import HybridBox, RangeMR
    rows = {"lien_h1": lambda: LienFader(break_pips=20), "hybrid_h1": lambda: HybridBox(),
            "confirm_h1": lambda: BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=1.0),
            "boxmr_h1": lambda: InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5)}
    out = []
    for name, make in rows.items():
        out.append(f"### {name}")
        variants = (("normal ladder (before)", make()), ("OWNER: cut 0.10R, all out at +0.30R", TightStop(make(), 0.10, 0.30)),
                    ("cut 0.10R, all out at +0.20R", TightStop(make(), 0.10, 0.20)), ("cut 0.10R, all out at +0.50R", TightStop(make(), 0.10, 0.50)),
                    ("cut 0.25R, all out at +0.75R", TightStop(make(), 0.25, 0.75)), ("cut 0.50R, all out at +1.50R", TightStop(make(), 0.50, 1.50)))
        for label, strat in variants:
            m = run(strat, data, a, b)
            out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def confluence_check(data_by_gran: dict, a, b) -> list[str]:
    """Owner 2026-10-22: the oversold-confluence rules (strategies.OversoldConfluence, live as conf_d1 / conf_h4) on each bar size, with the
    live exits (loss cut at 0.50R, all out at +1.25R) and with the plain stop / middle-band exit. Bid/ask fills, pessimistic."""
    from .strategies import OversoldConfluence
    out = []
    for gran, data in data_by_gran.items():
        out.append(f"### {gran}")
        for label, strat in (("live exits (cut 0.5R, out at 1.25R)", TightStop(OversoldConfluence(), 0.50, 1.25)),
                             ("plain: 1 ATR stop, out at the middle band", OversoldConfluence()),
                             ("2 of 4 + live exits", TightStop(OversoldConfluence(need=2), 0.50, 1.25)),
                             ("no volume filter + live exits", TightStop(OversoldConfluence(vol_mult=0.0), 0.50, 1.25))):
            m = run(strat, data, a, b)
            out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def velez_fx_check(data_h1: dict, data_d: dict, a, b) -> list[str]:
    """Owner 2026-10-22 ("hybrid our OANDA experts with the Velez list to optimize the weaker experts"): every live / logged forex strategy with
    the live exits (cut 0.5R, out at 1.25R), alone and with Velez's trend / look-left / no-chase rules (strategies.VelezFx)."""
    from .strategies import ConsensusMR, F0Reversion, HybridBox, OversoldConfluence, RangeBox200, RangeMR, VelezFx
    rows = {"lien_h1": (lambda: LienFader(break_pips=20), data_h1), "hybrid_h1": (lambda: HybridBox(), data_h1),
            "confirm_h1": (lambda: BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=1.0), data_h1),
            "boxmr_h1": (lambda: InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5), data_h1),
            "conf_d1": (lambda: OversoldConfluence(), data_d), "box_h1 (log-only)": (lambda: RangeBox200(), data_h1)}
    variants = (("as live", {}), ("+ Velez trend", {"env": True}), ("+ look left", {"env": False, "look_left": True}),
                ("+ no chase", {"env": False, "no_chase": True}), ("+ trend + look left", {"env": True, "look_left": True}),
                ("+ all three", {"env": True, "look_left": True, "no_chase": True}))
    out = []
    for name, (make, data) in rows.items():
        out.append(f"### {name}")
        for label, kw in variants:
            base = make() if not kw else VelezFx(make(), **kw)
            m = run(TightStop(base, 0.50, 1.25), data, a, b)
            out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def velez_fx2_check(data_h1: dict, a, b) -> list[str]:
    """Owner 2026-10-22 ("build it and test"): Velez's setups as forex experts on H1 (all together and each alone) and as confirmation for the
    live forex experts (an entry needs a Velez setup the same way within 2 bars). Live exits (cut 0.5R, out at 1.25R), bid/ask."""
    from .strategies import FX_VELEZ, HybridBox, RangeMR, VelezConfirmFx, VelezFx, VelezSetupsFx
    out = ["### Velez setups as forex experts"]
    for label, strat in [("all 14 setups", VelezSetupsFx())] + [(t, VelezSetupsFx(setups=(t,))) for t in FX_VELEZ]:
        m = run(TightStop(strat, 0.50, 1.25), data_h1, a, b)
        out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                   f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    out.append("### live experts confirmed by a Velez setup")
    rows = {"hybrid_h1": lambda: HybridBox(), "confirm_h1 (live: + trend)": lambda: VelezFx(BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=1.0), env=True),
            "boxmr_h1 (live: + look left)": lambda: VelezFx(InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5), env=False, look_left=True)}
    for name, make in rows.items():
        for label, strat in (("as live", make()), ("+ Velez setup confirm", VelezConfirmFx(make()))):
            m = run(TightStop(strat, 0.50, 1.25), data_h1, a, b)
            out.append(f"- {name} {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


def range_map_check(data_h1: dict, a, b) -> list[str]:
    """Owner 2026-10-25 (PDF "Range trading: corrected decision map"): the three range setups on H1, each with the map's own exits (stop at the
    setup / invalidation level, take-profit at the box middle or the far edge) and with the live exits (cut 0.5R, out at 1.25R), next to
    what is live. A support reversal (buy the range low after it holds), B confirmed breakout, C failed-breakdown reclaim (a close below the
    range, then back inside; stop under the false-break low; TP1 the middle, then the range high). "+ Velez confirm" = a Velez bullish
    (bearish) setup the same way within 2 bars, i.e. the map's "defined bullish trigger". Shorts mirror every rule."""
    from .strategies import BreakRetest, RangeBox200, RangeBoxFailed, RangeMR, VelezConfirmFx, VelezFx
    rows = [
        ("A support reversal, exit at the middle", lambda: RangeBox200(target="mid")),
        ("A support reversal, exit at the range high", lambda: RangeBox200(target="edge")),
        ("A + Velez confirm, exit at the middle", lambda: VelezConfirmFx(RangeBox200(target="mid"))),
        ("A live today: boxmr_h1", lambda: VelezFx(InBox(RangeMR(bb_k=2.0, stop_atr=1.5, asian_only=False), tp_frac=0.5), env=False, look_left=True)),
        ("B breakout on a close", lambda: RangeBox200(breakout="only")),
        ("B + Velez trend (20 over 200)", lambda: VelezFx(RangeBox200(breakout="only"), env=True)),
        ("B live today: confirm_h1", lambda: VelezFx(BoxConfirm(mode="continuation", vol_mult=1.5, atr_ratio=1.1, vote=1.0), env=True)),
        ("C reclaim, TP the middle", lambda: RangeBoxFailed(target="mid")),
        ("C reclaim, TP the range high", lambda: RangeBoxFailed(target="edge")),
        ("C + Velez confirm, TP the middle", lambda: VelezConfirmFx(RangeBoxFailed(target="mid"))),
        ("C + volume on the reclaim bar, TP the middle", lambda: RangeBoxFailed(target="mid", vol_mult=1.2)),
        # owner 2026-10-25 break-and-retest (strategies.BreakRetest): break, retest of the broken edge, rejection bar, entry above its high
        ("R break-retest, ranging boxes, 2R", lambda: BreakRetest()),
        ("R break-retest, ranging boxes, 1.5R", lambda: BreakRetest(target_r=1.5)),
        ("R break-retest, any 100-bar box, 2R", lambda: BreakRetest(ranging=False)),
        ("R break-retest, any box, 2R + Velez trend", lambda: VelezFx(BreakRetest(ranging=False), env=True)),
    ]
    out = []
    for name, make in rows:
        for label, strat in (("map exits", make()), ("live exits 0.5R/1.25R", TightStop(make(), 0.50, 1.25))):
            m = run(strat, data_h1, a, b)
            out.append(f"- {name} | {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                       f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out


HYBRID_GRID_FILTERS = {"none": {}, "look left": {"look_left": True}, "narrow state": {"narrow": True}, "wide state": {"wide": True},
                       "3-5 bar rule": {"bars35": True}, "tail bar": {"tail": True}, "counter-trend": {"counter": True},
                       "narrow + look left": {"narrow": True, "look_left": True}, "wide + tail": {"wide": True, "tail": True},
                       "3-5 bar + tail": {"bars35": True, "tail": True}, "narrow + 3-5 bar": {"narrow": True, "bars35": True},
                       "counter + 3-5 bar": {"counter": True, "bars35": True}, "wide + 3-5 bar": {"wide": True, "bars35": True},
                       "narrow + tail": {"narrow": True, "tail": True}, "look left + tail": {"look_left": True, "tail": True}}
HYBRID_GRID_EXITS = {"cut 0.5R / out 1.25R (live)": (0.50, 1.25), "stop 1R / out 1R": (1.0, 1.0), "stop 1R / out 2R": (1.0, 2.0),
                     "stop 1.5R / out 0.5R": (1.5, 0.5), "stop 1R / out 0.75R": (1.0, 0.75)}


def hybrid_tune(data_h1: dict, train, validate, recent) -> list[str]:
    """Owner 2026-10-22 ("keep altering hybrid_h1 with more Velez until profitable"): hybrid_h1 x Velez filters x exit pairs. Pre-declared rule:
    pick on TRAIN the best profit factor with 15+ trades; it is ADOPTED only if VALIDATE is also profitable (PF > 1) with 8+ trades. The last
    2 weeks are shown for reference."""
    from .strategies import HybridBox, VelezFx
    res = []
    for fn, fkw in HYBRID_GRID_FILTERS.items():
        for en, (cut, tp) in HYBRID_GRID_EXITS.items():
            make = (lambda fkw=fkw: VelezFx(HybridBox(), env=False, **fkw) if fkw else HybridBox())
            row = {"name": f"{fn} | {en}"}
            for per, (a, b) in (("train", train), ("validate", validate), ("2w", recent)):
                m = run(TightStop(make(), cut, tp), data_h1, a, b)
                row[per] = (m.get("trades") or 0, m.get("win_rate") or 0.0, m.get("profit_factor") or 0.0, m.get("total_return") or 0.0)
            res.append(row)
    fmt = lambda t: f"{t[0]}t {t[1]:.0%} PF {t[2]:.2f} {t[3]:+.2%}"     # noqa: E731
    out = [f"- {r['name']}: TRAIN {fmt(r['train'])} | VALIDATE {fmt(r['validate'])} | 2W {fmt(r['2w'])}" for r in res]
    ok = [r for r in res if r["train"][0] >= 15]
    best = max(ok, key=lambda r: r["train"][2]) if ok else None
    if best:
        adopt = best["validate"][2] > 1.0 and best["validate"][0] >= 8
        out.insert(0, f"CHOSEN ON TRAIN: {best['name']} -> {'ADOPT (validate profitable)' if adopt else 'NOT adopted (validate not profitable)'}")
    passing = [r for r in res if r["train"][2] > 1 and r["validate"][2] > 1 and r["train"][0] >= 10 and r["validate"][0] >= 8]
    out.insert(1, "PROFITABLE IN BOTH HALVES: " + (", ".join(f"{r['name']} (PF {r['train'][2]:.2f}/{r['validate'][2]:.2f})" for r in passing) or "none"))
    return out


def pristine_check(data_by_gran: dict, a, b) -> list[str]:
    """Owner 2026-10-22 (deep research: Pristine 2001 + iFundTraders 2010 FX course): PBS/VBS, CBS, COG and PBO with each trailing method
    (stop only, bar-by-bar, 8 SMA, 20 SMA) and with the live exits (cut 0.5R, out 1.25R). Bid/ask, pessimistic."""
    from .strategies import PristineFx
    out = []
    for gran, data in data_by_gran.items():
        out.append(f"### {gran}")
        for kind in ("pbs", "cbs", "cog", "pbo"):
            for label, strat in [(f"{kind} live exits", TightStop(PristineFx(kind, "stop"), 0.50, 1.25))] + \
                                [(f"{kind} trail {t}", PristineFx(kind, t)) for t in ("bar", "ma8", "ma20")]:
                m = run(strat, data, a, b)
                out.append(f"- {label}: {m.get('trades') or 0} trades, win {m.get('win_rate') or 0:.0%}, PF {m.get('profit_factor') or 0:.2f}, "
                           f"total {m.get('total_return') or 0:+.2%}, maxDD {m.get('max_drawdown') or 0:.1%}")
    return out
