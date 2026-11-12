"""NBA in-game prop pacer for AJ Bet Bot alerts (Marvin approved 2026-10-30 7:15 PM ET).
Separate from pacer_live.py so Kalpoly lanes are untouched. Runs in this folder's venv (sklearn); AJ calls it as a
subprocess: stdin JSON list of {event_id, player, stat, thr} -> stdout JSON list of {p_over, info} or {err}.
Off: env PACER_NBA_OFF=1 or file PACER_NBA_OFF in /opt/marv-feed or this folder."""
import json, os, sys, re, math, pickle, unicodedata, urllib.request, getpass
from datetime import datetime
from zoneinfo import ZoneInfo
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
RATES = "/opt/polymarket-paper/data/nba_rates_cache.json"
STATS = ("PTS", "REB", "AST", "3PM", "STL", "BLK", "PRA")
def is_off():
    if os.environ.get("PACER_NBA_OFF", "").strip().lower() in ("1", "on", "true", "yes", "off"): return True
    return any(os.path.exists(os.path.join(d, "PACER_NBA_OFF")) for d in ("/opt/marv-feed", HERE))
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]+", "", s.replace("-", " "))
    return " ".join(w for w in s.split() if w not in {"jr", "sr", "ii", "iii", "iv", "v"})
def _get(u): return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=12).read())
def _n(v):
    try: return float(str(v).split("-")[0].replace(",", ""))
    except Exception: return 0.0
def _gc():
    import importlib.util
    mod = getattr(_gc, "mod", None)
    if mod is not None:
        return mod
    here = os.path.dirname(os.path.abspath(__file__)); up = here; cands = []
    for _ in range(5):
        cands.append(os.path.join(up, "game_clock.py")); up = os.path.dirname(up)
    cands.append("/opt/marv-feed/game_clock.py")
    for c in cands:
        if os.path.isfile(c):
            spec = importlib.util.spec_from_file_location("game_clock_pacer_nba", c)
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod); _gc.mod = mod; return mod
    raise ImportError("game_clock.py")
def clk(c, period_minutes=12.0):
    """Minutes left in the period. Ambiguous or unparseable clocks raise so the bet is not priced."""
    left = _gc().parse_period_clock(c, period_minutes)
    if left is None:
        raise ValueError("ambiguous or unparseable clock: %r" % (c,))
    return left
def state(eid):
    s = _get(f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary?event={eid}")
    comp = s["header"]["competitions"][0]; st = comp.get("status") or {}
    per = int(st.get("period") or 0); left = clk(st.get("displayClock") or "12:00")
    el = 48.0 if per > 4 else min(48.0, max(0.0, (per - 1) * 12 + 12 - left))
    side_of = {c["team"]["id"]: c["homeAway"] for c in comp["competitors"]}
    score = {c["homeAway"]: _n(c.get("score")) for c in comp["competitors"]}
    pl = {}
    for tm in (s.get("boxscore") or {}).get("players", []) or []:
        side = side_of.get(tm["team"]["id"]); b = tm["statistics"][0]; nm = b.get("names") or []
        for a in b.get("athletes", []):
            v = dict(zip(nm, a.get("stats") or []))
            c = {"PTS": _n(v.get("PTS")), "REB": _n(v.get("REB")), "AST": _n(v.get("AST")), "3PM": _n(v.get("3PT")), "STL": _n(v.get("STL")), "BLK": _n(v.get("BLK"))}
            c["PRA"] = c["PTS"] + c["REB"] + c["AST"]
            pl[norm(a["athlete"]["displayName"])] = dict(id=a["athlete"]["id"], side=side, starter=int(bool(a.get("starter"))), MIN=_n(v.get("MIN")), PF=int(_n(v.get("PF"))), cur=c)
    return dict(el=el, per=per, score=score, players=pl)
def rates(aid):
    try:
        r = json.load(open(RATES)).get(str(aid))
        if r and r.get("min"): return r
    except Exception: pass
    fn = f"/tmp/pacer_rates_{getpass.getuser()}_nba.json"; day = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    try: cache = json.load(open(fn))
    except Exception: cache = {}
    if str(aid) in cache and cache[str(aid)].get("d") == day: return cache[str(aid)]
    d = _get(f"https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{aid}/gamelog")
    st = next((x for x in (d.get("seasonTypes") or []) if "Regular" in (x.get("displayName") or "")), None); r = {"d": day}
    if st:
        cat = (st.get("categories") or [{}])[0]; gp = len(cat.get("events") or []); t = dict(zip(d.get("names") or [], cat.get("totals") or []))
        mn = _n(t.get("minutes"))
        if gp >= 3 and mn > 0:
            pg = {"PTS": _n(t.get("points")), "REB": _n(t.get("totalRebounds")), "AST": _n(t.get("assists")), "3PM": _n(t.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted")),
                  "STL": _n(t.get("steals")), "BLK": _n(t.get("blocks"))}
            pg["PRA"] = pg["PTS"] + pg["REB"] + pg["AST"]
            r = {"d": day, "gp": gp, "min": mn, **{k: v / mn for k, v in pg.items()}}
    cache[str(aid)] = r
    try: json.dump(cache, open(fn + ".tmp", "w")); os.replace(fn + ".tmp", fn)
    except Exception: pass
    return r
def price(q, P, S, pd):
        try:
            if q["stat"] not in STATS: raise ValueError("stat not covered")
            import time as _t
            eid = str(q["event_id"])
            if eid not in S or _t.time() - S[eid][0] > 20: S[eid] = (_t.time(), state(eid))
            g = S[eid][1]
            p = g["players"].get(norm(q["player"]))
            if not p: raise ValueError("player not in ESPN box")
            rem = 48.0 - g["el"]
            if rem <= 0: raise ValueError("game over/OT")
            r = rates(p["id"])
            if not r.get("min") or q["stat"] not in r: raise ValueError("no season rate")
            cur = p["cur"][q["stat"]]; need = int(q["thr"]) - cur
            if need <= 0: raise ValueError("already hit")
            opp = "away" if p["side"] == "home" else "home"; margin = g["score"].get(p["side"], 0) - g["score"].get(opp, 0)
            d = pd.DataFrame([dict(avgmin=r["min"], mp=p["MIN"], el=g["el"], rem=rem, pf=p["PF"], margin=margin, starter=p["starter"], on=1,
                                   st=q["stat"], cur=cur, need=need, rate=r[q["stat"]])])
            return ({"p_over": float(P.prob(d)[0]), "info": {"cur": cur, "rate": round(r[q["stat"]] * r["min"], 2), "mp": p["MIN"], "el": round(g["el"], 1),
                        "min_left": round(float(P.left(d)[0]), 1), "margin": margin}})
        except Exception as e:
            return {"err": f"{type(e).__name__}: {str(e)[:100]}"}
def main():
    import pandas as pd
    from pacer import Pacer, FEAT  # noqa
    P = pickle.load(open(os.path.join(HERE, "pacer_nba.pkl"), "rb")); S = {}
    if "--serve" in sys.argv:   # 2026-10-31 Kalpoly co-process: one JSON request per line -> one JSON reply per line
        for line in sys.stdin:
            try: r = price(json.loads(line), P, S, pd)
            except Exception as e: r = {"err": f"{type(e).__name__}: {str(e)[:100]}"}
            sys.stdout.write(json.dumps(r) + "\n"); sys.stdout.flush()
        return
    json.dump([price(q, P, S, pd) for q in json.load(sys.stdin)], sys.stdout)
if __name__ == "__main__":
    main()
