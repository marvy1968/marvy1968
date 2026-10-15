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
from .strategies import DirectionZoneMap, Laddered, Locked, Zoned, AsianBreakout, ConsensusMR, F0Reversion, LienFader, MaCrossBigBuild, Donchian, EmaPullback, NR7Breakout, RangeBoxFade, RangeBox200, RangeMR, RegimeSwitch, TsMom

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
    "fx_box200_m15": ("M15", RangeBox200, {"lookback": [100]}),
    "fx_box200_h4": ("H4", RangeBox200, {"lookback": [100]}),
}

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
