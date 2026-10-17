"""CFB pregame player props SHADOW (Marvin 2026-10-17): tracking only, never sends Telegram.

Every FBS game on today's slate that Marv projects (state/predictions.json) gets Bovada pregame props
(pass/rush/receiving yards; ~3 credits per game, each event fetched at most once per 6 h, cached in
state/cache/props_cfb_live/, capped by CFB_PROPS_MAX_CREDITS, default 200/day). Projection = the existing
marv.props.cfb model (player form + usage + opponent yards allowed + implied team total) with the game script
taken 50/50 from the market and Marv's own margin/total, then QB/skill news from marv.news_adjust (player listed
out/doubtful -> no grade). Grade per prop: 👍 the side with edge >= 5% and p >= 55%, else ➖.
Ledger: state/props_cfb_shadow.json (graded nightly from free play-by-play box stats); summary for /reportcard:
state/props_cfb_shadow_summary.json. No alert ever until 100+ graded 👍 picks are profitable (and Marvin says so).
"""
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger("cfb_props_shadow")
ROOT = Path(__file__).resolve().parent.parent
ST = Path(os.environ.get("STATE_DIR", ROOT / "state"))
LEDGER, SUMMARY = ST / "props_cfb_shadow.json", ST / "props_cfb_shadow_summary.json"
PC = ST / "cache" / "props_cfb_live"
SPORT = "americanfootball_ncaaf"
MKTS = ["player_pass_yds", "player_rush_yds", "player_reception_yds"]
MIN_EDGE, MIN_P = 0.05, 0.55


def _load(p, d):
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return d


def _write(p, obj):
    tmp = p.with_suffix(".tmp"); tmp.write_text(json.dumps(obj, indent=1)); tmp.replace(p)


def _marv_games(now):
    from .data.teams import similarity
    P = _load(ST / "predictions.json", {})
    return [v for v in P.values() if v.get("sport") == "cfb" and now <= datetime.fromisoformat(v["start"]) <= now + timedelta(hours=18)], similarity


