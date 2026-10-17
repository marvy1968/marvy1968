"""Replay March_edge's own prop value finder (ajbet.valuefinder.prop_findings, live config) on the cached real 2025 NFL
prop snapshots (Bovada/DraftKings/FanDuel ~1h before kickoff, state/cache/props_hist; 0 API credits), grade vs nflverse
box scores, and attach Marv prop features computed only from earlier games. Read-only on both bots."""
import glob, json, pickle, sys, warnings, numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/moonrider1968/aj-bet-bot"); sys.path.insert(0, "/opt/marv-bot")
from ajbet import valuefinder as VF
from marv.props import nfl as P
from marv.props.odds import norm_name
from marv import propgrade as PG
R = "/opt/marv-bot"; ST = f"{R}/state"; OUT = "/tmp/pg"
vcfg = json.load(open(f"{OUT}/vcfg.json"))
MK = {m.key: k for k, m in P.MARKETS.items()}
excl = set(vcfg.get("prop_alert_exclude") or [])
picks = []
for fn in sorted(glob.glob(f"{ST}/cache/props_hist/props_*.json")):
    ev = json.load(open(fn)); ev = ev.get("data", ev) if isinstance(ev, dict) and "data" in ev and isinstance(ev["data"], dict) else ev
    fs = VF.prop_findings(ev, "nfl", vcfg, markets=list(MK))
    fs = [f for f in fs if f.market_flag and f.market not in excl]
    fs = VF.best_per_selection(fs)
    for f in fs:
        lv = VF.line_vs_consensus(f)
        if vcfg.get("prop_require_line_not_worse", True) and lv is not None and lv < 0: continue
        picks.append(dict(event_id=f.event_id, home=f.home, away=f.away, commence=f.commence, market=f.market, player=f.player,
                          side=f.selection.lower(), line=f.point, book=f.book, price=f.price, fair_p=f.fair_p, ev=f.ev,
                          my_price=f.extra.get("my_price")))
C = pd.DataFrame(picks); print("March_edge-rule picks (EV>=5%)", len(C), C.book.value_counts().to_dict())
# ---- map to nflverse player + game, actual result ----
players = P.load_players(__import__("pathlib").Path(f"{ST}/cache"), list(range(2018, 2027)), None)
games = P.load_games(__import__("pathlib").Path(f"{ST}/cache"))
g25 = games[games.season == 2025].copy(); g25["day"] = pd.to_datetime(g25.gameday)
p25 = players[players.season == 2025].assign(_n=lambda d: d.player_display_name.map(norm_name))
rows_out = []
C["cu"] = pd.to_datetime(C.commence, utc=True); C["day"] = C.cu.dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
for r in C.itertuples():
    gm = g25[(g25.day - r.day).abs() <= pd.Timedelta(days=1)]
    cand = p25[(p25._n == norm_name(r.player)) & p25.game_id.isin(gm.game_id)]
    if len(cand) != 1: continue
    pr = cand.iloc[0]; stat = P.MARKETS[MK[r.market]].stat; act = float(pr[stat] if pr[stat] == pr[stat] else 0)
    won = act > r.line if r.side == "over" else act < r.line
    rows_out.append(dict(r._asdict(), player_id=pr.player_id, game_id=pr.game_id, team=pr.team, week=int(pr.week), actual=act,
                         push=act == r.line, won=bool(won)))
C = pd.DataFrame(rows_out).drop(columns=["Index"], errors="ignore"); print("mapped + graded", len(C))
# ---- Marv features (same as research/propgrade_v1/features.py; models trained 2019-24 only) ----
rows = pickle.load(open(f"{R}/research/propgrade_v1/out/rows.pkl", "rb"))[1]
models = pickle.load(open(f"{R}/research/propgrade_v1/out/models.pkl", "rb"))
rk = rows.set_index(["player_id", "game_id"]); GD = dict(zip(games.game_id, pd.to_datetime(games.gameday)))
HIST = {pid: h.assign(_d=h.game_id.map(GD)).sort_values("_d", ascending=False) for pid, h in players[players.player_id.isin(C.player_id)].groupby("player_id")}
def form_p(pid, before, mkey, line):
    stat, usage, mu, _ = PG.MARKETS[{"pass_yds": "pass", "rush_yds": "rush", "rec_yds": "rec", "receptions": "receptions"}[mkey]]
    h = HIST.get(pid)
    if h is None: return np.nan
    h = h[(h._d < before) & (h[usage].fillna(0) >= mu)].head(16)
    if len(h) < PG.MIN_GAMES: return np.nan
    v = h[stat].fillna(0).to_numpy(float); w = 0.5 ** (np.arange(len(v)) / PG.HALF_LIFE)
    return PG.p_over(v, w, float(line))
fe = []
for r in C.itertuples():
    key = (r.player_id, r.game_id)
    if key not in rk.index: fe.append({}); continue
    rr = rk.loc[key]; rr = rr.iloc[0] if isinstance(rr, pd.DataFrame) else rr
    mkey = MK[r.market]; m = models[(2025, mkey)]; mk = P.MARKETS[mkey]; X = pd.DataFrame([rr.to_dict()])
    proj = float(m.project(X)[0]); pm = float(m.p_over(np.array([proj]), np.array([r.line]))[0])
    u = mk.usage; ew, l3 = rr.get(f"ewm_{u}"), rr.get(f"l3_{u}")
    fe.append({"proj": proj, "pm_over": pm, "pf_over": form_p(r.player_id, pd.Timestamp(rr["date"]), mkey, r.line),
               "total_line": rr["total_line"], "implied_pts": rr["implied_pts"], "def_ratio": rr.get(f"def_{mk.stat}"),
               "usage_trend": (l3 - ew) / ew if ew and ew > 0 and l3 == l3 else 0.0, "is_home": rr.get("is_home"), "date": str(rr["date"])[:10]})
C = pd.concat([C.reset_index(drop=True), pd.DataFrame(fe)], axis=1)
C["nv_side"] = C.fair_p
C.to_csv(f"{OUT}/C.csv", index=False); print("saved", len(C), "with features", C.pm_over.notna().sum())
print(C.groupby("week").size().to_dict())
