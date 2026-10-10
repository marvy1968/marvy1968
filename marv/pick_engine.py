"""Marv pick engine (CFB / NFL / WNBA): power rating + trend analyzer + Monte Carlo, combined into ONE pick.

  1. Power rating   opponent-adjusted offense / defense ratings, weighted ridge least squares on every finished game of
                    this and last season (exponential time decay, home edge fitted, CFB margins past 28 dampened):
                    home pts = mean + off[home] + def[away] + hfa/2 -> power margin and power total.
  2. Trend analyzer marv/hybrid.py refined engine: trend-catcher modifiers (last-3 turnover spike / ypp or rebound
                    margin drop), velocity-decayed category levels, winner-take-all category matrix -> trend margin;
                    restricted Monte Carlo on pace x points-per-possession -> trend total.
  3. Blend          margin = b0*home + b1*power + b2*trend, total = c0 + c1*power_total + c2*trend_total; weights are
                    least-squares fits on EARLIER seasons only (tools/pick_engine_bt.py -> BLEND below).
  4. Monte Carlo    10,000 correlated (margin, total) draws around the blend with the residual sd / correlation measured
                    on earlier seasons -> P(win), P(cover), P(over). Used ONLY to choose and rank the pick.

The pick = the side each market's simulation favours; the "best pick" is the market whose simulated side is furthest
from a coin flip. Every probability stays internal: a % prints only through marv/proven.py (walk-forward bar), and
the backtest (state/reports/pick_engine_backtest.json) decides that. Offline (cached files only), never raises.
"""

import math
import time
from pathlib import Path

import numpy as np

SPORTS = ("cfb", "nfl", "wnba")
HALF_LIFE = {"cfb": 120.0, "nfl": 150.0, "wnba": 60.0}  # days
MOV_CAP = {"cfb": 28.0}
SHRINK = {"cfb": 3.0, "nfl": 3.0, "wnba": 4.0}         # pseudo-games of an average team (ridge)
HFA_PRIOR = {"cfb": 3.0, "nfl": 2.0, "wnba": 2.0}
SIMS = 10000
# NCAAF path: institutional hybrid simulation (marv/institutional.py) on top of the blend. PAPER: picks are leans with no
# % (marv/proven.py); env INSTITUTIONAL_CFB=0 falls back to the plain normal Monte Carlo.
import os as _os
INSTITUTIONAL_CFB = _os.environ.get("INSTITUTIONAL_CFB", "1").lower() not in ("0", "false", "off", "no")
# NFL path: marv/nfl_institutional.py (O-line health + steam + skew-normal MC, margin std 13.5). PAPER, no %;
# env INSTITUTIONAL_NFL=0 falls back to the plain normal Monte Carlo.
INSTITUTIONAL_NFL = _os.environ.get("INSTITUTIONAL_NFL", "1").lower() not in ("0", "false", "off", "no")
# WNBA path: marv/wnba_institutional.py (net rating + rolling TS / def TS + rotation depth + steam + skew-normal MC,
# margin std 7.5). PAPER, no %; env INSTITUTIONAL_WNBA=0 falls back to the plain normal Monte Carlo.
INSTITUTIONAL_WNBA = _os.environ.get("INSTITUTIONAL_WNBA", "1").lower() not in ("0", "false", "off", "no")
# Walk-forward blend fits (tools/pick_engine_bt.py, latest = fit on every completed season before the current one).
# Overwritten by the backtest's latest fit; these are the defaults used when the report is missing.
BLEND = {
    "cfb": {"side": [2.0, 0.5, 0.5], "tot": [0.0, 0.5, 0.5], "sd_m": 15.5, "sd_t": 16.0, "rho": 0.0},
    "nfl": {"side": [1.5, 0.5, 0.5], "tot": [0.0, 0.5, 0.5], "sd_m": 13.0, "sd_t": 13.5, "rho": 0.0},
    "wnba": {"side": [1.5, 0.5, 0.5], "tot": [0.0, 0.5, 0.5], "sd_m": 11.5, "sd_t": 16.0, "rho": 0.0},
}
REPORT = "reports/pick_engine_backtest.json"
_CACHE: dict = {}
_TTL = 1800


