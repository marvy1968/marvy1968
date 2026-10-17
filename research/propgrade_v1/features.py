"""Marv prop grade v1 backtest (2026-10-16). Paper research only; reads data, writes reports.
Populations:
  A = every real 2025 NFL prop line (props_hist, Bovada-first, 1h pre-kick), both sides as candidate picks.
      Tune = 2025 weeks 1-6, holdout = weeks 7-10 (time-based).
  B = March_edge's actual graded pregame NFL prop picks, 2026-10-01..2026-10-15 (never used for tuning).
Every feature is computed from games before the prop's game (build_rows shifts; PropModel trained on earlier seasons)."""
import json, math, pickle, time, sqlite3, sys, warnings
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, "/opt/marv-bot")
from marv.props import nfl as P
from marv.props.odds import norm_name
from marv import propgrade as PG

ST = Path("/opt/marv-bot/state"); CACHE = ST / "cache"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/propgrade_bt"); OUT.mkdir(parents=True, exist_ok=True)
MK = {m.key: k for k, m in P.MARKETS.items()}

def implied(a): a = float(a); return 100 / (a + 100) if a > 0 else -a / (-a + 100)
def payout(a): a = float(a); return a / 100 if a > 0 else 100 / -a
def lg(p): p = np.clip(np.asarray(p, float), 0.01, 0.99); return np.log(p / (1 - p))

players = P.load_players(CACHE, list(range(2018, 2027)), None)
games = P.load_games(CACHE)

# ---- B: March_edge picks (2026) ----
con = sqlite3.connect("file:/home/moonrider1968/aj-bet-bot/ajbet.db?mode=ro", uri=True)
b = pd.read_sql("select id, ts, commence, player, market, side, line, price, ev, fair_p, model_p, status, final_score, tag, league "
                "from paper_bets where market like 'player%' and ev is not null order by id", con)
b["tsu"] = pd.to_datetime(b["ts"], utc=True, format="ISO8601"); b["cu"] = pd.to_datetime(b["commence"], utc=True, format="ISO8601")
b["phase"] = np.where(b["tsu"] < b["cu"], "pregame", "live")
b = b.drop_duplicates(["league", "player", "market", "side", "line", "commence"], keep="first")
b.to_csv(OUT / "B_all_picks.csv", index=False)
bn = b[(b.league == "nfl") & (b.phase == "pregame") & b.market.isin(MK) & b.status.isin(["won", "lost"])].copy()

# map each B pick to an nflverse player + game; add synthetic pre-game rows when the game isn't in the stats yet
p26 = players[players.season == 2026].sort_values("week")
last = p26.groupby("player_id").tail(1).assign(_n=lambda d: d.player_display_name.map(norm_name))
g26 = games[games.season == 2026].copy(); g26["day"] = pd.to_datetime(g26.gameday)
fut, keymap = [], {}
for r in bn.itertuples():
    cand = last[last._n == norm_name(r.player)]
    if len(cand) != 1: continue
    pr = cand.iloc[0]; day = r.cu.tz_convert("America/New_York").tz_localize(None).normalize()
    gm = g26[((g26.home_team == pr.team) | (g26.away_team == pr.team)) & ((g26.day - day).abs() <= pd.Timedelta(days=1))]
    if gm.empty: continue
    gm = gm.iloc[0]; keymap[r.id] = (pr.player_id, gm.game_id)
    if not ((players.player_id == pr.player_id) & (players.game_id == gm.game_id)).any():
        fut.append({"player_id": pr.player_id, "player_display_name": pr.player_display_name, "position": pr.position,
                    "season": 2026, "week": gm.week, "game_id": gm.game_id, "team": pr.team,
                    "opponent_team": gm.away_team if gm.home_team == pr.team else gm.home_team})
allp = pd.concat([players, pd.DataFrame(fut)], ignore_index=True)
T0 = time.time()
rp = OUT / "rows.pkl"
if rp.exists() and pickle.load(open(rp, "rb"))[0] == len(allp):
    rows = pickle.load(open(rp, "rb"))[1]
else:
    rows = P.build_rows(allp, games); pickle.dump((len(allp), rows), open(rp, "wb"))
print("rows built", round(time.time() - T0), "s", flush=True)
print("B nfl pregame graded:", len(bn), "mapped:", len(keymap), "synthetic rows:", len(fut))

# ---- models: walk-forward (2025 eval: train 2019-24; 2026 eval: train 2020-25) ----
mp = OUT / "models.pkl"
if mp.exists():
    models = pickle.load(open(mp, "rb"))
