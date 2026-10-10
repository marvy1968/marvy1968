"""Institutional hybrid simulation (NCAAF / CFB), PAPER.

institutional_hybrid_simulation() refines the pick engine's blend (marv/pick_engine.py: power rating + velocity-decayed
trend analyzer) with three adjustments and a skewed Monte Carlo:

  1. Tiered BCR       blue-chip ratio (4/5-star share of the last 4 signing classes, state/cache/cfb_bcr.csv) bucketed
                      into tiers 0 (<0.20) / 1 (0.20-0.45) / 2 (0.45-0.70) / 3 (0.70+); margin += B * (tier_home -
                      tier_away). Elite (tier 3) favourite vs a tier 0-1 underdog = BCR guard: never an upset fade.
  2. Steam            line move open -> current (HOME spread; negative = money on the home side), velocity-decayed so
                      a big move saturates instead of growing linearly: eff = move / (1 + |move| / V0);
                      margin -= S * eff_spread, total += ST * eff_total.
  3. Velocity decay   already inside the trend leg (marv/hybrid.py refined levels: a * d / (1 + |d| / (v0 * sd))), and
                      reused here for the steam term.
  4. Skew-normal MC   10,000 draws of margin / total residuals from scipy skewnorm fits (shape, loc, scale) measured on
                      EARLIER seasons, margin residuals oriented to the projected favourite (blowout tail) ->
                      P(win), P(cover), P(over). Probabilities stay internal (marv/proven.py decides what prints).

Every coefficient (B, S, ST, skew fits) comes from tools/institutional_bt.py fits on earlier seasons only; PARAMS below are
the latest fit (all completed seasons) and are overwritten from state/reports/institutional_backtest.json when present.
Backtest verdict lives in that report and in ANALYSIS.md; nothing here is proven, so no % prints. Never raises.
"""

import json
import math
import time
from pathlib import Path

import numpy as np

SIMS = 10000
BCR_EDGES = (0.20, 0.45, 0.70)
STEAM_V0 = 3.0      # points: velocity-decay scale for line moves (a 3-pt move counts as 1.5, a 10-pt move as 2.3)
STEAM_CAP = 10.0    # ignore bigger open->close gaps (data errors / opener posted before QB news)
REPORT = "reports/institutional_backtest.json"
# defaults (overwritten by the backtest's latest fit)
PARAMS = {"B": 0.0, "S": 0.0, "ST": 0.0,
          "skew_m": [0.0, 0.0, 15.5], "skew_t": [0.0, 0.0, 16.0]}  # skewnorm (a, loc, scale)
_LOADED = {"t": 0.0}


def bcr_tier(b: float | None) -> int | None:
    if b is None or b != b:
        return None
    return sum(b >= e for e in BCR_EDGES)


def decay(x: float | None, v0: float = STEAM_V0) -> float:
    if x is None or x != x or abs(x) > STEAM_CAP:
        return 0.0
    return x / (1 + abs(x) / v0)


def steam(open_: float | None, now: float | None) -> float:
    """Velocity-decayed move open -> now (0 when either line is missing)."""
    if open_ is None or now is None or open_ != open_ or now != now:
        return 0.0
    return decay(now - open_)


def bcr_guard(spread: float | None, th: int | None, ta: int | None) -> str | None:
    """'home'/'away' = elite favourite (tier 3) vs tier 0-1 dog: never fade it. Needs a 6.5+ point favourite."""
    if spread is None or spread != spread or abs(spread) < 6.5 or th is None or ta is None:
        return None
    fav, ft, dt = ("home", th, ta) if spread < 0 else ("away", ta, th)
    return fav if ft == 3 and dt <= 1 else None


def adjust(bm: float, bt: float, th: int | None, ta: int | None, s_open, s_now, t_open, t_now, p: dict) -> tuple:
    """(margin, total, parts) after tiered BCR + steam."""
    tb = 0.0 if th is None or ta is None else p["B"] * (th - ta)
    es, et = steam(s_open, s_now), steam(t_open, t_now)
    m = bm + tb - p["S"] * es
    t = bt + p["ST"] * et
    return m, t, {"bcr_pts": round(tb, 2), "steam_spread": round(es, 2), "steam_total": round(et, 2),
                  "steam_pts": round(-p["S"] * es, 2), "steam_tot_pts": round(p["ST"] * et, 2)}


