"""Marv Predict v2 -- PAPER Saturday CFB card, top-30, power ratings ONLY (no Monte Carlo, no market blend).

Data: CFBD parquet (history/lines/player box) + ESPN (schedule, fresh midweek scores, DraftKings ML/spread/total,
and FCS/ID check) + Marv's Odds API line file (state/lines.json) for spread/total. Nothing is sent anywhere.
Rules:
  ML: rating margin -> P(win) normal(margin, 16). Category points: 1 better offense rating, 1 better defense rating.
      2-0 sweep -> that side. Split (1-1) or |margin| < 3 -> star H2H tie-break (QB pass yds/g, RB1 rush yds/g,
      WR1 rec yds/g, season to date; kicker not in data). Star tally decides; star tie -> margin side.
  O/U: rating total vs line gives a side; trend FADE (both teams over in 2+ of last 3 -> UNDER, both under 2+ -> OVER).
       A play only when rating side and fade side agree. Injuries / QB changes are INFO ONLY (backtest: adjusting hurts).
  Display gate (marv/proven.py): ML shows NO % / fair price / edge (ratings ML loses at Bovada and the market's
       Brier beats it). O/U UNDER from the over-trend fade prints its proven backtest; O/U OVER (under-trend fade)
       is unproven, no %.
  Also writes state/marv_predict/h2h_cfb.json for the March_edge H2H overlay (marv.bridge.overlay).
Usage: python cfb_card.py 2026-10-10 OUTDIR
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, "/opt/marv-bot")
from marv import proven  # noqa: E402
from marv.models import Game  # noqa: E402
from marv.ratings import fit_ratings  # noqa: E402
from marv.stats.ncaaf import NCAAF, TOP_N  # noqa: E402

CACHE = Path("/opt/marv-bot/state/cache")
LINES = Path("/opt/marv-bot/state/lines.json")
H2H_OUT = Path("/opt/marv-bot/state/marv_predict/h2h_cfb.json")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/football/college-football"
UA = {"User-Agent": "Mozilla/5.0 (marv-predict-paper)"}
SD = 16.0


def phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def american(p):
    p = min(max(p, 0.01), 0.99)
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


def implied(price):
    price = float(price)
    return -price / (-price + 100) if price < 0 else 100 / (price + 100)


def espn_day(day):
    r = requests.get(f"{ESPN}/scoreboard", params={"dates": day.strftime("%Y%m%d"), "groups": 80, "limit": 400},
                     headers=UA, timeout=30)
    r.raise_for_status()
    return r.json().get("events", [])


def parse(ev):
    c = ev["competitions"][0]
    t = {x["homeAway"]: x for x in c["competitors"]}
    o = (c.get("odds") or [{}])[0]
    ml = o.get("moneyline") or {}

    def mlp(side):
        for k in ("close", "open"):
            v = ((ml.get(side) or {}).get(k) or {}).get("odds")
            if v and v not in ("EVEN", "OFF"):
                return float(v.replace("+", ""))
        return np.nan
    done = c.get("status", {}).get("type", {}).get("completed", False)
    return dict(game_id=str(ev["id"]), hid=str(t["home"]["team"]["id"]), aid=str(t["away"]["team"]["id"]), date=pd.Timestamp(ev["date"]).tz_convert(None),
                home_e=t["home"]["team"]["location"], away_e=t["away"]["team"]["location"],
                neutral=bool(c.get("neutralSite")), done=done,
                hp=float(t["home"]["score"]) if done else np.nan, ap=float(t["away"]["score"]) if done else np.nan,
                dk_hml=mlp("home"), dk_aml=mlp("away"), dk_total=o.get("overUnder"), dk_details=o.get("details"))


def espn_summary(gid):
    r = requests.get(f"{ESPN}/summary", params={"event": gid}, headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return 0.0


def espn_stars(summary, team_ids):
    """Season leaders (QB pass yds, RB rush yds, WR rec yds) from ESPN's game summary, keyed by ESPN team id."""
    out = {}
    for t in summary.get("leaders", []):
        tid = str(t["team"].get("id"))
        d = {}
        for c in t.get("leaders", []):
            key = {"passingYards": "QB", "rushingYards": "RB", "receivingYards": "WR"}.get(c.get("name"))
            if key and c.get("leaders"):
                l = c["leaders"][0]
                d[key] = (l["athlete"].get("displayName", "?"), _num(l.get("value", 0)))
        out[tid] = d
    return out


