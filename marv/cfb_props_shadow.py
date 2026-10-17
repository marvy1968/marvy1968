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


SUSPECT = 0.60  # 2026-10-17 (Marvin): a yardage projection below 60% of the line (or above 1/0.6) is a data problem


def suspect(proj: float, line: float) -> bool:
    return line > 0 and (proj < SUSPECT * line or proj > line / SUSPECT)


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

ESPN_SUM = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"


def _espn_box(gid: str, cache: Path) -> dict:
    import requests
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / f"{gid}.json"
    if f.exists():
        return _load(f, {})
    try:
        js = requests.get(ESPN_SUM, params={"event": gid}, timeout=20).json()
        box = {"players": js.get("boxscore", {}).get("players", []),
               "teams": [{"id": c["team"]["id"], "homeAway": c.get("homeAway")}
                         for c in (js.get("header", {}).get("competitions") or [{}])[0].get("competitors", [])]}
        f.write_text(json.dumps(box))
        return box
    except Exception:  # noqa: BLE001
        return {}


def fcs_games(games: pd.DataFrame, season: int, idmap: dict) -> pd.DataFrame:
    """2026-10-17: completed games vs FCS (or any) opponents missing from Marv's FBS game table, from ESPN team
    schedules of today's slate teams (idmap: ESPN team id -> Marv team name). Free, cached per day."""
    import requests
    have = set(games["game_id"].astype(str))
    cache = ST / "cache" / "espn_cfb_sched"; cache.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    rows = []
    for tid, name in idmap.items():
        f = cache / f"{tid}_{day}.json"
        try:
            js = _load(f, None) if f.exists() else None
            if js is None:
                js = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/{tid}/schedule",
                                  params={"season": season}, timeout=20).json()
                f.write_text(json.dumps(js))
        except Exception:  # noqa: BLE001
            continue
        for ev in js.get("events", []):
            c = (ev.get("competitions") or [{}])[0]
            if str(ev.get("id")) in have or not ((c.get("status") or {}).get("type") or {}).get("completed"):
                continue
            t = {x.get("homeAway"): x for x in c.get("competitors", [])}
            if "home" not in t or "away" not in t:
                continue
            nm = lambda x: idmap.get(str(x["team"]["id"])) or x["team"].get("location") or x["team"].get("displayName")
            sc = lambda x: float((x.get("score") or {}).get("value", x.get("score", 0)) if isinstance(x.get("score"), dict) else x.get("score") or 0)
            rows.append({"game_id": str(ev["id"]), "date": pd.Timestamp(ev["date"]).tz_convert("America/New_York").tz_localize(None).normalize(),
                         "season": season, "home": nm(t["home"]), "away": nm(t["away"]), "neutral": bool(c.get("neutralSite")),
                         "home_points": sc(t["home"]), "away_points": sc(t["away"]), "spread": np.nan, "total": np.nan,
                         "home_elo": np.nan, "away_elo": np.nan, "home_fbs": True, "away_fbs": False})
            have.add(str(ev["id"]))
    out = pd.DataFrame(rows)
    if not out.empty and games["game_id"].dtype != object:
        out = out[out["game_id"].str.isdigit()]
        out["game_id"] = out["game_id"].astype(games["game_id"].dtype)
    log.info("ESPN schedules: %d extra (FCS/missing) games for %d slate teams", len(out), len(idmap))
    return out