else:
    models = {}
    for season in (2025, 2026):
        for key, mk in P.MARKETS.items():
            data = P.eligible(rows, mk); tr = data[(data.season < season) & (data.season >= season - 6)]
            models[(season, key)] = P.PropModel(mk).fit(tr); print("fit", season, key, len(tr), round(time.time() - T0), flush=True)
    pickle.dump(models, open(mp, "wb"))
GD = dict(zip(games.game_id, pd.to_datetime(games.gameday)))
HIST = {pid: h.assign(_d=h.game_id.map(GD)).sort_values("_d", ascending=False) for pid, h in allp.groupby("player_id")}

def form_p(pid, before_date, mkey, line):
    stat, usage, mu, _ = PG.MARKETS[{"pass_yds": "pass", "rush_yds": "rush", "rec_yds": "rec", "receptions": "receptions"}[mkey]]
    h = HIST.get(pid)
    if h is None: return np.nan
    h = h[(h._d < before_date) & (h[usage].fillna(0) >= mu)].head(16)
    if len(h) < PG.MIN_GAMES: return np.nan
    v = h[stat].fillna(0).to_numpy(float); w = 0.5 ** (np.arange(len(v)) / PG.HALF_LIFE)
    return PG.p_over(v, w, float(line))

def feats(r, season, mkey, line):
    m = models[(season, mkey)]; mk = P.MARKETS[mkey]
    X = pd.DataFrame([r])
    proj = float(m.project(X)[0]); pm = float(m.p_over(np.array([proj]), np.array([line]))[0])
    u = mk.usage; ew, l3 = r.get(f"ewm_{u}"), r.get(f"l3_{u}")
    return {"proj": proj, "pm_over": pm, "pf_over": form_p(r["player_id"], pd.Timestamp(r["date"]), mkey, line),
            "total_line": r["total_line"], "implied_pts": r["implied_pts"], "spread_team": 2 * r["implied_pts"] - r["total_line"],
            "def_ratio": r.get(f"def_{mk.stat}"), "usage_trend": (l3 - ew) / ew if ew and ew > 0 and l3 == l3 else 0.0,
            "position": r["position"], "games_before": r["games_before"]}

# ---- A: 2025 real lines ----
A = pd.read_csv(ST / "reports/props_backtest_real.csv", low_memory=False)
A = A[A.market.isin(MK)]
print("A loop start", round(time.time() - T0), flush=True)
rk = rows.set_index(["player_id", "game_id"])
arows = []
for r in A.itertuples():
    key = (r.player_id, r.game_id)
    if key not in rk.index: continue
    rr = rk.loc[key]; rr = rr.iloc[0] if isinstance(rr, pd.DataFrame) else rr
    f = feats(dict(rr.to_dict(), player_id=r.player_id), 2025, MK[r.market], r.line)
    for side, pr, opp in (("over", r.over_price, r.under_price), ("under", r.under_price, r.over_price)):
        if pr != pr or opp != opp: continue
        a, o = implied(pr), implied(opp); nv = a / (a + o)
        won = (r.actual > r.line) if side == "over" else (r.actual < r.line)
        arows.append(dict(f, pop="A", week=r.week, date=r.gameday, player=r.player, market=r.market, side=side, line=r.line,
                          price=pr, nv_side=nv, imp_side=a, actual=r.actual, push=r.actual == r.line, won=bool(won), ev=nv * (1 + payout(pr)) - 1))
A2 = pd.DataFrame(arows)

# ---- B features ----
brows = []
for r in bn.itertuples():
    if r.id not in keymap: continue
    pid, gid = keymap[r.id]
    rr = rk.loc[(pid, gid)]; rr = rr.iloc[0] if isinstance(rr, pd.DataFrame) else rr
    f = feats(dict(rr.to_dict(), player_id=pid), 2026, MK[r.market], r.line)
    brows.append(dict(f, pop="B", id=r.id, ts=r.ts, date=str(r.cu.date()), game_id=gid, player=r.player, market=r.market, side=r.side,
                      line=r.line, price=r.price, nv_side=r.fair_p, imp_side=implied(r.price), push=False, won=r.status == "won",
                      ev=r.ev, tag=r.tag))
B2 = pd.DataFrame(brows)
A2.to_csv(OUT / "A_features.csv", index=False); B2.to_csv(OUT / "B_features.csv", index=False)
print("done", round(time.time() - T0), "s"); print("A rows", len(A2), "B rows", len(B2))