# ------------------------------------------------------------------ game results
def results(cache: Path, sport: str, seasons) -> "pd.DataFrame":
    """game_id, season, date, home, away, neutral, hp, ap for finished games."""
    import pandas as pd
    cols = ["game_id", "season", "date", "home", "away", "neutral", "hp", "ap"]
    if sport == "cfb":
        out = []
        for s in seasons:
            f = cache / f"cfb_games_{s}.parquet"
            if f.exists():
                g = pd.read_parquet(f, columns=["game_id", "date", "home", "away", "neutral", "home_points", "away_points"])
                g["season"] = s
                out.append(g)
        if not out:
            return pd.DataFrame(columns=cols)
        g = pd.concat(out, ignore_index=True)
        g = g[g.home_points.notna() & g.away_points.notna()]
        d = pd.DataFrame({"game_id": g.game_id.astype(str), "season": g.season.astype(int),
                          "date": pd.to_datetime(g.date, utc=True).dt.tz_convert(None),
                          "home": g.home, "away": g.away, "neutral": g.neutral.fillna(False).astype(bool),
                          "hp": g.home_points.astype(float), "ap": g.away_points.astype(float)})
        return d.reset_index(drop=True)
    if sport == "nfl":
        from .data.nflverse import NFL_TEAMS
        f = cache / "nflverse_games.csv"
        if not f.exists():
            return pd.DataFrame(columns=cols)
        g = pd.read_csv(f, usecols=["game_id", "season", "gameday", "home_team", "away_team", "home_score",
                                    "away_score", "location"])
        g = g[g.home_score.notna() & g.season.isin(list(seasons))]
        return pd.DataFrame({"game_id": g.game_id.astype(str), "season": g.season.astype(int),
                             "date": pd.to_datetime(g.gameday), "home": g.home_team.map(lambda a: NFL_TEAMS.get(a, a)),
                             "away": g.away_team.map(lambda a: NFL_TEAMS.get(a, a)), "neutral": g.location.eq("Neutral"),
                             "hp": g.home_score.astype(float), "ap": g.away_score.astype(float)}).reset_index(drop=True)
    from . import hybrid as HY
    tg = HY.bb_team_games(cache, sport, seasons)
    if tg.empty:
        return pd.DataFrame(columns=cols)
    h = tg[tg.is_home == 1]
    return pd.DataFrame({"game_id": h.game_id.astype(str), "season": h.season.astype(int), "date": h.date,
                         "home": h.team, "away": h.opp, "neutral": False, "hp": h.pts.astype(float),
                         "ap": h.pa.astype(float)}).reset_index(drop=True)


# ------------------------------------------------------------------ 1. power rating
def fit_power(res, as_of, sport: str) -> dict | None:
    """Weighted ridge fit on games strictly before as_of. {'mean','hfa','off','def','n'} or None."""
    import pandas as pd
    as_of = pd.Timestamp(as_of)
    g = res[res.date < as_of.normalize()]
    if len(g) < 30:
        return None
    teams = sorted(set(g.home) | set(g.away))
    ix = {t: i for i, t in enumerate(teams)}
    T = len(teams)
    hp, ap = g.hp.to_numpy(float), g.ap.to_numpy(float)
    cap = MOV_CAP.get(sport)
    if cap:
        mg, tot = hp - ap, hp + ap
        big = np.abs(mg) > cap
        mg = np.where(big, np.sign(mg) * (cap + np.sqrt(np.maximum(np.abs(mg) - cap, 0))), mg)
        hp, ap = (tot + mg) / 2, (tot - mg) / 2
    age = (as_of.normalize() - g.date).dt.days.to_numpy(float)
    w = 0.5 ** (age / HALF_LIFE[sport])
    s = np.where(g.neutral.to_numpy(bool), 0.0, 0.5)
    hi = g.home.map(ix).to_numpy()
    ai = g.away.map(ix).to_numpy()
    n = len(g)
    # rows: [home scoring, away scoring]; params: mean, hfa, off[T], def[T]
    P = 2 + 2 * T
    X = np.zeros((2 * n, P))
    r = np.arange(n)
    X[:, 0] = 1
    X[r, 1], X[n + r, 1] = s, -s
    X[r, 2 + hi], X[r, 2 + T + ai] = 1, 1
    X[n + r, 2 + ai], X[n + r, 2 + T + hi] = 1, 1
    y = np.concatenate([hp, ap])
    W = np.concatenate([w, w])
    XtW = X.T * W
    A = XtW @ X
    b = XtW @ y
    lam = np.zeros(P)
    lam[2:] = SHRINK[sport]
    lam[1] = 50.0
    b[1] += 50.0 * HFA_PRIOR[sport]
    beta = np.linalg.solve(A + np.diag(lam), b)
    off, dfn = beta[2:2 + T], beta[2 + T:]
    played = {}
    for t in list(g.home) + list(g.away):
        played[t] = played.get(t, 0) + 1
    return {"mean": float(beta[0]), "hfa": float(beta[1]), "off": dict(zip(teams, off - off.mean())),
            "def": dict(zip(teams, dfn - dfn.mean())), "n": played,
            "mean_adj": float(beta[0] + off.mean() + dfn.mean())}


