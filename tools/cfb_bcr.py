"""Blue-chip ratio per CFB team-season from CFBD /recruiting/players (cached in state/cache/cfbd_api).
BCR(season) = 4/5-star high-school signees in classes season-3..season / all rated signees in those classes.
Writes state/cache/cfb_bcr.csv: season,team,bcr,signees,talent,depth (depth = talent / FBS median that season).
Usage (needs CFBD_API_KEY in the environment): .venv/bin/python tools/cfb_bcr.py
"""
import json, sys, collections, statistics, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marv.data.cfbd import CFBDClient
st = Path(__file__).resolve().parents[1] / "state"
cl = CFBDClient(os.environ.get("CFBD_API_KEY", ""), cache_dir=st / "cache" / "cfbd_api")
cls = {}
for y in range(2012, 2027):
    try:
        d = cl._get("/recruiting/players", year=y, classification="HighSchool")
    except Exception as e:
        print("fail", y, type(e).__name__); continue
    c = collections.defaultdict(lambda: [0, 0])
    for p in d or []:
        t = p.get("committedTo"); stars = p.get("stars") or 0
        if not t or not stars: continue
        c[t][1] += 1; c[t][0] += stars >= 4
    cls[y] = c; print(y, len(d or []), "recruits", flush=True)
rows = ["season,team,bcr,signees,talent,depth"]
for season in range(2015, 2027):
    tal = {}
    for f in (st / "cache" / "cfbd" / f"talent_{season}.json", st / "cache" / "cfbd_api" / f"talent_year{season}.json"):
        if f.exists():
            try:
                tal = {t.get("team", t.get("school")): float(t["talent"]) for t in json.loads(f.read_text())}; break
            except Exception: pass
    med = statistics.median(tal.values()) if tal else None
    teams = set().union(*[set(cls.get(y, {}).keys()) for y in range(season - 3, season + 1)])
    for t in teams:
        bc = sum(cls.get(y, {}).get(t, [0, 0])[0] for y in range(season - 3, season + 1))
        n = sum(cls.get(y, {}).get(t, [0, 0])[1] for y in range(season - 3, season + 1))
        if n < 20: continue
        tv = tal.get(t); dep = round(tv / med, 4) if tv and med else ""
        rows.append(f"{season},{t},{bc/n:.4f},{n},{tv if tv else ''},{dep}")
(st / "cache" / "cfb_bcr.csv").write_text("\n".join(rows) + "\n")
print("rows", len(rows) - 1)