def simulate(m: float, t: float, p: dict, spread=None, line=None, n: int = SIMS, seed: int = 0) -> dict:
    """Skew-normal Monte Carlo. Margin residual skew is oriented to the projected favourite."""
    from scipy.stats import skewnorm
    rng = np.random.default_rng(seed)
    am, lm, sm = p["skew_m"]
    at, lt, stt = p["skew_t"]
    sg = 1.0 if m >= 0 else -1.0
    M = m + sg * skewnorm.rvs(am, lm, sm, size=n, random_state=rng)
    T = t + skewnorm.rvs(at, lt, stt, size=n, random_state=rng)
    out = {"p_home": float((M > 0).mean())}
    if spread is not None and spread == spread:
        c = M + spread
        c = c[np.abs(c) > 1e-9]
        out["p_home_cover"] = float((c > 0).mean()) if len(c) else 0.5
    if line is not None and line == line:
        o = T - line
        o = o[np.abs(o) > 1e-9]
        out["p_over"] = float((o > 0).mean()) if len(o) else 0.5
    return out


def institutional_hybrid_simulation(bm: float, bt: float, bcr_home: float | None = None, bcr_away: float | None = None,
                                    spread: float | None = None, line: float | None = None,
                                    spread_open: float | None = None, line_open: float | None = None,
                                    params: dict | None = None, n: int = SIMS, seed: int = 0) -> dict:
    """One game. bm / bt = pick-engine blend margin (HOME) / total; spread = current HOME spread. Pure function."""
    p = params or PARAMS
    th, ta = bcr_tier(bcr_home), bcr_tier(bcr_away)
    m, t, parts = adjust(bm, bt, th, ta, spread_open, spread, line_open, line, p)
    sim = simulate(m, t, p, spread, line, n=n, seed=seed)
    return {"margin": round(m, 1), "total": round(t, 1), "tiers": (th, ta), **parts, "sim": sim,
            "bcr_guard": bcr_guard(spread, th, ta)}


# ------------------------------------------------------------------ live helpers
def load(state_dir: Path) -> dict:
    if time.time() - _LOADED["t"] < 1800:
        return PARAMS
    _LOADED["t"] = time.time()
    try:
        rep = json.loads((Path(state_dir) / REPORT).read_text())
        lf = rep.get("latest_fit")
        if lf:
            PARAMS.update({k: lf[k] for k in PARAMS if k in lf})
    except (OSError, ValueError, KeyError):
        pass
    return PARAMS


def opener(state_dir: Path, home: str, away: str) -> tuple[float | None, float | None]:
    """Opening (first-seen) HOME spread / total for a CFB game from state/lines.json (marv/linemove.py)."""
    try:
        from .data.teams import similarity
        d = json.loads((Path(state_dir) / "lines.json").read_text())
        best, sc = None, 0.0
        for k, v in d.items():
            if not k.startswith(("cfb:", "ncaaf:")):
                continue
            s = min(similarity(v.get("home", ""), home), similarity(v.get("away", ""), away))
            if s > sc:
                best, sc = v, s
        if best and sc >= 0.75:
            return (best.get("spread_open_src", best.get("spread")), best.get("total_open_src", best.get("total")))
    except Exception:  # noqa: BLE001
        pass
    return None, None


def text(r: dict, home: str, away: str) -> str:
    """No %: 'Institutional: margin Texas +6.2 (BCR +1.4, steam +0.8) · total 52 · skew-MC lean ...'."""
    lead = home if r["margin"] >= 0 else away
    out = f"Institutional sim: {lead} {abs(r['margin']):.1f}"
    bits = []
    if r.get("bcr_pts"):
        bits.append(f"BCR tiers {r['tiers'][0]}/{r['tiers'][1]} {r['bcr_pts']:+.1f}")
    if r.get("steam_pts"):
        bits.append(f"steam {r['steam_pts']:+.1f}")
    if bits:
        out += " (" + ", ".join(bits) + ")"
    out += f" · total {r['total']:.0f}"
    if r.get("bcr_guard"):
        out += f" · BCR guard {home if r['bcr_guard'] == 'home' else away}: no upset fade"
    return out + " · paper, unproven"