def run(now=None):
    from .data import oddsapi
    from .props import cfb as C, odds as O
    from .props.cfb_run import _models, season_of
    from .props.basketball_run import _team
    from .props.run import evaluate
    key = os.environ.get("ODDS_API_KEY", "")
    if not key:
        return "no key"
    now = now or datetime.now(timezone.utc)
    marv, sim = _marv_games(now)
    if not marv:
        return "no Marv CFB games in the next 18h"
    PC.mkdir(parents=True, exist_ok=True)
    budget = O.Budget(int(os.environ.get("CFB_PROPS_MAX_CREDITS", "200")))
    evs = O.events(key, SPORT)  # free
    pairs = []
    for e in evs:
        st = datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00"))
        if not (now <= st <= now + timedelta(hours=18)):
            continue
        best = max(marv, key=lambda r: min(sim(r["home"], e["home_team"]), sim(r["away"], e["away_team"])), default=None)
        if best and min(sim(best["home"], e["home_team"]), sim(best["away"], e["away_team"])) >= 0.75:
            pairs.append((e, best))
    got, used = [], 0
    for e, r in pairs:
        f = PC / f"{e['id']}.json"
        fresh = f.exists() and (now.timestamp() - f.stat().st_mtime) < 6 * 3600
        data = _load(f, {}) if f.exists() else {}
        if not fresh:
            try:
                data = O.event_props(key, SPORT, e["id"], MKTS, "bovada", budget); f.write_text(json.dumps(data)); used += 1
            except RuntimeError:
                pass
            except Exception as exc:  # noqa: BLE001
                log.warning("props %s: %s", e["id"], exc)
        lr = O.prop_rows(data, "bovada") if data else pd.DataFrame()
        if not lr.empty:
            lr = lr[lr["book"].astype(str).str.lower().str.contains("bovada")] if "book" in lr else lr
            got.append(lr.assign(game_id=e["id"]))
    if not got:
        return f"no Bovada CFB props posted ({budget.used} credits)"
    season = season_of(now)
    log.info("props fetched: %d events with lines, %s credits; building rows", len(got), budget.used)
    games, players = C.load(ST / "cache", list(range(season - 2, season + 1)), season)  # 3 seasons: lean + fast
    log.info("loaded %d games / %d player rows", len(games), len(players))
    teams = sorted(set(players["team"]))
    mlines = {}
    try:
        for ev in oddsapi.fetch(key, SPORT):  # same cached-in-run game odds Marv uses; 2 credits
            mlines[ev["id"]] = oddsapi.consensus(ev, "bovada")
    except Exception as exc:  # noqa: BLE001
        log.warning("game lines: %s", exc)
    last = players.merge(games[["game_id", "date"]], on="game_id").sort_values("date").groupby(["team", "player"]).tail(1)
    fg, fp, meta = [], [], {}
    posted = set(pd.concat(got, ignore_index=True)["player"].map(O.norm_name))
    for e, r in pairs:
        home, away = _team(e["home_team"], teams), _team(e["away_team"], teams)
        if not (home and away):
            continue
        day = pd.Timestamp(e["commence_time"]).tz_convert("America/New_York").tz_localize(None).normalize()
        o = mlines.get(e["id"])
        ms, mt = getattr(o, "spread", None), getattr(o, "total", None)
        vs, vt = -float(r["model_margin"]), float(r["model_total"])  # Marv: home spread view
        sp = vs if ms is None else 0.5 * ms + 0.5 * vs
        tt = vt if mt is None else 0.5 * mt + 0.5 * vt
        meta[e["id"]] = dict(game=f"{r['away']} @ {r['home']}", start=r["start"], espn=r["game_id"], home=r["home"], away=r["away"])
        fg.append({"game_id": e["id"], "date": day, "season": season, "home": home, "away": away, "neutral": False,
                   "home_points": np.nan, "away_points": np.nan, "spread": sp, "total": tt,
                   "home_elo": np.nan, "away_elo": np.nan, "home_fbs": True, "away_fbs": True})
        for team, opp in ((home, away), (away, home)):
            for q in last[(last["team"] == team) & (last["date"] >= day - pd.Timedelta(days=300))
                          & last["player"].map(O.norm_name).isin(posted)].itertuples():
                fp.append({"game_id": e["id"], "team": team, "opponent_team": opp, "player": q.player, "season": season})
    log.info("building rows (%d future player rows)", len(fp))
    rows = C.build_rows(pd.concat([games, pd.DataFrame(fg)], ignore_index=True), pd.concat([players, pd.DataFrame(fp)], ignore_index=True))
    log.info("rows built; fitting models")
    models = _models(rows[~rows["game_id"].isin(set(meta))], season)  # history rows: same features, built once
    log.info("models fit; pricing")
    df = evaluate(pd.concat(got, ignore_index=True), rows[rows["game_id"].isin(meta)], models, C.MARKETS)
    if df.empty:
        return "no props matched to players"
    # news: player listed out/doubtful in ESPN game injuries -> no grade
    log.info("priced %d props; news check", len(df))
    from . import news_adjust as N
    hurt = {}
    for gid, m in meta.items():
        try:
            _, inj = N._leaders("cfb", m["espn"])
            for t in (m["home"], m["away"]):
                for x in N._team_inj(inj, t):
                    if x["status"].startswith(N.BAD) or x["status"].startswith("questionable"):
                        hurt[O.norm_name(x["name"])] = x["status"]
        except Exception:  # noqa: BLE001
            pass
    book = _load(LEDGER, [])
    idx = {(b["event"], b["player"], b["market"]): b for b in book}
    out = []
    for r in df.itertuples():
        st = hurt.get(O.norm_name(r.player))
        g = "👍" if (r.edge >= MIN_EDGE and r.p_side >= MIN_P and not st) else "➖"
        rec = dict(event=r.game_id, espn=meta[r.game_id]["espn"], game=meta[r.game_id]["game"], start=meta[r.game_id]["start"],
                   date=str(pd.Timestamp(r.date).date()), player=r.player, team=r.team, market=r.market, stat=r.stat,
                   line=float(r.line), side=r.side, price=float(r.price), proj=round(float(r.proj), 1),
                   p=round(float(r.p_side), 3), edge=round(float(r.edge), 3), grade=g, news=st or "",
                   at=now.isoformat(), result=None)
        k = (rec["event"], rec["player"], rec["market"])
        if k in idx and idx[k].get("result") is None:
            idx[k].update(rec)  # refresh until kickoff
        elif k not in idx:
            book.append(rec); idx[k] = rec
        out.append(rec)
    _write(LEDGER, book)
    summarize(book)
    top = sorted([x for x in out if x["grade"] == "👍"], key=lambda x: -x["edge"])[:5]
    log.info("cfb props shadow: %d games, %d props priced, %d 👍, %s credits", len(meta), len(out), sum(x["grade"] == "👍" for x in out), budget.used)
    return json.dumps({"games": len(meta), "props": len(out), "thumbs": sum(x["grade"] == "👍" for x in out),
                       "credits": budget.used, "top5": top}, indent=1)


