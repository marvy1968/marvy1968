"""Defense quality + recent defensive trend for the Marv H2H insight (NFL + college football).

Reads only box scores Marv already caches (no network, safe inside the 1.5 s overlay timeout):
  NFL: state/cache/nflverse_games.csv (points allowed) + nfl_stats_team_week_<season>.csv (yards allowed =
       opponent's passing + rushing yards)
  CFB: state/cache/cfb_games_<season>.parquet (points) + cfb_teamgames_<season>.parquet (yards, CFBD/ESPN box)
A team's profile compares its points/yards allowed per game over the LAST 3 games with its season average and
ranks the season numbers against the league (FBS only for CFB).
  bad    = season points OR yards allowed per game in the league's worst quarter
  fading = last-3 allowed is worse than the season average by >= FADE_PTS points or >= FADE_YDS yards per game
           and the last-3 points allowed are at or above the league average
Descriptive only: no probability comes from here (marv/proven.py still gates any %).
"""

import time
from pathlib import Path

# heavy favourite: ML -200 or shorter, or favoured by this many points on the spread / Marv margin (~-200 equivalent)
HEAVY = {"nfl": 4.5, "cfb": 6.5, "nba": 7.5, "wnba": 7.5, "ncaab": 10.5, "ncaaw": 12.5, "euroleague": 7.5}
HEAVY_ML = -200
HEAVY_MARGIN = {"nfl": 7.0, "cfb": 10.0, "nba": 9.0, "wnba": 9.0, "ncaab": 12.0, "ncaaw": 15.0, "euroleague": 9.0}  # Marv / ratings margin counted as "large"
FADE_PTS = {"nfl": 3.0, "cfb": 5.0}
FADE_YDS = {"nfl": 30.0, "cfb": 40.0}
MIN_GAMES = 3
_CACHE: dict = {}
_TTL = 1800


def _nfl_table(cache: Path, as_of):
    import pandas as pd
    from .data.nflverse import NFL_TEAMS
    g = pd.read_csv(cache / "nflverse_games.csv", usecols=["game_id", "season", "game_type", "gameday", "away_team",
                                                           "home_team", "away_score", "home_score"])
    g = g[g.home_score.notna() & (g.gameday < as_of.strftime("%Y-%m-%d")) & g.game_type.isin(["REG", "WC", "DIV", "CON", "SB"])]
    if g.empty:
        return {}
    season = int(g.season.max())
    g = g[g.season == season]
    yds = {}
    f = cache / f"nfl_stats_team_week_{season}.csv"
    if f.exists():
        s = pd.read_csv(f, usecols=["game_id", "team", "passing_yards", "rushing_yards"])
        for r in s.itertuples():  # yards GAINED by r.team -> yards ALLOWED by its opponent in that game
            yds[(r.game_id, r.team)] = float(r.passing_yards or 0) + float(r.rushing_yards or 0)
    out: dict = {}
    for r in g.sort_values("gameday").itertuples():
        for t, o, pa in ((r.home_team, r.away_team, r.away_score), (r.away_team, r.home_team, r.home_score)):
            out.setdefault(NFL_TEAMS.get(t, t), []).append((r.gameday, float(pa), yds.get((r.game_id, o))))
    return out


def _cfb_table(cache: Path, as_of):
    import pandas as pd
    season = as_of.year if as_of.month >= 7 else as_of.year - 1
    f = cache / f"cfb_games_{season}.parquet"
    if not f.exists():
        return {}
    g = pd.read_parquet(f, columns=["game_id", "date", "home", "away", "home_points", "away_points", "home_fbs", "away_fbs"])
    g = g[g.home_points.notna() & (pd.to_datetime(g.date).dt.strftime("%Y-%m-%d") < as_of.strftime("%Y-%m-%d"))]
    yds = {}
    tf = cache / f"cfb_teamgames_{season}.parquet"
    if tf.exists():
        s = pd.read_parquet(tf, columns=["game_id", "team", "yards"])
        for r in s.itertuples():
            yds[(str(r.game_id), r.team)] = float(r.yards) if r.yards == r.yards else None
    out: dict = {}
    fbs = set(g.loc[g.home_fbs == True, "home"]) | set(g.loc[g.away_fbs == True, "away"])  # noqa: E712
    for r in g.sort_values("date").itertuples():
        for t, o, pa in ((r.home, r.away, r.away_points), (r.away, r.home, r.home_points)):
            out.setdefault(t, []).append((str(r.date)[:10], float(pa), yds.get((str(r.game_id), o))))
    return {t: v for t, v in out.items() if t in fbs}


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def league(state_dir: Path, sport: str, as_of=None) -> dict:
    """{team: profile} for every team with >= MIN_GAMES completed games before as_of. Cached in memory."""
    from datetime import datetime, timezone
    as_of = as_of or datetime.now(timezone.utc)
    key = (str(state_dir), sport, as_of.strftime("%Y-%m-%d"))  # tables only use games before this date
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    if hit:  # stale: serve it now, refresh in the background (keeps the 1.5 s overlay fast)
        import threading
        _CACHE[key] = (time.time(), hit[1])
        threading.Thread(target=_build, args=(state_dir, sport, as_of, key), daemon=True).start()
        return hit[1]
    return _build(state_dir, sport, as_of, key)


