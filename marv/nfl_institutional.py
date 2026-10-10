"""NFL institutional simulation, PAPER: offensive-line health + velocity-decayed steam + skew-normal MC (std 13.5).

nfl_institutional_simulation() sits on the NFL pick-engine blend (marv/pick_engine.py: power rating + trend analyzer),
the same way marv/institutional.py does for college football:

  1. O-line health   expected starting five = top-5 linemen (T/G/C/OL) by offensive snap share over the team's last 4
                     games, share >= 0.50 (nflverse snap counts, this season only, before this week). Lost = sum of their
                     usual shares for starters ruled Out / Doubtful (1.0), Questionable (Q_WEIGHT) on this week's report,
                     or absent from the team's previous game and not cleared on the report (IR / injured). health = 1 -
                     lost / 5. margin += O * (lost_away - lost_home); total += OT * (lost_home + lost_away).
  2. Steam           Bovada open -> current line (HOME spread / total), velocity-decayed (marv/institutional.py decay():
                     move / (1 + |move| / 3), moves past 10 ignored); margin -= S * steam_spread, total += ST * steam_tot.
  3. Skew-normal MC  10,000 draws. Margin residual: skewnorm shape fitted on EARLIER seasons, oriented to the projected
                     favourite, rescaled to mean 0 and std SD_MARGIN = 13.5 (owner setting); total residual: skewnorm fit
                     (shape / loc / scale) on earlier seasons -> P(win), P(cover), P(over). Internal only.

O / OT / S / ST and the skew fits come from tools/nfl_institutional_bt.py (walk-forward, earlier seasons only); PARAMS are
overwritten by state/reports/nfl_institutional_backtest.json "latest_fit". Nothing here is proven, so nothing prints a %.
env INSTITUTIONAL_NFL=0 falls back to the plain pick-engine normal MC. Never raises in the live helpers.
"""

import json
import math
import re
import time
from pathlib import Path

import numpy as np

from .institutional import steam

SIMS = 10000
SD_MARGIN = 13.5
OL_POS = {"T", "G", "C", "OL", "OT", "OG"}
Q_WEIGHT = 0.25
REPORT = "reports/nfl_institutional_backtest.json"
PARAMS = {"O": 0.0, "OT": 0.0, "S": 0.0, "ST": 0.0, "skew_a": 0.0, "sd_m": SD_MARGIN,
          "skew_t": [0.0, 0.0, 13.5]}
_LOADED = {"t": 0.0}
_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b\.?")
TEAM_FIX = {"LAR": "LA", "STL": "LA", "SD": "LAC", "OAK": "LV"}


def _key(n) -> str:
    n = str(n).lower().replace(".", "").replace("'", "").replace("-", " ")
    return " ".join(_SUFFIX.sub("", n).split())


# ------------------------------------------------------------------ 1. o-line health
def ol_lost(snaps, inj, team: str, week: int) -> float | None:
    """Usual-snap-share-weighted starting OL missing for TEAM in WEEK (0..5). None = no earlier games this season.
    snaps / inj: one season's nflverse frames (team already normalised, 'key' = cleaned name)."""
    h = snaps[(snaps.team == team) & (snaps.week < week)]
    if h.empty:
        return None
    wk = sorted(h.week.unique())[-4:]
    h = h[h.week.isin(wk) & h.position.isin(OL_POS)]
    usual = h.groupby("key").offense_pct.sum() / len(wk)
    starters = usual[usual >= 0.5].sort_values(ascending=False).head(5)
    if starters.empty:
        return 0.0
    last = set(h[h.week == wk[-1]].key)
    rep = inj[(inj.team == team) & (inj.week == week)]
    status = dict(zip(rep.key, rep.report_status.fillna("")))
    lost = 0.0
    for k, u in starters.items():
        s = status.get(k)
        w = 1.0 if s in ("Out", "Doubtful") else Q_WEIGHT if s == "Questionable" else 0.0
        if s is None and k not in last:
            w = 1.0  # missed last game and not on this week's report -> still out (IR)
        lost += w * float(u)
    return round(lost, 3)


def load_season(cache: Path, season: int):
    import pandas as pd
    s = pd.read_csv(Path(cache) / f"nfl_snaps_{season}.csv", low_memory=False,
                    usecols=["week", "player", "position", "team", "offense_pct", "game_type"])
    i = pd.read_csv(Path(cache) / f"nfl_injuries_{season}.csv", low_memory=False,
                    usecols=["week", "full_name", "team", "report_status"])
    for d in (s, i):
        d["team"] = d.team.map(lambda t: TEAM_FIX.get(str(t), str(t)))
    s["key"] = s.player.map(_key)
    i["key"] = i.full_name.map(_key)
    s["offense_pct"] = s.offense_pct.fillna(0.0)
    return s, i


# ------------------------------------------------------------------ 2/3. adjust + skew-normal MC
def adjust(bm: float, bt: float, lost_h, lost_a, s_open, s_now, t_open, t_now, p: dict) -> tuple:
    lh = 0.0 if lost_h is None or lost_h != lost_h else float(lost_h)
    la = 0.0 if lost_a is None or lost_a != lost_a else float(lost_a)
    es, et = steam(s_open, s_now), steam(t_open, t_now)
    ol_m, ol_t = p["O"] * (la - lh), p["OT"] * (lh + la)
    m = bm + ol_m - p["S"] * es
    t = bt + ol_t + p["ST"] * et
    return m, t, {"ol_lost": (round(lh, 2), round(la, 2)), "ol_pts": round(ol_m, 2), "ol_tot_pts": round(ol_t, 2),
                  "steam_spread": round(es, 2), "steam_total": round(et, 2), "steam_pts": round(-p["S"] * es, 2),
                  "steam_tot_pts": round(p["ST"] * et, 2)}


