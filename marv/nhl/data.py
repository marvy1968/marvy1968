"""Fetch NHL history: NHL API scores+goalies (2021-22..2025-26) and ESPN closing lines (2025-26)."""
import json, os, sys, time, glob, urllib.request
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor
OUT = sys.argv[1]; os.makedirs(OUT, exist_ok=True)
def get(u, tries=4):
    for i in range(tries):
        try:
            return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=30))
        except Exception as e:
            time.sleep(1.5 * (i + 1))
    return None
SEASONS = [(date(2021,10,12),date(2022,6,30)),(date(2022,10,7),date(2023,6,30)),(date(2023,10,10),date(2024,6,30)),
           (date(2024,10,4),date(2025,6,30)),(date(2025,10,7),date(2026,6,30))]
gf = os.path.join(OUT, "games.json")
games = json.load(open(gf)) if os.path.exists(gf) else {}
days = [a + timedelta(d) for a, b in SEASONS for d in range((b - a).days + 1)]
def day(d):
    j = get(f"https://api-web.nhle.com/v1/score/{d}")
    res = []
    for g in (j or {}).get("games", []):
        if g.get("gameType") not in (2, 3) or g.get("gameState") not in ("OFF", "FINAL"): continue
        res.append({"id": g["id"], "date": str(d), "start": g.get("startTimeUTC"), "type": g["gameType"],
            "home": g["homeTeam"]["abbrev"], "away": g["awayTeam"]["abbrev"],
            "hg": g["homeTeam"].get("score"), "ag": g["awayTeam"].get("score"),
            "hs": g["homeTeam"].get("sog"), "as": g["awayTeam"].get("sog"),
            "last": (g.get("gameOutcome") or {}).get("lastPeriodType")})
    return res
todo = [d for d in days if not any(v["date"] == str(d) for v in games.values())] if games else days
with ThreadPoolExecutor(8) as ex:
    for r in ex.map(day, todo):
        for g in r: games[str(g["id"])] = g
print("games", len(games)); json.dump(games, open(gf, "w"))
def box(gid):
    j = get(f"https://api-web.nhle.com/v1/gamecenter/{gid}/boxscore")
    if not j: return gid, None
    out = {}
    for side in ("homeTeam", "awayTeam"):
        gl = (j.get("playerByGameStats") or {}).get(side, {}).get("goalies", [])
        st = [x for x in gl if x.get("starter")] or gl[:1]
        out[side[:4]] = st[0]["playerId"] if st else None
    return gid, out
need = [k for k, v in games.items() if "hgoalie" not in v]
with ThreadPoolExecutor(8) as ex:
    for gid, o in ex.map(box, need):
        if o: games[gid]["hgoalie"], games[gid]["agoalie"] = o["home"], o["away"]
json.dump(games, open(gf, "w")); print("goalies done")
# ESPN odds 2025-26 season (+ any later)
of = os.path.join(OUT, "odds.json"); odds = json.load(open(of)) if os.path.exists(of) else {}
evs = []
d = date(2025, 10, 7)
while d <= date.today():
    j = get(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={d:%Y%m%d}")
    for e in (j or {}).get("events", []):
        c = e["competitions"][0]; t = {x["homeAway"]: x["team"]["abbreviation"] for x in c["competitors"]}
        evs.append((e["id"], str(d), t.get("home"), t.get("away")))
    d += timedelta(1)
def summ(ev):
    j = get(f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/summary?event={ev[0]}")
    pc = (j or {}).get("pickcenter") or []
    return ev, (pc[0] if pc else None)
with ThreadPoolExecutor(8) as ex:
    for ev, p in ex.map(summ, [e for e in evs if e[0] not in odds]):
        if p:
            odds[ev[0]] = {"date": ev[1], "home": ev[2], "away": ev[3], "hml": (p.get("homeTeamOdds") or {}).get("moneyLine"),
                "aml": (p.get("awayTeamOdds") or {}).get("moneyLine"), "total": p.get("overUnder"),
                "over": p.get("overOdds"), "under": p.get("underOdds"), "spread": p.get("spread"),
                "hpl": (p.get("homeTeamOdds") or {}).get("spreadOdds"), "apl": (p.get("awayTeamOdds") or {}).get("spreadOdds")}
json.dump(odds, open(of, "w")); print("odds", len(odds))
