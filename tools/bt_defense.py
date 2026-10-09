"""Light walk-forward check: heavy favourites with a bad / fading defense (marv/defense.py rules, as of the day before
each game) vs other heavy favourites. NFL 2021-25 (nflverse closing spread), CFB 2021-25 (CFBD spread). Descriptive."""
import sys
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
sys.path.insert(0, "/opt/marv-bot")
from marv import defense as D
from marv.data.nflverse import NFL_TEAMS
ST = Path("/opt/marv-bot/state")
rows = []
g = pd.read_csv(ST / "cache/nflverse_games.csv")
g = g[g.season.between(2021, 2025) & g.home_score.notna() & g.spread_line.notna() & (g.game_type == "REG")]
for r in g.itertuples():
    sp = r.spread_line  # + = home favoured
    if abs(sp) < D.HEAVY["nfl"]:
        continue
    fav, dog = (r.home_team, r.away_team) if sp > 0 else (r.away_team, r.home_team)
    fs, ds = (r.home_score, r.away_score) if sp > 0 else (r.away_score, r.home_score)
    p = D.profile(ST, "nfl", NFL_TEAMS.get(fav, fav), datetime.strptime(r.gameday, "%Y-%m-%d").replace(tzinfo=timezone.utc))
    if not p or "bad" not in p:
        continue
    rows.append(("nfl", r.season, p["bad"], p["fading"], fs > ds, fs - ds > abs(sp), fs - ds == abs(sp)))
for y in range(2021, 2026):
    c = pd.read_parquet(ST / f"cache/cfb_games_{y}.parquet")
    c = c[c.home_points.notna() & c.spread.notna() & c.home_fbs & c.away_fbs]
    for r in c.itertuples():
        sp = -r.spread  # + = home favoured
        if abs(sp) < D.HEAVY["cfb"]:
            continue
        fav = r.home if sp > 0 else r.away
        fs, ds = (r.home_points, r.away_points) if sp > 0 else (r.away_points, r.home_points)
        p = D.profile(ST, "cfb", fav, pd.Timestamp(r.date).to_pydatetime().replace(tzinfo=timezone.utc))
        if not p or "bad" not in p:
            continue
        rows.append(("cfb", y, p["bad"], p["fading"], fs > ds, fs - ds > abs(sp), fs - ds == abs(sp)))
d = pd.DataFrame(rows, columns=["sport", "season", "bad", "fading", "win", "cover", "push"])
d = d[~d.push]
d["grp"] = d.apply(lambda r: "bad+fading" if r.bad and r.fading else "bad only" if r.bad else "fading only" if r.fading else "neither", axis=1)
for s, x in d.groupby("sport"):
    print(f"== {s} heavy favourites (n={len(x)})")
    print(x.groupby("grp").agg(n=("win", "size"), fav_win=("win", "mean"), fav_cover=("cover", "mean")).round(3))
    x2 = x.assign(flag=x.bad | x.fading)
    print(x2.groupby("flag").agg(n=("win", "size"), fav_win=("win", "mean"), fav_cover=("cover", "mean")).round(3))
    print(x2[x2.flag].groupby("season").agg(n=("win", "size"), fav_cover=("cover", "mean")).round(3).T)