def warm(state_dir: Path) -> None:
    """Pre-load both tables in a background thread (bridge start-up), so the first overlay does not pay the load."""
    import threading
    threading.Thread(target=lambda: [league(state_dir, s) for s in ("nfl", "cfb")], daemon=True).start()


def _build(state_dir: Path, sport: str, as_of, key) -> dict:
    cache = Path(state_dir) / "cache"
    try:
        tab = _nfl_table(cache, as_of) if sport == "nfl" else _cfb_table(cache, as_of) if sport == "cfb" else {}
    except Exception:  # noqa: BLE001 - never break the overlay
        tab = {}
    prof = {}
    for t, rows in tab.items():
        if len(rows) < MIN_GAMES:
            continue
        pa, ya = [r[1] for r in rows], [r[2] for r in rows]
        l3 = rows[-3:]
        prof[t] = {"team": t, "n": len(rows), "pa": _avg(pa), "ya": _avg(ya), "pa3": _avg([r[1] for r in l3]),
                   "ya3": _avg([r[2] for r in l3]) if all(r[2] is not None for r in l3) else None}
    n = len(prof)
    if n >= 8:
        lg_pa = _avg([p["pa"] for p in prof.values()])
        lg = {}
        for k in ("pa", "ya"):
            vals = [p[k] for p in prof.values() if p[k] is not None]
            lg[k] = _avg(vals)
            for p in prof.values():  # rank 1 = worst (most allowed); ties share the better rank
                p[k + "_rank"] = 1 + sum(v > p[k] for v in vals) if p[k] is not None else None
                p[k + "_of"] = len(vals)
        for p in prof.values():
            worst_q = lambda k: (p.get(k + "_rank") is not None and p[k + "_rank"] <= max(1, round(p[k + "_of"] / 4))  # noqa: E731
                                 and p[k] > lg[k])
            p["bad"] = worst_q("pa") or worst_q("ya")
            dp = p["pa3"] - p["pa"]
            dy = (p["ya3"] - p["ya"]) if p["ya3"] is not None and p["ya"] is not None else 0.0
            p["fading"] = p["n"] >= 4 and p["pa3"] >= lg_pa and (dp >= FADE_PTS[sport] or dy >= FADE_YDS[sport])
            p["lg_pa"] = lg_pa
    else:
        prof = {}
    _CACHE[key] = (time.time(), prof)
    return prof


def profile(state_dir: Path, sport: str, team: str, as_of=None) -> dict | None:
    from .data.teams import similarity
    prof = league(state_dir, sport, as_of)
    if not prof:
        return None
    if team in prof:
        return prof[team]
    best = max(prof, key=lambda t: similarity(t, team))
    return prof[best] if similarity(best, team) >= 0.75 else None


def describe(p: dict, short: str) -> str:
    """'Cowboys D trending down (allowed 33/g, 410 yds last 3 vs 27/g season; 31st of 32)'."""
    tag = "bad + trending down" if p["bad"] and p["fading"] else "trending down" if p["fading"] else "bad"
    l3 = f"{p['pa3']:.0f}/g" + (f", {p['ya3']:.0f} yds" if p.get("ya3") else "") + " last 3"
    ss = f"{p['pa']:.0f}/g" + (f", {p['ya']:.0f} yds" if p.get("ya") else "") + " season"
    by = min((k for k in ("pa", "ya") if p.get(k + "_rank")), key=lambda k: p[k + "_rank"] / p[k + "_of"], default=None)
    rank = (f"; {_ord(p[by + '_of'] - p[by + '_rank'] + 1)} of {p[by + '_of']} in {'pts' if by == 'pa' else 'yds'} allowed"
            if by else "")
    return f"{short} D {tag} (allowed {l3} vs {ss}{rank})"


def _ord(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"