def grade():
    from .props import cfb as C, odds as O
    from .props.cfb_run import season_of
    from .props.run import payout
    book = _load(LEDGER, [])
    open_ = [b for b in book if b["result"] is None and b["start"] < (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()]
    if open_:
        season = season_of(datetime.now(timezone.utc))
        games, players = C.load(ST / "cache", [season], season)
        p = players.merge(games[["game_id", "date"]], on="game_id")
        p["_k"] = list(zip(p["date"].dt.strftime("%Y-%m-%d"), p["team"], p["player"].map(O.norm_name)))
        stats = p.drop_duplicates("_k").set_index("_k")
        for b in open_:
            k = (b["date"], b["team"], O.norm_name(b["player"]))
            if k in stats.index:
                v = float(stats.loc[[k], b["stat"]].fillna(0).iloc[0]); b["actual"] = v
                b["result"] = "push" if v == b["line"] else ("win" if (v > b["line"]) == (b["side"] == "Over") else "loss")
            elif b["start"] < (datetime.now(timezone.utc) - timedelta(days=4)).isoformat():
                b["result"] = "void"  # player didn't record a stat line (DNP) -> Bovada voids
        _write(LEDGER, book)
    return summarize(book)


def summarize(book):
    from .props.run import payout
    def rec(rows):
        d = [b for b in rows if b["result"] in ("win", "loss")]
        w = sum(b["result"] == "win" for b in d)
        u = sum(payout(b["price"]) if b["result"] == "win" else -1 for b in d)
        return {"w": w, "l": len(d) - w, "n": len(d), "units": round(u, 2), "roi": round(u / len(d), 4) if d else None}
    s = {"thumbs": rec([b for b in book if b["grade"] == "👍"]), "all_leans": rec(book),
         "proven": False, "updated": datetime.now(timezone.utc).isoformat()}
    t = s["thumbs"]; s["proven"] = bool(t["n"] >= 100 and (t["units"] or 0) > 0)
    _write(SUMMARY, s)
    return json.dumps(s)


def lookup(player: str, market: str, game_hint: str = ""):
    """Latest shadow projection for one prop (used by bridge H2H)."""
    from .props.odds import norm_name
    n = norm_name(player)
    c = [b for b in _load(LEDGER, []) if b["market"] == market and norm_name(b["player"]) == n]
    return sorted(c, key=lambda b: b["at"])[-1] if c else None


LABEL = {"player_pass_yds": "pass yds", "player_rush_yds": "rush yds", "player_reception_yds": "rec yds"}


def card_line(now=None):
    """One line for the /parlay + daily card: best upcoming 👍 Bovada CFB pregame prop (no % until proven)."""
    now = (now or datetime.now(timezone.utc)).isoformat()
    c = [b for b in _load(LEDGER, []) if b.get("grade") == "👍" and b.get("result") is None and b["start"] > now]
    if not c:
        return ""
    b = max(c, key=lambda x: x["edge"])
    s = _load(SUMMARY, {}); t = s.get("thumbs") or {}
    if s.get("proven") and t.get("n"):
        rec = f"{t['w']}-{t['l']} ({t['w'] / t['n']:.0%})"
    else:
        rec = f"{t.get('w', 0)}-{t.get('l', 0)} tracking"
    return (f"🏈 CFB prop 👍 {b['player']} {b['side']} {b['line']:g} {LABEL.get(b['market'], b['market'])} "
            f"({int(b['price']):+d} Bovada) · Marv proj {b['proj']:g} · {rec}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(grade() if "--grade" in sys.argv else run())
