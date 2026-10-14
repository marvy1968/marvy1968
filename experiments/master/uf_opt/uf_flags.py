"""Per-game Master-UF reason flags (live rules in marv/tci_ppe_uf.py), walk-forward, for the UF optimisation study.
rivalry: NFL div_game; CFB same conference & |spread|<=7; WNBA met within 4 days & |spread|<=4.
emotional: either team lost its last game by >= BLOWOUT[sport]. weather (NFL): wind>=20 or temp<=20.
injury (NFL only; no historical injury data for other sports): from nflverse weekly injury reports, Q/D/Out(game-status)
  any   = any player listed Questionable/Doubtful on either team
  start = such a player averaged >=60% offense/defense snaps over the team's prior 2026 games (starter)
  key   = such a player is QB1 or the team's top-snap RB / WR / TE (= clvinj 'leaders' key_player proxy, the live rule)"""
import sys, numpy as np, pandas as pd, warnings; warnings.filterwarnings("ignore")
sys.path.insert(0, "/opt/marv-bot"); sys.path.insert(0, "/opt/marv-bot/experiments/tci_ppe_uf")
import bt as B
C = B.C; BLOW = dict(nfl=17, cfb=21, wnba=20, soccer=3)

def emo(df, blow):
    fin = df.dropna(subset=["hs", "as_"]).sort_values("date")
    lo = pd.concat([pd.DataFrame({"team": fin.home, "date": fin.date, "m": fin.hs-fin.as_}),
                    pd.DataFrame({"team": fin.away, "date": fin.date, "m": fin.as_-fin.hs})]).sort_values("date")
    out = []
    for _, x in df.iterrows():
        f = False
        for t in (x.home, x.away):
            a = lo[(lo.team == t) & (lo.date < x.date)].tail(1)
            if len(a) and a.m.iloc[0] <= -blow: f = True
        out.append(f)
    return out

def nfl_inj():
    inj = pd.read_csv(C+"nfl_injuries_2026.csv"); sn = pd.read_csv(C+"nfl_snaps_2026.csv")
    sn["pct"] = sn[["offense_pct", "defense_pct"]].max(axis=1)
    q = inj[inj.report_status.isin(["Questionable", "Doubtful"])]
    rows = {}
    for (tm, wk), g in q.groupby(["team", "week"]):
        prior = sn[(sn.team == tm) & (sn.week < wk)]
        avg = prior.groupby("player").pct.mean(); off = prior.groupby(["player", "position"]).offense_snaps.sum().reset_index()
        key = set()
        for pos in ("QB", "RB", "WR", "TE"):
            p = off[off.position == pos].sort_values("offense_snaps", ascending=False).head(1)
            key |= set(p.player)
        names = set(g.full_name)
        rows[(tm, wk)] = dict(any=len(names) > 0, start=any(avg.get(n, 0) >= 0.6 for n in names), key=bool(names & key))
    return rows

def flags():
    out = []
    g = pd.read_csv(C+"nflverse_games.csv"); g = g[(g.season == 2026)]
    IR = nfl_inj()
    for sp in ("nfl", "cfb", "wnba", "soccer"):
        df, R, P = getattr(B, sp)(); df = df[(df.date >= "2026-07-01") & (df.date < "2026-10-13")].copy()
        if sp == "wnba":  # live rule has no playoff restriction
            d0, _, _ = B.wnba(); d0 = d0.sort_values("date")
            df["rival"] = [bool(((d0.date < d) & (d0.date >= d-pd.Timedelta(days=4)) & (((d0.home == x) & (d0.away == y)) | ((d0.home == y) & (d0.away == x)))).any()) and abs(s) <= 4
                           for d, x, y, s in zip(df.date, df.home, df.away, df.spread.fillna(99))]
        df["emotional"] = emo(df, BLOW[sp]); df["sport"] = sp
        df["weather"] = False; df["inj_any"] = df["inj_start"] = df["inj_key"] = False
        if sp == "nfl":
            gg = g.set_index("game_id")
            df["weather"] = [bool((gg.wind.get(i, np.nan) >= 20) or (gg.temp.get(i, np.nan) <= 20)) for i in df.gid]
            wk = gg.week
            for k in ("any", "start", "key"):
                df["inj_"+k] = [IR.get((h, wk.get(i)), {}).get(k, False) or IR.get((a, wk.get(i)), {}).get(k, False) for i, h, a in zip(df.gid, df.home, df.away)]
        df["gkey"] = df.date.dt.strftime("%Y-%m-%d")+"|"+df.away.astype(str)+"@"+df.home.astype(str)
        out.append(df[["sport", "gid", "gkey", "date", "spread", "rival", "emotional", "weather", "inj_any", "inj_start", "inj_key"]])
    return pd.concat(out)

if __name__ == "__main__":
    F = flags(); F.to_csv("/opt/marv-bot/experiments/master/uf_opt/flags.csv", index=False)
    print(F.groupby("sport")[["rival", "emotional", "weather", "inj_any", "inj_start", "inj_key"]].sum())
