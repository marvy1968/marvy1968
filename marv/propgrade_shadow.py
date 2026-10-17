"""Prop grade v2 SHADOW tracker (Marvin 2026-10-16). Paper/tracking only: never Telegram, never an order, never a filter.

Every March_edge pregame NFL player prop (ajbet.db paper_bets, read-only: sent alerts + watchlist) gets Marv's prop grade
(marv.propgrade.prop_grade: 👍 / neutral / 👎) from information known before kickoff, plus Marv's game read (team win p,
the check the Kalshi demo legacy prop path uses) and the game-script read. One line per pick in
state/propgrade/shadow.jsonl; state/propgrade/summary.json compares, on graded picks:
  (a) March_edge + Marv game read not opposing   (b) prop grade 👍 only   (c) every March_edge pick
Promotion bar (to let MARV_PROPGRADE_LIVE=1 filter the Kalshi demo): 100+ graded 👍 and (b) ROI above (a) and (c).
Run: python -m marv.propgrade_shadow [--report]    (marv-propgrade.timer)"""
import json, math, os, pickle, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import propgrade as PG
from .props import nfl as P
from .props.odds import norm_name

ROOT = Path(__file__).resolve().parent.parent
ST = Path(os.environ.get("MARV_STATE", ROOT / "state")); CACHE = ST / "cache"
OUTD = ST / "propgrade"; LEDGER = OUTD / "shadow.jsonl"; SUMMARY = OUTD / "summary.json"
MODELS = OUTD / "models_nfl_2026.pkl"         # Marv prop projection models trained on 2020-25 only
AJDB = os.environ.get("AJBET_DB", "/home/moonrider1968/aj-bet-bot/ajbet.db")
PREDS = ST / "predictions.json"
MK = {m.key: k for k, m in P.MARKETS.items()}
LOOKBACK_D = int(os.environ.get("MARV_PROPGRADE_LOOKBACK_D", "4"))


def implied(a):
    a = float(a); return 100 / (a + 100) if a > 0 else -a / (-a + 100)


def payout(a):
    a = float(a); return a / 100 if a > 0 else 100 / -a


def _ledger():
    out = {}
    if LEDGER.exists():
        for ln in LEDGER.read_text().splitlines():
            try:
                r = json.loads(ln); out[r["bet_id"]] = r
            except Exception:
                pass
    return out


def me_picks(since: datetime) -> pd.DataFrame:
    con = sqlite3.connect(f"file:{AJDB}?mode=ro", uri=True)
    b = pd.read_sql("select id, alert_id, ts, commence, league, player, market, side, line, price, ev, fair_p, status, tag "
                    "from paper_bets where league = 'nfl' and market in ({}) and commence >= ?".format(",".join("?" * len(MK))),
                    con, params=[*MK, since.strftime("%Y-%m-%dT%H:%M:%SZ")])
    con.close()
    if b.empty:
        return b
    b["tsu"] = pd.to_datetime(b.ts, utc=True, format="ISO8601"); b["cu"] = pd.to_datetime(b.commence, utc=True, format="ISO8601")
    return b[b.tsu < b.cu]          # pregame picks only (in-game props are a different problem)


def defense_table(players: pd.DataFrame, games: pd.DataFrame) -> pd.DataFrame:
    """Opponent defense vs position before each game (same as the defense block of marv.props.nfl.build_rows),
    on ALL players so it can be merged onto rows built for a small subset of players."""
    p = players.merge(games[["game_id", "season", "week", "gameday"]], on=["game_id", "season", "week"], how="inner")
    cols = ("passing_yards", "rushing_yards", "receiving_yards", "receptions")
    pa = p.groupby(["game_id", "opponent_team", "position"], as_index=False)[list(cols)].sum()
    pa = pa.merge(games[["game_id", "gameday", "season"]], on="game_id"); pa["date"] = pd.to_datetime(pa["gameday"])
    pa = pa.sort_values("date")
    for c in cols:
        prev = pa.groupby(["opponent_team", "position"])[c].shift()
        pa[f"def_{c}"] = prev.groupby([pa["opponent_team"], pa["position"]]).transform(lambda s: s.ewm(halflife=6, min_periods=1).mean())
        league = pa.groupby(["season", "position"])[c].transform(lambda s: s.shift().expanding().mean())
        pa[f"def_{c}"] = pa[f"def_{c}"] / league.replace(0, np.nan)
    return pa[["game_id", "opponent_team", "position", *[f"def_{c}" for c in cols]]]