def skew_params(a: float, sd: float = SD_MARGIN) -> tuple[float, float, float]:
    """(a, loc, scale) of a skew-normal with shape a, mean 0 and standard deviation sd."""
    d = a / math.sqrt(1 + a * a)
    scale = sd / math.sqrt(1 - 2 * d * d / math.pi)
    return a, -scale * d * math.sqrt(2 / math.pi), scale


def simulate(m: float, t: float, p: dict, spread=None, line=None, n: int = SIMS, seed: int = 0) -> dict:
    from scipy.stats import skewnorm
    rng = np.random.default_rng(seed)
    a, loc, sc = skew_params(p.get("skew_a", 0.0), p.get("sd_m", SD_MARGIN))
    at, lt, st = p["skew_t"]
    sg = 1.0 if m >= 0 else -1.0
    M = m + sg * skewnorm.rvs(a, loc, sc, size=n, random_state=rng)
    T = t + skewnorm.rvs(at, lt, st, size=n, random_state=rng)
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


def nfl_institutional_simulation(bm: float, bt: float, ol_lost_home: float | None = None,
                                 ol_lost_away: float | None = None, spread: float | None = None,
                                 line: float | None = None, spread_open: float | None = None,
                                 line_open: float | None = None, params: dict | None = None, n: int = SIMS,
                                 seed: int = 0) -> dict:
    """One NFL game. bm / bt = pick-engine blend margin (HOME) / total; spread = current HOME spread. Pure."""
    p = params or PARAMS
    m, t, parts = adjust(bm, bt, ol_lost_home, ol_lost_away, spread_open, spread, line_open, line, p)
    sim = simulate(m, t, p, spread, line, n=n, seed=seed)
    return {"margin": round(m, 1), "total": round(t, 1), **parts,
            "ol_health": tuple(None if x is None else round(1 - x / 5, 2) for x in (ol_lost_home, ol_lost_away)),
            "sim": sim}


# ------------------------------------------------------------------ live helpers
def load(state_dir: Path) -> dict:
    if time.time() - _LOADED["t"] < 1800:
        return PARAMS
    _LOADED["t"] = time.time()
    try:
        lf = json.loads((Path(state_dir) / REPORT).read_text()).get("latest_fit")
        if lf:
            PARAMS.update({k: lf[k] for k in PARAMS if k in lf})
    except (OSError, ValueError, KeyError):
        pass
    return PARAMS


def opener(state_dir: Path, home: str, away: str) -> tuple[float | None, float | None]:
    """First-seen HOME spread / total for an NFL game from state/lines.json (marv/linemove.py)."""
    try:
        from .data.teams import similarity
        from datetime import datetime, timedelta, timezone
        d = json.loads((Path(state_dir) / "lines.json").read_text())
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        best, sc = None, 0.0
        for k, v in d.items():
            if not k.startswith("nfl:") or str(v.get("start", "9999")) < recent:
                continue
            s = min(similarity(v.get("home", ""), home), similarity(v.get("away", ""), away))
            if s > sc:
                best, sc = v, s
        if best and sc >= 0.75:
            return (best.get("spread_open_src", best.get("spread")), best.get("total_open_src", best.get("total")))
    except Exception:  # noqa: BLE001
        pass
    return None, None


_OL: dict = {}


def ol_for_game(state_dir: Path, home: str, away: str, as_of=None) -> tuple[float | None, float | None]:
    """(lost_home, lost_away) for the team's next unplayed game (nflverse schedule). Cached 30 min. Never raises."""
    try:
        import pandas as pd
        from datetime import datetime, timezone
        from .data.nflverse import NFL_TEAMS
        cache = Path(state_dir) / "cache"
        as_of = as_of or datetime.now(timezone.utc)
        abbr = {v: k for k, v in NFL_TEAMS.items()}
        g = pd.read_csv(cache / "nflverse_games.csv", usecols=["season", "week", "gameday", "home_team", "away_team",
                                                               "home_score"])
        h, a = abbr.get(home, home), abbr.get(away, away)
        day = pd.Timestamp(as_of).tz_localize(None) if pd.Timestamp(as_of).tzinfo else pd.Timestamp(as_of)
        r = g[(g.home_team == h) & (g.away_team == a) & (pd.to_datetime(g.gameday) >= day.normalize() - pd.Timedelta(days=1))]
        if r.empty:
            return None, None
        season, week = int(r.season.iloc[0]), int(r.week.iloc[0])
        key = (str(state_dir), season, week, h, a)
        hit = _OL.get(key)
        if hit and time.time() - hit[0] < 1800:
            return hit[1]
        s, i = load_season(cache, season)
        out = (ol_lost(s, i, TEAM_FIX.get(h, h), week), ol_lost(s, i, TEAM_FIX.get(a, a), week))
        _OL[key] = (time.time(), out)
        return out
    except Exception:  # noqa: BLE001
        return None, None
