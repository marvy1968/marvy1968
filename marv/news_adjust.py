"""News-adjusted projections (Marvin 2026-10-17). Projections only: no messages, no vetoes, NBA untouched.
`python -m marv.news_adjust` (timer, every 20 min, nice 19): pulls free news and nudges predictions.json for games
that have not started:
  NFL  ESPN injuries API + starting QB = QB with most attempts in the team's latest game (nflverse player-week).
       Starter OUT/DOUBTFUL -> home margin shift = clip(4.91 x (starter EPA/db - backup EPA/db), +-7);
       backup = team's next QB by attempts (career EPA/db shrunk to replacement -0.15 below 300 dropbacks).
       Total -= 0.5 x |shift|.  (Calibrated 2020-23, tested 2024-25: half of the fitted 9.8-10.5 pts per EPA/db.)
       Top RB/WR (ESPN season leader) OUT -> 0.5 pt (small, uncalibrated).
  WNBA ESPN injuries + 12+ ppg players (box scores): margin 0.06 pt and total 0.022 pt per lost ppg
       (calibrated 2022-23, tested 2024-26, half of the fitted size).
  CFB  ESPN game summary injuries: passing leader OUT -> 2.5 pts margin, -1.5 total (uncalibrated, conservative).
  QUESTIONABLE is flagged but not adjusted. Original numbers kept in rec['pre_news']; re-applied from them each run,
  so a fresh Marv run (which rewrites predictions.json) is simply re-adjusted. Log: state/news_adj/log.json,
  CLV of adjusted vs other Marv picks: state/news_adj/summary.json. MARV_NEWS_ADJ=off disables."""
import json, os, sqlite3, sys, time
from datetime import datetime, timedelta, timezone
from math import erf, sqrt
from pathlib import Path
import requests

ST = Path(os.environ.get("MARV_STATE", "/opt/marv-bot/state")); PRED = ST / "predictions.json"
D = ST / "news_adj"; LOG = D / "log.json"; SUMM = D / "summary.json"
SITE = "https://site.api.espn.com/apis/site/v2/sports"
SD = {"nfl": 13.5, "cfb": 16.0, "wnba": 11.0}
QB_B, QB_CAP, REPL = 4.91, 7.0, -0.15
W_M, W_T = 0.06, 0.022
BAD = ("out", "doubtful", "injured reserve", "suspension")
UA = {"User-Agent": "Mozilla/5.0 (marv-news)"}


def _ncdf(x): return 0.5 * (1 + erf(x / sqrt(2)))
def _ninv(p):
    lo, hi = -8.0, 8.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if _ncdf(mid) < p else (lo, mid)
    return (lo + hi) / 2


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=20, headers=UA, **kw)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def espn_injuries(sport):
    js = _get(f"{SITE}/{ {'nfl': 'football/nfl', 'wnba': 'basketball/wnba'}[sport] }/injuries") or {}
    out = {}
    for t in js.get("injuries", []) or []:
        latest = {}
        for i in t.get("injuries", []) or []:
            a = i.get("athlete") or {}; nm = a.get("displayName", "")
            if nm and (nm not in latest or (i.get("date") or "") > latest[nm]["date"]):
                latest[nm] = {"name": nm, "pos": (a.get("position") or {}).get("abbreviation", ""), "status": str(i.get("status", "")).lower(), "date": i.get("date") or ""}
        out[t.get("displayName", "")] = list(latest.values())
    return out


def _short(n):
    p = n.split(); return f"{p[0][0]}. {' '.join(p[1:])}" if len(p) > 1 else n


# ---------------------------------------------------------------- NFL QB values from nflverse (cached by Marv)
_QB = {}
def nfl_qbs(season):
    if season in _QB:
        return _QB[season]
    import pandas as pd
    from .data.teams import NFL_TEAMS
    fr = []
    for y in range(2018, season + 1):
        p = ST / "cache" / f"nfl_players_week_{y}.csv"
        if p.exists():
            fr.append(pd.read_csv(p, low_memory=False, usecols=["player_display_name", "position", "team", "season", "week", "attempts", "sacks_suffered", "passing_epa"]))
    df = pd.concat(fr); df = df[df.position == "QB"].fillna({"attempts": 0, "sacks_suffered": 0, "passing_epa": 0})
    df["db"] = df.attempts + df.sacks_suffered
    car = df.groupby("player_display_name").agg(epa=("passing_epa", "sum"), db=("db", "sum"))
    def val(n):
        if n not in car.index: return REPL
        e, d = car.loc[n]; v = e / d if d > 0 else REPL; w = min(d, 300) / 300
        return v * w + REPL * (1 - w)
    cur = df[df.season == season]
    teams = {}
    for t, g in cur.groupby("team"):
        last = g[g.week == g.week.max()].sort_values("attempts", ascending=False)
        starter = last.player_display_name.iloc[0]
        others = g[g.player_display_name != starter].groupby("player_display_name").attempts.sum().sort_values(ascending=False)
        backup = others.index[0] if len(others) else None
        teams[NFL_TEAMS.get(t, t)] = dict(starter=starter, v_s=val(starter), backup=backup, v_b=val(backup) if backup else REPL)
    _QB[season] = teams
    return teams