def game_read(team: str, game_id: str, preds: dict):
    """Marv team win p for the player's team from predictions.json (what the Kalshi legacy prop path checks)."""
    for v in preds.values():
        if isinstance(v, dict) and v.get("sport") == "nfl" and v.get("game_id") == game_id and v.get("home_win") is not None:
            home = game_id.split("_")[3]; hw = float(v.get("master_home_win") or v["home_win"])
            tw = hw if team == home else 1 - hw
            return {"team_win_p": round(tw, 4), "verdict": "backs" if tw > 0.5 else "opposes"}
    return {"team_win_p": None, "verdict": "missing"}


def script_read(commence: str, home: str | None = None):
    try:
        sys.path.insert(0, "/opt/marv-feed"); import game_script as GS      # Marv Master game-script / pace read
        return GS.read("nfl", commence)
    except Exception as ex:
        return {"grade": None, "why": f"script n/a: {str(ex)[:60]}"}


def run() -> dict:
    t0 = time.time(); OUTD.mkdir(parents=True, exist_ok=True)
    led = _ledger(); now = datetime.now(timezone.utc)
    b = me_picks(now - timedelta(days=LOOKBACK_D))
    new = b[~b.id.isin(list(led))] if not b.empty else b
    stats = {"picks_window": int(len(b)), "new": int(len(new))}
    if new.empty or not MODELS.exists():
        stats["note"] = "nothing new" if new.empty else f"missing {MODELS.name}"
        return stats
    players = P.load_players(CACHE, list(range(2019, 2027)), 2026); games = P.load_games(CACHE)
    p26 = players[players.season == 2026].sort_values("week")
    last = p26.groupby("player_id").tail(1).assign(_n=lambda d: d.player_display_name.map(norm_name))
    g26 = games[games.season == 2026].copy(); g26["day"] = pd.to_datetime(g26.gameday)
    keym, fut = {}, []
    for r in new.itertuples():
        cand = last[last._n == norm_name(r.player)]
        if len(cand) != 1:
            continue
        pr = cand.iloc[0]; day = r.cu.tz_convert("America/New_York").tz_localize(None).normalize()
        gm = g26[((g26.home_team == pr.team) | (g26.away_team == pr.team)) & ((g26.day - day).abs() <= pd.Timedelta(days=1))]
        if gm.empty:
            continue
        gm = gm.iloc[0]; keym[r.id] = (pr.player_id, gm.game_id, pr.team)
        if not ((players.player_id == pr.player_id) & (players.game_id == gm.game_id)).any():
            fut.append({"player_id": pr.player_id, "player_display_name": pr.player_display_name, "position": pr.position, "season": 2026,
                        "week": gm.week, "game_id": gm.game_id, "team": pr.team,
                        "opponent_team": gm.away_team if gm.home_team == pr.team else gm.home_team})
    allp = pd.concat([players, pd.DataFrame(fut)], ignore_index=True) if fut else players
    ids = {v[0] for v in keym.values()}
    sub = allp[allp.player_id.isin(ids)]
    rows = P.build_rows(sub, games).drop(columns=[c for c in ("def_passing_yards", "def_rushing_yards", "def_receiving_yards", "def_receptions")], errors="ignore")
    rows = rows.merge(defense_table(allp, games), on=["game_id", "opponent_team", "position"], how="left")
    rk = rows.set_index(["player_id", "game_id"])
    models = pickle.load(open(MODELS, "rb"))
    gd = dict(zip(games.game_id, pd.to_datetime(games.gameday)))
    preds = json.loads(PREDS.read_text()) if PREDS.exists() else {}
    out = []
    for r in new.itertuples():
        rec = {"bet_id": int(r.id), "alert_id": None if pd.isna(r.alert_id) else int(r.alert_id), "graded_at": now.isoformat(),
               "ts": r.ts, "commence": r.commence, "player": r.player, "market": r.market, "side": r.side, "line": r.line,
               "price": r.price, "me_ev": r.ev, "me_fair_p": r.fair_p, "tag": r.tag, "stars": 1 if r.ev < 0.07 else (2 if r.ev <= 0.10 else 3)}
        if r.id not in keym or keym[r.id][:2] not in rk.index:
            rec.update(grade="neutral", thumb=PG.THUMBS["neutral"], why="player/game not in Marv data", game=None)
            out.append(rec); continue
        pid, gid, team = keym[r.id]
        rr = rk.loc[(pid, gid)]; rr = rr.iloc[0] if isinstance(rr, pd.DataFrame) else rr
        mkey = MK[r.market]; mk = P.MARKETS[mkey]; m = models[mkey]
        proj = float(m.project(pd.DataFrame([rr]))[0]); pm = float(m.p_over(np.array([proj]), np.array([float(r.line)]))[0])
        h = allp[(allp.player_id == pid)].assign(_d=lambda d: d.game_id.map(gd))
        h = h[(h._d < pd.Timestamp(rr["date"])) & (h[PG.MARKETS[PG.GRADE_MARKETS[r.market]][1]].fillna(0) >= PG.MARKETS[PG.GRADE_MARKETS[r.market]][2])]
        h = h.sort_values("_d", ascending=False).head(16)
        pf = np.nan
        if len(h) >= PG.MIN_GAMES:
            v = h[mk.stat].fillna(0).to_numpy(float); w = 0.5 ** (np.arange(len(v)) / PG.HALF_LIFE); pf = PG.p_over(v, w, float(r.line))
        u = mk.usage; ew, l3 = rr.get(f"ewm_{u}"), rr.get(f"l3_{u}")
        ut = (l3 - ew) / ew if ew and ew > 0 and l3 == l3 else 0.0
        nv = float(r.fair_p) if r.fair_p == r.fair_p and r.fair_p else implied(r.price)
        sc = script_read(r.commence)
        g = PG.prop_grade(r.side, float(r.price), nv, pm, pf, rr.get(f"def_{mk.stat}"), ut, rr.get("implied_pts"), sc)
        rec.update(g, game=gid, team=team, proj=round(proj, 1), pm_over=round(pm, 4), pf_over=None if pf != pf else round(float(pf), 4),
                   marv_game_read=game_read(team, gid, preds), script=sc)
        out.append(rec)
    PG.cap_same_game_overs([x for x in out if x.get("game")])
    with open(LEDGER, "a") as f:
        for x in out:
            f.write(json.dumps(x, default=lambda o: None if o != o else (float(o) if isinstance(o, (np.floating, np.integer)) else str(o))) + "\n")
    stats.update(graded=len(out), up=sum(x["grade"] == "up" for x in out), secs=round(time.time() - t0, 1))
    return stats


