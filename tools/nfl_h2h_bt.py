"""Marv Predict NFL: ratings-only + category points + star H2H tie-break + O/U trend rules, WALK-FORWARD.

Every week is predicted from opponent-adjusted ratings fit ONLY on games finished before that week's first
kickoff (marv.stats.nfl rating params, unchanged; nothing tuned here). Star stats are season-to-date before the
week (previous season for week 1). Graded two ways:
  * Bovada pregame lines (The Odds API historical, ~15 min before the first kickoff of each game day), 2022-2026
  * nflverse closing lines with their juice (bigger independent sample, 2016-2026)
Rules (same as the CFB card, all NFL teams):
  ML pick: category sweep (better offense AND defense rating) with |margin| >= 3 -> that side;
           otherwise (1-1 split or |margin| < 3) star H2H tally (QB pass yds/g, RB1 rush yds/g, WR1 rec yds/g,
           K FG%) decides; star tie -> rating margin side.
  O/U: rating total side; trend follow / fade (both teams over 2+ of last 3 -> UNDER; both 0-1 -> OVER);
       fade + rating agree.
Usage: nice /opt/marv-bot/.venv/bin/python nfl_h2h_bt.py OUTDIR
"""
import csv, json, math, sys
from datetime import timedelta
from pathlib import Path
import numpy as np, pandas as pd

sys.path.insert(0, "/opt/marv-bot")
from marv.data.nflverse import NFL_TEAMS  # noqa: E402
from marv.models import Game  # noqa: E402
from marv.ratings import fit_ratings  # noqa: E402
from marv.stats.nfl import NFL  # noqa: E402

CACHE = Path("/opt/marv-bot/state/cache")
SD = 13.5          # marv.bridge GAME["nfl"]["margin_sd"]
CLOSE = 3.0        # |rating margin| < 3 -> close game, star tie-break
FIRST_TEST = 2016
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/marv-predict/nfl"); OUT.mkdir(parents=True, exist_ok=True)


def phi(x): return 0.5 * (1 + math.erf(x / math.sqrt(2)))
def pay(p): return p / 100 if p > 0 else 100 / -p
def imp(p): return 100 / (p + 100) if p > 0 else -p / (-p + 100)


g = pd.read_csv(CACHE / "nflverse_games.csv")
g = g[g.season >= FIRST_TEST - 3].copy()
g["ko"] = pd.to_datetime(g.gameday + " " + g.gametime.fillna("13:00")).dt.tz_localize("America/New_York").dt.tz_convert("UTC")
g["home"] = g.home_team.map(lambda a: NFL_TEAMS.get(a, a)); g["away"] = g.away_team.map(lambda a: NFL_TEAMS.get(a, a))
done = g[g.home_score.notna()].copy()

# ---- Bovada pregame lines
bov = {}
for p in sorted((CACHE / "bovada_nfl_hist").glob("*.json")):
    snap = pd.Timestamp(p.stem)
    for e in json.loads(p.read_text()).get("data", []):
        ct = pd.Timestamp(e["commence_time"])
        if ct <= snap:
            continue  # already started: live line
        for b in e.get("bookmakers", []):
            d = {}
            for m in b.get("markets", []):
                for o in m["outcomes"]:
                    side = "h" if o["name"] == e["home_team"] else "a" if o["name"] == e["away_team"] else o["name"][0].lower()
                    d[f"{m['key']}_{side}"] = (o.get("point"), o["price"])
            key = (ct.date(), e["home_team"])
            if key not in bov or snap > bov[key]["snap"]:
                bov[key] = {**d, "snap": snap}
def bline(r):
    for dd in (0, -1, 1):
        x = bov.get(((r.ko + timedelta(days=dd)).date(), r.home))
        if x:
            return x
    return None

# ---- star stats (season to date before the week)
pw = pd.concat([pd.read_csv(CACHE / f"nfl_players_week_{y}.csv", low_memory=False) for y in range(2018, 2027)
                if (CACHE / f"nfl_players_week_{y}.csv").exists()], ignore_index=True)
pw = pw[pw.season_type.isin(["REG", "POST"])]
for c in ("passing_yards", "rushing_yards", "receiving_yards", "fg_made", "fg_att"):
    pw[c] = pd.to_numeric(pw[c], errors="coerce").fillna(0)
pw["wk"] = pw.week + np.where(pw.season_type == "POST", 0, 0)


PW = {k: v for k, v in pw.groupby(["season", "team"])}
EMPTY = pw.iloc[:0]