def power_read(pw: dict, home: str, away: str, neutral: bool = False) -> tuple[float, float] | None:
    if not pw or home not in pw["off"] or away not in pw["off"]:
        return None
    h = 0.0 if neutral else pw["hfa"]
    m = pw["off"][home] + pw["def"][away] - pw["off"][away] - pw["def"][home] + h
    t = 2 * pw["mean_adj"] + pw["off"][home] + pw["def"][away] + pw["off"][away] + pw["def"][home]
    return float(m), float(t)


# ------------------------------------------------------------------ 3/4. blend + Monte Carlo
def blend(sport: str, power_m: float, trend_m: float, power_t: float, trend_t: float, neutral: bool,
          cfg: dict | None = None) -> tuple[float, float]:
    c = cfg or BLEND[sport]
    b, t = c["side"], c["tot"]
    return (b[0] * (0.0 if neutral else 1.0) + b[1] * power_m + b[2] * trend_m,
            t[0] + t[1] * power_t + t[2] * trend_t)


def simulate(margin: float, total: float, sd_m: float, sd_t: float, rho: float, spread: float | None = None,
             line: float | None = None, n: int = SIMS, seed: int = 0) -> dict:
    """spread = HOME spread (negative = home favoured). Probabilities are internal (never printed unproven)."""
    rng = np.random.default_rng(seed)
    z1 = rng.standard_normal(n)
    z2 = rho * z1 + math.sqrt(max(0.0, 1 - rho * rho)) * rng.standard_normal(n)
    M = margin + sd_m * z1
    T = total + sd_t * z2
    out = {"p_home": float((M > 0).mean())}
    if spread is not None and spread == spread:
        c = M + spread
        dec = c != 0
        out["p_home_cover"] = float((c[dec] > 0).mean()) if dec.any() else 0.5
    if line is not None and line == line:
        o = T - line
        dec = o != 0
        out["p_over"] = float((o[dec] > 0).mean()) if dec.any() else 0.5
    return out


def decide(sim: dict, margin: float, total: float, spread: float | None, line: float | None) -> dict:
    """Sides per market + the best market (simulated side furthest from 50%)."""
    picks = {"ml": "home" if sim["p_home"] >= 0.5 else "away"}
    strength = {"ml": abs(sim["p_home"] - 0.5)}
    if "p_home_cover" in sim:
        picks["spread"] = "home" if sim["p_home_cover"] >= 0.5 else "away"
        strength["spread"] = abs(sim["p_home_cover"] - 0.5)
    if "p_over" in sim:
        picks["total"] = "over" if sim["p_over"] >= 0.5 else "under"
        strength["total"] = abs(sim["p_over"] - 0.5)
    against = {k: v for k, v in strength.items() if k != "ml"}
    best = max(against, key=against.get) if against else "ml"
    return {"picks": picks, "strength": strength, "best": best}


def tier(x: float) -> str:
    """Word label for how far the simulation leans (NOT a probability)."""
    return "strong lean" if x >= 0.10 else "lean" if x >= 0.05 else "slight lean"


# ------------------------------------------------------------------ live
def _load_blend(state_dir: Path) -> None:
    import json
    key = ("blend", str(state_dir))
    if key in _CACHE and time.time() - _CACHE[key] < _TTL:
        return
    _CACHE[key] = time.time()
    try:
        rep = json.loads((Path(state_dir) / REPORT).read_text())
        for sp in SPORTS:
            lf = (rep.get(sp) or {}).get("latest_fit")
            if lf:
                BLEND[sp] = lf
    except (OSError, ValueError):
        pass


def _fit_power(state_dir: Path, sport: str, as_of) -> dict | None:
    from . import hybrid as HY
    try:
        s = HY.season_for(sport, as_of)
        res = results(Path(state_dir) / "cache", sport, [s - 1, s])
        return fit_power(res, as_of.replace(tzinfo=None) if as_of.tzinfo else as_of, sport)
    except Exception:  # noqa: BLE001
        return None