def espn_players(games: pd.DataFrame, season: int, teams: set) -> pd.DataFrame:
    """2026-10-17: current-season player game logs from ESPN box scores (the free play-by-play name parser mangles
    2026 names, e.g. 'o Huddle-Shotgun #16 A.Manning'). Only completed games of teams on today's slate; cached."""
    from concurrent.futures import ThreadPoolExecutor
    cache = ST / "cache" / "espn_cfb_box"
    cache.mkdir(parents=True, exist_ok=True)
    g = games[(games["season"] == season) & games["home_points"].notna() & (games["home"].isin(teams) | games["away"].isin(teams))]
    ids = [str(x) for x in g["game_id"]]
    with ThreadPoolExecutor(6) as ex:
        boxes = dict(zip(ids, ex.map(lambda i: _espn_box(i, cache), ids)))
    rows = []
    gi = g.assign(game_id=g["game_id"].astype(str)).set_index("game_id")
    for gid, box in boxes.items():
        ha = {str(t["id"]): t.get("homeAway") for t in box.get("teams", [])}
        for tp in box.get("players", []):
            side = ha.get(str(tp["team"]["id"]))
            if side not in ("home", "away"):
                continue
            team = gi.at[gid, side]; opp = gi.at[gid, "away" if side == "home" else "home"]
            per = {}
            for st in tp.get("statistics", []):
                keys = st.get("keys", [])
                for a in st.get("athletes", []):
                    nm = a["athlete"].get("displayName", "")
                    v = dict(zip(keys, a.get("stats", [])))
                    r = per.setdefault(nm, dict(passing_yards=0.0, attempts=0.0, completions=0.0, receiving_yards=0.0,
                                                receptions=0.0, targets=0.0, rushing_yards=0.0, carries=0.0))
                    num = lambda k: float(str(v.get(k, "0")).replace("--", "0") or 0)
                    if st.get("name") == "passing":
                        ca = str(v.get("completions/passingAttempts", "0/0")).split("/")
                        r["completions"], r["attempts"] = float(ca[0] or 0), float(ca[-1] or 0)
                        r["passing_yards"] = num("passingYards")
                    elif st.get("name") == "rushing":
                        r["carries"], r["rushing_yards"] = num("rushingAttempts"), num("rushingYards")
                    elif st.get("name") == "receiving":
                        r["receptions"], r["receiving_yards"] = num("receptions"), num("receivingYards")
                        r["targets"] = max(r["receptions"], num("receivingTargets"))
            for nm, r in per.items():
                if nm and nm.strip() != "Team":
                    rows.append(dict(game_id=gi.loc[gid, "game_id"] if "game_id" in gi.columns else gid, team=team,
                                     opponent_team=opp, player=nm, season=season, **r))
    out = pd.DataFrame(rows)
    if not out.empty:  # same dtype as the history game ids
        out["game_id"] = out["game_id"].astype(games["game_id"].dtype) if games["game_id"].dtype != object else out["game_id"]
    log.info("ESPN box players: %d games, %d player rows", len(boxes), len(out))
    return out


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
    slate_t = {t for e, _ in pairs for t in (_team(e["home_team"], teams), _team(e["away_team"], teams)) if t}
    idmap = {}
    for e, r in pairs:  # ESPN team id -> Marv team name, from today's ESPN event header
        bx = _espn_box(str(r["game_id"]), ST / "cache" / "espn_cfb_box_today")
        for t in bx.get("teams", []):
            nm = _team(e["home_team"] if t.get("homeAway") == "home" else e["away_team"], teams)
            if nm:
                idmap[str(t["id"])] = nm
    try:
        xg = fcs_games(games, season, idmap)
        if not xg.empty:
            games = pd.concat([games, xg], ignore_index=True)
    except Exception:  # noqa: BLE001
        log.exception("FCS games")
    ep = espn_players(games, season, slate_t)
    if not ep.empty:  # current season from ESPN boxes instead of the mangled play-by-play names
        players = pd.concat([players[players["season"] != season], ep], ignore_index=True)
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
    df = df.rename(columns={c: c[:-2] for c in df.columns if c.endswith("_x") and c[:-2] not in df.columns})
    if "date" not in df.columns and "date_y" in df.columns:
        df = df.rename(columns={"date_y": "date"})
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
        if suspect(float(r.proj), float(r.line)):
            g = "➖"; st = st or "suspect projection (<60% or >167% of line)"
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
        games, _ = C.load(ST / "cache", [season], season)
        players = espn_players(games, season, {b["team"] for b in open_})  # ESPN boxes (clean names)
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
    c = [b for b in _load(LEDGER, []) if b.get("grade") == "👍" and b.get("result") is None and b["start"] > now
         and not suspect(float(b["proj"]), float(b["line"]))]
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