def report() -> dict:
    led = _ledger()
    if not led:
        return {"note": "no shadow grades yet"}
    con = sqlite3.connect(f"file:{AJDB}?mode=ro", uri=True)
    st = dict(con.execute("select id, status from paper_bets where id in ({})".format(",".join(str(int(k)) for k in led))).fetchall())
    con.close()
    rows = [dict(v, status=st.get(k)) for k, v in led.items()]
    g = [r for r in rows if r["status"] in ("won", "lost")]

    def agg(xs):
        n = len(xs); w = sum(x["status"] == "won" for x in xs)
        pnl = sum(payout(x["price"]) if x["status"] == "won" else -1.0 for x in xs)
        return {"n": n, "W": w, "L": n - w, "win_pct": round(100 * w / n, 1) if n else None, "roi_pct": round(100 * pnl / n, 1) if n else None}
    s = {"updated": datetime.now(timezone.utc).isoformat(), "graded_total": len(g), "pending": len(rows) - len(g),
         "(a) ME + Marv game read not opposing": agg([x for x in g if (x.get("marv_game_read") or {}).get("verdict") != "opposes"]),
         "(b) prop grade 👍": agg([x for x in g if x.get("grade") == "up"]),
         "(c) all March_edge NFL pregame props": agg(g),
         "grade neutral": agg([x for x in g if x.get("grade") == "neutral"]), "grade 👎": agg([x for x in g if x.get("grade") == "down"]),
         "promotion_bar": "100+ graded 👍 and (b) ROI above both (a) and (c); then Marvin decides on MARV_PROPGRADE_LIVE=1",
         "live_flag": PG.GRADE_LIVE}
    b_ = s["(b) prop grade 👍"]
    s["meets_bar"] = bool(b_["n"] >= 100 and b_["roi_pct"] is not None
                          and b_["roi_pct"] > (s["(a) ME + Marv game read not opposing"]["roi_pct"] or -1e9)
                          and b_["roi_pct"] > (s["(c) all March_edge NFL pregame props"]["roi_pct"] or -1e9))
    OUTD.mkdir(parents=True, exist_ok=True); SUMMARY.write_text(json.dumps(s, indent=1, ensure_ascii=False))
    return s


if __name__ == "__main__":
    if "--report" not in sys.argv:
        print(json.dumps(run(), default=str))
    print(json.dumps(report(), ensure_ascii=False, default=str))