def power(state_dir: Path, sport: str, as_of=None, block: bool = True) -> dict | None:
    """Power ratings as of today (cached 30 min). block=False (overlay path): never fits in the request; a cold or stale
    cache is refreshed in a background thread and the last fit (or None) is returned right away."""
    import threading
    from datetime import datetime, timezone
    as_of = as_of or datetime.now(timezone.utc)
    key = (str(state_dir), sport, as_of.strftime("%Y-%m-%d"))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    if not block:
        if not _CACHE.get(("busy",) + key):
            _CACHE[("busy",) + key] = True

            def run():
                try:
                    _CACHE[key] = (time.time(), _fit_power(state_dir, sport, as_of))
                finally:
                    _CACHE.pop(("busy",) + key, None)
            threading.Thread(target=run, daemon=True).start()
        return hit[1] if hit else None
    pw = _fit_power(state_dir, sport, as_of)
    _CACHE[key] = (time.time(), pw)
    return pw


def warm(state_dir: Path) -> None:
    for sp in SPORTS:
        power(state_dir, sp)


def _name(pw: dict, team: str) -> str | None:
    from .data.teams import similarity
    if team in pw["off"]:
        return team
    best = max(pw["off"], key=lambda t: similarity(t, team))
    return best if similarity(best, team) >= 0.75 else None


def game(state_dir: Path, sport: str, home: str, away: str, spread: float | None = None, line: float | None = None,
         neutral: bool = False, as_of=None, block: bool = True) -> dict | None:
    """Combined pick for one game or None (missing data). spread = HOME spread. Never raises."""
    from . import hybrid as HY
    if sport not in SPORTS:
        return None
    try:
        _load_blend(state_dir)
        pw = power(state_dir, sport, as_of, block)
        if not pw:
            return None
        hn, an = _name(pw, home), _name(pw, away)
        if not hn or not an:
            return None
        pr = power_read(pw, hn, an, neutral)
        hy = HY.game(state_dir, sport, home, away, spread, line, neutral, as_of)
        if not pr or not hy:
            return None
        trend_t = hy["mc"]["median"]
        m, t = blend(sport, pr[0], hy["proj_margin"], pr[1], trend_t, neutral)
        c = BLEND[sport]
        inst = None
        if sport == "cfb" and INSTITUTIONAL_CFB:
            inst = _institutional(state_dir, home, away, m, t, spread, line, as_of)
        elif sport == "nfl" and INSTITUTIONAL_NFL:
            inst = _nfl_institutional(state_dir, home, away, m, t, spread, line, as_of)
        elif sport == "wnba" and INSTITUTIONAL_WNBA:
            inst = _wnba_institutional(state_dir, home, away, m, t, spread, line, as_of)
        if inst:  # NCAAF: tiered BCR + steam (velocity-decayed) + skew-normal MC replace the normal MC (paper)
            m, t, sim = inst["margin"], inst["total"], inst["sim"]
        else:
            sim = simulate(m, t, c["sd_m"], c["sd_t"], c.get("rho", 0.0), spread, line)
        d = decide(sim, m, t, spread, line)
        agree = {"power": pr[0] > 0, "trend": hy["proj_margin"] > 0, "mc": sim["p_home"] >= 0.5}
        ats_agree = None
        if spread is not None and spread == spread:
            ats_agree = {"power": pr[0] + spread > 0, "trend": hy["proj_margin"] + spread > 0,
                         "mc": sim.get("p_home_cover", 0.5) >= 0.5}
        return {"home": home, "away": away, "power_margin": round(pr[0], 1), "power_total": round(pr[1], 1),
                "trend_margin": hy["proj_margin"], "trend_total": trend_t, "mod_home": hy["mod_home"],
                "mod_away": hy["mod_away"], "margin": round(m, 1), "total": round(t, 1), "spread": spread,
                "line": line, "sim": sim, **d, "ml_agree": agree, "ats_agree": ats_agree, "institutional": inst}
    except Exception:  # noqa: BLE001
        return None


