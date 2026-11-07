"""Build the in-game prop dataset from ESPN NBA play-by-play (sportsdataverse espn_nba_pbp parquet).

One row per player per game per break (end of Q1, Q2, Q3):
  so-far stats (pts, reb, ast, fg3, fga, fta, tov, pf), minutes played so far (rebuilt from substitutions),
  on court at the break, team / opponent possessions so far, score margin, break wallclock window,
  and the player's FINAL stats (overtime included, as Kalshi and sportsbooks settle).
Plus leakage-free pregame anchors: each player's exponentially weighted averages from his EARLIER games only.

usage: python research/prop_pacer/build.py OUT.parquet [nba|ncaab|ncaaw] PBP_season1.parquet PBP_season2.parquet ...
(NBA: espn_nba_pbp; men's college: espn_mens_college_basketball_pbp; women's: espn_womens_college_basketball_pbp)
"""

import sys

import numpy as np
import pandas as pd

STATS = ["pts", "reb", "ast", "fg3", "fga", "fta", "tov", "pf"]
# regulation periods, seconds per period, overtime seconds, fouls to foul out
LEAGUES = {"nba": (4, 720, 300, 6), "ncaab": (2, 1200, 300, 5), "ncaaw": (4, 600, 300, 5)}


def _events(g: pd.DataFrame) -> pd.DataFrame:
    """Per play: which player gets which stat (one row per player-stat credit)."""
    tt = g["type_text"].fillna("")
    a1, a2 = g["athlete_id_1"], g["athlete_id_2"]
    pa = g["points_attempted"].fillna(0)
    shoot = g["shooting_play"].fillna(False).astype(bool)
    made = g["scoring_play"].fillna(False).astype(bool)
    rows = []

    def add(mask, who, stat, val):
        m = mask & who.notna()
        if m.any():
            rows.append(pd.DataFrame({"i": g.index[m], "player": who[m].astype("int64"), "stat": stat,
                                      "val": np.asarray(val[m] if hasattr(val, "__getitem__") and not np.isscalar(val) else val)}))

    add(made & shoot, a1, "pts", g["score_value"].astype(float))
    add(made & shoot & (g["score_value"] == 3), a1, "fg3", pd.Series(1.0, index=g.index))
    add(made & shoot & pa.isin([2, 3]), a2, "ast", pd.Series(1.0, index=g.index))
    add(shoot & pa.isin([2, 3]), a1, "fga", pd.Series(1.0, index=g.index))
    add(shoot & (pa == 1), a1, "fta", pd.Series(1.0, index=g.index))
    add(tt.str.contains("Rebound") & ~g["text"].fillna("").str.contains("team rebound"), a1, "reb",
        pd.Series(1.0, index=g.index))
    add(tt.str.contains("Turnover"), a1, "tov", pd.Series(1.0, index=g.index))
    add(tt.str.contains("Foul") & ~tt.str.contains("Turnover|Technical"), a1, "pf", pd.Series(1.0, index=g.index))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=["i", "player", "stat", "val"])


def _minutes(g: pd.DataFrame, team_of: dict, league: str = "nba") -> pd.DataFrame:
    """Seconds on court per player per period from substitutions (players with an event in a period before entering
    it are on the floor at its start). Returns rows (player, period, sec_on, on_at_end)."""
    out = []
    # who comes in / goes out on each substitution: NBA "A enters the game for B" (A = id 1, B = id 2);
    # college has one event per player: "A subbing in" / "A subbing out"
    g = g.copy()
    txt = g["text"].fillna("")
    sub = g["type_text"] == "Substitution"
    college_in, college_out = sub & txt.str.contains("subbing in"), sub & txt.str.contains("subbing out")
    g["sub_in"] = np.where(college_in, g["athlete_id_1"], np.where(sub & ~college_out, g["athlete_id_1"], np.nan))
    g["sub_out"] = np.where(college_out, g["athlete_id_1"], np.where(sub & ~college_in, g["athlete_id_2"], np.nan))
    for per, pg in g.groupby("period_number", sort=True):
        n_reg, plen, ot, _ = LEAGUES[league]
        length = plen if per <= n_reg else ot
        start_rem = pg["clock_minutes"].astype(float) * 60 + pg["clock_seconds"].astype(float)  # seconds left in period
        first: dict[int, str] = {}
        for _, r in pg.iterrows():
            if r["type_text"] == "Substitution":
                for who, kind in ((r["sub_in"], "in"), (r["sub_out"], "out")):
                    if pd.notna(who) and int(who) not in first:
                        first[int(who)] = kind
            else:
                for who in (r["athlete_id_1"], r["athlete_id_2"], r["athlete_id_3"]):
                    if pd.notna(who) and int(who) not in first and int(who) in team_of:
                        first[int(who)] = "on"
        on = {p: length for p, k in first.items() if k in ("on", "out")}  # on court since t=length (seconds left)
        secs = {p: 0.0 for p in first}
        for (_, r), rem in zip(pg.iterrows(), start_rem):
            if r["type_text"] != "Substitution":
                continue
            rem = float(rem) if pd.notna(rem) else 0.0
            p_in, p_out = r["sub_in"], r["sub_out"]
            if pd.notna(p_out) and int(p_out) in on:
                secs[int(p_out)] += on.pop(int(p_out)) - rem
            if pd.notna(p_in) and int(p_in) not in on:
                on[int(p_in)] = rem
        for p, since in on.items():
            secs[p] = secs.get(p, 0.0) + since
        for p, s in secs.items():
            out.append((p, per, min(max(s, 0.0), length), p in on))
    return pd.DataFrame(out, columns=["player", "period", "sec", "on_end"])