def _leaders(sport, event_id):
    """ESPN summary: injuries + season leaders (QB/RB/WR) per team; used for CFB and NFL skill players."""
    js = _get(f"{SITE}/{ {'nfl': 'football/nfl', 'cfb': 'football/college-football'}[sport] }/summary", params={"event": event_id}) or {}
    lead = {}
    for t in js.get("leaders", []) or []:
        nm = (t.get("team") or {}).get("displayName", "")
        for cat in t.get("leaders", []) or []:
            ls = cat.get("leaders") or []
            if ls:
                lead.setdefault(nm, {})[cat.get("name")] = (ls[0].get("athlete") or {}).get("displayName", "")
    inj = {}
    for t in js.get("injuries", []) or []:
        nm = (t.get("team") or {}).get("displayName", "")
        inj[nm] = [{"name": (i.get("athlete") or {}).get("displayName", ""), "status": str(i.get("status", "")).lower()} for i in t.get("injuries", []) or []]
    return lead, inj


def _sim(a, b):
    a, b = (a or "").lower(), (b or "").lower(); return bool(a and b and (a in b or b in a))


def _team_key(tbl, team):
    """2026-10-17: best name match (was substring: 'Oregon' hit 'Oregon State Beavers'; exact-key lookups missed
    'Oregon' vs ESPN 'Oregon Ducks', so CFB QB/skill checks silently found nothing)."""
    try:
        from .data.teams import similarity
    except Exception:  # noqa: BLE001
        similarity = None
    best, sc = None, 0.0
    for k in tbl:
        v = similarity(k, team) if similarity else float(_sim(k, team))
        kl, tl = (k or "").lower(), (team or "").lower()
        if kl.startswith(tl + " ") and len(kl.split()) - len(tl.split()) == 1:
            v = 1.5  # "Kansas" -> "Kansas Jayhawks" (team + one-word mascot), not "Kansas State Wildcats"
        if kl == tl:
            v = 2.0
        if v > sc:
            best, sc = k, v
    return best if sc >= 0.75 else None


def _team_inj(tbl, team):
    k = _team_key(tbl, team)
    return tbl[k] if k is not None else []


def _team_lead(tbl, team):
    k = _team_key(tbl or {}, team)
    return tbl[k] if k is not None else {}


def adjust_game(sport, rec, ctx):
    """(home margin shift, total shift, why list, flags list)."""
    dm = dt = 0.0; why, flags = [], []
    for side, sg in (("home", 1), ("away", -1)):
        team = rec[side]
        if sport == "nfl":
            q = _team_lead(ctx["qbs"], team) or None; inj = _team_inj(ctx["inj"], team)
            if q:
                st = next((x["status"] for x in inj if x["name"] == q["starter"]), "")
                if st.startswith(BAD):
                    sh = max(-QB_CAP, min(QB_CAP, QB_B * (q["v_s"] - q["v_b"])))
                    dm -= sg * sh; dt -= 0.5 * abs(sh); why.append(f"QB {_short(q['starter'])} out")
                elif st.startswith("questionable"):
                    flags.append(f"QB {_short(q['starter'])} questionable")
            lead = _team_lead(ctx.get("lead", {}), team)
            for cat in ("rushingYards", "receivingYards"):
                n = lead.get(cat)
                if n and any(x["name"] == n and x["status"].startswith(BAD) for x in inj):
                    dm -= sg * 0.5; dt -= 0.3; why.append(f"{'RB' if cat[0] == 'r' and cat[1] == 'u' else 'WR'} {_short(n)} out")
        elif sport == "wnba":
            inj = _team_inj(ctx["inj"], team); lost = 0.0; names = []
            for x in inj:
                ppg = ctx["ppg"].get(x["name"])
                if ppg and ppg >= 12 and x["status"].startswith(BAD):
                    lost += ppg; names.append(_short(x["name"]))
            if lost:
                dm -= sg * W_M * lost; dt -= W_T * lost; why.append(f"{', '.join(names)} out")
        elif sport == "cfb":
            lead = _team_lead(ctx.get("lead", {}), team); inj = _team_inj(ctx.get("ginj", {}), team)
            n = lead.get("passingYards")
            if n and any(x["name"] == n and x["status"].startswith(BAD) for x in inj):
                dm -= sg * 2.5; dt -= 1.5; why.append(f"QB {_short(n)} out")
    return dm, dt, why, flags


def _wnba_ppg():
    try:
        import pandas as pd
        b = pd.read_parquet(ST / "cache" / "wnba_player_box_2026.parquet", columns=["athlete_display_name", "points", "minutes"])
        b = b[pd.to_numeric(b.minutes, errors="coerce").fillna(0) > 0]
        return b.groupby("athlete_display_name").points.mean().to_dict()
    except Exception:  # noqa: BLE001
        return {}