def _institutional(state_dir: Path, home: str, away: str, m: float, t: float, spread, line, as_of) -> dict | None:
    """marv/institutional.py read on top of the blend (CFB). None on any missing piece -> plain MC. Never raises."""
    from datetime import datetime, timezone
    from . import hybrid as HY
    from . import institutional as IN
    try:
        IN.load(state_dir)
        s = HY.season_for("cfb", as_of or datetime.now(timezone.utc))
        bh, ba = HY.cfb_bcr(state_dir, home, s), HY.cfb_bcr(state_dir, away, s)
        so, to = IN.opener(state_dir, home, away)
        return IN.institutional_hybrid_simulation(m, t, bh[0] if bh else None, ba[0] if ba else None, spread, line,
                                                  so, to)
    except Exception:  # noqa: BLE001
        return None


def _nfl_institutional(state_dir: Path, home: str, away: str, m: float, t: float, spread, line,
                       as_of) -> dict | None:
    """marv/nfl_institutional.py read on top of the blend (NFL). None on failure -> plain MC. Never raises."""
    from . import nfl_institutional as NI
    try:
        p = NI.load(state_dir)
        lh, la = NI.ol_for_game(state_dir, home, away, as_of)
        so, to = NI.opener(state_dir, home, away)
        r = NI.nfl_institutional_simulation(m, t, lh, la, spread, line, so, to, p)
        r["tiers"] = (None, None)
        return r
    except Exception:  # noqa: BLE001
        return None


def _wnba_institutional(state_dir: Path, home: str, away: str, m: float, t: float, spread, line,
                        as_of) -> dict | None:
    """marv/wnba_institutional.py read on top of the blend (WNBA). None on failure -> plain MC. Never raises."""
    from . import wnba_institutional as WI
    try:
        p = WI.load(state_dir)
        f = WI.live_features(state_dir, home, away, as_of)
        so, to = WI.opener(state_dir, home, away)
        r = WI.wnba_institutional_simulation(m, t, f, spread, line, so, to, p)
        r["tiers"] = (None, None)
        return r
    except Exception:  # noqa: BLE001
        return None


def text(r: dict, sport: str) -> str:
    """One line, no %: 'Pick engine: ML Texas (power +6.1 / trend +4.0 / blend +5.3, 3/3 agree) · best: Under 51.5 (lean)'."""
    sh = (lambda s: s.split()[-1] if sport == "nfl" and len(s.split()) > 1 else s)
    home, away = r["home"], r["away"]
    side = home if r["picks"]["ml"] == "home" else away
    sg = 1 if r["picks"]["ml"] == "home" else -1
    n_ag = sum(v == (r["picks"]["ml"] == "home") for v in r["ml_agree"].values())
    out = (f"Pick engine: ML {sh(side)} (power {sg * r['power_margin']:+.1f} / trend {sg * r['trend_margin']:+.1f} / "
           f"blend {sg * r['margin']:+.1f}, {n_ag}/3 agree)")
    if "spread" in r["picks"]:
        ps = home if r["picks"]["spread"] == "home" else away
        sp = r["spread"] if r["picks"]["spread"] == "home" else -r["spread"]
        out += f" · ATS {sh(ps)} {sp:+g} ({tier(r['strength']['spread'])})"
    if "total" in r["picks"]:
        out += f" · {r['picks']['total'].title()} {r['line']:g} (blend total {r['total']:.0f}, {tier(r['strength']['total'])})"
    if r["best"] != "ml":
        out += f" · best: {'ATS' if r['best'] == 'spread' else 'O/U'}"
    inst = r.get("institutional")
    if inst:
        bits = [f"BCR tiers {inst['tiers'][0]}/{inst['tiers'][1]}"] if None not in inst.get("tiers", (None,)) else []
        ol = inst.get("ol_lost")
        if ol and max(ol) >= 0.5:
            bits.append(f"OL starters out {sh(home)} {ol[0]:.1f} / {sh(away)} {ol[1]:.1f}")
        f = inst.get("features") or {}
        if sport == "wnba" and f.get("net_d") is not None:
            bits.append(f"net rtg {sg * f['net_d']:+.1f}/100")
        if sport == "wnba" and f.get("miss_d") is not None and abs(f["miss_d"]) >= 0.5:
            bits.append(f"rotation mins out {sg * -f['miss_d']:+.1f} starters")
        if inst.get("steam_pts"):
            bits.append(f"steam {sg * inst['steam_pts']:+.1f}")
        out += " · institutional skew-MC" + (f" ({', '.join(bits)})" if bits else "")
        if inst.get("bcr_guard"):
            out += f" · BCR guard {sh(home if inst['bcr_guard'] == 'home' else away)}: no upset fade"
    return out