def last_game_qb(gid, team_id):
    """Who threw the most passes for this team in its last game (ESPN box score)."""
    try:
        box = espn_summary(gid).get("boxscore", {})
    except Exception:
        return None
    for tp in box.get("players", []):
        if str(tp["team"].get("id")) != str(team_id):
            continue
        for stat in tp.get("statistics", []):
            if stat.get("name") == "passing" and stat.get("athletes"):
                keys = stat.get("keys", [])
                i = keys.index("completions/passingAttempts") if "completions/passingAttempts" in keys else 0
                best = max(stat["athletes"], key=lambda a: _num(str(a["stats"][i]).split("/")[-1]) if a.get("stats") else 0)
                return best["athlete"].get("displayName")
    return None


def main(card_day, outdir):
    day = pd.Timestamp(card_day)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    season = day.year
    g = pd.concat([pd.read_parquet(CACHE / f"cfb_games_{y}.parquet") for y in (season - 1, season)], ignore_index=True)
    g["game_id"] = g["game_id"].astype(str)
    g = g.drop_duplicates("game_id").set_index("game_id")
    notes = []
    # ESPN: fresh scores for the 6 days before the card (CFBD cache can lag midweek), then the card's schedule.
    fresh = 0
    for d in pd.date_range(day - timedelta(days=6), day - timedelta(days=1)):
        try:
            for ev in espn_day(d):
                e = parse(ev)
                if e["done"] and e["game_id"] in g.index and pd.isna(g.at[e["game_id"], "home_points"]):
                    g.at[e["game_id"], "home_points"], g.at[e["game_id"], "away_points"] = e["hp"], e["ap"]
                    fresh += 1
        except Exception as exc:  # ESPN down -> CFBD only
            notes.append(f"ESPN scores {d.date()} unavailable: {exc}")
    notes.append(f"ESPN filled {fresh} recent final scores missing from the CFBD cache")
    sched = []
    for d in (day, day + timedelta(days=1)):  # late Saturday kickoffs land on Sunday UTC
        sched += [parse(ev) for ev in espn_day(d)]
    sched = [s for s in sched if (s["date"] - timedelta(hours=5)).date() == day.date()]
    g = g.reset_index()
    fbs = g[g["home_fbs"] & g["away_fbs"] & g["home_points"].notna() & (g["date"] < day)
            & (g["date"] >= day - timedelta(days=500))]
    games = [Game(id=r.game_id, sport="cfb", start=r.date.to_pydatetime(), home=r.home, away=r.away,
                  neutral=bool(r.neutral), completed=True, home_score=r.home_points, away_score=r.away_points)
             for r in fbs.itertuples()]
    rt = fit_ratings(games, NCAAF.rating_params, as_of=day.to_pydatetime())
    ranked = sorted(rt.offense, key=rt.strength, reverse=True)
    top = set(ranked[:TOP_N])
    # O/U trend: last 3 vs closing total
    line = g["total"]
    done = g["home_points"].notna() & line.notna() & (g["date"] < day)
    tr = pd.concat([pd.DataFrame({"date": g["date"], "team": g[s], "over": (g["home_points"] + g["away_points"] > line)})[done]
                    for s in ("home", "away")]).sort_values("date")
    last3 = tr.groupby("team").tail(3).groupby("team")["over"].agg(["sum", "count"])
    played = g[g["home_points"].notna() & (g["date"] < day) & (g["season"] == season)]
    gp = pd.concat([played["home"], played["away"]]).value_counts()
    def last_gid(team):
        d = played[(played["home"] == team) | (played["away"] == team)]
        return d.sort_values("date")["game_id"].iloc[-1] if len(d) else None
    lines = json.loads(LINES.read_text()) if LINES.exists() else {}
    byid = g.set_index("game_id")
    rows = []
    for s in sched:
        gid = s["game_id"]
        home, away = (byid.at[gid, "home"], byid.at[gid, "away"]) if gid in byid.index else (s["home_e"], s["away_e"])
        if home not in top and away not in top:
            continue
        if rt.games_played.get(home, 0) < 3 or rt.games_played.get(away, 0) < 3:
            continue
        hs, as_ = rt.expected(home, away, s["neutral"])
        margin, total = hs - as_, hs + as_
        ph = phi(margin / SD)
        off = np.sign(rt.offense[home] - rt.offense[away])
        dfn = np.sign(rt.defense[away] - rt.defense[home])
        ch, ca = int(off > 0) + int(dfn > 0), int(off < 0) + int(dfn < 0)
        method, star_txt, tally = "", "", None
        if ch == 2 or ca == 2:
            pick = home if ch == 2 else away
            method = "category sweep 2-0"
        else:
            pick = home if margin > 0 else away
            method = "split 1-1, margin"
        info = []
        try:
            summ = espn_summary(gid)
            sl = espn_stars(summ, (s["hid"], s["aid"]))
        except Exception as exc:
            sl = {}
            info.append(f"ESPN leaders unavailable ({exc})")
        sh, sa = sl.get(s["hid"], {}), sl.get(s["aid"], {})
        if all(k in sh and k in sa for k in ("QB", "RB", "WR")):
            tally = {home: 0, away: 0}
            parts = []
            for k in ("QB", "RB", "WR"):
                a, b = sh[k][1] / max(gp.get(home, 1), 1), sa[k][1] / max(gp.get(away, 1), 1)
                if round(a) != round(b):
                    tally[home if a > b else away] += 1
                parts.append(f"{k} {sh[k][0]} {a:.0f}/g v {sa[k][0]} {b:.0f}/g")
            star_txt = f"stars {home} {tally[home]}-{tally[away]} {away} ({'; '.join(parts)}; K n/a)"
            if ch == ca and tally[home] != tally[away]:
                pick = home if tally[home] > tally[away] else away
                method = "split 1-1, star H2H tie-break"
            # QB check (ESPN box of each team's last game vs season passing leader) -- info only
            for t, tid, d in ((home, s["hid"], sh), (away, s["aid"], sa)):
                lg = last_gid(t)
                q = last_game_qb(lg, tid) if lg else None
                if q and q != d["QB"][0]:
                    info.append(f"{t}: last game QB {q}, season leader {d['QB'][0]} -- QB change? (info only, lean-under note)")
        p_pick = ph if pick == home else 1 - ph
        ln = lines.get(f"cfb:{gid}", {})
        mkt_total = ln.get("total") if ln.get("total") is not None else s["dk_total"]
        mkt_spread = ln.get("spread")
        ou, ou_txt, fade_side = "", "", ""
        if mkt_total:
            rating_side = "OVER" if total > float(mkt_total) else "UNDER"
            lh, la = last3.loc[home] if home in last3.index else None, last3.loc[away] if away in last3.index else None
            fade = ""
            if lh is not None and la is not None and lh["count"] == 3 and la["count"] == 3:
                if lh["sum"] >= 2 and la["sum"] >= 2:
                    fade = "UNDER"
                elif lh["sum"] <= 1 and la["sum"] <= 1:
                    fade = "OVER"
                ou_txt = f"last3 overs {int(lh['sum'])}/3 & {int(la['sum'])}/3"
            ou = f"{rating_side} {mkt_total}" if fade == rating_side else ""
            fade_side = fade
            ou_txt = f"rating total {total:.1f} vs {mkt_total} -> {rating_side}; fade-trend {fade or 'none'} ({ou_txt})"
        dk = s["dk_hml"] if pick == home else s["dk_aml"]
        edge = (p_pick - implied(dk)) if not pd.isna(dk) else np.nan
        if not pd.isna(dk) and abs(p_pick - implied(dk)) > 0.15:
            info.append("ratings far from the market price -- low trust (ratings-only, early season)")
        paper_play = (not pd.isna(edge)) and edge >= 0.04 and p_pick >= 0.5
        rows.append(dict(game_id=gid, kickoff_ct=(s["date"] - timedelta(hours=5)).strftime("%a %I:%M %p"),
                         home=home, away=away, home_rank=ranked.index(home) + 1, away_rank=ranked.index(away) + 1,
                         margin=round(margin, 1), pick=pick, p_pick=round(p_pick, 3), fair=american(p_pick),
                         cat=f"{ch}-{ca}", method=method, dk_ml=dk, edge=None if pd.isna(edge) else round(edge, 3),
                         spread_oddsapi=mkt_spread, total_line=mkt_total, rating_total=round(total, 1), ou_play=ou,
                         ou_detail=ou_txt, stars=star_txt, paper_play=paper_play, info=" | ".join(info), dk=s["dk_details"],
                         cat_home=ch, cat_away=ca, stars_home=tally[home] if tally else None,
                         stars_away=tally[away] if tally else None, fade=fade_side))
    df = pd.DataFrame(rows).sort_values("p_pick", ascending=False)
    df.to_csv(outdir / f"cfb_card_{day.date()}.csv", index=False)
    try:  # H2H data for the March_edge overlay (bridge.overlay); ratings-only numbers, no probabilities
        H2H_OUT.parent.mkdir(parents=True, exist_ok=True)
        keep = ["game_id", "home", "away", "margin", "rating_total", "cat_home", "cat_away", "stars_home", "stars_away",
                "fade", "pick", "method", "total_line"]
        recs = json.loads(df[keep].to_json(orient="records"))
        tmp = H2H_OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps({"card_day": str(day.date()), "built": datetime.now(timezone.utc).isoformat(),
                                   "games": recs}, indent=1))
        tmp.replace(H2H_OUT)
    except OSError as exc:
        notes.append(f"h2h overlay file not written: {exc}")
    md = [f"# Marv Predict (ratings-only) PAPER card -- CFB {day.date()} -- top {TOP_N}", "",
          "PAPER ONLY. Not sent to Telegram. No ML % is shown: ratings ML hits ~75% (92% at 80%+) but ROI is negative at Bovada"
          " and the market's probability is better calibrated, so it is unproven. A % appears only for proven logic (proven-logic.md).",
          "O/U shown only when rating total and FADE-trend agree. Injuries/QB changes are info only (ESPN CFB injury feed is empty/stale).", ""]
    md += [f"- {n}" for n in notes] + [""]
    for r in df.itertuples():
        md.append(f"**{r.away} (#{r.away_rank}) @ {r.home} (#{r.home_rank})** {r.kickoff_ct} CT")
        md.append(("  PAPER TRACK (model price gap, unproven)\n" if r.paper_play else "")
                  + f"  ML: {r.pick} ({proven.pct('cfb', 'ml')}) | {r.method} (cat {r.cat}) | margin {r.margin:+.1f}"
                  + (f" | DK {r.dk_ml:+.0f}" if not pd.isna(r.dk_ml) else " | DK ML n/a"))
        if r.ou_play:
            sig = "OVER-FADE" if r.ou_play.startswith("UNDER") else "UNDER-FADE"
            ou_show = f"{r.ou_play} ({proven.pct('cfb', 'total', signal=sig)})"
        else:
            ou_show = "no play"
        md.append(f"  Line: {r.dk} / OddsAPI spread {r.spread_oddsapi} | O/U: {ou_show} -- {r.ou_detail}")
        if r.stars:
            md.append(f"  {r.stars}")
        if r.info:
            md.append(f"  info: {r.info}")
        md.append("")
    (outdir / f"cfb_card_{day.date()}.md").write_text("\n".join(md))
    print("\n".join(md))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