def game_rows(g: pd.DataFrame, league: str = "nba") -> list[dict]:
    n_reg, plen, _, foul_out = LEAGUES[league]
    g = g.sort_values("game_play_number").reset_index(drop=True)
    for c in ("athlete_id_3", "athlete_name_2"):
        if c not in g:
            g[c] = np.nan
    if "points_attempted" not in g:  # older college files: shot value from the play text
        t = g["text"].fillna("")
        g["points_attempted"] = np.where(~g["shooting_play"].fillna(False).astype(bool), 0,
                                         np.where(t.str.contains("Free Throw"), 1,
                                                  np.where(t.str.contains("Three Point"), 3, 2)))
    if g["period_number"].max() < n_reg:
        return []
    home, away = g["home_team_id"].iloc[0], g["away_team_id"].iloc[0]
    ev = _events(g)
    if ev.empty:
        return []
    ev["period"] = g.loc[ev["i"], "period_number"].to_numpy()
    team_of = {}
    for _, r in g[g["athlete_id_1"].notna() & g["team_id"].notna() & (g["type_text"] != "Substitution")].iterrows():
        team_of.setdefault(int(r["athlete_id_1"]), r["team_id"])
    for _, r in g[(g["type_text"] == "Substitution") & g["team_id"].notna()].iterrows():
        for who in (r["athlete_id_1"], r["athlete_id_2"]):
            if pd.notna(who):
                team_of.setdefault(int(who), r["team_id"])
    mins = _minutes(g, team_of, league)
    names = {}
    for c_id, c_name in (("athlete_id_1", "athlete_name_1"), ("athlete_id_2", "athlete_name_2")):
        for pid, nm in zip(g[c_id], g[c_name]):
            if pd.notna(pid) and isinstance(nm, str):
                names.setdefault(int(pid), nm)
    # team possessions by period: FGA + 0.44 FTA - OREB + TOV
    tt = g["type_text"].fillna("")
    pa = g["points_attempted"].fillna(0)
    shoot = g["shooting_play"].fillna(False).astype(bool)
    poss = (shoot & pa.isin([2, 3])).astype(float) + 0.44 * (shoot & (pa == 1)).astype(float) \
        - (tt == "Offensive Rebound").astype(float) + tt.str.contains("Turnover").astype(float)
    tposs = pd.DataFrame({"team": g["team_id"], "period": g["period_number"], "poss": poss}).dropna()
    tposs = tposs.groupby(["team", "period"])["poss"].sum()
    ends = g.groupby("period_number").agg(last_wall=("wallclock", "last"), first_wall=("wallclock", "first"),
                                          hs=("home_score", "last"), as_=("away_score", "last"))
    players = sorted(set(ev["player"]) | set(mins["player"]))
    final = ev.pivot_table(index="player", columns="stat", values="val", aggfunc="sum").reindex(players).fillna(0)
    fmin = mins.groupby("player")["sec"].sum().reindex(players).fillna(0) / 60
    out = []
    meta = {"game_id": int(g["game_id"].iloc[0]), "date": str(g["game_date"].iloc[0]),
            "season": int(g["season"].iloc[0]), "season_type": int(g["season_type"].iloc[0]),
            "home": g["home_team_name"].iloc[0], "away": g["away_team_name"].iloc[0],
            "home_abbr": g["home_team_abbrev"].iloc[0], "away_abbr": g["away_team_abbrev"].iloc[0],
            "tip": g["wallclock"].dropna().iloc[0] if g["wallclock"].notna().any() else None,
            "periods": int(g["period_number"].max()), "league": league, "game_min": n_reg * plen / 60,
            "foul_out": foul_out,
            "final_home": float(ends["hs"].iloc[-1]), "final_away": float(ends["as_"].iloc[-1])}
    for k in range(1, n_reg):
        if k + 1 not in ends.index:
            continue
        so = ev[ev["period"] <= k].pivot_table(index="player", columns="stat", values="val", aggfunc="sum")
        so = so.reindex(players).fillna(0)
        mk = mins[mins["period"] <= k].groupby("player")["sec"].sum().reindex(players).fillna(0) / 60
        on_end = mins[mins["period"] == k].set_index("player")["on_end"].reindex(players).fillna(False)
        hs, as_ = float(ends.loc[k, "hs"]), float(ends.loc[k, "as_"])
        for p in players:
            t = team_of.get(p)
            if t is None or fmin.get(p, 0) <= 0 and final.loc[p].sum() == 0:
                continue
            is_home = t == home
            opp = away if is_home else home
            row = {**meta, "player": int(p), "name": names.get(p, ""), "team_id": t, "is_home": is_home, "k": k,
                   "elapsed": plen / 60 * k, "frac": k / n_reg, "min_sf": float(mk[p]), "on_court": bool(on_end[p]),
                   "min_final": float(fmin[p]),
                   "tposs_sf": float(sum(tposs.get((t, q), 0) for q in range(1, k + 1))),
                   "oposs_sf": float(sum(tposs.get((opp, q), 0) for q in range(1, k + 1))),
                   "margin": (hs - as_) * (1 if is_home else -1),
                   "break_start": ends.loc[k, "last_wall"], "break_end": ends.loc[k + 1, "first_wall"]}
            for s in STATS:
                row[f"{s}_sf"] = float(so.loc[p].get(s, 0.0))
                row[f"{s}_final"] = float(final.loc[p].get(s, 0.0))
            out.append(row)
    return out