def stars(season, week, team_abbr):
    d = PW.get((season, team_abbr), EMPTY); d = d[d.wk < week]
    if d.wk.nunique() < 2:
        d = PW.get((season - 1, team_abbr), EMPTY)
    if d.empty:
        return None
    ng = max(d.wk.nunique(), 1)
    out = {}
    for k, col, pos in (("QB", "passing_yards", ("QB",)), ("RB", "rushing_yards", ("RB", "FB")), ("WR", "receiving_yards", ("WR",))):
        s = d[d.position.isin(pos)].groupby("player_display_name")[col].sum()
        out[k] = s.max() / ng if len(s) else 0.0
    kk = d.groupby("player_display_name")[["fg_made", "fg_att"]].sum()
    kk = kk[kk.fg_att >= 5]
    out["K"] = (kk.fg_made / kk.fg_att).loc[kk.fg_att.idxmax()] if len(kk) else None
    return out


def tally(sh, sa):
    if not sh or not sa:
        return None
    h = a = 0
    for k, tol in (("QB", 5), ("RB", 3), ("WR", 3), ("K", 0.02)):
        x, y = sh.get(k), sa.get(k)
        if x is None or y is None or abs(x - y) <= tol:
            continue
        h, a = (h + 1, a) if x > y else (h, a + 1)
    return h, a

# ---- O/U trend: last 3 vs closing total (nflverse), before each game
t = pd.concat([pd.DataFrame({"game_id": done.game_id, "ko": done.ko, "team": done[s],
                             "over": (done.home_score + done.away_score > done.total_line).astype(float)})
               for s in ("home", "away")]).sort_values(["team", "ko"])
t.loc[t.over.isna(), "over"] = np.nan
t["over3"] = t.groupby("team").over.transform(lambda s: s.shift().rolling(3, min_periods=3).sum())
over3 = t.set_index(["game_id", "team"]).over3

rows = []
test = done[done.season >= FIRST_TEST].copy()
test["wkkey"] = test.season.astype(str) + "_" + test.week.astype(str).str.zfill(2)
games_all = [Game(id=r.game_id, sport="nfl", start=r.ko.to_pydatetime(), home=r.home, away=r.away,
                  neutral=r.location == "Neutral", completed=True, home_score=r.home_score, away_score=r.away_score)
             for r in done.itertuples()]
for wk, wg in test.groupby("wkkey"):
    as_of = wg.ko.min()
    hist = [x for x in games_all if x.start < as_of.to_pydatetime() and x.start >= (as_of - timedelta(days=600)).to_pydatetime()]
    rt = fit_ratings(hist, NFL.rating_params, as_of=as_of.to_pydatetime())
    for r in wg.itertuples():
        if rt.games_played.get(r.home, 0) < 4 or rt.games_played.get(r.away, 0) < 4:
            continue
        hs, as_ = rt.expected(r.home, r.away, r.location == "Neutral")
        margin, total = hs - as_, hs + as_
        ph = phi(margin / SD)
        off = np.sign(rt.offense[r.home] - rt.offense[r.away]); dfn = np.sign(rt.defense[r.away] - rt.defense[r.home])
        ch, ca = int(off > 0) + int(dfn > 0), int(off < 0) + int(dfn < 0)
        st = tally(stars(r.season, r.week, r.home_team), stars(r.season, r.week, r.away_team)) if r.season >= 2018 else None
        if (ch == 2 or ca == 2) and abs(margin) >= CLOSE:
            pick, method = ("H" if ch == 2 else "A"), "sweep"
        elif st and st[0] != st[1]:
            pick, method = ("H" if st[0] > st[1] else "A"), "star"
        else:
            pick, method = ("H" if margin > 0 else "A"), "margin"
        b = bline(r)
        rows.append(dict(game_id=r.game_id, season=r.season, week=r.week, home=r.home, away=r.away,
                         hp=r.home_score, ap=r.away_score, margin=round(margin, 2), total=round(total, 2), p_home=ph,
                         cat_h=ch, cat_a=ca, st_h=st[0] if st else None, st_a=st[1] if st else None, pick=pick, method=method,
                         n_hml=r.home_moneyline, n_aml=r.away_moneyline, n_spread=-r.spread_line if pd.notna(r.spread_line) else None,
                         n_hso=r.home_spread_odds, n_aso=r.away_spread_odds, n_total=r.total_line, n_oo=r.over_odds, n_uo=r.under_odds,
                         b_hml=(b or {}).get("h2h_h", (None, None))[1], b_aml=(b or {}).get("h2h_a", (None, None))[1],
                         b_spread=(b or {}).get("spreads_h", (None, None))[0], b_hso=(b or {}).get("spreads_h", (None, None))[1],
                         b_aso=(b or {}).get("spreads_a", (None, None))[1],
                         b_total=(b or {}).get("totals_o", (None, None))[0], b_oo=(b or {}).get("totals_o", (None, None))[1],
                         b_uo=(b or {}).get("totals_u", (None, None))[1],
                         h_over3=over3.get((r.game_id, r.home)), a_over3=over3.get((r.game_id, r.away))))
    print(wk, len(rows), file=sys.stderr)
df = pd.DataFrame(rows)
df.to_csv(OUT / "nfl_h2h_bt_picks.csv", index=False)
print("rows", len(df))