def run():
    if os.environ.get("MARV_NEWS_ADJ", "on").lower() in ("0", "off", "false", "no"):
        return 0
    D.mkdir(parents=True, exist_ok=True)
    P = json.loads(PRED.read_text()); now = datetime.now(timezone.utc)
    log = json.loads(LOG.read_text()) if LOG.exists() else []
    ctx = {"nfl": None, "wnba": None}
    changed = 0
    for key, rec in P.items():
        sport = rec.get("sport")
        if sport not in SD:
            continue
        try:
            st = datetime.fromisoformat(str(rec.get("start")).replace("Z", "+00:00"))
        except Exception:  # noqa: BLE001
            continue
        if not (now < st < now + timedelta(days=7)):
            continue
        raw = rec.get("pre_news") or {k: rec.get(k) for k in ("model_margin", "model_total", "home_win", "home_exp", "away_exp")}
        if raw.get("model_margin") is None:
            continue
        if ctx.get(sport) is None:
            if sport == "nfl":
                ctx["nfl"] = {"inj": espn_injuries("nfl"), "qbs": nfl_qbs(st.year if st.month >= 3 else st.year - 1)}
            elif sport == "wnba":
                ctx["wnba"] = {"inj": espn_injuries("wnba"), "ppg": _wnba_ppg()}
            else:
                ctx["cfb"] = {}
        c = dict(ctx[sport])
        if sport in ("cfb", "nfl") and rec.get("game_id") and str(rec["game_id"]).isdigit():
            lead, ginj = _leaders(sport, rec["game_id"]); c["lead"], c["ginj"] = lead, ginj
        elif sport == "nfl" and rec.get("espn_id"):
            c["lead"], _ = _leaders("nfl", rec["espn_id"])
        dm, dt, why, flags = adjust_game(sport, rec, c)
        new = dict(raw)
        if why:
            new["model_margin"] = round(raw["model_margin"] + dm, 2); new["model_total"] = round(raw["model_total"] + dt, 2)
            p0 = min(max(raw.get("home_win") or 0.5, 0.01), 0.99)
            new["home_win"] = round(_ncdf(_ninv(p0) + dm / SD[sport]), 4)
            if raw.get("home_exp") is not None:
                new["home_exp"] = round(raw["home_exp"] + dm / 2 + dt / 2, 2); new["away_exp"] = round(raw["away_exp"] - dm / 2 + dt / 2, 2)
        prev = rec.get("news_adj") or {}
        info = {"why": why, "flags": flags, "margin": round(dm, 2), "total": round(dt, 2), "at": now.isoformat()} if (why or flags) else None
        if (prev.get("why"), prev.get("flags")) != ((info or {}).get("why"), (info or {}).get("flags")):
            changed += 1
            if info:
                log.append({"key": key, "sport": sport, "game_id": rec.get("game_id"), "home": rec["home"], "away": rec["away"], **info})
        rec.update(new); rec["pre_news"] = raw
        if info:
            rec["news_adj"] = info
        else:
            rec.pop("news_adj", None)
    # re-read and merge only our fields to avoid clobbering concurrent writers
    cur = json.loads(PRED.read_text())
    for k, rec in P.items():
        if k in cur and "pre_news" in rec:
            for f in ("model_margin", "model_total", "home_win", "home_exp", "away_exp", "pre_news"):
                if f in rec:
                    cur[k][f] = rec[f]
            if "news_adj" in rec:
                cur[k]["news_adj"] = rec["news_adj"]
            else:
                cur[k].pop("news_adj", None)
    tmp = PRED.with_suffix(".news.tmp"); tmp.write_text(json.dumps(cur)); os.replace(tmp, PRED)
    LOG.write_text(json.dumps(log[-2000:], indent=1))
    summary(log)
    print(f"news_adjust: {sum(1 for r in cur.values() if r.get('news_adj'))} games with news, {changed} changed")
    return changed


def summary(log):
    """CLV of Marv picks on news-adjusted games vs all other Marv picks (marv_clv.db, beat = beat the close)."""
    ids = {str(x.get("game_id")) for x in log if x.get("why")}
    out = {"updated": datetime.now(timezone.utc).isoformat(), "adjusted_games": len(ids)}
    try:
        c = sqlite3.connect(f"file:{ST / 'marv_clv.db'}?mode=ro", uri=True)
        rows = c.execute("SELECT event_id, beat FROM clv WHERE src='marv' AND beat IS NOT NULL").fetchall()
        a = [b for e, b in rows if str(e) in ids]; o = [b for e, b in rows if str(e) not in ids]
        out["adjusted"] = {"n": len(a), "beat": round(sum(a) / len(a), 3) if a else None}
        out["other"] = {"n": len(o), "beat": round(sum(o) / len(o), 3) if o else None}
        out["line"] = (f"📰 News-adjusted Marv picks: beat close {out['adjusted']['beat']:.0%} of {len(a)}" if a else
                       "📰 News-adjusted Marv picks: none graded yet") + (f" (others {out['other']['beat']:.0%} of {len(o)})" if o else "")
    except Exception as e:  # noqa: BLE001
        out["line"] = "📰 News-adjusted Marv picks: CLV not available"; out["err"] = str(e)[:100]
    SUMM.write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    sys.exit(0 if run() is not None else 1)