def anchors(df: pd.DataFrame) -> pd.DataFrame:
    """Leakage-free pregame expectations from each player's earlier games (EWM, half-life 10 games)."""
    games = df[df["k"] == 1][["player", "game_id", "date", "team_id", "min_final",
                              *[f"{s}_final" for s in STATS]]].drop_duplicates(["player", "game_id"])
    games = games.sort_values(["player", "date", "game_id"])
    g = games.groupby("player", sort=False)
    for c in ["min_final", "pts_final", "reb_final", "ast_final", "fg3_final", "fga_final", "fta_final", "pf_final"]:
        games[f"pre_{c[:-6]}"] = g[c].transform(lambda s: s.shift().ewm(halflife=10, min_periods=1).mean())
    games["pre_n"] = g.cumcount()
    games["pre_min_l5"] = g["min_final"].transform(lambda s: s.shift().rolling(5, min_periods=1).mean())
    # team pace prior: possessions per 48 from earlier games
    last = df.groupby("game_id")["k"].transform("max")
    tg = df[df["k"] == last].groupby(["game_id", "team_id", "date"], as_index=False)[["tposs_sf", "elapsed"]].first()
    tg["pace36"] = tg["tposs_sf"] / tg["elapsed"] * 36  # possessions per 36 minutes
    tg = tg.sort_values(["team_id", "date"])
    tg["pre_pace36"] = tg.groupby("team_id")["pace36"].transform(lambda s: s.shift().ewm(halflife=10, min_periods=1).mean())
    keep = ["player", "game_id"] + [c for c in games.columns if c.startswith("pre_")]
    df = df.merge(games[keep], on=["player", "game_id"], how="left")
    df = df.merge(tg[["game_id", "team_id", "pre_pace36"]], on=["game_id", "team_id"], how="left")
    return df


def main(out: str, *paths: str) -> None:
    league = "nba"
    if paths and paths[0] in LEAGUES:
        league, paths = paths[0], paths[1:]
    rows = []
    for path in paths:
        import pyarrow.parquet as pq
        have = set(pq.read_schema(path).names)
        p = pd.read_parquet(path, columns=[c for c in ["game_id", "game_play_number", "type_text", "text", "athlete_id_1",
                                           "athlete_id_2", "athlete_id_3", "athlete_name_1", "athlete_name_2",
                                           "team_id", "scoring_play", "score_value", "shooting_play",
                                           "points_attempted", "period_number", "clock_minutes", "clock_seconds",
                                           "home_score", "away_score", "wallclock", "game_date", "season",
                                           "season_type", "home_team_id", "away_team_id", "home_team_name",
                                           "away_team_name", "home_team_abbrev", "away_team_abbrev"] if c in have])
        p = p[p["season_type"].isin([2, 3])]
        for gid, g in p.groupby("game_id", sort=False):
            try:
                rows += game_rows(g, league)
            except Exception as exc:  # a malformed game never stops the build
                print("skip", gid, exc, file=sys.stderr)
        print(path, len(rows), file=sys.stderr)
    df = anchors(pd.DataFrame(rows))
    df.to_parquet(out)
    print(out, df.shape)


if __name__ == "__main__":
    main(sys.argv[1], *sys.argv[2:])
